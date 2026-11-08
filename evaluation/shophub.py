"""python -m evaluation.shophub --source D:/my-test-framework"""
import argparse
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import requests
from playwright.sync_api import expect

from agent.healer import Healer
from agent.runner import Runner
from schemas.step import Step


def exercise(base_url, output):
    """真实 UI 登录、故障注入、自愈加购、API 回读和购物车页面核验。"""
    with Runner(base_url=base_url) as runner:
        page = runner.page
        page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        try:
            page.goto(base_url + '/login.html')
            page.locator('#username').fill('alice')
            page.locator('#password').fill('alice123')
            page.locator('#loginBtn').click()
            expect(page).to_have_url(base_url + '/index.html')
            target_css = 'button[data-action="add"][data-store-id="1"][data-product-id="1"]'
            target = page.locator(target_css)
            expect(target).to_be_visible()
            token = page.evaluate("localStorage.getItem('shop_token')")
            session = requests.Session()
            session.trust_env = False
            session.headers['Authorization'] = 'Bearer ' + token
            initial = session.get(base_url + '/cart', timeout=5)
            initial.raise_for_status()
            if initial.json()['items']:
                raise AssertionError('Isolated test cart must start empty')
            page.locator('.product-card').filter(has=target).locator('input').fill('2')
            # 前端属性变化，不更改事件委托依赖的 data-action。
            target.evaluate("el => el.setAttribute('data-testid', 'healer-before')")
            step = Step(action='click', selector='[data-testid="healer-before"]', target={
                'tag': 'button', 'attributes': {'data-action': 'add', 'data-store-id': '1', 'data-product-id': '1'}})
            target.evaluate("el => el.setAttribute('data-testid', 'healer-after')")
            try:
                page.locator(step.selector).click(timeout=150)
            except Exception as exc:
                failure = runner._capture(step, str(exc))
            else:
                raise AssertionError('Old selector unexpectedly worked')
            healer = Healer(None, page, str(output / 'heals.jsonl'), allow_llm=False)
            candidate = healer.heal(failure, 0, 'shophub-cart')
            if not candidate:
                raise AssertionError('Healer refused the expected target')
            if not page.locator(candidate).evaluate('(el, css) => el === document.querySelector(css)', target_css):
                raise AssertionError('Healer selected a different product')
            with page.expect_response(lambda r: r.url == base_url + '/cart' and r.request.method == 'POST') as response:
                page.locator(candidate).click()
            if response.value.status != 200:
                raise AssertionError('Cart mutation failed')
            cart_response = session.get(base_url + '/cart', timeout=5)
            cart_response.raise_for_status()
            items = cart_response.json()['items']
            actual = [(i['store_id'], i['product_id'], i['quantity']) for i in items]
            if actual != [(1, 1, 2)]:
                raise AssertionError(f'Wrong cart identity or quantity: {actual}')
            page.goto(base_url + '/cart.html')
            row = page.locator('.line-item').filter(has=page.locator('button[data-action="remove"][data-store-id="1"][data-product-id="1"]'))
            expect(row).to_have_count(1)
            expect(row.locator('.line-item__qty')).to_have_text('× 2')
            expect(page.locator('.line-item')).to_have_count(1)
            session.close()
            return {'passed': True, 'initial_cart_empty': True, 'selector_before': step.selector,
                    'candidate': candidate, 'healing_layer': healer.records[-1].layer,
                    'api_cart': actual, 'ui_cart_verified': True, 'model_used': False}
        finally:
            page.context.tracing.stop(path=str(output / 'trace.zip'))


def stop_and_cleanup(process, command, ownership, flags):
    """即使清理失败也返回明确状态，保留所有权文件以供恢复。"""
    result = {'service_stopped': False, 'database_cleanup_verified': False}
    try:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        result['service_stopped'] = process.poll() is not None
        if not result['service_stopped']:
            result['cleanup_status'] = 'service_still_running'
            return result
        cleanup = subprocess.run(command + ['--cleanup'], capture_output=True, text=True,
                                 timeout=15, creationflags=flags)
        result['database_cleanup_verified'] = cleanup.returncode == 0 and not ownership.exists()
        result['cleanup_status'] = cleanup.stdout.strip() if cleanup.returncode == 0 else 'failed; inspect ownership.json'
    except (subprocess.SubprocessError, OSError) as exc:
        result['service_stopped'] = process.poll() is not None
        result['cleanup_status'] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    python = source / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists() or not (source / 'server/main.py').exists():
        parser.error('ShopHub source and its installed .venv are required')
    run_id = uuid.uuid4().hex[:8]
    output = (Path('reports') / f'shophub-{run_id}').resolve()
    output.mkdir(parents=True)
    ownership = output / 'ownership.json'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base_url = f'http://127.0.0.1:{port}'
    command = [str(python), str(Path(__file__).with_name('shophub_service.py')),
               '--source', str(source), '--database', 'healer_eval_' + run_id,
               '--ownership', str(ownership), '--port', str(port)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = {'passed': False, 'database': 'healer_eval_' + run_id}
    log_path = output / 'service.log'
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=flags)
        try:
            session = requests.Session()
            session.trust_env = False
            deadline = time.monotonic() + 25
            while True:
                if process.poll() is not None:
                    raise RuntimeError('ShopHub service exited during startup; inspect service.log')
                try:
                    response = session.get(base_url + '/health', timeout=1)
                    if response.status_code == 200:
                        break
                except requests.RequestException:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError('ShopHub startup timed out')
                time.sleep(.2)
            session.close()
            result.update(exercise(base_url, output))
        except Exception as exc:
            result['error_type'] = type(exc).__name__
            result['error'] = str(exc)
        finally:
            result.update(stop_and_cleanup(process, command, ownership, flags))
            if not result['database_cleanup_verified']:
                result['cleanup_command'] = command + ['--cleanup']
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'Report: {output / "result.json"}')
    if result['passed'] and result['database_cleanup_verified']:
        log_path.unlink()
    else:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
