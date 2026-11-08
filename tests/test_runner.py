from agent.runner import Runner
from schemas.step import Step


def test_missing_locator_capture_does_not_wait_on_absent_element():
    class Missing:
        def count(self):
            return 0
        @property
        def first(self):
            raise AssertionError('Do not wait for an element known to be missing')
    class Page:
        def locator(self, selector):
            return Missing()
        def evaluate(self, *args):
            return '<button id="new">Add</button>'
    runner = Runner()
    runner.page = Page()
    # Capture must inspect count before attempting an auto-waiting element lookup.
    missing = runner.page.locator('#old')
    calls = []
    original = missing.count
    missing.count = lambda: calls.append(True) or original()
    runner.page.locator = lambda selector: missing
    assert 'new' in runner._try_element_html(Step(action='click', selector='#old'))
    assert calls
