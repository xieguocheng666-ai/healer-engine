"""② Runner：Playwright 同步 API 逐条执行步骤，捕获失败详情。

失败分两类：
- 定位失败（TimeoutError / strict mode violation 等 Playwright 异常）→ 交给 Healer。
- 断言失败（AssertionError）→ 不进自愈，直接交给 Analyzer。

run_step 返回 (status, detail)：
  ("passed", None) / ("locator_failed", FailureDetail) / ("assert_failed", FailureDetail)
"""

import re

from playwright.sync_api import (
    sync_playwright,
    expect,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)

from config.settings import SUT_BASE_URL
from schemas.step import Step, FailureDetail


class Runner:
    def __init__(self, base_url: str = SUT_BASE_URL, headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self._pw = None
        self._browser = None
        self.page = None

    def __enter__(self) -> "Runner":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self._browser.new_page()
        return self

    def __exit__(self, *exc) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def run_step(self, step: Step) -> tuple[str, FailureDetail | None]:
        """执行单步。返回 (status, detail)，status ∈ passed/locator_failed/assert_failed。"""
        try:
            self._execute(step)
            return "passed", None
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            return "locator_failed", self._capture(step, f"{type(e).__name__}: {e}")
        except AssertionError as e:
            return "assert_failed", self._capture(step, f"AssertionError: {e}")

    def _execute(self, step: Step) -> None:
        if step.action == "goto":
            self.page.goto(step.url, wait_until="load", timeout=15000)
        elif step.action == "fill":
            self.page.locator(step.selector).fill(step.value, timeout=10000)
        elif step.action == "click":
            self.page.locator(step.selector).click(timeout=10000)
        elif step.action == "wait":
            self.page.locator(step.selector).wait_for(state="visible", timeout=10000)
        elif step.action == "assert":
            if step.assert_type == "url_contains":
                # 用 expect 自动等待跳转（登录跳转有 450ms 延迟），而非立即读 page.url
                expect(self.page).to_have_url(re.compile(re.escape(step.value)), timeout=10000)
            elif step.assert_type == "text_visible":
                expect(self.page.locator(f"text={step.value}").first).to_be_visible(timeout=10000)

    def _capture(self, step: Step, error: str) -> FailureDetail:
        return FailureDetail(
            step=step,
            error=error,
            page_url=self.page.url,
            element_html=self._try_element_html(step),
            screenshot=self._try_screenshot(),
        )

    def _try_element_html(self, step: Step) -> str | None:
        if step.selector:
            try:
                return self.page.locator(step.selector).first.evaluate("el => el.outerHTML")
            except Exception:
                pass
        # 定位失败兜底：按 action 抓同类型元素的 HTML，而非整页 body，
        # 让 Healer 的 L1 预检能从「相关元素」里提取候选，而不是从整页里误提无关元素。
        try:
            if step.action == "fill":
                qs = "input"
            elif step.action == "click":
                qs = "button, a"
            else:
                qs = "body"
            return self.page.evaluate(
                "qs => Array.from(document.querySelectorAll(qs)).map(e => e.outerHTML).join('\\n')",
                qs,
            )[:2000]
        except Exception:
            return None

    def _try_screenshot(self) -> bytes | None:
        try:
            return self.page.screenshot()
        except Exception:
            return None
