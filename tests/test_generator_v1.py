import json

from agent.generator import Generator


def test_generation_includes_page_evidence_and_retries_with_error():
    class Client:
        def __init__(self):
            self.messages = []
        def chat(self, messages, **kwargs):
            self.messages.append(list(messages))
            return '[{"action":"click"}]' if len(self.messages) == 1 else '[{"action":"click","selector":"#real"}]'
    client = Client()
    steps = Generator(client, 'http://local').generate('click the real button', page_context=json.dumps({'elements':[{'tag':'button','text':'actual-button-42','attributes':{'id':'real'}}]}))
    assert steps[0].selector == '#real'
    assert 'actual-button-42' in json.dumps(client.messages[0])
    assert 'selector' in json.dumps(client.messages[1][-1])


def test_page_snapshot_excludes_filled_values():
    from playwright.sync_api import sync_playwright
    from agent.generator import snapshot_page
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<input id="password" value="secret-sentinel"><button id="actual">Add</button>')
            snapshot = snapshot_page(page)
        finally:
            browser.close()
    assert 'secret-sentinel' not in snapshot
    assert 'actual' in snapshot


def test_empty_snapshot_cannot_ground_invented_target():
    import pytest
    class Client:
        def chat(self, *args, **kwargs):
            return '[{"action":"click","selector":"#invented","target":{"tag":"button","text":"Invented"}}]'
    with pytest.raises(RuntimeError):
        Generator(Client()).generate('click invented', max_retries=1, page_context='{"elements":[]}')


def test_large_snapshot_stays_valid_json():
    from playwright.sync_api import sync_playwright
    from agent.generator import snapshot_page
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            page=browser.new_page()
            page.set_content(''.join(f'<button data-long="{"x"*200}" data-other="{"y"*200}">Button {i}</button>' for i in range(80)))
            snapshot=snapshot_page(page)
        finally:
            browser.close()
    assert isinstance(json.loads(snapshot)['elements'],list)
    assert len(snapshot) <= 16000
