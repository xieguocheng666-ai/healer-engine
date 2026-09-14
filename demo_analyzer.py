"""失败分析演示：Analyzer 区分「选择器问题」vs「产品 bug」（文本 LLM + 视觉双通道）。"""

import requests

from agent.analyzer import Analyzer
from agent.runner import Runner
from config.settings import SUT_BASE_URL
from llm.client import LLMClient
from schemas.step import Step


def login_and_enter_index(page) -> None:
    resp = requests.post(f"{SUT_BASE_URL}/login", json={"username": "alice", "password": "alice123"})
    token = resp.json()["token"]
    page.goto(f"{SUT_BASE_URL}/login.html")
    page.evaluate(f"localStorage.setItem('shop_token', '{token}')")
    page.goto(f"{SUT_BASE_URL}/index.html")
    page.wait_for_timeout(1500)


def main() -> None:
    client = LLMClient()
    with Runner() as runner:
        login_and_enter_index(runner.page)
        analyzer = Analyzer(client)

        # 场景1：选择器问题（元素实际存在，只是选择器写脆了）
        bad = Step(action="click", selector="[data-action='add-to-cart']")
        status1, detail1 = runner.run_step(bad)
        print("=== 场景1：失效选择器（元素实际存在）===")
        print(f"执行结果：{status1}")
        a1 = analyzer.analyze(detail1)
        print(f"判定：{a1['category']}（置信度 {a1['confidence']}，需复核 {a1['need_review']}）")
        print(f"证据：{a1['evidence']}")

        # 场景2：产品 bug（断言一个不存在的功能/文案）
        buggy = Step(action="assert", assert_type="text_visible", value="免费领取1000元优惠券")
        status2, detail2 = runner.run_step(buggy)
        print("\n=== 场景2：断言不存在的功能文案 ===")
        print(f"执行结果：{status2}")
        a2 = analyzer.analyze(detail2)
        print(f"判定：{a2['category']}（置信度 {a2['confidence']}，需复核 {a2['need_review']}）")
        print(f"证据：{a2['evidence']}")

        print("\n失败分析演示完成")


if __name__ == "__main__":
    main()
