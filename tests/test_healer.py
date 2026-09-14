from agent.healer import Healer


def test_extract_candidates_from_html():
    h = Healer(client=None, page=None)
    html = '<button id="login-btn" data-action="submit" aria-label="登录" name="submit">登录</button>'
    candidates = h._extract_candidates(html)
    assert "#login-btn" in candidates
    assert '[data-action="submit"]' in candidates
    assert '[aria-label="登录"]' in candidates
    assert '[name="submit"]' in candidates


def test_extract_candidates_empty_html():
    h = Healer(client=None, page=None)
    assert h._extract_candidates("") == []


def test_extract_candidates_combo_selector():
    h = Healer(client=None, page=None)
    html = '<button data-action="add" data-store-id="1" data-product-id="2">加购</button>'
    candidates = h._extract_candidates(html)
    assert '[data-store-id="1"][data-product-id="2"]' in candidates
    assert candidates.index('[data-store-id="1"][data-product-id="2"]') < candidates.index('[data-action="add"]')


def test_extract_candidates_skips_numeric_id():
    h = Healer(client=None, page=None)
    html = '<div id="1">x</div>'
    assert h._extract_candidates(html) == []


def test_is_critical_login_url():
    h = Healer(client=None, page=None)
    assert h._is_critical("http://127.0.0.1:8000/login.html") is True
    assert h._is_critical("http://127.0.0.1:8000/payment.html") is True
    assert h._is_critical("http://127.0.0.1:8000/index.html") is False
