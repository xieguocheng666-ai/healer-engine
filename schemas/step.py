"""步骤与失败详情的 pydantic 数据模型。

Step 是 LLM 生成结果的唯一合法载体：action 集合封闭，非法 action
或缺失必填字段会在校验层被直接拒绝，保证「非法步骤不进入执行」。
FailureDetail 是 Runner 捕获的失败详情，作为 Healer / Analyzer 的输入。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, model_validator

Action = Literal["goto", "fill", "click", "assert", "wait"]
AssertType = Literal["url_contains", "text_visible"]


class Step(BaseModel):
    """一条 UI 测试步骤。"""

    action: Action
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    assert_type: AssertType | None = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> "Step":
        if self.action == "goto" and not self.url:
            raise ValueError("goto 步骤必须提供 url")
        if self.action in ("fill", "click", "wait") and not self.selector:
            raise ValueError(f"{self.action} 步骤必须提供 selector")
        if self.action == "fill" and self.value is None:
            raise ValueError("fill 步骤必须提供 value")
        if self.action == "assert" and (not self.assert_type or self.value is None):
            raise ValueError("assert 步骤必须提供 assert_type 和 value")
        return self


@dataclass
class FailureDetail:
    """Runner 捕获的失败详情。"""

    step: Step
    error: str
    page_url: str
    element_html: str | None = None
    screenshot: bytes | None = None
