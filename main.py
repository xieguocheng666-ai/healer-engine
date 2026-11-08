"""可复用的测试计划执行编排；命令行入口见 healer_cli.py。"""
import json
import sys
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path

from agent.analyzer import Analyzer
from agent.generator import Generator
from agent.healer import Healer
from agent.metrics import Metrics
from agent.runner import Runner
from config.settings import MAX_HEAL_ROUNDS_PER_RUN, SUT_BASE_URL
from llm.client import LLMClient
from schemas.step import Step


DEFAULT_INSTRUCTION = '打开登录页，输入账号 alice 密码 alice123，点击登录，断言 URL 包含 index.html'


def _redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[已隐藏]')
        return value
    if isinstance(value, dict):
        return {k: _redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    return value


def _save(report, directory, secrets=()):
    from reporting.html import render_report
    report = _redact(report, secrets)
    path = directory / 'report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    render_report(report, directory / 'report.html')
    return report, path


def execute_plan(steps, base_url=SUT_BASE_URL, output_dir='reports', *, mode='rules',
                 client=None, instruction='', timeout_ms=10000, analyze=False):
    """执行副本，保留原计划；rules/none 不初始化或调用模型。"""
    if mode not in ('none', 'rules', 'hybrid'):
        raise ValueError('mode must be none, rules or hybrid')
    originals = [s.model_copy(deep=True) if isinstance(s, Step) else Step(**s) for s in steps]
    if not originals:
        raise ValueError('Plan must contain at least one step')
    current = [s.model_copy(deep=True) for s in originals]
    secrets = sorted({s.value for s in originals if s.action == 'fill' and s.value}, key=len, reverse=True)
    run_id = uuid.uuid4().hex[:8]
    directory = Path(output_dir) / run_id
    directory.mkdir(parents=True)
    (directory / 'plan.json').write_text(json.dumps([s.model_dump(exclude_none=True) for s in originals], ensure_ascii=False, indent=2), encoding='utf-8')
    metrics = Metrics()
    results, analyses, records = [], [], []
    attempts = successes = 0
    error = None
    failed = False
    metrics.start_timer('execution')
    try:
        if mode == 'hybrid' and client is None:
            client = LLMClient()
        with Runner(base_url=base_url, timeout_ms=timeout_ms, trace_path=directory / 'trace.zip') as runner:
            healer = Healer(client, runner.page, audit_path=str(directory / 'heals.jsonl'), allow_llm=mode == 'hybrid')
            records = healer.records
            for index, step in enumerate(current):
                if failed:
                    results.append({'index': index, 'action': step.action, 'status': 'blocked'})
                    continue
                recovered = False
                while True:
                    status, detail = runner.run_step(step)
                    if status == 'passed':
                        results.append({'index': index, 'action': step.action,
                                        'status': 'healed' if recovered else 'passed',
                                        'original_selector': originals[index].selector, 'selector': step.selector})
                        successes += int(recovered)
                        break
                    if status == 'locator_failed' and mode != 'none' and attempts < MAX_HEAL_ROUNDS_PER_RUN:
                        attempts += 1
                        metrics.start_timer('healing')
                        selector = healer.heal(detail, index, run_id)
                        metrics.stop_timer('healing')
                        if selector:
                            step.selector = selector
                            recovered = True
                            continue
                    if status == 'locator_failed' and attempts >= MAX_HEAL_ROUNDS_PER_RUN:
                        status = 'locator_exhausted'
                    failed = True
                    results.append({'index': index, 'action': step.action, 'status': status,
                                    'original_selector': originals[index].selector,
                                    'selector': step.selector, 'error': detail.error})
                    if detail.screenshot:
                        (directory / f'failure-{index}.png').write_bytes(detail.screenshot)
                    if analyze and mode == 'hybrid':
                        metrics.start_timer('analysis')
                        analyses.append({'index': index, 'analysis': Analyzer(client).analyze(detail)})
                        metrics.stop_timer('analysis')
                    else:
                        analyses.append({'index': index, 'analysis': {'category': '未判定', 'need_review': True,
                                         'evidence': detail.error, 'suggestion': '检查失败证据与业务预期'}})
                    break
    except Exception as exc:
        failed = True
        error = {'type': type(exc).__name__, 'message': str(exc)}
        completed = {r['index'] for r in results}
        results.extend({'index': i, 'action': s.action, 'status': 'blocked'}
                       for i, s in enumerate(current) if i not in completed)
    finally:
        metrics.stop_timer('execution')
    last_change = max((i for i, s in enumerate(current) if s.action in ('goto', 'click', 'fill')), default=-1)
    verified = any(i > last_change and s.action == 'assert' for i, s in enumerate(current))
    business = 'failed' if failed else ('passed' if verified else 'unverified')
    model_attempts = getattr(client, 'attempts', []) if client else []
    report = metrics.report(getattr(client, 'calls', []) if client else [], records, attempts, successes,
                            model_attempts=model_attempts)
    report['healing']['business_recovery'] = business if successes else 'not_applicable'
    report.update({'schema_version': 3, 'run_id': run_id, 'mode': mode,
                   'run_status': business, 'business_validation': business,
                   'instruction': instruction, 'steps_generated': len(current), 'step_results': results,
                   'analyses': analyses, 'error': error,
                   'heal_records': [asdict(r) if is_dataclass(r) else vars(r) for r in records],
                   'model_attempts': [asdict(a) if is_dataclass(a) else a for a in model_attempts],
                   'artifacts': {'trace': 'trace.zip' if (directory / 'trace.zip').exists() else None,
                                 'audit': 'heals.jsonl' if (directory / 'heals.jsonl').exists() else None,
                                 'plan': 'plan.json', 'proposed_plan': 'proposed-plan.json'},
                   'artifact_privacy': '原始计划、建议计划和 trace 可能含表单值及会话，仅供本地保管'})
    (directory / 'proposed-plan.json').write_text(json.dumps([s.model_dump(exclude_none=True) for s in current], ensure_ascii=False, indent=2), encoding='utf-8')
    return _save(report, directory, secrets)


def run_pipeline(instruction: str, base_url=SUT_BASE_URL, output_dir='reports'):
    """兼容旧自然语言入口；新入口建议先 generate 保存计划再 run。"""
    try:
        client = LLMClient()
        steps = Generator(client, base_url).generate(instruction)
    except Exception as exc:
        directory = Path(output_dir) / uuid.uuid4().hex[:8]
        directory.mkdir(parents=True)
        return _save({'schema_version': 3, 'run_status': 'failed', 'business_validation': 'unverified',
                      'error': {'type': type(exc).__name__, 'message': '生成失败，请检查模型配置和结构化输出'},
                      'step_results': [], 'artifacts': {}}, directory)
    return execute_plan(steps, base_url, output_dir, mode='hybrid', client=client, instruction=instruction)


def main():
    from healer_cli import main as cli_main
    return cli_main()


if __name__ == '__main__':
    sys.exit(main())
