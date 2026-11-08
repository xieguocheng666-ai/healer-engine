import json

import pytest


def test_cli_runs_offline_plan_and_returns_success(tmp_path):
    from healer_cli import main
    page = tmp_path / 'page.html'
    page.write_text('<div id="result">Done</div>')
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps([{'action':'goto','url':page.as_uri()},
        {'action':'assert','selector':'#result','assert_type':'text_equals','value':'Done'}]))
    assert main(['run','--plan',str(plan),'--output',str(tmp_path/'reports')]) == 0
    assert len(list((tmp_path/'reports').glob('*/report.html'))) == 1


def test_invalid_plan_is_nonzero_with_failure_artifact(tmp_path):
    from healer_cli import main
    plan = tmp_path / 'bad.json'
    plan.write_text('[{"action":"execute_arbitrary_code"}]')
    assert main(['run','--plan',str(plan),'--output',str(tmp_path/'reports')]) == 1
    report = list((tmp_path/'reports').glob('*/report.json'))[0]
    assert json.loads(report.read_text(encoding='utf-8'))['run_status'] == 'failed'


def test_empty_plan_is_rejected(tmp_path):
    from healer_cli import load_plan
    path = tmp_path/'plan.json'
    path.write_text('[]')
    with pytest.raises(ValueError):
        load_plan(path)
