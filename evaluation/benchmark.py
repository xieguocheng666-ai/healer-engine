"""python -m evaluation.benchmark [--with-model] [--repeats 3]"""
import argparse
import hashlib
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from playwright.sync_api import Error, sync_playwright

from agent.healer import Healer
from evaluation.cases import CASES
from llm.client import LLMClient
from schemas.step import FailureDetail, Step


class RecordedClient:
    """限制真实调用数量并记录错误，不把模型失败当作成功拒绝。"""
    def __init__(self, client, max_calls):
        self.client = client
        self.max_calls = max_calls
        self.attempts = 0
        self.errors = []

    def chat(self, *args, **kwargs):
        if self.attempts >= self.max_calls:
            self.errors.append('call_budget_exhausted')
            raise RuntimeError('Model call budget exhausted')
        self.attempts += 1
        try:
            return self.client.chat(*args, **kwargs)
        except Exception as exc:
            response = getattr(exc, 'response', None)
            code = getattr(response, 'status_code', None)
            self.errors.append(f'{type(exc).__name__}:{code}' if code else type(exc).__name__)
            raise


def summarize(rows):
    recoverable_failures = sum(r['eligible'] and not r.get('initial_passed', False) for r in rows)
    recovered = sum(r['business_passed'] and r['identity_correct'] is True and not r.get('initial_passed', False) for r in rows)
    return {
        'cases': len(rows),
        'eligible': sum(r['eligible'] for r in rows),
        'initial_success': sum(r.get('initial_passed', False) for r in rows),
        'recoverable_failures': recoverable_failures,
        'recovered_steps': recovered,
        'recovery_rate': round(recovered / recoverable_failures, 4) if recoverable_failures else None,
        'identity_correct': sum(r['candidate'] is not None and r['identity_correct'] is True for r in rows),
        'business_success': sum(r['business_passed'] for r in rows),
        'false_heal_count': sum(r['candidate'] is not None and r['identity_correct'] is False for r in rows),
        'refusals': sum(r['refused'] for r in rows),
        'correct_decisions': sum(r['decision_correct'] for r in rows),
        'model_errors': sum(r['model_error'] is not None for r in rows),
        'execution_errors': sum(r.get('execution_error') is not None for r in rows),
        'model_attempts': sum(r['model_attempts'] for r in rows),
        'elapsed_seconds': round(sum(r['elapsed_seconds'] for r in rows), 4),
    }


def run_case(browser, case, mode, output, gateway=None, repeat=0):
    context = browser.new_context()
    trace_path = output / f'{mode}-{repeat}-{case.name}.zip'
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    start = time.perf_counter()
    attempts_before = gateway.attempts if gateway else 0
    errors_before = len(gateway.errors) if gateway else 0
    calls_before = len(gateway.client.calls) if gateway else 0
    candidate = None
    reason = 'healing_disabled'
    initial_passed = False
    business = False
    try:
        page.set_content(case.html)
        step = Step(action=case.action, selector=case.original, value=case.value, target=case.target)
        try:
            if step.action == 'fill':
                page.locator(step.selector).fill(step.value, timeout=150)
            else:
                page.locator(step.selector).click(timeout=150)
            initial_passed = True
            candidate = step.selector
            reason = 'initial_passed'
        except Error as exc:
            if mode != 'none':
                failure = FailureDetail(step, str(exc), case.url, case.html)
                healer = Healer(gateway, page, str(output / f'{mode}.jsonl'), allow_llm=mode == 'hybrid')
                candidate = healer.heal(failure, 0, case.name)
                reason = healer.records[-1].reason
        identity = None
        execution_error = None
        try:
            identity = None if candidate is None else bool(case.expected_css and page.locator(candidate).evaluate(
                '(el, css) => el === document.querySelector(css)', case.expected_css))
            # 即使候选误选也真实执行，以独立预期判断副作用；只有本地合成页面。
            if candidate:
                if not initial_passed:
                    if step.action == 'fill':
                        page.locator(candidate).fill(step.value, timeout=750)
                    else:
                        page.locator(candidate).click(timeout=750)
                if case.expected_css:
                    business = (page.locator(case.expected_css).input_value() == case.value if case.action == 'fill'
                                else page.evaluate('document.body.dataset.result') == 'ok')
        except Error as exc:
            execution_error = type(exc).__name__
        error = gateway.errors[-1] if gateway and len(gateway.errors) > errors_before else None
        usage = [asdict(c) for c in gateway.client.calls[calls_before:]] if gateway else []
        return {'scenario': case.name, 'mode': mode, 'repeat': repeat,
                'eligible': case.expected_css is not None, 'initial_passed': initial_passed,
                'candidate': candidate, 'identity_correct': identity, 'business_passed': business,
                'refused': candidate is None, 'reason': reason,
                'decision_correct': error is None and execution_error is None and (identity is True if case.expected_css else candidate is None),
                'model_attempts': gateway.attempts - attempts_before if gateway else 0,
                'model_error': error, 'execution_error': execution_error, 'usage': usage,
                'elapsed_seconds': round(time.perf_counter() - start, 4), 'trace': str(trace_path.resolve())}
    finally:
        try:
            context.tracing.stop(path=str(trace_path))
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-model', action='store_true')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--max-model-calls', type=int, default=12)
    args = parser.parse_args()
    if args.repeats < 1 or args.max_model_calls < 1:
        parser.error('repeats and max-model-calls must be positive')
    output = Path('reports') / f'evaluation-{uuid.uuid4().hex[:8]}'
    output.mkdir(parents=True)
    modes = ['none', 'rules'] + (['hybrid'] if args.with_model else [])
    gateway = RecordedClient(LLMClient(), args.max_model_calls) if args.with_model else None
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for repeat in range(args.repeats):
                for mode in modes:
                    for case in CASES:
                        row = run_case(browser, case, mode, output, gateway, repeat)
                        rows.append(row)
                        # 增量落盘，长评估中断也保留已完成证据。
                        with (output / 'cases.jsonl').open('a', encoding='utf-8') as f:
                            f.write(json.dumps(row, ensure_ascii=False) + '\n')
                    print(json.dumps({'repeat': repeat, 'mode': mode, **summarize([r for r in rows if r['repeat'] == repeat and r['mode'] == mode])}), flush=True)
        finally:
            browser.close()
    summary = {mode: summarize([r for r in rows if r['mode'] == mode]) for mode in modes}
    report = {'schema_version': 2, 'engine_version': '1.0.0', 'dataset': 'synthetic-v1', 'unique_scenarios': len(CASES),
              'model_requested': args.with_model, 'model_evaluated': bool(gateway and gateway.attempts),
              'dataset_sha256': hashlib.sha256(json.dumps([asdict(c) for c in CASES], sort_keys=True).encode()).hexdigest(),
              'scenario_definitions': [asdict(c) for c in CASES],
              'repeats': args.repeats, 'model': gateway.client.text_model if gateway else None,
              'cost_yuan': None, 'cost_status': 'not_verified', 'summary': summary,
              'usage': [asdict(c) for c in gateway.client.calls] if gateway else [], 'cases': rows}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Report: {(output / "report.json").resolve()}', flush=True)
    if any(r['false_heal_count'] or r['model_errors'] or r['execution_errors'] for r in summary.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
