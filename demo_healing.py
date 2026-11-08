"""离线真实浏览器验证：python demo_healing.py，无需 API key 或电商服务。"""
import json
import uuid
from pathlib import Path

from playwright.sync_api import TimeoutError

from agent.healer import Healer
from agent.runner import Runner
from schemas.step import Step


class OfflineClient:
    """禁用模型，不把固定响应伪装成模型效果。"""
    def chat(self, *args, **kwargs):
        raise RuntimeError("Model disabled for offline benchmark")


SCENARIOS = [
    ("changed_id", '<button id="new">Add</button>', {"tag": "button", "text": "Add"}, "#new"),
    ("wrong_button_first", '<button id="delete">Delete</button><button id="new">Add</button>',
     {"tag": "button", "text": "Add"}, "#new"),
    ("ambiguous", '<button id="a">Add</button><button id="b">Add</button>',
     {"tag": "button", "text": "Add"}, None),
    ("removed", '<button id="delete">Delete</button>', {"tag": "button", "text": "Add"}, None),
    ("disabled", '<button id="a" disabled>Add</button>', {"tag": "button", "text": "Add"}, None),
    ("missing_identity", '<button id="delete">Delete</button>', None, None),
    ("product_identity", '''<button id="wrong" data-store-id="a" data-product-id="1">Add</button>
      <button id="correct" data-store-id="a" data-product-id="2"
      onclick="document.querySelector('#cart').textContent='product-2:1'">Add</button>
      <div id="cart">empty</div>''',
     {"tag": "button", "text": "Add", "attributes": {"data-store-id": "a", "data-product-id": "2"}}, '#correct'),
]


def summarize(rows):
    """目标误修与业务失败分开；仅适用于有人工预期的离线样本。"""
    return {'mode': 'offline_rules_only', 'model_evaluated': False,
            'cases': rows, 'passed': sum(r['correct'] for r in rows), 'total': len(rows),
            'false_heal_count': sum(r['candidate'] is not None and not r['identity_correct'] for r in rows)}


def main():
    output = Path('reports/offline-validation')
    output.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    rows = []
    with Runner() as runner:
        for name, html, target, expected in SCENARIOS:
            runner.page.set_content(html)
            step = Step(action='click', selector='#old-missing', target=target)
            try:
                runner.page.locator(step.selector).click(timeout=100)
            except TimeoutError as exc:
                failure = runner._capture(step, str(exc))
            else:
                raise AssertionError('Scenario did not trigger a locator failure')
            healer = Healer(OfflineClient(), runner.page, str(output / f'heals-{run_id}.jsonl'))
            candidate = healer.heal(failure, 0, run_id)
            correct = candidate is None if expected is None else (
                candidate is not None and runner.page.locator(candidate).evaluate(
                    '(el, expected) => el === document.querySelector(expected)', expected))
            business = 'not_applicable'
            identity_correct = correct
            if correct and candidate:
                step.selector = candidate
                status, _ = runner.run_step(step)
                correct = status == 'passed'
                if name == 'product_identity':
                    assertion = Step(action='assert', assert_type='text_visible', value='product-2:1')
                    business, _ = runner.run_step(assertion)
                    correct = correct and business == 'passed'
            rows.append({'scenario': name, 'expected': expected, 'candidate': candidate,
                         'correct': correct, 'identity_correct': identity_correct, 'business_assertion': business,
                         'reason': healer.records[-1].reason})
    result = summarize(rows)
    path = output / f'result-{run_id}.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'Report: {path.resolve()}')
    if result['passed'] != result['total']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
