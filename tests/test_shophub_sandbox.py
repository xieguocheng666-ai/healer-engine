import pytest


@pytest.mark.parametrize('name', ['shop', 'healer_eval_', 'healer_eval_12345678;DROP DATABASE shop', '../shop'])
def test_database_name_cannot_target_original_schema(name):
    from evaluation.shophub_service import validate_database
    with pytest.raises(ValueError):
        validate_database(name)


def test_generated_database_name_is_allowed():
    from evaluation.shophub_service import validate_database
    assert validate_database('healer_eval_a123b456') == 'healer_eval_a123b456'


def test_cleanup_timeout_is_reported_instead_of_losing_result(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace
    from evaluation.shophub import stop_and_cleanup
    ownership = tmp_path / 'ownership.json'
    ownership.write_text('{}')
    process = SimpleNamespace(poll=lambda: 0, wait=lambda timeout: 0)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('cleanup', 15)
    monkeypatch.setattr(subprocess, 'run', timeout)
    result = stop_and_cleanup(process, ['python', 'service.py'], ownership, 0)
    assert result['service_stopped'] is True
    assert result['database_cleanup_verified'] is False
    assert ownership.exists()
