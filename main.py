"""AI 测试智能体入口：自然语言 → 生成 → 执行 → 自愈 → 分析 → 量化报告。

用法：
    python main.py "打开登录页，输入账号 alice 密码 alice123，点击登录，断言进入首页"
不带参数时用内置默认指令。
"""

import json
import sys
import uuid
from pathlib import Path

from agent.analyzer import Analyzer
from agent.generator import Generator
from agent.healer import Healer
from agent.metrics import Metrics
from agent.runner import Runner
from config.settings import MAX_HEAL_ROUNDS_PER_RUN, SUT_BASE_URL
from llm.client import LLMClient

DEFAULT_INSTRUCTION = (
    "打开登录页 http://127.0.0.1:8000/login.html，"
    "在用户名输入框填 alice，密码输入框填 alice123，"
    "点击登录按钮，断言 URL 包含 index.html"
)


def run_pipeline(instruction: str, base_url: str = SUT_BASE_URL, output_dir: str = "reports"):
    run_id = uuid.uuid4().hex[:8]
    client = LLMClient()
    metrics = Metrics()

    # ① 生成
    metrics.start_timer("generation")
    steps = Generator(client, base_url).generate(instruction)
    metrics.stop_timer("generation")

    heal_triggered = heal_success = 0
    step_results: list[dict] = []
    analyses: list[dict] = []

    # ②③④ 执行 + 自愈 + 分析
    metrics.start_timer("execution")
    with Runner(base_url=base_url) as runner:
        healer = Healer(client, runner.page)
        analyzer = Analyzer(client)
        for i, step in enumerate(steps):
            heal_round = 0
            while True:
                status, detail = runner.run_step(step)

                if status == "passed":
                    step_results.append(
                        {
                            "index": i,
                            "action": step.action,
                            "status": "healed" if heal_round else "passed",
                            "selector": step.selector,
                        }
                    )
                    if heal_round:
                        heal_success += 1
                    break

                if status == "assert_failed":
                    metrics.start_timer("analysis")
                    analyses.append({"index": i, "analysis": analyzer.analyze(detail)})
                    metrics.stop_timer("analysis")
                    step_results.append({"index": i, "action": step.action, "status": "assert_failed"})
                    break

                # status == "locator_failed"
                if heal_round >= MAX_HEAL_ROUNDS_PER_RUN:
                    metrics.start_timer("analysis")
                    analyses.append({"index": i, "analysis": analyzer.analyze(detail)})
                    metrics.stop_timer("analysis")
                    step_results.append({"index": i, "action": step.action, "status": "locator_exhausted"})
                    break

                heal_triggered += 1
                metrics.start_timer("healing")
                new_selector = healer.heal(detail, i, run_id)
                metrics.stop_timer("healing")

                if new_selector is None:
                    metrics.start_timer("analysis")
                    analyses.append({"index": i, "analysis": analyzer.analyze(detail)})
                    metrics.stop_timer("analysis")
                    step_results.append({"index": i, "action": step.action, "status": "locator_failed"})
                    break

                step.selector = new_selector
                heal_round += 1
    metrics.stop_timer("execution")

    # ⑤ 报告
    report = metrics.report(client.calls, healer.records, heal_triggered, heal_success)
    report.update(
        {
            "run_id": run_id,
            "instruction": instruction,
            "steps_generated": len(steps),
            "step_results": step_results,
            "analyses": analyses,
        }
    )

    Path(output_dir).mkdir(exist_ok=True)
    out_path = Path(output_dir) / f"report-{run_id}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, out_path


def main() -> None:
    instruction = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTRUCTION
    report, out_path = run_pipeline(instruction)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告已保存：{out_path}")


if __name__ == "__main__":
    main()
