"""③ Healer：分层自愈（L1 免费预检 → L2 LLM 重定位）+ 护栏 + 审计日志。

五条护栏（防误修，面试重点）：
1. 只修定位器，绝不修断言 —— assert 失败由 Runner 分流，不进入本模块。
2. 验证通过才应用 —— 候选必须唯一、可操作且符合独立目标约束，否则丢弃。
3. 关键流程不自动修 —— page_url 命中 login/payment/checkout 时，只记审计、不自动应用。
4. 同一选择器失败超上限停止 —— 超过 MAX_HEAL_ATTEMPTS_PER_SELECTOR 次不再尝试。
5. 重试上限由编排层控制（单用例自愈重跑最多 MAX_HEAL_ROUNDS_PER_RUN 轮）。

每次自愈都记录目标、候选和拒绝原因；普通运行没有人工标注，误修数保持未知。
"""

import json
import re
import time
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

from config.settings import (
    CRITICAL_URL_KEYWORDS,
    MAX_HEAL_ATTEMPTS_PER_SELECTOR,
)
from llm.client import LLMClient
from schemas.step import FailureDetail, Step


@dataclass
class HealRecord:
    run_id: str
    step_index: int
    original_selector: str
    new_selector: str | None
    layer: str | None  # "L1" / "L2" / None（未命中）
    verified: bool
    guardrail_hit: bool
    auto_applied: bool
    timestamp: float
    reason: str = "candidate_verified"
    target: dict | None = None


