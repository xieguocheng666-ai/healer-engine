import pytest
from pydantic import ValidationError

from schemas.step import Step, FailureDetail


def test_valid_goto_step():
    s = Step(action="goto", url="http://127.0.0.1:8000/login.html")
    assert s.action == "goto"
    assert s.url == "http://127.0.0.1:8000/login.html"


def test_valid_fill_step():
    s = Step(action="fill", selector="[data-action='username']", value="alice")
    assert s.value == "alice"


def test_valid_assert_step():
    s = Step(action="assert", assert_type="url_contains", value="index.html")
    assert s.assert_type == "url_contains"


def test_invalid_action_rejected():
    with pytest.raises(ValidationError):
        Step(action="hover")


def test_invalid_assert_type_rejected():
    with pytest.raises(ValidationError):
        Step(action="assert", assert_type="element_visible", value="x")


def test_goto_without_url_rejected():
    with pytest.raises(ValidationError):
        Step(action="goto")


def test_fill_without_selector_rejected():
    with pytest.raises(ValidationError):
        Step(action="fill", value="alice")


def test_assert_without_value_rejected():
    with pytest.raises(ValidationError):
        Step(action="assert", assert_type="url_contains")


def test_failure_detail_holds_step_and_error():
    step = Step(action="click", selector="[data-action='submit']")
    fd = FailureDetail(step=step, error="TimeoutError", page_url="http://x")
    assert fd.step is step
    assert fd.error == "TimeoutError"
    assert fd.element_html is None


@pytest.mark.parametrize('target', [
    {'tag': 'button'}, {'tag': 'button', 'text': '  '},
    {'tag': 'button, a', 'text': 'Add'},
    {'tag': 'button', 'attributes': {'data-product-id': ''}},
    {'tag': 'button', 'text': 'Add', 'unknown': 'ignored'},
])
def test_invalid_target_contract_rejected(target):
    with pytest.raises(ValidationError):
        Step(action='click', selector='#old', target=target)


@pytest.mark.parametrize('kind', ['text_equals', 'count', 'value_equals', 'attribute_equals'])
def test_structured_assertion_requires_selector(kind):
    with pytest.raises(ValidationError):
        Step(action='assert', assert_type=kind, value='1', attribute='data-id')


def test_exact_count_assertion_is_supported():
    assert Step(action='assert', selector='.item', assert_type='count', value='2').assert_type == 'count'


def test_unknown_step_fields_rejected():
    with pytest.raises(ValidationError):
        Step(action='click', selector='#a', execute_python='print(1)')
