"""Healer 安装后的统一入口：healer --help。"""
import argparse
import functools
import json
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from agent.generator import Generator, snapshot_page
from agent.runner import Runner
from config.settings import SUT_BASE_URL, TEXT_MODEL, ZHIPU_API_KEY
from llm.client import LLMClient
from main import _save, execute_plan
from schemas.step import Step


def load_plan(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        if data.get('schema_version', 1) != 1:
            raise ValueError('不支持的计划版本')
        data = data.get('steps')
    if not isinstance(data, list) or not data:
        raise ValueError('计划必须是非空步骤数组，或含 steps 数组的对象')
    return [Step(**item) for item in data]


def _print_result(report, path):
    print(json.dumps({'status': report['run_status'], 'business_validation': report['business_validation'],
                      'report': str(path.resolve()), 'html': str(path.with_name('report.html').resolve())}, ensure_ascii=False))
    return 0 if report['run_status'] == 'passed' else 1


def _demo(output, timeout_ms):
    root = Path(str(files('healer_examples')))
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plan = load_plan(root / 'plan.json')
        report, path = execute_plan(plan, f'http://127.0.0.1:{server.server_port}', output,
                                    mode='rules', timeout_ms=timeout_ms, instruction='离线示例：商品 2 加购并校验数量')
        return _print_result(report, path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='healer', description='AI 增强 UI 自动化与安全自愈工具')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='执行已保存的 JSON 计划，默认离线规则模式')
    run.add_argument('--plan', required=True, type=Path)
    run.add_argument('--base-url', default=SUT_BASE_URL)
    run.add_argument('--mode', choices=['none', 'rules', 'hybrid'], default='rules')
    run.add_argument('--analyze', action='store_true', help='混合模式失败时额外调用模型分析')
    run.add_argument('--output', type=Path, default=Path('reports'))
    run.add_argument('--timeout-ms', type=int, default=10000)
    demo = sub.add_parser('demo', help='启动临时本地页面并演示安全自愈，无需模型或数据库')
    demo.add_argument('--output', type=Path, default=Path('reports'))
    demo.add_argument('--timeout-ms', type=int, default=500)
    generate = sub.add_parser('generate', help='只读页面快照后生成可查看的计划，不执行计划')
    generate.add_argument('instruction')
    generate.add_argument('--url', required=True)
    generate.add_argument('--plan-output', type=Path, default=Path('generated-plan.json'))
    generate.add_argument('--output', type=Path, default=Path('reports'))
    benchmark = sub.add_parser('benchmark', help='运行固定场景对照评估')
    benchmark.add_argument('--with-model', action='store_true')
    benchmark.add_argument('--repeats', type=int, default=1)
    benchmark.add_argument('--max-model-calls', type=int, default=12)
    shop = sub.add_parser('shophub', help='独立临时数据库中的真实 ShopHub 业务验证')
    shop.add_argument('--source', type=Path, required=True)
    sub.add_parser('doctor', help='检查本机配置，不打印密钥或调用模型')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            if args.timeout_ms <= 0:
                raise ValueError('timeout-ms 必须为正数')
            report, path = execute_plan(load_plan(args.plan), args.base_url, args.output,
                mode=args.mode, timeout_ms=args.timeout_ms, analyze=args.analyze)
            return _print_result(report, path)
        if args.command == 'demo':
            if args.timeout_ms <= 0:
                raise ValueError('timeout-ms 必须为正数')
            return _demo(args.output, args.timeout_ms)
        if args.command == 'generate':
            with Runner() as runner:
                runner.page.goto(args.url)
                evidence = snapshot_page(runner.page)
            client = LLMClient(max_calls=3)
            steps = Generator(client, args.url).generate(args.instruction, page_context=evidence)
            args.plan_output.parent.mkdir(parents=True, exist_ok=True)
            args.plan_output.write_text(json.dumps([s.model_dump(exclude_none=True) for s in steps], ensure_ascii=False, indent=2), encoding='utf-8')
            args.plan_output.with_suffix('.snapshot.json').write_text(evidence, encoding='utf-8')
            args.plan_output.with_suffix('.generation.json').write_text(json.dumps({
                'schema_version': 1, 'model': client.text_model, 'executed': False,
                'steps': len(steps), 'attempts': [asdict(a) for a in client.attempts],
                'usage': [asdict(c) for c in client.calls], 'cost_yuan': None,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'plan': str(args.plan_output.resolve()), 'steps': len(steps),
                              'executed': False, 'model_calls': len(client.attempts)}, ensure_ascii=False))
            return 0
        if args.command == 'benchmark':
            command = [sys.executable, '-m', 'evaluation.benchmark', '--repeats', str(args.repeats),
                       '--max-model-calls', str(args.max_model_calls)]
            if args.with_model:
                command.append('--with-model')
            return subprocess.run(command, check=False).returncode
        if args.command == 'shophub':
            return subprocess.run([sys.executable, '-m', 'evaluation.shophub', '--source', str(args.source)], check=False).returncode
        if args.command == 'doctor':
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                installed = Path(p.chromium.executable_path).exists()
            print(json.dumps({'chromium_installed': installed, 'model_key_configured': bool(ZHIPU_API_KEY),
                              'text_model': TEXT_MODEL, 'rules_mode_requires_key': False}, ensure_ascii=False))
            return 0 if installed else 1
    except Exception as exc:
        directory = Path(getattr(args, 'output', 'reports')) / uuid.uuid4().hex[:8]
        directory.mkdir(parents=True, exist_ok=True)
        report, path = _save({'schema_version': 3, 'run_status': 'failed', 'business_validation': 'unverified',
                              'error': {'type': type(exc).__name__, 'message': '命令未完成，请检查计划结构、页面地址和模型配置'},
                              'step_results': [], 'artifacts': {}}, directory)
        return _print_result(report, path)
    return 1


if __name__ == '__main__':
    sys.exit(main())
