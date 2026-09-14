import json

import pytest

from agent.generator import _extract_json_array


def test_extract_plain_array():
    assert _extract_json_array('[{"action":"goto","url":"x"}]') == [{"action": "goto", "url": "x"}]


def test_extract_with_code_block():
    text = '```json\n[{"action":"goto","url":"x"}]\n```'
    assert _extract_json_array(text) == [{"action": "goto", "url": "x"}]


def test_extract_ignores_surrounding_text():
    text = '好的，结果如下：[{"action":"goto","url":"x"}] 希望有用'
    assert _extract_json_array(text) == [{"action": "goto", "url": "x"}]


def test_extract_no_array_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json_array("这里没有 JSON 数组")
