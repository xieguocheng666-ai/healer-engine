"""① Generator：自然语言 → 结构化步骤 JSON（pydantic 校验 + 重试）。

三步「正经」机制：
1. Prompt 固化 schema —— system prompt 附完整 action 集 + 定位器规则 + 示例，要求只输出 JSON。
2. 解析 + pydantic 校验 —— json 提取 → Step 列表解析 → 校验失败打回重试（最多 3 次）。
3. 定位器优先 data-* 语义属性 —— 降低后续自愈触发率。
"""

import json
import re

from pydantic import ValidationError

from config.settings import SUT_BASE_URL
from llm.client import LLMClient
from schemas.step import Step

SYSTEM_PROMPT = """你是 UI 自动化测试用例生成器。把用户的自然语言指令转成一条条可执行的测试步骤，输出一个 JSON 数组。

步骤字段（action 决定哪些字段必填）：
- goto: 打开页面，需 url
- fill: 输入文本，需 selector + value
- click: 点击，需 selector
- wait: 等待元素出现，需 selector
- assert: 断言，需 assert_type（取值 url_contains / text_visible）+ value

定位器规则（重要，直接影响测试稳定性）：
1. 优先用 id（如 #username、#password、#loginBtn）
2. 其次 name、placeholder、aria-label
3. 页面若有 data-action、data-store-id、data-product-id 等语义属性，优先用它们
4. 避免依赖易变的 class 和深层 DOM 层级

只输出 JSON 数组本身，不要任何解释，不要 markdown 代码块标记。
示例（选择器为被测系统真实存在的）：[{"action":"goto","url":"http://127.0.0.1:8000/login.html"},{"action":"fill","selector":"#username","value":"alice"},{"action":"fill","selector":"#password","value":"alice123"},{"action":"click","selector":"#loginBtn"},{"action":"assert","assert_type":"url_contains","value":"index.html"}]
"""


def _extract_json_array(text: str) -> list:
    """从 LLM 输出里稳健地提取 JSON 数组（容忍 markdown 代码块包裹）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("响应中未找到 JSON 数组", text, 0)
    return json.loads(text[start : end + 1])


class Generator:
    """自然语言用例生成器。"""

    def __init__(self, client: LLMClient, base_url: str = SUT_BASE_URL):
        self.client = client
        self.base_url = base_url

    def generate(self, instruction: str, max_retries: int = 3) -> list[Step]:
        """生成步骤列表。非法 JSON / 非法 action / 缺字段都会重试，最终失败抛 RuntimeError。"""
        last_err: Exception | None = None
        for _ in range(max_retries):
            content = self.client.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                ],
                stage="generation",
            )
            try:
                raw = _extract_json_array(content)
                steps = [Step(**item) for item in raw]
                if not steps:
                    raise ValueError("生成步骤为空列表")
                return steps
            except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as e:
                last_err = e
        raise RuntimeError(f"用例生成失败（已重试 {max_retries} 次）：{last_err}")
