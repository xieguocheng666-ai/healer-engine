"""⑤ Metrics：横切度量，聚合 token 成本 + 自愈指标 + 执行耗时，产出量化报告。

所有指标都从真实运行中统计，禁止硬编码演示值：
- token 成本：从 LLMClient.calls 的 usage 字段真实汇总，按模型分列，乘以官方定价。
- heal rate / false-heal count：heal rate 由编排层统计「重跑通过」计数；false-heal 从 heals.jsonl 审计日志聚合。
- 执行耗时：各阶段 start_timer / stop_timer 真实计时。
"""

import time

# 智谱官方定价（元/千 tokens）。来源：bigmodel.cn/pricing，2026-09 查证。
# glm-4-flash = 0.002 元/千 tokens；glm-4v-flash = 免费。第三方平台价格另计。
PRICING_PER_1K = {
    "glm-4-flash": {"input": 0.002, "output": 0.002},
    "glm-4v-flash": {"input": 0.0, "output": 0.0},
}


class Metrics:
    def __init__(self):
        self._timers: dict[str, float] = {}
        self.stages: dict[str, float] = {}

    # ---- 计时 ----
    def start_timer(self, stage: str) -> None:
        self._timers[stage] = time.perf_counter()

    def stop_timer(self, stage: str) -> None:
        if stage in self._timers:
            elapsed = time.perf_counter() - self._timers[stage]
            self.stages[stage] = round(self.stages.get(stage, 0.0) + elapsed, 4)

    # ---- token 成本 ----
    def compute_cost(self, calls: list) -> tuple[list[dict], float]:
        by_model: dict[str, dict] = {}
        for c in calls:
            m = by_model.setdefault(c.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += c.input_tokens
            m["output_tokens"] += c.output_tokens

        rows = []
        total = 0.0
        for model, m in by_model.items():
            price = PRICING_PER_1K.get(model, {"input": 0.0, "output": 0.0})
            input_cost = m["input_tokens"] / 1000 * price["input"]
            output_cost = m["output_tokens"] / 1000 * price["output"]
            cost = input_cost + output_cost
            total += cost
            rows.append(
                {
                    "model": model,
                    "calls": m["calls"],
                    "input_tokens": m["input_tokens"],
                    "output_tokens": m["output_tokens"],
                    "cost_yuan": round(cost, 6),
                }
            )
        return rows, round(total, 6)

    # ---- 自愈指标 ----
    def compute_heal(self, heal_records: list, heal_triggered: int, heal_success: int) -> dict:
        false_heal = sum(1 for r in heal_records if r.guardrail_hit)
        heal_rate = round(heal_success / heal_triggered, 4) if heal_triggered else None
        return {
            "heal_triggered": heal_triggered,
            "heal_success": heal_success,
            "heal_rate": heal_rate,
            "false_heal_count": false_heal,
        }

    def report(self, calls: list, heal_records: list, heal_triggered: int, heal_success: int) -> dict:
        cost_rows, total_cost = self.compute_cost(calls)
        heal = self.compute_heal(heal_records, heal_triggered, heal_success)
        return {
            "token_cost": {"by_model": cost_rows, "total_yuan": total_cost},
            "healing": heal,
            "latency_seconds": self.stages,
        }
