"""③ Healer：分层自愈（L1 免费预检 → L2 LLM 重定位）+ 护栏 + 审计日志。

五条护栏（防误修，面试重点）：
1. 只修定位器，绝不修断言 —— assert 失败由 Runner 分流，不进入本模块。
2. 验证通过才应用 —— 新选择器必须在真实 DOM 满足 count==1 且可见，否则丢弃。
3. 关键流程不自动修 —— page_url 命中 login/payment/checkout 时，只记审计、不自动应用。
4. 同一选择器失败超上限停止 —— 超过 MAX_HEAL_ATTEMPTS_PER_SELECTOR 次不再尝试。
5. 重试上限由编排层控制（单用例自愈重跑最多 MAX_HEAL_ROUNDS_PER_RUN 轮）。

每次自愈（成功/失败/触栏）都追加一行到 heals.jsonl，是 false-heal count 与 heal rate 的唯一数据源。
"""

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

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


class Healer:
    def __init__(self, client: LLMClient, page, audit_path: str = "heals.jsonl"):
        self.client = client
        self.page = page
        self.audit_path = audit_path
        self.records: list[HealRecord] = []
        self._attempts: dict[str, int] = {}

    def heal(self, failure: FailureDetail, step_index: int, run_id: str) -> str | None:
        """尝试自愈定位失败。返回可应用的新选择器，或 None（修不好 / 触栏不应用）。"""
        original = failure.step.selector or ""

        # 护栏 #4：同一选择器失败超上限，停止自愈
        if self._attempts.get(original, 0) >= MAX_HEAL_ATTEMPTS_PER_SELECTOR:
            self._record(run_id, step_index, original, None, None, False, True, False)
            return None
        self._attempts[original] = self._attempts.get(original, 0) + 1

        # 分层：L1 免费预检 → L2 LLM 重定位
        new_selector = self._try_l1(failure)
        layer = "L1" if new_selector else None
        if new_selector is None:
            new_selector = self._try_l2(failure)
            layer = "L2" if new_selector else None
        if new_selector is None:
            self._record(run_id, step_index, original, None, None, False, False, False)
            return None

        # 护栏 #2：验证通过才应用
        verified = self._verify(new_selector)
        if not verified:
            self._record(run_id, step_index, original, new_selector, layer, False, True, False)
            return None

        # 护栏 #3：关键流程只记审计、不自动应用
        critical = self._is_critical(failure.page_url)
        auto_applied = not critical
        self._record(run_id, step_index, original, new_selector, layer, True, critical, auto_applied)
        return new_selector if auto_applied else None

    # ---- L1 免费预检 ----
    def _try_l1(self, failure: FailureDetail) -> str | None:
        html = failure.element_html or ""
        for sel in self._extract_candidates(html):
            if self._verify(sel):
                return sel
        return None

    def _extract_candidates(self, html: str) -> list[str]:
        """从失败元素 HTML 提取稳定属性候选，按唯一性排序。

        逐个元素解析属性，优先「复合属性组合」（如 store_id+product_id），
        解决平台电商「同一商品多店在售、单属性不唯一」的定位问题。
        """
        candidates: list[str] = []
        for tag in re.findall(r"<[^>]+>", html):
            attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', tag))
            if not attrs:
                continue
            # id（跳过纯数字开头的非法 CSS id，如 id="1"）
            if "id" in attrs and not attrs["id"][:1].isdigit():
                candidates.append(f"#{attrs['id']}")
            # 复合唯一键：store_id + product_id 组合
            combo = []
            if "data-store-id" in attrs:
                combo.append(f'data-store-id="{attrs["data-store-id"]}"')
            if "data-product-id" in attrs:
                combo.append(f'data-product-id="{attrs["data-product-id"]}"')
            if len(combo) >= 2:
                candidates.append("[" + "][".join(combo) + "]")
            # 单个稳定属性
            for attr in ("data-testid", "aria-label", "data-action", "name", "placeholder"):
                if attr in attrs:
                    candidates.append(f'[{attr}="{attrs[attr]}"]')
        return candidates

    # ---- L2 LLM 重定位 ----
    def _try_l2(self, failure: FailureDetail) -> str | None:
        prompt = (
            f"下面的 UI 测试步骤因选择器定位失败，请重新定位目标元素，输出一个可用的 CSS 选择器。\n\n"
            f"失败信息：{failure.error}\n"
            f"当前页面 URL：{failure.page_url}\n"
            f"步骤意图：{self._describe_step(failure.step)}\n"
            f"原选择器：{failure.step.selector}\n\n"
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
            return f"在输入框填入「{step.value}」"
        if step.action == "click":
            return "点击一个按钮或链接"
        if step.action == "wait":
            return "等待一个元素出现"
        if step.action == "assert":
            return f"断言「{step.assert_type}={step.value}」"
        return step.action

    # ---- 验证 / 护栏判定 ----
    def _verify(self, selector: str) -> bool:
        try:
            loc = self.page.locator(selector)
            return loc.count() == 1 and loc.first.is_visible()
        except Exception:
            return False

    def _is_critical(self, url: str | None) -> bool:
        return any(k in (url or "").lower() for k in CRITICAL_URL_KEYWORDS)

    # ---- 审计日志 ----
    def _record(self, run_id, step_index, original, new_selector, layer, verified, guardrail_hit, auto_applied):
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
        )
        self.records.append(record)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
