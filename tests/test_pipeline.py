"""编排测试替换外部模型和浏览器，运行真实编排逻辑。"""
from types import SimpleNamespace

import pytest

import main
from agent.runner import Runner
from playwright.sync_api import Error
from schemas.step import FailureDetail, Step


def execute(monkeypatch, tmp_path, steps, outcomes):
    executed = []
    class ScriptedRunner:
        page = None
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def run_step(self, step):
            executed.append(step.action)
            status = outcomes[len(executed)-1]
            detail = FailureDetail(step, 'failure', 'http://local/products')
            return status, None if status == 'passed' else detail
    class Repair:
        def __init__(self, *args, **kwargs):
            self.records = []
        def heal(self, detail, index, run_id):
            self.records.append(SimpleNamespace(step_index=index, guardrail_hit=False))
            return '#fixed'
    monkeypatch.setattr(main, 'LLMClient', lambda: SimpleNamespace(calls=[]))
    monkeypatch.setattr(main.Generator, 'generate', lambda self, text: steps)
    monkeypatch.setattr(main, 'Runner', ScriptedRunner)
    monkeypatch.setattr(main, 'Healer', Repair)
    monkeypatch.setattr(main.Analyzer, 'analyze', lambda self, detail: {'need_review': True})
    report, _ = main.run_pipeline('test', output_dir=str(tmp_path))
    return report, executed


def test_failed_step_blocks_following_steps(monkeypatch, tmp_path):
    steps = [Step(action='assert', assert_type='text_visible', value='cart'),
             Step(action='click', selector='#checkout')]
    report, executed = execute(monkeypatch, tmp_path, steps, ['assert_failed', 'passed'])
    assert executed == ['assert']
    assert report['step_results'][1]['status'] == 'blocked'
    assert report['business_validation'] == 'failed'


def test_action_recovered_but_business_failed(monkeypatch, tmp_path):
    steps = [Step(action='click', selector='#old'),
             Step(action='assert', assert_type='text_visible', value='correct product')]
    report, _ = execute(monkeypatch, tmp_path, steps, ['locator_failed', 'passed', 'assert_failed'])
    assert report['healing']['heal_success'] == 1
    assert report['business_validation'] == 'failed'
    assert report['healing']['business_recovery'] == 'failed'


def test_no_assertion_means_unverified_business(monkeypatch, tmp_path):
    report, _ = execute(monkeypatch, tmp_path, [Step(action='click', selector='#ok')], ['passed'])
    assert report['business_validation'] == 'unverified'


def test_recovered_action_and_final_assertion_report_passed(monkeypatch, tmp_path):
    steps = [Step(action='click', selector='#old'),
             Step(action='assert', assert_type='text_visible', value='cart product')]
    report, _ = execute(monkeypatch, tmp_path, steps, ['locator_failed', 'passed', 'passed'])
    assert report['business_validation'] == 'passed'
    assert report['healing']['business_recovery'] == 'passed'


def test_assertion_before_operation_does_not_verify_final_business(monkeypatch, tmp_path):
    steps = [Step(action='assert', assert_type='text_visible', value='product'),
             Step(action='click', selector='#add')]
    report, _ = execute(monkeypatch, tmp_path, steps, ['passed', 'passed'])
    assert report['business_validation'] == 'unverified'


def test_assertion_before_navigation_does_not_verify_destination(monkeypatch, tmp_path):
    steps = [Step(action='assert', assert_type='text_visible', value='source'),
             Step(action='goto', url='http://local/destination')]
    report, _ = execute(monkeypatch, tmp_path, steps, ['passed', 'passed'])
    assert report['business_validation'] == 'unverified'


def test_retry_limit_is_shared_across_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'MAX_HEAL_ROUNDS_PER_RUN', 1)
    steps = [Step(action='click', selector='#old1'), Step(action='click', selector='#old2')]
    report, executed = execute(monkeypatch, tmp_path, steps,
                               ['locator_failed', 'passed', 'locator_failed', 'passed'])
    assert executed == ['click', 'click', 'click']
    assert report['healing']['heal_triggered'] == 1


def test_navigation_error_does_not_enter_healer(monkeypatch):
    runner = Runner()
    def failed(step):
        raise Error('net::ERR_CONNECTION_REFUSED')
    monkeypatch.setattr(runner, '_execute', failed)
    monkeypatch.setattr(runner, '_capture', lambda step, error: FailureDetail(step, error, 'about:blank'))
    assert runner.run_step(Step(action='goto', url='http://local'))[0] == 'execution_failed'
