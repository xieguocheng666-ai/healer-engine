"""智谱开放平台 LLM 统一封装：文本调用 + 视觉调用 + token 记账。

每次调用都会追加一条 LLMUsage 记录（model / input_tokens / output_tokens / stage），
供 M5 Metrics 聚合出真实 token 成本——不写死、从 usage 字段真实读取。
"""

import base64
import time
from dataclasses import dataclass

import requests

from config.settings import (
    ZHIPU_API_KEY,
    ZHIPU_BASE_URL,
    TEXT_MODEL,
    VISION_MODEL,
)


@dataclass
class LLMUsage:
    """单次 LLM 调用的 token 记账。"""

    model: str
    input_tokens: int | None
    output_tokens: int | None
    stage: str


class LLMError(RuntimeError):
    """可捕获的模型调用错误；消息不包含服务端正文。"""


class LLMBudgetExceeded(LLMError):
    pass


class LLMResponseError(LLMError):
    pass


@dataclass
class LLMAttempt:
    model: str
    stage: str
    duration_seconds: float
    status_code: int | None
    error_type: str | None


class LLMClient:
    """智谱统一封装。同一实例可发文本 / 视觉两类请求，共用一份记账列表。"""

    def __init__(
        self,
        api_key: str = ZHIPU_API_KEY,
        base_url: str = ZHIPU_BASE_URL,
        text_model: str = TEXT_MODEL,
        vision_model: str = VISION_MODEL,
        timeout: float = 15,
        max_calls: int = 20,
        max_retries: int = 1,
    ):
        if not api_key:
            raise ValueError("ZHIPU_API_KEY 未配置，请检查 config/.env")
        if timeout <= 0 or max_calls < 0 or max_retries < 0:
            raise ValueError("timeout must be positive; budgets must be nonnegative")
        self.timeout = timeout
        self.max_calls = max_calls
        self.max_retries = max_retries
        self.attempts: list[LLMAttempt] = []
        self.api_key = api_key
        self.base_url = base_url
        self.text_model = text_model
        self.vision_model = vision_model
        self.calls: list[LLMUsage] = []

    def chat(self, messages: list[dict], stage: str = "default", model: str | None = None) -> str:
        """文本对话。messages 为标准 OpenAI 格式：[{role, content}]。"""
        return self._request(messages, model or self.text_model, stage)

    def chat_vision(self, prompt: str, image_bytes: bytes, stage: str = "vision") -> str:
        """视觉问答。image_bytes 为 PNG/JPEG 字节，内部转 base64 data URL。"""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ]
        return self._request(messages, self.vision_model, stage)

    def _request(self, messages: list[dict], model: str, stage: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "messages": messages}
        for retry in range(self.max_retries + 1):
            if len(self.attempts) >= self.max_calls:
                raise LLMBudgetExceeded("LLM HTTP attempt budget exhausted")
            started = time.perf_counter()
            status = None
            error_type = None
            retryable = False
            failure = None
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                status = resp.status_code if isinstance(resp.status_code, int) else None
                resp.raise_for_status()
                try:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if not isinstance(content, str):
                        raise TypeError()
                except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                    raise LLMResponseError("Invalid LLM response structure or content") from None
                usage = data.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                def token_count(key):
                    value = usage.get(key)
                    return value if type(value) is int and value >= 0 else None
                self.calls.append(LLMUsage(model, token_count("prompt_tokens"),
                                           token_count("completion_tokens"), stage))
                return content
            except LLMResponseError as exc:
                error_type, failure = "invalid_response", exc
            except requests.Timeout:
                error_type, retryable = "timeout", True
            except requests.ConnectionError:
                error_type, retryable = "connection", True
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and isinstance(response.status_code, int):
                    status = response.status_code
                error_type = "http"
                retryable = status == 429 or (status is not None and 500 <= status <= 599)
            except requests.RequestException:
                error_type = "request"
            finally:
                self.attempts.append(LLMAttempt(model, stage, time.perf_counter() - started,
                                                status, error_type))
            if not retryable or retry == self.max_retries:
                raise failure or LLMError(f"LLM request failed: {error_type}, status={status}") from None
