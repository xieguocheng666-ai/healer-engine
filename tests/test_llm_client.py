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


def test_retry_budget_shared_and_attempts_safe():
    import requests
    client = LLMClient(api_key="secret", max_calls=2, max_retries=1)
    with patch("requests.post", side_effect=[requests.Timeout("secret prompt"), _mock_response("ok")]) as post:
        assert client.chat([]) == "ok"
        with pytest.raises(Exception, match="budget"):
            client.chat_vision("prompt", b"x")
    assert post.call_count == 2
    assert len(client.attempts) == 2
    assert client.attempts[0].error_type == "timeout"
    assert "secret" not in repr(client.attempts)


def test_missing_usage_is_unknown_and_timeout_default():
    client = LLMClient(api_key="k")
    with patch("requests.post", return_value=_mock_response("ok")) as post:
        client.chat([])
    assert client.calls[0].input_tokens is None
    assert post.call_args.kwargs["timeout"] == 15


@pytest.mark.parametrize("data", [None, {}, {"choices": []}, {"choices": [{"message": {"content": None}}]}])
def test_invalid_response_never_retries(data):
    response = _mock_response("ok")
    response.json.return_value = data
    client = LLMClient(api_key="k")
    with patch("requests.post", return_value=response) as post:
        with pytest.raises(Exception, match="response"):
            client.chat([])
    assert post.call_count == 1
    assert client.attempts[0].error_type == "invalid_response"


@pytest.mark.parametrize("status,expected", [(401, 1), (403, 1), (429, 2), (500, 2)])
def test_http_retry_only_transient(status, expected):
    import requests
    response = _mock_response("ok")
    response.status_code = status
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    client = LLMClient(api_key="k", max_retries=1)
    with patch("requests.post", return_value=response) as post:
        with pytest.raises(Exception):
            client.chat([])
    assert post.call_count == expected
    assert all(a.status_code == status for a in client.attempts)


def test_connection_failure_retries_and_zero_budget_sends_nothing():
    import requests
    from llm.client import LLMBudgetExceeded, LLMError
    client = LLMClient(api_key="k", max_retries=1)
    with patch("requests.post", side_effect=requests.ConnectionError("sensitive")) as post:
        with pytest.raises(LLMError, match="connection"):
            client.chat([])
    assert post.call_count == 2
    assert len(client.calls) == 0
    assert all(a.duration_seconds >= 0 for a in client.attempts)
    with patch("requests.post") as post:
        with pytest.raises(LLMBudgetExceeded):
            LLMClient(api_key="k", max_calls=0).chat([])
    post.assert_not_called()


def test_invalid_json_is_safe_response_error():
    from llm.client import LLMResponseError
    response = _mock_response("ok")
    response.json.side_effect = ValueError("sensitive response")
    client = LLMClient(api_key="k")
    with patch("requests.post", return_value=response) as post:
        with pytest.raises(LLMResponseError) as error:
            client.chat([])
    assert "sensitive" not in str(error.value)
    assert post.call_count == 1


def test_explicit_endpoint_and_model():
    client = LLMClient(api_key="k", base_url="https://example.test/v1", timeout=3)
    with patch("requests.post", return_value=_mock_response("ok")) as post:
        client.chat([], model="chosen")
    assert post.call_args.args[0] == "https://example.test/v1/chat/completions"
    assert post.call_args.kwargs["json"]["model"] == "chosen"
    assert post.call_args.kwargs["timeout"] == 3
