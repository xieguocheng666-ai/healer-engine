"""离线 HTML 摘要；不输出指令、计划或原始填写值。"""

from html import escape
from pathlib import Path
from urllib.parse import unquote, urlsplit


_LABELS = {
    "passed": "通过", "failed": "失败", "unverified": "未验证",
    "healed": "修复后通过", "blocked": "已阻断", "assert_failed": "断言失败",
    "execution_failed": "执行失败", "locator_failed": "定位失败",
    "locator_exhausted": "定位修复次数耗尽", "not_applicable": "不适用",
    "goto": "打开页面", "click": "点击", "fill": "填写", "assert": "断言",
}

_REASONS = {
    "unsupported_action": "动作不支持定位修复", "sensitive_step": "敏感步骤禁止自动修复",
    "critical_flow": "关键流程禁止自动修复", "missing_target": "缺少独立目标约束",
    "target_missing_ambiguous_or_unavailable": "目标缺失、不唯一或不可操作",
    "attempt_limit": "修复次数达到上限", "no_candidate": "未找到候选定位器",
    "candidate_rejected": "候选未通过目标验证", "candidate_verified": "候选通过目标验证",
}
_ERRORS = {
    "GenerationError": "执行计划生成失败", "ValidationError": "输入或执行计划验证失败",
    "RuntimeError": "运行期间发生错误", "TimeoutError": "操作等待超时",
    "AssertionError": "预期结果验证失败", "ValueError": "输入数据无效",
    "ConnectionError": "连接失败", "LLMError": "模型调用失败",
}


def _selector_presence(value):
    return "已记录（隐藏内容）" if value else "未记录"


