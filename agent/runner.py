"""② Runner：Playwright 同步 API 逐条执行步骤，捕获失败详情。

失败分两类：
- 定位失败（TimeoutError / strict mode violation 等 Playwright 异常）→ 交给 Healer。
- 断言失败（AssertionError）→ 不进自愈，直接交给 Analyzer。

run_step 返回 (status, detail)：
  passed / locator_failed / assert_failed / execution_failed（导航或环境异常）。
"""

import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import (
    sync_playwright,
    expect,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)

from config.settings import SUT_BASE_URL
from schemas.step import Step, FailureDetail


class Runner:
    def __init__(self, base_url: str = SUT_BASE_URL, headless: bool = True, *, timeout_ms: int = 10000, trace_path=None):
        self.base_url = base_url
        self.headless = headless
        self._pw = None
        self._browser = None
        self.page = None
        self.timeout_ms = timeout_ms
        self.trace_path = Path(trace_path) if trace_path else None
        self._context = None

    def __enter__(self) -> "Runner":
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context()
            if self.trace_path:
                self.trace_path.parent.mkdir(parents=True, exist_ok=True)
                self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
            self.page = self._context.new_page()
            self.page.set_default_timeout(self.timeout_ms)
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._context and self.trace_path:
                self._context.tracing.stop(path=str(self.trace_path))
        finally:
            try:
                if self._browser:
                    self._browser.close()
            finally:
                if self._pw:
                    self._pw.stop()

    def run_step(self, step: Step) -> tuple[str, FailureDetail | None]:
        """执行单步，区分操作定位失败、断言失败和导航/环境异常。"""
        try:
            self._execute(step)
            return "passed", None
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            message = str(e).lower()
            non_locator = step.action == "goto" or any(
                marker in message for marker in ("has been closed", "target closed", "crash", "net::err_")
            )
            status = "execution_failed" if non_locator else ("assert_failed" if step.action == "assert" else "locator_failed")
            return status, self._capture(step, f"{type(e).__name__}: {e}")
        except AssertionError as e:
            return "assert_failed", self._capture(step, f"AssertionError: {e}")

    def _execute(self, step: Step) -> None:
        if step.action in ('click', 'fill', 'wait') and step.target is not None:
            from agent.healer import Healer
            if not Healer(None, self.page, allow_llm=False)._verify(step.selector, step):
                raise PlaywrightError('定位器不满足独立目标约束，拒绝执行')
        if step.action == "goto":
            self.page.goto(urljoin(self.base_url, step.url), wait_until="load", timeout=self.timeout_ms)
        elif step.action == "fill":
            self.page.locator(step.selector).fill(step.value, timeout=self.timeout_ms)
        elif step.action == "click":
            self.page.locator(step.selector).click(timeout=self.timeout_ms)
        elif step.action == "wait":
            self.page.locator(step.selector).wait_for(state="visible", timeout=self.timeout_ms)
        elif step.action == "assert":
            if step.assert_type == "url_contains":
                # 用 expect 自动等待跳转（登录跳转有 450ms 延迟），而非立即读 page.url
                expect(self.page).to_have_url(re.compile(re.escape(step.value)), timeout=self.timeout_ms)
            elif step.assert_type == "text_visible":
                loc = self.page.locator(step.selector).get_by_text(step.value, exact=True) if step.selector else self.page.get_by_text(step.value, exact=True)
                expect(loc).to_be_visible(timeout=self.timeout_ms)
            elif step.assert_type == "text_equals":
                expect(self.page.locator(step.selector)).to_have_text(step.value, timeout=self.timeout_ms)
            elif step.assert_type == "count":
                expect(self.page.locator(step.selector)).to_have_count(int(step.value), timeout=self.timeout_ms)
            elif step.assert_type == "value_equals":
                expect(self.page.locator(step.selector)).to_have_value(step.value, timeout=self.timeout_ms)
            elif step.assert_type == "attribute_equals":
                expect(self.page.locator(step.selector)).to_have_attribute(step.attribute, step.value, timeout=self.timeout_ms)

    def _capture(self, step: Step, error: str) -> FailureDetail:
        evidence_step = step.model_copy(deep=True)
        html = self._try_element_html(step)
        # 模型不需要填写内容来判断目标；保留原步骤供执行，失败证据用副本。
        if step.action == 'fill' and step.value:
            error = error.replace(step.value, '[已隐藏]')
            if html:
                html = html.replace(step.value, '[已隐藏]')
            evidence_step.value = '[已隐藏]'
            if evidence_step.selector:
                evidence_step.selector = evidence_step.selector.replace(step.value, '[已隐藏]')
        if html:
            html = re.sub(r'''(?is)\bvalue\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)''', 'value="[已隐藏]"', html)
            html = re.sub(r'(?is)(<textarea\b[^>]*>).*?(</textarea>)', r'\1[已隐藏]\2', html)
        return FailureDetail(
            step=evidence_step,
            error=error,
            page_url=self.page.url,
            element_html=html,
            screenshot=self._try_screenshot(),
        )

    def _try_element_html(self, step: Step) -> str | None:
        if step.selector:
            try:
                loc = self.page.locator(step.selector)
                if loc.count():
                    return loc.first.evaluate("el => el.outerHTML", timeout=500)
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
