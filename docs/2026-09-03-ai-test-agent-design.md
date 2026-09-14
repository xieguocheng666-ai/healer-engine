# AI 测试智能体 · 设计规格

> 日期：2026-09-03 ｜ 状态：待审阅 ｜ 项目定位：秋招第二项目（加分项），正经真实项目、非玩具
> 前置已定（brainstorming 结论）：选型 A（Agent+Playwright 自愈）／三 Agent 全做／被测对象复用电商前端／文本自愈+视觉仅用于失败分析／用例生成产结构化步骤 JSON／自研实现并吸收业界分层自愈·护栏·度量

---

## 1. 项目定位

一个「AI 测试智能体」：给一句自然语言，自动生成 UI 测试、执行、测试失败时自动自愈（重新定位选择器）、并分析失败原因（产品 bug vs 选择器问题）、最终输出量化报告。

**核心深度三条（面试可脱稿讲）：**
1. **自愈 Healer**：UI 测试最痛的 flaky——前端一改 class 选择器就挂。自愈 = 失败 → 分层重定位 → 自动重试。
2. **失败智能分析**：区分「产品真 bug」vs「选择器写脆了」，把测试工程师的日常判断自动化。
3. **量化**：heal rate、false-heal count、token 成本、执行耗时——真跑出数据，不写死。

**非玩具的硬标准：** LLM 输出必须过 schema 校验；自愈必须过护栏；量化指标必须从真实运行中统计，禁止硬编码演示值。

## 2. 目标与成功标准

| 目标 | 验收标准 |
|------|---------|
| 用例生成真可用 | 一句自然语言 → 合法 `steps.json` → Runner 能执行 |
| 自愈真有效 | 人为制造选择器失效 → 自愈恢复 → 测试重跑通过；heal rate 有真实数值 |
| 失败分析真判断 | 对「选择器失效」和「产品 bug」两类失败，能给出正确结论 |
| 量化真统计 | 每次运行产出 heal rate / false-heal count / token 成本 / 耗时四项指标 |
| 深度可讲 | 每个模块「吃什么、吐什么、为什么这样设计」能脱稿讲清 |

## 3. 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 语言 | Python 3.11 | 与电商项目一致 |
| 执行引擎 | Playwright（同步 API） | 复用既有底子 |
| LLM 调用 | requests + 智谱开放平台 | `glm-4-flash`（文本，生成/自愈/分析）、`glm-4v-flash`（视觉，仅失败分析） |
| Schema 校验 | pydantic | 校验 LLM 输出的 steps JSON（电商项目已用过，用户熟悉） |
| 被测系统 | 电商 SUT 前端（`D:\my-test-framework\server\static\` 5 页） | 两项目串讲 |
| 配置 | .env | API key 不进 git |

LLM 端点：`https://open.bigmodel.cn/api/paas/v4/chat/completions`，`Authorization: Bearer <key>`。

## 4. 整体架构（五个模块一条链）

```
 "测一下登录功能"（自然语言）
       │
       ▼
┌───────────────────────────┐
│ ① Generator 用例生成        │  glm-4-flash
│   自然语言 → steps.json      │
└─────────────┬─────────────┘
              │ steps.json（pydantic 校验通过）
              ▼
┌───────────────────────────┐
│ ② Runner 执行引擎           │  Playwright ──→ 被测系统（电商 5 页）
│   逐条执行，捕获失败详情      │
└─────────────┬─────────────┘
              │
       ┌──────┴───────┐
     成功│             │ 失败（定位失败）
       ▼             ▼
  记录结果    ┌────────────────────────────┐
              │ ③ Healer 自愈（分层 + 护栏）  │
              │   L1 免费预检（$0）           │
              │   L2 LLM 重定位（glm-4-flash）│
              │   护栏：只修定位器不修断言      │
              └─────────────┬──────────────┘
                            │
                 ┌──────────┴──────────┐
              修好了│                    │ 修不好
                 ▼                     ▼
           重跑（重试）      ┌──────────────────────────┐
                            │ ④ Analyzer 失败分析         │
                            │   文本 LLM + 视觉截图        │
                            │   判断：产品 bug / 选择器问题 │
                            └──────────┬───────────────┘
                                       ▼
┌────────────────────────────────────────────────────┐
│ ⑤ Metrics 度量（横切，全程记录）                       │
│   heal rate / false-heal count / token 成本 / 耗时    │
└────────────────────────────────────────────────────┘
```

