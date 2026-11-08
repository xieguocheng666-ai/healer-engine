from agent.metrics import Metrics
from llm.client import LLMUsage


def test_compute_cost_groups_by_model():
    calls = [
        LLMUsage(model="glm-4-flash", input_tokens=1000, output_tokens=500, stage="generation"),
        LLMUsage(model="glm-4-flash", input_tokens=1000, output_tokens=500, stage="healing"),
    ]
    m = Metrics(pricing_per_1k={"glm-4-flash": {"input": 0.002, "output": 0.002}, "glm-4v-flash": {"input": 0, "output": 0}})
    rows, total = m.compute_cost(calls)
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 2000
    assert rows[0]["output_tokens"] == 1000
    # 0.002 元/千tokens：输入 2000→0.004，输出 1000→0.002，合计 0.006
    assert abs(total - 0.006) < 1e-9


def test_compute_cost_free_vision_model():
    calls = [LLMUsage(model="glm-4v-flash", input_tokens=999999, output_tokens=1, stage="analysis_vision")]
    m = Metrics(pricing_per_1k={"glm-4-flash": {"input": 0.002, "output": 0.002}, "glm-4v-flash": {"input": 0, "output": 0}})
    rows, total = m.compute_cost(calls)
    assert abs(total - 0.0) < 1e-9


def test_compute_heal_rate_and_false_heal():
    class R:
        def __init__(self, guardrail_hit):
            self.guardrail_hit = guardrail_hit

    records = [R(False), R(False), R(True)]
    m = Metrics()
    heal = m.compute_heal(records, heal_triggered=3, heal_success=2)
    assert heal["heal_rate"] == round(2 / 3, 4)
    assert heal["false_heal_count"] is None
    assert heal["guardrail_rejections"] == 1


def test_compute_heal_zero_triggered():
    m = Metrics()
    heal = m.compute_heal([], heal_triggered=0, heal_success=0)
    assert heal["heal_rate"] is None
    assert heal["false_heal_count"] is None


def test_heal_rate_uses_steps_not_attempts():
    from types import SimpleNamespace
    records = [SimpleNamespace(step_index=0, guardrail_hit=False) for _ in range(3)]
    heal = Metrics().compute_heal(records, heal_triggered=3, heal_success=1)
    assert heal["heal_rate"] == 1.0
    assert heal["healing_attempts"] == 3
    assert heal["steps_attempted"] == 1


def test_unknown_price_and_usage_do_not_become_free():
    row, total = Metrics().compute_cost([LLMUsage("custom", 100, 10, "x")])
    assert total is None
    assert row[0]["cost_yuan"] is None
    assert "pricing_unknown" in row[0]["unknown_reasons"]
    rows, total = Metrics(pricing_per_1k={"custom": {"input": 1, "output": 2}}).compute_cost([
        LLMUsage("custom", 100, 10, "x"), LLMUsage("custom", None, 10, "x")])
    assert total is None
    assert rows[0]["input_tokens"] is None
    assert "usage_unknown" in rows[0]["unknown_reasons"]


def test_no_calls_cost_zero():
    assert Metrics().compute_cost([]) == ([], 0.0)


def test_failed_attempt_cost_is_unknown_even_without_success_calls():
    from llm.client import LLMAttempt
    attempts = [LLMAttempt("custom", "generation", 0.1, None, "timeout")]
    report = Metrics().report([], [], 0, 0, model_attempts=attempts)
    assert report["token_cost"]["total_yuan"] is None
    row = report["token_cost"]["by_model"][0]
    assert row["model"] == "custom"
    assert row["cost_yuan"] is None
    assert "attempt_usage_unknown" in row["unknown_reasons"]


def test_retry_success_does_not_hide_failed_attempt_cost():
    from llm.client import LLMAttempt
    calls = [LLMUsage("custom", 100, 20, "generation")]
    attempts = [LLMAttempt("custom", "generation", 0.1, 429, "http"),
                LLMAttempt("custom", "generation", 0.1, 200, None)]
    metrics = Metrics(pricing_per_1k={"custom": {"input": 1, "output": 1}})
    report = metrics.report(calls, [], 0, 0, model_attempts=attempts)
    assert report["token_cost"]["total_yuan"] is None
    assert report["token_cost"]["by_model"][0]["input_tokens"] is None


def test_success_attempts_preserve_known_cost():
    from llm.client import LLMAttempt
    metrics = Metrics(pricing_per_1k={"custom": {"input": 1, "output": 1}})
    report = metrics.report([LLMUsage("custom", 100, 20, "x")], [], 0, 0,
                            model_attempts=[LLMAttempt("custom", "x", 0.1, 200, None)])
    assert report["token_cost"]["total_yuan"] == 0.12
