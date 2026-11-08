from agent.metrics import Metrics
from llm.client import LLMUsage


def test_compute_cost_groups_by_model():
    calls = [
        LLMUsage(model="glm-4-flash", input_tokens=1000, output_tokens=500, stage="generation"),
        LLMUsage(model="glm-4-flash", input_tokens=1000, output_tokens=500, stage="healing"),
    ]
    m = Metrics()
    rows, total = m.compute_cost(calls)
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 2000
    assert rows[0]["output_tokens"] == 1000
    # 0.002 元/千tokens：输入 2000→0.004，输出 1000→0.002，合计 0.006
    assert abs(total - 0.006) < 1e-9


def test_compute_cost_free_vision_model():
    calls = [LLMUsage(model="glm-4v-flash", input_tokens=999999, output_tokens=1, stage="analysis_vision")]
    m = Metrics()
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
