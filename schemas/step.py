"""步骤与失败详情的 pydantic 数据模型。

Step 是 LLM 生成结果的唯一合法载体：action 集合封闭，非法 action
或缺失必填字段会在校验层被直接拒绝，保证「非法步骤不进入执行」。
FailureDetail 是 Runner 捕获的失败详情，作为 Healer / Analyzer 的输入。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Action = Literal["goto", "fill", "click", "assert", "wait"]
AssertType = Literal["url_contains", "text_visible", "text_equals", "count", "value_equals", "attribute_equals"]


class Target(BaseModel):
    """独立于失效选择器的目标约束；全部条件必须同时成立。"""

    model_config = ConfigDict(extra="forbid")
    tag: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    text: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_identity(self):
        if self.text is not None:
            self.text = " ".join(self.text.split())
        if not self.text and not self.attributes:
            raise ValueError("目标必须有精确文字或属性，只有标签不足以识别目标")
        if any(not key.strip() or not value.strip() for key, value in self.attributes.items()):
            raise ValueError("目标属性名和值不能为空")
        return self


class Step(BaseModel):
    """一条 UI 测试步骤。"""

    model_config = ConfigDict(extra="forbid")
    action: Action
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    assert_type: AssertType | None = None
    target: Target | None = None
    attribute: str | None = None
    sensitive: bool = False

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
        if self.action == "assert" and self.assert_type in ("text_equals", "count", "value_equals", "attribute_equals") and not self.selector:
            raise ValueError("精确断言必须提供 selector")
        if self.action == "assert" and self.assert_type == "attribute_equals" and not self.attribute:
            raise ValueError("属性断言必须提供 attribute")
        if self.action == "assert" and self.assert_type == "count" and (not self.value.isdigit()):
            raise ValueError("元素数量必须为非负整数")
        return self


@dataclass
class FailureDetail:
    """Runner 捕获的失败详情。"""

    step: Step
    error: str
    page_url: str
    element_html: str | None = None
    screenshot: bytes | None = None
