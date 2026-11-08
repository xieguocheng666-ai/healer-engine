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


def test_model_cannot_override_target(page, tmp_path):
    class WrongModel:
        def chat(self, *args, **kwargs):
            return '#delete'
    # A target exists but lacks stable attributes, forcing model fallback.
    _, selector = heal(page, tmp_path,
        '<button id="delete">Delete</button><button>Add</button>',
        {"tag": "button", "text": "Add"}, client=WrongModel())
    assert selector is None


def test_model_candidate_matching_target_can_be_applied(page, tmp_path):
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