## 5. 各模块设计

### 5.1 ① Generator（用例生成）

**职责：** 自然语言 → 合法 `steps.json`。

**Step Schema（pydantic 定义，action 集合封闭）：**

```python
class Step(BaseModel):
    action: Literal["goto", "fill", "click", "assert", "wait"]
    url: str | None = None          # goto 用
    selector: str | None = None     # fill/click 用
    value: str | None = None        # fill 输入值 / assert 期望值
    assert_type: str | None = None  # assert 用：url_contains / text_visible
```

**action 语义：**

| action | 含义 | 参数 |
|--------|------|------|
| `goto` | 打开页面 | `url` |
| `fill` | 输入文本 | `selector` + `value` |
| `click` | 点击 | `selector` |
| `assert` | 断言 | `assert_type` + `value` |
| `wait` | 等待元素出现 | `selector` |

**三步「正经」机制：**
1. **Prompt 固化 schema**：给 LLM 的 system prompt 附上完整 action 集 + 一个示例 steps，要求只输出 JSON。
2. **解析 + pydantic 校验**：`json.loads` → `Step` 列表解析 → 校验失败即打回重试（最多 3 次），仍失败则报错退出，不执行非法步骤。
3. **定位器优先 `data-*` 语义属性**：prompt 里明确告知电商前端用 `data-action` / `data-store-id` / `data-product-id` 等稳定属性，降低后续自愈触发率。

### 5.2 ② Runner（执行引擎）

**职责：** 逐条执行 `steps.json`，捕获失败详情。

**执行规则：**
- 每条步骤用 Playwright 同步 API 执行（`page.goto` / `locator.fill` / `locator.click` / `expect`）。
- 定位失败（`TimeoutError` / `strict mode violation`）→ 抛给 Healer，附**失败详情**。
- 断言失败（`AssertionError`）→ **不进自愈**，直接记失败，转 Analyzer。

**失败详情结构（自愈和分析的输入）：**
```python
@dataclass
class FailureDetail:
    step: Step               # 失败的那一步
    error: str               # Playwright 异常信息
    page_url: str            # 当前页面 URL
    element_html: str | None # 失败元素的外层 HTML 片段（尽量抓取）
    screenshot: bytes | None # 页面截图（供 Analyzer 视觉分析）
```

### 5.3 ③ Healer（自愈，核心深度）

**职责：** 对定位失败，分层重定位 + 护栏约束 + 重试。

**分层策略（业界主流，成本优先）：**

| 层 | 策略 | 成本 | 说明 |
|----|------|------|------|
| L1 免费预检 | 用 `id` / `data-testid` / `aria-label` / `role+name` / SUT 语义属性（`data-action` 等）重定位 | $0 | 业界参考约 30% 命中，本项目 M3 后以实测为准 |
| L2 LLM 重定位 | 把失败元素 HTML + 上下文喂 `glm-4-flash`，产出新 CSS 选择器 | 有 token | 预检不中才走 |

**LLM 重定位的输入：** 失败步骤的 `selector` + `element_html` + `page_url` + `error` + 步骤意图；输出：一个新 CSS 选择器。

**护栏（guardrails，防误修——面试重点）：**
1. **只修定位器，绝不修断言**：`assert` 失败不进入 Healer。
2. **验证通过才应用**：新选择器必须在真实 DOM 上满足 `locator.count() == 1` 且可见，否则丢弃。
3. **关键流程不自动修**：`page_url` 命中 `login` / `payment` / `checkout` 等关键词时，自愈结果记入审计日志、不自动应用。
4. **失败上限**：同一选择器自愈失败超过 3 次，停止自愈，转 Analyzer。
5. **重试上限**：单个用例自愈重跑最多 3 轮，防止死循环。

**重试：** 自愈成功 → 更新该 step 的 selector → Runner 重跑该步骤（非整条链路重跑）。

**审计日志（`heals.jsonl`）：** 每次自愈——无论成功、失败、还是触及护栏——都追加一行 JSON：`{run_id, step_index, 原selector, 新selector, 层(L1/L2), 是否通过验证, 是否触及护栏, 是否自动应用, 时间戳}`。这是 false-heal count 与 heal rate 的唯一数据源，禁止硬编码。

### 5.4 ④ Analyzer（失败分析）

**职责：** 对自愈救不回来的失败，判断性质。

**输入：** `FailureDetail`（含截图）。
**输出：** 结论 `产品 bug | 选择器问题 | 环境/数据问题` + 证据 + 建议。

