"""自愈演示：登录后人为制造选择器失效，验证 Healer 分层自愈恢复并重跑通过。

这是本项目核心深度点的可运行证明：不是「LLM 生成对了就完事」，
而是「生成的选择器错了 / 前端改了 → Healer 重新定位 → 测试自愈通过」。
"""

import requests

from agent.healer import Healer
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

        # 人为制造失效选择器：真实加购按钮是 [data-action="add"]，故意写成 add-to-cart
        bad = Step(action="click", selector="[data-action='add-to-cart']")
        status, detail = runner.run_step(bad)
        print(f"[1] 失效选择器执行结果：{status}")
        assert status == "locator_failed", "预期定位失败"

        healer = Healer(client, runner.page)
        new_selector = healer.heal(detail, 0, "demo-heal")
        print(f"[2] Healer 自愈返回新选择器：{new_selector}")
        for r in healer.records:
            print(
                f"    审计：layer={r.layer} verified={r.verified} "
                f"guardrail_hit={r.guardrail_hit} auto_applied={r.auto_applied}"
            )
        assert new_selector is not None, "预期自愈成功"

        bad.selector = new_selector
        status2, _ = runner.run_step(bad)
        print(f"[3] 自愈后重跑结果：{status2}")
        assert status2 == "passed", "预期自愈后重跑通过"

        print("自愈有效性验证通过：选择器失效 -> Healer 恢复 -> 重跑通过")


if __name__ == "__main__":
    main()
