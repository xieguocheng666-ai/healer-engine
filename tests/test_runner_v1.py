import zipfile

import pytest
from agent.runner import Runner
from schemas.step import Step


def test_real_runner_structured_assertions_and_trace(tmp_path):
    trace = tmp_path / 'trace.zip'
    with Runner(timeout_ms=200, trace_path=trace) as runner:
        runner.page.set_content('<div class="item" data-product="2">Product 2: quantity 1</div><input value="hello">')
        for step in [
            Step(action='assert', selector='.item', assert_type='text_equals', value='Product 2: quantity 1'),
            Step(action='assert', selector='.item', assert_type='count', value='1'),
            Step(action='assert', selector='input', assert_type='value_equals', value='hello'),
            Step(action='assert', selector='.item', assert_type='attribute_equals', attribute='data-product', value='2'),
        ]:
            assert runner.run_step(step)[0] == 'passed'
        assert runner.run_step(Step(action='assert', selector='.item', assert_type='count', value='2'))[0] == 'assert_failed'
    assert zipfile.is_zipfile(trace)


def test_relative_navigation_uses_base_url(monkeypatch):
    runner = Runner(base_url='http://localhost:8765')
    visited = []
    class Page:
        def goto(self, url, **kwargs):
            visited.append(url)
    runner.page = Page()
    runner._execute(Step(action='goto', url='/products.html'))
    assert visited == ['http://localhost:8765/products.html']


def test_failure_capture_does_not_send_fill_value_to_model(tmp_path):
    with Runner(timeout_ms=100) as runner:
        runner.page.set_content('<input id="new" value="private-other-value"><textarea>private-area</textarea>')
        step = Step(action='fill', selector='#old', value='secret-sentinel')
        detail = runner._capture(step, 'fill failed for secret-sentinel')
        assert 'secret-sentinel' not in detail.error
        assert 'secret-sentinel' not in detail.step.model_dump_json()
        assert 'private-other-value' not in detail.element_html
        assert step.value == 'secret-sentinel'


def test_existing_selector_cannot_bypass_target_contract():
    with Runner(timeout_ms=100) as runner:
        runner.page.set_content('<button id="delete" onclick="document.body.dataset.deleted=1">Delete</button><button id="add">Add</button>')
        step = Step(action='click', selector='#delete', target={'tag':'button','text':'Add'})
        assert runner.run_step(step)[0] == 'locator_failed'
        assert runner.page.evaluate('document.body.dataset.deleted') is None