**实现：**
- 文本 LLM（`glm-4-flash`）：读 error + element_html + 页面上下文，判断「元素确实存在但选择器失效」vs「元素根本不存在（功能/数据缺失）」。
- 视觉模型（`glm-4v-flash`）：读截图，判断「页面是否正常渲染、目标元素是否可见/缺失」。
- 两者结论一致 → 高置信度；不一致 → 标记为「需人工复核」。

### 5.5 ⑤ Metrics（度量，横切）

**职责：** 全程记账，产出量化报告。

| 指标 | 定义 | 目标 |
|------|------|------|
| heal rate | 自愈成功次数 ÷ 自愈触发总次数 | 真实数值 |
| false-heal count | 自愈触碰断言 / 关键流程 / 验证未过的次数（护栏违规计数） | 0 |
| token 成本 | 各 LLM 调用的 input+output token 汇总（按模型分列） | 真实数值 |
| 执行耗时 | 生成 / 执行 / 自愈 / 分析各阶段计时 | 真实数值 |

**实现：** 每次 LLM 调用记录 `{model, input_tokens, output_tokens, cost, stage}`（落 `llm_calls.jsonl`）；每次自愈记录写入 `heals.jsonl`（字段见 5.3）；最终聚合为 JSON 报告。

## 6. 目录结构

```
ai-test-agent/
├── agent/
│   ├── generator.py     # ① 用例生成
│   ├── runner.py        # ② 执行引擎
│   ├── healer.py        # ③ 自愈（分层 + 护栏）
│   ├── analyzer.py      # ④ 失败分析
│   └── metrics.py       # ⑤ 度量
├── schemas/
│   └── step.py          # Step / FailureDetail 的 pydantic 模型
├── llm/
│   └── client.py        # 智谱 LLM 统一封装（文本/视觉、token 记账）
├── config/
│   └── .env             # ZHIPU_API_KEY（不进 git）
├── tests/               # 自愈/分析的验证测试（人为制造选择器失效）
├── main.py              # 入口：自然语言 → 完整流水线 → 报告
├── requirements.txt
└── README.md
```

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| LLM 返回非法 JSON | 重试最多 3 次，仍失败则抛出明确错误 |
| LLM 返回的 action 不在集合内 | pydantic 校验拒绝，重试 |
| 自愈新选择器验证不通过（count≠1） | 丢弃，计数 false-heal，继续下一层 |
| 自愈重试超过上限 | 停止自愈，转 Analyzer |
| 网络超时（LLM 调用） | 与电商 ApiClient 一致：重试 + 超时 |
| 断言失败 | 不进入自愈，直接转 Analyzer |

## 8. 测试策略（本项目自身的测试）

- **自愈有效性**：人为改电商前端的某个 `data-*` 属性（制造选择器失效），断言 Healer 能恢复并重跑通过。
- **护栏**：制造断言失败，断言「不进自愈」；制造登录页定位失败，断言「不自动修」。
- **量化**：跑一轮完整流水线，断言报告里四项指标字段齐全且为真实统计值。
- **校验**：喂非法 JSON 给 Generator，断言「拒绝执行」。

## 9. MVP 范围与里程碑

| 阶段 | 内容 | 交付 |
|------|------|------|
| M1 | 骨架 + LLM 客户端 + Step schema | 能调通智谱、能校验 JSON |
| M2 | Generator + Runner | 自然语言 → 执行真测试 |
| M3 | Healer（分层 + 护栏 + 重试） | 选择器失效能自愈 |
| M4 | Analyzer（文本 + 视觉） | 能区分产品 bug vs 选择器问题 |
| M5 | Metrics + 报告 | 四项量化指标真实跑出 |
| M6 | 自身测试 + README + 串讲 | 每个模块可脱稿讲清 |

## 10. 关键风险与对策

| 风险 | 对策 |
|------|------|
| 免费模型输出不稳定 | schema 校验 + 重试 + 护栏兜底；已验证文本模型生成定位器可靠 |
| 自愈掩盖真实 bug | 护栏（只修定位器不修断言 + 关键流程不自动修 + 验证通过才应用） |
| 量化数据是"演的" | 全部从真实运行统计，禁止硬编码；Metrics 与流水线强绑定 |

## 11. 待实现前确认的开放项

- 无（架构、技术栈、范围、实现方案均已在 brainstorming 阶段拍板）。