class Healer:
    def __init__(self, client: LLMClient, page, audit_path: str = "heals.jsonl", *, allow_llm: bool = True):
        self.client = client
        self.page = page
        self.audit_path = audit_path
        self.records: list[HealRecord] = []
        self._attempts: dict[str, int] = {}
        self.allow_llm = allow_llm

    def heal(self, failure: FailureDetail, step_index: int, run_id: str) -> str | None:
        """尝试自愈定位失败。返回可应用的新选择器，或 None（修不好 / 触栏不应用）。"""
        original = failure.step.selector or ""
        self._target = failure.step.target
        # 在花费模型调用或挑选候选之前拒绝不安全输入。
        reason = None
        if failure.step.action not in ("click", "fill", "wait"):
            reason = "unsupported_action"
        elif failure.step.sensitive:
            reason = "sensitive_step"
        elif self._is_critical(failure.page_url) or self._is_critical(self.page.url):
            reason = "critical_flow"
        elif self._target is None:
            reason = "missing_target"
        elif not self._target_available(failure.step):
            reason = "target_missing_ambiguous_or_unavailable"
        if reason:
            self._record(run_id, step_index, original, None, None, False, True, False, reason)
            return None

        # 护栏 #4：同一选择器失败超上限，停止自愈
        if self._attempts.get(original, 0) >= MAX_HEAL_ATTEMPTS_PER_SELECTOR:
            self._record(run_id, step_index, original, None, None, False, True, False, "attempt_limit")
            return None
        self._attempts[original] = self._attempts.get(original, 0) + 1

        # 分层：L1 免费预检 → L2 LLM 重定位
        new_selector = self._try_l1(failure)
        layer = "L1" if new_selector else None
        if new_selector is None:
            new_selector = self._try_l2(failure)
            layer = "L2" if new_selector else None
        if new_selector is None:
            self._record(run_id, step_index, original, None, None, False, False, False, "no_candidate")
            return None

        # 护栏 #2：验证通过才应用
        verified = self._verify(new_selector, failure.step)
        if not verified:
            self._record(run_id, step_index, original, new_selector, layer, False, True, False, "candidate_rejected")
            return None

        # 护栏 #3：关键流程只记审计、不自动应用
        critical = self._is_critical(failure.page_url) or self._is_critical(self.page.url)
        auto_applied = not critical
        self._record(run_id, step_index, original, new_selector, layer, True, critical, auto_applied)
        return new_selector if auto_applied else None

    # ---- L1 免费预检 ----
    def _try_l1(self, failure: FailureDetail) -> str | None:
        html = failure.element_html or ""
        for sel in self._extract_candidates(html):
            if self._verify(sel, failure.step):
                return sel
        for sel in self._semantic_candidates(failure.step):
            if self._verify(sel, failure.step):
                return sel
        return None

    @staticmethod
    def _css_string(value: str) -> str:
        # CSS hex escapes terminate with a space so following digits stay literal.
        return '"' + ''.join(
            f"\\{ord(char):x} " if char in '\\"' or ord(char) < 32 or ord(char) == 127
            else char for char in value
        ) + '"'

    def _semantic_candidates(self, step: Step) -> list[str]:
        target = step.target
        base = target.tag
        for name, value in target.attributes.items():
            # Escape every identifier character, including punctuation and leading digits.
            escaped_name = ''.join(f"\\{ord(char):x} " for char in name)
            base += f'[{escaped_name}={self._css_string(value)}]'
        candidates = [base] if target.attributes else []
        if target.text:
            candidates.append(f'{base}:text-is({self._css_string(target.text)})')
            # :text-is selects the smallest text element. XPath string(.) also
            # handles a button whose entire label is nested inside a span.
            whitespace = '\t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
            normalized = f'normalize-space(translate(string(.), {self._xpath_string(whitespace)}, {self._xpath_string(" " * len(whitespace))}))'
            candidates.append(f'{base} >> xpath=self::*[{normalized}={self._xpath_string(target.text)}]')
        return candidates

    @staticmethod
    def _xpath_string(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        return 'concat(' + ', "\'", '.join(f"'{part}'" for part in value.split("'")) + ')'

    def _extract_candidates(self, html: str) -> list[str]:
        """从失败元素 HTML 提取稳定属性候选，按唯一性排序。

        逐个元素解析属性，优先「复合属性组合」（如 store_id+product_id），
        解决平台电商「同一商品多店在售、单属性不唯一」的定位问题。
        """
        candidates: list[str] = []
        class AttributeParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.elements = []

            def handle_starttag(self, tag, attrs):
                self.elements.append(dict(attrs))

        parser = AttributeParser()
        parser.feed(html)
        for attrs in parser.elements:
            if not attrs:
                continue
            # id（跳过纯数字开头的非法 CSS id，如 id="1"）
            element_id = attrs.get("id")
            if element_id and not element_id[:1].isdigit():
                if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", element_id):
                    candidates.append(f"#{element_id}")
                else:
                    candidates.append(f'[id={self._css_string(element_id)}]')
            # 复合唯一键：store_id + product_id 组合
            combo = []
            if attrs.get("data-store-id") is not None:
                combo.append(f'data-store-id={self._css_string(attrs["data-store-id"])}')
            if attrs.get("data-product-id") is not None:
                combo.append(f'data-product-id={self._css_string(attrs["data-product-id"])}')
            if len(combo) >= 2:
                candidates.append("[" + "][".join(combo) + "]")
            # 单个稳定属性
            for attr in ("data-testid", "aria-label", "data-action", "name", "placeholder"):
                if attrs.get(attr) is not None:
                    # Attribute names here are fixed, valid CSS identifiers.
                    candidates.append(f'[{attr}={self._css_string(attrs[attr])}]')
        return candidates

    # ---- L2 LLM 重定位 ----
    def _try_l2(self, failure: FailureDetail) -> str | None:
        if not self.allow_llm:
            return None
        prompt = (
            f"下面的 UI 测试步骤因选择器定位失败，请重新定位目标元素，输出一个可用的 CSS 选择器。\n\n"
            f"失败信息：{failure.error}\n"
            f"当前页面 URL：{failure.page_url}\n"
            f"步骤意图：{self._describe_step(failure.step)}\n"
            f"原选择器：{failure.step.selector}\n\n"
            f"不可修改的目标约束：{failure.step.target.model_dump_json()}\n"
            f"页面 HTML 片段（可能截断）：\n{failure.element_html or '（无 HTML 片段）'}\n\n"
            f"只输出一个新的 CSS 选择器字符串，不要任何解释。若无法确定，输出 NONE。"
        )
        try:
            content = self.client.chat(
                [
                    {"role": "system", "content": "你是选择器自愈助手，只输出 CSS 选择器或 NONE。"},
                    {"role": "user", "content": prompt},
                ],
                stage="healing",
            )
        except Exception:
            return None
        sel = content.strip().strip("`'\"").strip()
        if not sel or sel.upper() == "NONE":
            return None
        return sel

    def _describe_step(self, step: Step) -> str:
        if step.action == "fill":
            return "在目标输入框填入内容"
        if step.action == "click":
            return "点击一个按钮或链接"
        if step.action == "wait":
            return "等待一个元素出现"
        if step.action == "assert":
            return f"断言「{step.assert_type}={step.value}」"
        return step.action

    # ---- 验证 / 护栏判定 ----
    def _matching_indices(self, step: Step) -> list[int]:
        return self.page.locator(step.target.tag).evaluate_all(
            r"""(elements, target) => elements.flatMap((el, i) => {
                const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
                return ((!target.text || text === target.text) &&
                    Object.entries(target.attributes).every(([k,v]) => el.getAttribute(k) === v)) ? [i] : [];
            })""", step.target.model_dump())

    def _target_available(self, step: Step) -> bool:
        try:
            indices = self._matching_indices(step)
            if len(indices) != 1:
                return False
            loc = self.page.locator(step.target.tag).nth(indices[0])
            if not loc.is_visible():
                return False
            if step.action == "click":
                loc.click(trial=True, timeout=750)
            elif step.action == "fill" and not loc.is_editable():
                return False
            return True
        except Exception:
            return False

    def _verify(self, selector: str, step: Step | None = None) -> bool:
        try:
            loc = self.page.locator(selector)
            if loc.count() != 1 or not loc.is_visible() or step is None or step.target is None:
                return False
            indices = self._matching_indices(step)
            if len(indices) != 1:
                return False
            target = self.page.locator(step.target.tag).nth(indices[0]).element_handle()
            try:
                if not loc.evaluate("(el, target) => el === target", target):
                    return False
            finally:
                if target is not None:
                    target.dispose()
            return self._target_available(step)
        except Exception:
            return False

    def _is_critical(self, url: str | None) -> bool:
        return any(k in (url or "").lower() for k in CRITICAL_URL_KEYWORDS)

    # ---- 审计日志 ----
    def _record(self, run_id, step_index, original, new_selector, layer, verified, guardrail_hit, auto_applied, reason="candidate_verified"):
        record = HealRecord(
            run_id=run_id,
            step_index=step_index,
            original_selector=original,
            new_selector=new_selector,
            layer=layer,
            verified=verified,
            guardrail_hit=guardrail_hit,
            auto_applied=auto_applied,
            timestamp=time.time(),
            reason=reason,
            target=self._target.model_dump() if getattr(self, "_target", None) else None,
        )
        self.records.append(record)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
