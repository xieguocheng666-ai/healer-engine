from html.parser import HTMLParser
from pathlib import Path

import pytest


def render(report, tmp_path):
    from reporting.html import render_report
    output = tmp_path / "nested" / "report.html"
    assert render_report(report, output) == output
    return output.read_text(encoding="utf-8")


def test_failed_report_without_steps_and_unknown_cost(tmp_path):
    html = render({"run_status": "failed", "error": {"type": "GenerationError", "message": "PRIVATE_ERROR"}}, tmp_path)
    assert "失败" in html and "PRIVATE_ERROR" not in html
    assert "未知" in html and "暂无步骤记录" in html
    assert "<script" not in html and "原始填写值" in html


def test_report_shows_results_and_escapes_text_without_filling_values(tmp_path):
    html = render({
        "run_status": "passed", "business_validation": "passed", "run_id": "<img src=x onerror=alert(1)>",
        "instruction": "密码 SECRET_PASSWORD", "plan": [{"value": "SECRET_PLAN"}],
        "step_results": [{"index": 0, "action": "fill", "status": "healed", "selector": "#user<script>", "value": "SECRET_VALUE"}],
        "healing": {"heal_success": 1, "guardrail_rejections": 2},
        "token_cost": {"by_model": [{"model": "model", "calls": 1, "input_tokens": 10, "output_tokens": 20, "cost_yuan": None}], "total_yuan": None},
        "analyses": [{"analysis": "<script>alert(1)</script>"}],
    }, tmp_path)
    assert "&lt;img" in html and "#user" not in html
    assert "<script>" not in html
    assert "SECRET_" not in html
    assert "修复后通过" in html and "拒绝" in html and "model" in html


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


@pytest.mark.parametrize("unsafe", ["javascript:alert(1)", "https://example.org/x", "//evil/x", "//[", "/tmp/x", "../secret", "C:\\secret", "%2e%2e/secret", "data:text/html,x"])
def test_only_safe_relative_evidence_links(tmp_path, unsafe):
    html = render({"artifacts": {"trace": "trace.zip", "audit": unsafe}}, tmp_path)
    parser = Links()
    parser.feed(html)
    assert parser.links == ["trace.zip"]


def test_empty_report_is_compatible(tmp_path):
    assert "未验证" in render({}, tmp_path)


def test_error_and_selector_never_reveal_free_text(tmp_path):
    html = render({"error": {"type": "PRIVATE_TYPE", "message": "PRIVATE_ERROR"},
                   "step_results": [{"selector": '[value="PRIVATE_VALUE"]'}, {"selector": 'text=PRIVATE_TEXT'}]}, tmp_path)
    assert "PRIVATE_" not in html
    assert "已记录（隐藏内容）" in html


def test_heal_records_show_safe_reason_and_application_status(tmp_path):
    html = render({"heal_records": [
        {"step_index": 1, "reason": "critical_flow", "layer": "L1", "guardrail_hit": True, "auto_applied": False,
         "target": "PRIVATE_TARGET", "original_selector": "PRIVATE_OLD", "new_selector": "PRIVATE_NEW"},
        {"step_index": 2, "reason": "PRIVATE_REASON", "layer": "PRIVATE_LAYER", "auto_applied": True},
    ]}, tmp_path)
    assert "关键流程禁止自动修复" in html
    assert "L1：本地规则" in html and "已应用" in html and "已拒绝" in html
    assert "PRIVATE_" not in html


def test_no_model_calls_has_explicit_message(tmp_path):
    html = render({"token_cost": {"total_yuan": 0, "by_model": []}, "model_attempts": []}, tmp_path)
    assert "本次未调用模型" in html
    assert "暂无模型用量记录，成本未知" not in html


def test_failed_model_attempt_without_usage_keeps_cost_unknown(tmp_path):
    html = render({"token_cost": {"total_yuan": 0}, "model_attempts": [{"status": "failed"}]}, tmp_path)
    assert "暂无模型用量记录，成本未知" in html
    assert "本次未调用模型" not in html


def test_proposed_plan_link_has_correct_meaning(tmp_path):
    html = render({"artifacts": {"proposed_plan": "proposed-plan.json"}}, tmp_path)
    assert "修复后计划建议" in html
    assert "模型原始提案" not in html


def test_model_calls_distinguish_attempts_from_success(tmp_path):
    html = render({"model_attempts": [{}, {}]}, tmp_path)
    assert "成功返回次数" in html
    assert "HTTP 实际尝试 2 次（含失败/重试）" in html
    assert "HTTP 实际尝试 未知 次" in render({}, tmp_path)
