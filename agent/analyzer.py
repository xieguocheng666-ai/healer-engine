"""④ Analyzer：对自愈救不回来的失败，判断性质。

双通道：
- 文本 LLM（glm-4-flash）：读 error + element_html + 页面上下文，判断
  「元素确实存在但选择器失效」vs「元素根本不存在（功能/数据缺失）」。
- 视觉模型（glm-4v-flash）：读截图，判断页面是否正常渲染、目标元素是否可见。

两者结论一致 → 高置信度；不一致或缺截图 → 标记「需人工复核」。
"""

import json
import re

from llm.client import LLMClient
from schemas.step import FailureDetail, Step

_TEXT_PROMPT = """你是测试失败分析专家。判断下面这个测试失败属于哪一类。

失败步骤：{step_desc}
失败信息：{error}
当前页面 URL：{page_url}
元素 HTML 片段（失败元素或页面上下文，可能截断）：{element_html}

判断规则（严格按顺序）：
1. 先看「元素 HTML 片段」里是否存在与失败步骤目标相符的元素：
   - 存在 → 元素确实在页面上，只是选择器写脆了（class 变了、属性名写错）→ 分类「选择器问题」
   - 不存在 → 元素/功能根本缺失（功能未实现、数据缺失导致不渲染）→ 分类「产品 bug」
2. 若失败源于服务未启动、网络异常、数据加载失败等外部因素 → 分类「环境/数据问题」

只输出一个 JSON 对象：{{"category":"产品 bug|选择器问题|环境/数据问题","evidence":"一句话证据","suggestion":"一句修复建议"}}"""

_VISION_PROMPT = """你是页面视觉分析助手。这是一张测试失败时的页面截图，测试目标是：{step_desc}
请判断：页面是否正常渲染？这个目标元素/文本在截图中是否可见？

只输出一个 JSON 对象：{{"rendered": true 或 false, "target_visible": true 或 false, "note": "一句话说明"}}"""


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("响应中未找到 JSON 对象")
    return json.loads(text[start : end + 1])


class Analyzer:
    def __init__(self, client: LLMClient):
        self.client = client

    def analyze(self, failure: FailureDetail) -> dict:
        text_result = self._analyze_text(failure)
        vision_result = self._analyze_vision(failure)
        category, confidence, need_review = self._fuse(text_result, vision_result)
        return {
            "category": category,
            "confidence": confidence,
            "need_review": need_review,
            "evidence": text_result.get("evidence", ""),
            "suggestion": text_result.get("suggestion", ""),
            "text_verdict": text_result,
            "vision_verdict": vision_result,
        }

    def _analyze_text(self, failure: FailureDetail) -> dict:
        prompt = _TEXT_PROMPT.format(
            error=failure.error,
            page_url=failure.page_url,
            step_desc=self._describe_step(failure.step),
            element_html=failure.element_html or "（无 HTML 片段）",
        )
        try:
            content = self.client.chat(
                [
                    {"role": "system", "content": "你是测试失败分析专家，只输出 JSON 对象。"},
                    {"role": "user", "content": prompt},
                ],
                stage="analysis_text",
            )
            return _extract_json_object(content)
        except Exception as e:
            return {"category": "环境/数据问题", "evidence": f"文本分析失败：{e}", "suggestion": "人工排查"}

    def _analyze_vision(self, failure: FailureDetail) -> dict | None:
        if not failure.screenshot:
            return None
        try:
            prompt = _VISION_PROMPT.format(step_desc=self._describe_step(failure.step))
            content = self.client.chat_vision(prompt, failure.screenshot, stage="analysis_vision")
            return _extract_json_object(content)
        except Exception:
            return None

    def _fuse(self, text_result: dict, vision_result: dict | None) -> tuple[str, str, bool]:
        category = text_result.get("category", "环境/数据问题")
        if vision_result is None:
            return category, "medium", True

        target_visible = vision_result.get("target_visible", False)
        rendered = vision_result.get("rendered", False)

        # 文本说「选择器问题」= 元素存在；视觉说可见 → 一致，高置信
        if category == "选择器问题" and target_visible:
            return category, "high", False
        # 文本说「产品 bug」= 元素不存在；视觉说不可见且页面正常渲染 → 一致
        if category == "产品 bug" and rendered and not target_visible:
            return category, "high", False
        # 文本说「环境/数据问题」且视觉说未正常渲染 → 一致
        if category == "环境/数据问题" and not rendered:
            return category, "high", False
        # 其余：文本与视觉矛盾 → 需人工复核
        return category, "low", True

    def _describe_step(self, step: Step) -> str:
        if step.action == "fill":
            return f"填写输入框（{step.selector}），输入值不参与失败分析"
        if step.action == "click":
            return f"点击元素（{step.selector}）"
        if step.action == "wait":
            return f"等待元素出现（{step.selector}）"
        if step.action == "assert":
            return f"断言「{step.assert_type}={step.value}」"
        if step.action == "goto":
            return f"打开页面（{step.url}）"
        return step.action
