"""⑤ Metrics：横切度量，聚合 token 成本 + 自愈指标 + 执行耗时，产出量化报告。

所有指标都从真实运行中统计，禁止硬编码演示值：
- token 成本：从 LLMClient.calls 的 usage 字段真实汇总，按模型分列，乘以官方定价。
- heal rate：操作恢复步骤数 / 曾尝试修复步骤数；误修无标注时为未知。
- 执行耗时：各阶段 start_timer / stop_timer 真实计时。
"""

import time



class Metrics:
    def __init__(self, pricing_per_1k: dict | None = None):
        self.pricing_per_1k = pricing_per_1k or {}
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
    def compute_cost(self, calls: list, attempts: list | None = None) -> tuple[list[dict], float | None]:
        by_model: dict[str, dict] = {}
        for c in calls:
            m = by_model.setdefault(c.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            for key in ("input_tokens", "output_tokens"):
                value = getattr(c, key)
                m[key] = None if value is None or m[key] is None else m[key] + value
        # Each failed HTTP request may still have consumed billable tokens. Never
        # infer zero usage from a missing successful response, even after a retry.
        unaccounted: dict[str, int] = {}
        if attempts is not None:
            successful_usage = {}
            for c in calls:
                key = (c.model, c.stage)
                successful_usage[key] = successful_usage.get(key, 0) + 1
            for attempt in attempts:
                key = (attempt.model, attempt.stage)
                if attempt.error_type is None and successful_usage.get(key, 0):
                    successful_usage[key] -= 1
                else:
                    unaccounted[attempt.model] = unaccounted.get(attempt.model, 0) + 1
                    m = by_model.setdefault(attempt.model, {
                        "calls": 0, "input_tokens": 0, "output_tokens": 0})
                    m["input_tokens"] = m["output_tokens"] = None
        rows = []
        total = 0.0
        for model, m in by_model.items():
            reasons = []
            if unaccounted.get(model):
                reasons.append("attempt_usage_unknown")
            price = self.pricing_per_1k.get(model)
            if not isinstance(price, dict) or any(
                type(price.get(k)) not in (int, float) or price[k] < 0
                for k in ("input", "output")
            ):
                reasons.append("pricing_unknown")
            if m["input_tokens"] is None or m["output_tokens"] is None:
                reasons.append("usage_unknown")
            cost = None if reasons else round(
                (m["input_tokens"] * price["input"] + m["output_tokens"] * price["output"]) / 1000, 6)
            total = None if total is None or cost is None else total + cost
            rows.append({"model": model, **m, "cost_yuan": cost, "unknown_reasons": reasons})
        return rows, round(total, 6) if total is not None else None

    # ---- 自愈指标 ----
    def compute_heal(self, heal_records: list, heal_triggered: int, heal_success: int) -> dict:
        rejected = sum(1 for r in heal_records if r.guardrail_hit)
        steps_attempted = len({getattr(r, "step_index", i) for i, r in enumerate(heal_records)})
        heal_rate = round(heal_success / steps_attempted, 4) if steps_attempted else None
        return {
            "heal_triggered": heal_triggered,
            "heal_success": heal_success,
            "heal_rate": heal_rate,
            "healing_attempts": heal_triggered,
            "steps_attempted": steps_attempted,
            "guardrail_rejections": rejected,
            "false_heal_count": None,
            "false_heal_evaluation": "not_evaluated",
        }

    def report(self, calls: list, heal_records: list, heal_triggered: int, heal_success: int,
               model_attempts: list | None = None) -> dict:
        cost_rows, total_cost = self.compute_cost(calls, attempts=model_attempts)
        heal = self.compute_heal(heal_records, heal_triggered, heal_success)
        return {
            "token_cost": {"by_model": cost_rows, "total_yuan": total_cost},
            "healing": heal,
            "latency_seconds": self.stages,
        }