def _step_index(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "未知"


def _text(value):
    return escape(str(value), quote=True) if value is not None else "未知"


def _label(value):
    return _text(_LABELS.get(str(value), value if value is not None else "未知"))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _safe_link(value):
    if not isinstance(value, str) or not value:
        return None
    decoded = unquote(value)
    try:
        parts = urlsplit(decoded)
    except ValueError:
        return None
    if (parts.scheme or parts.netloc or parts.query or parts.fragment
            or decoded.startswith(("/", "\\")) or "\\" in decoded
            or ":" in decoded or "%" in decoded
            or any(ord(c) < 32 for c in decoded)
            or any(part in ("..", ".") for part in decoded.split("/"))):
        return None
    return escape(value, quote=True)


def render_report(report: dict, output_path: str | Path) -> Path:
    """写入可双击打开的 UTF-8 静态报告，返回输出路径。缺失指标显示未知。"""
    report = _mapping(report)
    healing = _mapping(report.get("healing"))
    cost = _mapping(report.get("token_cost"))
    attempts = report.get("model_attempts")
    attempt_count = str(len(attempts)) if isinstance(attempts, list) else "未知"
    empty_usage = "本次未调用模型" if cost.get("total_yuan") == 0 and attempts == [] else "暂无模型用量记录，成本未知"
    steps = _rows(report.get("step_results"))
    status = report.get("run_status", report.get("business_validation", "unverified"))
    tone = {"passed": "good", "failed": "bad"}.get(status, "neutral")
    step_rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in (
            _step_index(step.get("index")), _label(step.get("action")),
            _label(step.get("status")), _selector_presence(step.get("selector")),
        )) + "</tr>" for step in steps
    ) or '<tr><td colspan="4">暂无步骤记录</td></tr>'
    model_rows = "".join(
        "<tr>" + "".join(f"<td>{_text(row.get(key))}</td>" for key in
        ("model", "calls", "input_tokens", "output_tokens", "cost_yuan")) + "</tr>"
        for row in _rows(cost.get("by_model"))
    ) or f'<tr><td colspan="5">{empty_usage}</td></tr>'
    evidence = []
    for key, title in (("trace", "浏览器轨迹"), ("audit", "修复审计记录"),
                       ("plan", "执行计划"), ("proposed_plan", "修复后计划建议")):
        value = _mapping(report.get("artifacts")).get(key)
        href = _safe_link(value)
        if href:
            evidence.append(f'<li><a href="{href}">{title}</a></li>')
    evidence_html = "<ul>" + "".join(evidence) + "</ul>" if evidence else "<p>暂无可用的本地证据链接。</p>"
    # Exception messages and locator expressions can contain submitted values.
    # Render only known exception types and locator presence, never free text.
    error_type = _mapping(report.get("error")).get("type")
    error = f'<p class="error">{_ERRORS.get(str(error_type), "运行发生错误，详情见本地证据。")}</p>' if report.get("error") else ""
    heal_rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in (
            _step_index(row.get("step_index")),
            {"L1": "L1：本地规则", "L2": "L2：模型建议"}.get(str(row.get("layer")), "未进入修复层"),
            _REASONS.get(str(row.get("reason")), "未分类原因（内容隐藏）"),
            "已拒绝" if row.get("guardrail_hit") is True else ("已应用" if row.get("auto_applied") is True else "未应用"),
            _selector_presence(row.get("original_selector")), _selector_presence(row.get("new_selector")),
        )) + "</tr>" for row in _rows(report.get("heal_records"))
    ) or '<tr><td colspan="6">暂无修复审计记录</td></tr>'
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>测试运行报告</title><style>
:root{{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#233343;background:#f3f5f4}}
body{{margin:0;padding:40px 24px}}main{{max-width:1040px;margin:auto}}header{{border-top:5px solid #236452;padding:24px 0}}
h1{{font-size:32px;margin:10px 0}}h2{{font-size:20px;margin-top:0}}p{{line-height:1.7}}.eyebrow{{letter-spacing:.14em;color:#526e64;font-size:12px}}
.subtitle,footer{{color:#607068;font-size:13px}}section{{background:white;border:1px solid #dce4df;border-radius:12px;padding:24px;margin:20px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.card{{background:#f5f8f6;padding:18px;border-radius:8px}}
.value{{font-size:22px;margin-top:8px}}.good{{color:#236452}}.bad,.error{{color:#a33737}}.neutral{{color:#896320}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;border-bottom:1px solid #e5ebe7;padding:12px 8px;overflow-wrap:anywhere}}
th{{color:#62756a;font-weight:500}}.scroll{{overflow-x:auto}}a{{color:#236452;text-underline-offset:4px}}li{{padding:6px 0}}
@media(max-width:600px){{body{{padding:20px 12px}}section{{padding:16px}}h1{{font-size:26px}}}}@media print{{body{{background:white;padding:0}}section{{break-inside:avoid}}}}
</style></head><body><main>
<header><div class="eyebrow">AI TEST AGENT / RUN REPORT</div><h1>测试运行报告</h1>
<p class="subtitle">运行编号：{_text(report.get("run_id"))} · 报告版本：{_text(report.get("schema_version"))}</p></header>
<section><h2>运行结论</h2><div class="cards">
<div class="card">运行状态<div class="value {tone}">{_label(status)}</div></div>
<div class="card">业务验证<div class="value">{_label(report.get("business_validation", "unverified"))}</div></div>
<div class="card">步骤记录<div class="value">{len(steps)}</div></div>
<div class="card">模型总成本（元）<div class="value">{_text(cost.get("total_yuan"))}</div></div></div>
{error}<p class="subtitle">业务验证表示最终断言是否确认预期结果；未验证表示证据不足。成本未知表示没有可靠计费数据。</p></section>
<section><h2>步骤执行</h2><div class="scroll"><table><thead><tr><th>步骤索引（从 0 开始）</th><th>动作</th><th>结果</th><th>定位器记录</th></tr></thead><tbody>{step_rows}</tbody></table></div>
<p class="subtitle">定位器用于寻找页面元素；为避免泄露页面填写值，本页仅展示是否已记录。</p></section>
<section><h2>定位修复与拒绝</h2><div class="cards">
<div class="card">修复尝试<div class="value">{_text(healing.get("healing_attempts", healing.get("heal_triggered")))}</div></div>
<div class="card">恢复操作步骤<div class="value">{_text(healing.get("heal_success"))}</div></div>
<div class="card">规则拒绝次数<div class="value">{_text(healing.get("guardrail_rejections"))}</div></div>
<div class="card">误修数量<div class="value">{_text(healing.get("false_heal_count"))}</div></div></div>
<p class="subtitle">定位修复：更换查找元素的方式后重试。拒绝：候选修复未通过保护规则。操作恢复不代表业务验证通过；误修未标注时为未知。</p>
<div class="scroll"><table><thead><tr><th>步骤索引</th><th>修复层级</th><th>原因</th><th>应用状态</th><th>原定位器</th><th>新定位器</th></tr></thead><tbody>{heal_rows}</tbody></table></div></section>
<section><h2>模型用量与成本</h2><div class="scroll"><table><thead><tr><th>模型</th><th>成功返回次数</th><th>输入 token</th><th>输出 token</th><th>成本（元）</th></tr></thead><tbody>{model_rows}</tbody></table></div>
<p class="subtitle">HTTP 实际尝试 {attempt_count} 次（含失败/重试）。HTTP 是客户端向模型服务发送请求的协议。成功返回次数仅统计具有用量记录的返回。token 是模型处理文本的计量单位。未知成本不会按零元展示。</p></section>
<section><h2>本地证据</h2>{evidence_html}<p class="subtitle">链接相对于本报告所在目录；浏览器轨迹（trace.zip）需使用 Playwright 轨迹查看器打开。审计记录为逐行 JSON 文件。</p></section>
<footer>隐私说明：HTML 不展示原始填写值、密码、完整指令或执行计划正文。证据文件可能包含页面与执行数据，请在分享前检查。</footer>
</main></body></html>'''
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output
