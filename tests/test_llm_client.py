import pytest
from unittest.mock import Mock, patch

from llm.client import LLMClient


def _mock_response(content, usage=None):
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}], "usage": usage or {}}
    return resp


def test_chat_records_usage():
    resp = _mock_response("ok", {"prompt_tokens": 12, "completion_tokens": 5})
    client = LLMClient(api_key="k")
    with patch("requests.post", return_value=resp):
        content = client.chat([{"role": "user", "content": "hi"}], stage="generation")
    assert content == "ok"
    assert client.calls[0].input_tokens == 12
    assert client.calls[0].output_tokens == 5
    assert client.calls[0].stage == "generation"


def test_chat_sends_bearer_and_model():
    resp = _mock_response("ok")
    client = LLMClient(api_key="secret", text_model="glm-4-flash")
    with patch("requests.post", return_value=resp) as post:
        client.chat([{"role": "user", "content": "hi"}])
    _, kwargs = post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["model"] == "glm-4-flash"


def test_chat_vision_uses_vision_model_and_base64():
    resp = _mock_response("visible", {"prompt_tokens": 5, "completion_tokens": 2})
    client = LLMClient(api_key="k", vision_model="glm-4v-flash")
    with patch("requests.post", return_value=resp) as post:
        content = client.chat_vision("看图", b"\x89PNG", stage="analysis_vision")
    assert content == "visible"
    _, kwargs = post.call_args
    assert kwargs["json"]["model"] == "glm-4v-flash"
    content_field = kwargs["json"]["messages"][0]["content"]
    assert content_field[0]["type"] == "text"
    assert content_field[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert client.calls[0].stage == "analysis_vision"


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        LLMClient(api_key="")
