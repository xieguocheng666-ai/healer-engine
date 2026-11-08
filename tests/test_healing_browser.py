"""离线真实浏览器场景；不使用外部服务或付费模型。"""
import json

import pytest
from playwright.sync_api import sync_playwright

from agent.healer import Healer
from schemas.step import FailureDetail, Step


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


class NoModel:
    def __init__(self):
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("This scenario must not call the model")


def heal(page, tmp_path, html, target=None, url="http://local/products", client=None):
    page.set_content(html)
    step = Step(action="click", selector="#old-add", target=target)
    failure = FailureDetail(step, "missing locator", url, html)
    h = Healer(client or NoModel(), page, str(tmp_path / "audit.jsonl"))
    return h, h.heal(failure, 0, "browser-test")


def test_does_not_choose_delete_button(page, tmp_path):
    h, selector = heal(page, tmp_path,
        '<button id="delete">Delete order</button><button id="new-add">Add</button>',
        {"tag": "button", "text": "Add"})
    assert selector == "#new-add"
    assert h.records[-1].verified


def test_missing_target_refuses_before_model(page, tmp_path):
    h, selector = heal(page, tmp_path, '<button id="delete">Delete</button>')
    assert selector is None
    assert h.records[-1].reason == "missing_target"
    assert h.client.calls == 0


@pytest.mark.parametrize("html", [
    '<button id="a">Add</button><button id="b">Add</button>',
    '<button id="delete">Delete</button>',
    '<button id="a" disabled>Add</button>',
    '<button id="a" style="display:none">Add</button>',
])
def test_ambiguous_missing_or_unavailable_target_refused(page, tmp_path, html):
    _, selector = heal(page, tmp_path, html, {"tag": "button", "text": "Add"})
    assert selector is None


def test_critical_flow_refuses_before_model(page, tmp_path):
    h, selector = heal(page, tmp_path, '<button id="a">Add</button>',
        {"tag": "button", "text": "Add"}, url="http://local/checkout")
    assert selector is None
    assert h.records[-1].reason == "critical_flow"
    assert h.client.calls == 0


def test_correct_product_and_business_result(page, tmp_path):
    html = '''<button id="delete">Delete</button>
    <button data-action="add" data-store-id="a" data-product-id="1">Add</button>
    <button data-action="add" data-store-id="a" data-product-id="2"
      onclick="document.querySelector('#cart').textContent='product-2:1'">Add</button>
    <div id="cart">empty</div>'''
    h, selector = heal(page, tmp_path, html, {"tag": "button", "text": "Add",
        "attributes": {"data-store-id": "a", "data-product-id": "2"}})
    assert selector is not None
    page.locator(selector).click()
    assert page.locator('#cart').inner_text() == 'product-2:1'
    record = json.loads((tmp_path / 'audit.jsonl').read_text())
    assert record['new_selector'] == selector
    assert record['target']['attributes']['data-product-id'] == '2'


def test_model_cannot_override_target(page, tmp_path, monkeypatch):
    monkeypatch.setattr(Healer, '_try_l1', lambda self, failure: None)
    class WrongModel:
        def chat(self, *args, **kwargs):
            return '#delete'
    # Deliberately bypass L1 to exercise the L2 verification boundary.
    _, selector = heal(page, tmp_path,
        '<button id="delete">Delete</button><button>Add</button>',
        {"tag": "button", "text": "Add"}, client=WrongModel())
    assert selector is None


def test_model_candidate_matching_target_can_be_applied(page, tmp_path, monkeypatch):
    monkeypatch.setattr(Healer, '_try_l1', lambda self, failure: None)
    class Model:
        def chat(self, *args, **kwargs):
            return 'button'
    h, selector = heal(page, tmp_path, '<button>Add</button>',
        {'tag': 'button', 'text': 'Add'}, client=Model())
    assert selector == 'button'
    assert h.records[-1].layer == 'L2'


def test_fill_readonly_is_rejected(page, tmp_path):
    html = '<input id="new" name="email" readonly>'
    page.set_content(html)
    step = Step(action='fill', selector='#old', value='a@example.test',
                target={'tag': 'input', 'attributes': {'name': 'email'}})
    h = Healer(NoModel(), page, str(tmp_path / 'audit.jsonl'))
    assert h.heal(FailureDetail(step, 'missing', 'http://local/profile', html), 0, 'test') is None
    assert h.client.calls == 0


def test_assertion_cannot_be_healed_directly(page, tmp_path):
    step = Step(action='assert', assert_type='text_visible', value='expected')
    h = Healer(NoModel(), page, str(tmp_path / 'audit.jsonl'))
    assert h.heal(FailureDetail(step, 'assert failed', 'http://local'), 0, 'test') is None
    assert h.records[-1].reason == 'unsupported_action'
    assert h.client.calls == 0

