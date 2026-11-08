from types import SimpleNamespace


def test_rules_only_never_calls_model(tmp_path):
    from agent.healer import Healer
    class Client:
        def chat(self, *args, **kwargs):
            raise AssertionError('Rules-only mode called a model')
    h = Healer(Client(), None, str(tmp_path / 'audit'), allow_llm=False)
    assert h._try_l2(SimpleNamespace()) is None


def test_summary_separates_business_failure_from_wrong_identity():
    from evaluation.benchmark import summarize
    rows = [dict(eligible=True, candidate='#correct', identity_correct=True,
                 business_passed=False, model_error=None, refused=False,
                 decision_correct=True, elapsed_seconds=.1, model_attempts=0)]
    result = summarize(rows)
    assert result['false_heal_count'] == 0
    assert result['business_success'] == 0
    assert result['identity_correct'] == 1


def test_model_error_does_not_count_as_correct_refusal():
    from evaluation.benchmark import summarize
    rows = [dict(eligible=False, candidate=None, identity_correct=None,
                 business_passed=False, model_error='HTTPError', refused=True,
                 decision_correct=False, elapsed_seconds=.1, model_attempts=1)]
    result = summarize(rows)
    assert result['correct_decisions'] == 0
    assert result['model_errors'] == 1


def test_unchanged_selector_is_not_counted_as_a_repair():
    from evaluation.benchmark import summarize
    rows = [dict(eligible=True, initial_passed=True, candidate='#old', identity_correct=True,
                 business_passed=True, model_error=None, refused=False,
                 decision_correct=True, elapsed_seconds=.1, model_attempts=0)]
    result = summarize(rows)
    assert result['initial_success'] == 1
    assert result['recovered_steps'] == 0
    assert result['recovery_rate'] is None


def test_correct_target_with_broken_business_remains_failed(tmp_path):
    from playwright.sync_api import sync_playwright
    from evaluation.benchmark import run_case, summarize
    from evaluation.cases import Case, button
    import zipfile
    case = Case('broken_business', button('id="old"', result='wrong'),
                {'tag': 'button', 'text': 'Add'}, '#old')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            row = run_case(browser, case, 'rules', tmp_path)
        finally:
            browser.close()
    assert row['identity_correct'] is True
    assert row['business_passed'] is False
    assert summarize([row])['false_heal_count'] == 0
    assert zipfile.is_zipfile(row['trace'])


def test_model_call_budget_prevents_extra_requests():
    import pytest
    from evaluation.benchmark import RecordedClient
    class Client:
        calls = 0
        def chat(self):
            self.calls += 1
            return 'NONE'
    client = Client()
    gateway = RecordedClient(client, 1)
    assert gateway.chat() == 'NONE'
    with pytest.raises(RuntimeError):
        gateway.chat()
    assert client.calls == 1
    assert gateway.errors == ['call_budget_exhausted']


def test_detached_candidate_is_kept_as_failed_sample(monkeypatch, tmp_path):
    from playwright.sync_api import sync_playwright
    from agent.healer import Healer
    from evaluation.benchmark import run_case, summarize
    from evaluation.cases import Case, button
    original = Healer.heal
    def detach(self, *args, **kwargs):
        candidate = original(self, *args, **kwargs)
        self.page.set_default_timeout(100)
        self.page.locator(candidate).evaluate('(el) => el.remove()')
        return candidate
    monkeypatch.setattr(Healer, 'heal', detach)
    case = Case('detached', button('id="new"'), {'tag': 'button', 'text': 'Add'}, '#new')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            row = run_case(browser, case, 'rules', tmp_path)
        finally:
            browser.close()
    assert row['execution_error'] is not None
    assert row['business_passed'] is False
    assert row['decision_correct'] is False
    assert summarize([row])['execution_errors'] == 1
