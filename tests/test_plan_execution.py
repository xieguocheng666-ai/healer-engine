import json
from pathlib import Path

from schemas.step import Step


def test_offline_plan_runs_without_model_key_and_does_not_mutate_input(tmp_path):
    from main import execute_plan
    page = tmp_path / 'page.html'
    page.write_text('''<button id="new" onclick="document.querySelector('#cart').textContent='product2:1'">Add</button><div id="cart">empty</div>''')
    steps = [Step(action='goto', url=page.as_uri()),
             Step(action='click', selector='#old', target={'tag':'button','text':'Add'}),
             Step(action='assert', selector='#cart', assert_type='text_equals', value='product2:1')]
    report, path = execute_plan(steps, output_dir=tmp_path / 'runs', timeout_ms=200)
    assert report['business_validation'] == 'passed'
    assert report['healing']['heal_success'] == 1
    assert steps[1].selector == '#old'
    assert (path.parent / 'plan.json').exists()
    assert (path.parent / 'proposed-plan.json').exists()
    assert (path.parent / 'report.html').exists()
    assert (path.parent / 'trace.zip').exists()
    assert json.loads((path.parent / 'plan.json').read_text())[1]['selector'] == '#old'


def test_failed_execution_keeps_report_and_blocks_following_step(tmp_path):
    from main import execute_plan
    steps = [Step(action='goto', url='http://127.0.0.1:1/unreachable'),
             Step(action='click', selector='#danger')]
    report, path = execute_plan(steps, output_dir=tmp_path, timeout_ms=200)
    assert report['run_status'] == 'failed'
    assert report['step_results'][1]['status'] == 'blocked'
    assert path.exists()


def test_report_redacts_fill_values_and_preserves_private_plan(tmp_path):
    from main import execute_plan
    page = tmp_path / 'form.html'
    page.write_text('<input id="password"><div>Done</div>')
    steps = [Step(action='goto', url=page.as_uri()),
             Step(action='fill', selector='#password', value='secret-sentinel-xyz'),
             Step(action='assert', assert_type='text_visible', value='Done')]
    report, path = execute_plan(steps, output_dir=tmp_path / 'runs', instruction='use secret-sentinel-xyz')
    assert 'secret-sentinel-xyz' not in path.read_text(encoding='utf-8')
    assert 'secret-sentinel-xyz' not in (path.parent / 'report.html').read_text(encoding='utf-8')
    assert 'secret-sentinel-xyz' in (path.parent / 'plan.json').read_text()