@pytest.mark.parametrize(('html', 'target'), [
    ('<button>Add</button><button>Add another</button>', {'tag': 'button', 'text': 'Add'}),
    ('<button><span>确认</span> <b>提交</b></button>', {'tag': 'button', 'text': '确认 提交'}),
    ('<button><span>Add</span></button>', {'tag': 'button', 'text': 'Add'}),
    ('<button>他说 &quot;OK&quot;，it&#39;s done ✅</button>', {'tag': 'button', 'text': '他说 "OK"，it\'s done ✅'}),
    ('<button data-key="a&quot;]\\b">Add</button>', {'tag': 'button', 'attributes': {'data-key': 'a"]\\b'}}),
    ('<button data-x="1" data-y="2">Add</button><button data-x="1" data-y="3">Add</button><button data-x="0" data-y="2">Add</button>', {'tag': 'button', 'attributes': {'data-x': '1', 'data-y': '2'}}),
    ('<button data-x="1">Add</button><button data-x="1">Other</button><button>Add</button>', {'tag': 'button', 'text': 'Add', 'attributes': {'data-x': '1'}}),
])
def test_semantic_l1_recovers_without_model(page, tmp_path, html, target):
    h, selector = heal(page, tmp_path, html, target)
    assert selector is not None
    assert page.locator(selector).count() == 1
    assert h.records[-1].layer == 'L1'
    assert h.client.calls == 0


def test_fill_description_does_not_expose_value():
    step = Step(action='fill', selector='#old', value='private-password',
                target={'tag': 'input', 'attributes': {'name': 'password'}})
    assert 'private-password' not in Healer(None, None)._describe_step(step)


def test_sensitive_step_refuses_before_model(page, tmp_path):
    page.set_content('<button>Delete</button>')
    step = Step(action='click', selector='#old', sensitive=True,
                target={'tag': 'button', 'text': 'Delete'})
    h = Healer(NoModel(), page, str(tmp_path / 'audit.jsonl'))
    assert h.heal(FailureDetail(step, 'missing', 'http://local/products'), 0, 'test') is None
    assert h.records[-1].reason == 'sensitive_step'
    assert h.client.calls == 0


@pytest.mark.parametrize('text', ['He said "yes", it\'s done', 'a[b].* + (yes)?', '\u786e\u8ba4\u00a0\u63d0\u4ea4'])
def test_nested_label_exact_text_fallback(page, tmp_path, text):
    import html
    markup = f'<button><span>{html.escape(text)}</span></button><button>Other</button>'
    h, selector = heal(page, tmp_path, markup, {'tag': 'button', 'text': text})
    assert selector is not None
    assert page.locator(selector).text_content() == text
    assert h.client.calls == 0


def test_attribute_name_and_control_characters_are_escaped(page, tmp_path):
    page.set_content('<button>Add</button>')
    name = 'data:x.y'
    value = 'a"\'\\\n] \u786e\u8ba4'
    page.locator('button').evaluate('(el, pair) => el.setAttribute(pair[0], pair[1])', [name, value])
    step = Step(action='click', selector='#old', target={'tag': 'button', 'attributes': {name: value}})
    h = Healer(NoModel(), page, str(tmp_path / 'audit.jsonl'), allow_llm=False)
    selector = h.heal(FailureDetail(step, 'missing', 'http://local/products'), 0, 'test')
    assert selector is not None
    assert page.locator(selector).get_attribute(name) == value
    assert h.client.calls == 0


@pytest.mark.parametrize('matches, raises', [(True, False), (False, False), (False, True)])
def test_verify_disposes_temporary_handle(matches, raises):
    from unittest.mock import Mock
    step = Step(action='click', selector='#old', target={'tag': 'button', 'text': 'Add'})
    handle = Mock()
    loc = Mock()
    loc.count.return_value = 1
    loc.is_visible.return_value = True
    loc.nth.return_value.element_handle.return_value = handle
    loc.evaluate.return_value = matches
    if raises:
        loc.evaluate.side_effect = RuntimeError('detached')
    page = Mock()
    page.locator.return_value = loc
    h = Healer(None, page)
    h._matching_indices = Mock(return_value=[0])
    h._target_available = Mock(return_value=True)
    assert h._verify('button', step) is (matches and not raises)
    handle.dispose.assert_called_once()

@pytest.mark.parametrize('attributes', [
    {'id': 'a.b#c [x]'},
    {'id': 'a"\'\\> b'},
    {'data-action': 'a"\'\\\n> ]'},
    {'data-store-id': 'shop"\\]', 'data-product-id': 'item\n>\''},
])
def test_html_attribute_candidates_preserve_literal_values(page, attributes):
    import html
    markup = '<button ' + ' '.join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attributes.items()) + '>Add</button>'
    page.set_content(markup)
    candidates = Healer(None, page)._extract_candidates(markup)
    assert candidates
    for selector in candidates:
        assert page.locator(selector).count() == 1
        for name, value in attributes.items():
            assert page.locator(selector).get_attribute(name) == value


def test_html_candidates_parse_single_quotes_and_quoted_angle_brackets(page):
    markup = '<button data-action=\'a" > b\'>Add</button>'
    page.set_content(markup)
    candidates = Healer(None, page)._extract_candidates(markup)
    assert candidates
    assert page.locator(candidates[0]).get_attribute('data-action') == 'a" > b'
