"""智谱开放平台 LLM 统一封装：文本调用 + 视觉调用 + token 记账。

每次调用都会追加一条 LLMUsage 记录（model / input_tokens / output_tokens / stage），
供 M5 Metrics 聚合出真实 token 成本——不写死、从 usage 字段真实读取。
"""

import base64
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
    input_tokens: int
    output_tokens: int
    stage: str


class LLMClient:
    """智谱统一封装。同一实例可发文本 / 视觉两类请求，共用一份记账列表。"""

    def __init__(
        self,
        api_key: str = ZHIPU_API_KEY,
        base_url: str = ZHIPU_BASE_URL,
        text_model: str = TEXT_MODEL,
        vision_model: str = VISION_MODEL,
    ):
        if not api_key:
            raise ValueError("ZHIPU_API_KEY 未配置，请检查 config/.env")
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
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})
        self.calls.append(
            LLMUsage(
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                stage=stage,
            )
        )
        return data["choices"][0]["message"]["content"]
