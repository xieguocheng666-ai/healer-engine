"""固定的小规模合成场景，不冒充真实站点或泛化准确率。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    html: str
    target: dict | None
    expected_css: str | None
    original: str = '#old'
    action: str = 'click'
    value: str | None = None
    url: str = 'http://local/products'


def button(attrs='', text='Add', result='ok'):
    return f'''<button {attrs} onclick="document.body.dataset.result='{result}'">{text}</button>'''


ADD = {'tag': 'button', 'text': 'Add'}
CASES = [
    Case('unchanged', button('id="old"'), ADD, '#old'),
    Case('id_changed', button('id="new"'), ADD, '#new'),
    Case('wrong_button_first', button('id="delete"', 'Delete', 'wrong') + button('id="new"'), ADD, '#new'),
    Case('testid_changed', button('data-testid="new-add"'), ADD, '[data-testid="new-add"]'),
    Case('aria_label', button('aria-label="add-product"'), {'tag': 'button', 'attributes': {'aria-label': 'add-product'}}, 'button'),
    Case('same_product_two_stores', button('data-store-id="a" data-product-id="2"', result='wrong') + button('data-store-id="b" data-product-id="2"'),
         {'tag': 'button', 'text': 'Add', 'attributes': {'data-store-id': 'b', 'data-product-id': '2'}}, '[data-store-id="b"][data-product-id="2"]'),
    Case('same_store_two_products', button('data-store-id="b" data-product-id="1"', result='wrong') + button('data-store-id="b" data-product-id="2"'),
         {'tag': 'button', 'text': 'Add', 'attributes': {'data-store-id': 'b', 'data-product-id': '2'}}, '[data-store-id="b"][data-product-id="2"]'),
    Case('text_changed_stable_identity', button('data-testid="cart-add"', 'Buy'), {'tag': 'button', 'attributes': {'data-testid': 'cart-add'}}, 'button'),
    Case('text_changed_no_identity', button('id="new"', 'Buy'), ADD, None),
    Case('removed', button('id="delete"', 'Delete'), ADD, None),
    Case('ambiguous', button('id="one"') + button('id="two"'), ADD, None),
    Case('disabled', button('id="new" disabled'), ADD, None),
    Case('hidden', button('id="new" style="display:none"'), ADD, None),
    Case('missing_identity', button('id="new"'), None, None),
    Case('critical_checkout', button('id="new"'), ADD, None, url='http://local/checkout'),
    Case('overlay', button('id="new"') + '<div style="position:fixed;inset:0;z-index:99;background:white">Loading</div>', ADD, None),
    Case('readonly_input', '<input id="new" name="email" readonly>', {'tag': 'input', 'attributes': {'name': 'email'}}, None, action='fill', value='test@example.test'),
    Case('renamed_input', '<input id="new" name="email">', {'tag': 'input', 'attributes': {'name': 'email'}}, 'input', action='fill', value='test@example.test'),
    # 以下无 L1 支持的稳定属性，可观察模型补充选择器的价值与成本。
    Case('model_text_only', button('', 'Confirm cart'), {'tag': 'button', 'text': 'Confirm cart'}, 'button'),
    Case('model_title_only', button('title="Confirm cart"', 'Confirm cart'), {'tag': 'button', 'text': 'Confirm cart'}, 'button'),
    Case('model_class_only', button('class="cart-submit"', 'Confirm cart'), {'tag': 'button', 'text': 'Confirm cart'}, 'button'),
]
