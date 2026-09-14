# AI 测试智能体（Agent + Playwright 自愈）

一个真正能跑起来的 AI UI 测试智能体：输入一句自然语言，自动生成测试步骤 → Playwright 执行 → 定位失败时分层自愈 → 自愈救不回时判定失败性质 → 最终产出量化报告（token 成本 / 自愈率 / 误修数 / 各阶段耗时）。

不是「LLM 生成对了就完事」的玩具，核心深度在**自愈闭环**：生成的选择器错了 / 前端改了 → Healer 重新定位 → 测试自愈通过，并用 `heals.jsonl` 审计日志量化「自愈率」和「误修数」。

## 架构

```
自然语言指令
     │
     ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ ① Generator │──▶│ ② Runner    │──▶│ ③ Healer    │──▶│ ④ Analyzer  │──▶│ ⑤ Metrics   │
│  生成步骤    │   │  执行步骤    │   │  分层自愈    │   │  失败分析    │   │  量化报告    │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
     │                 │                 │                 │                 │
  steps.json       locator_failed    heals.jsonl       分类+证据          cost/heal/
                    assert_failed     审计日志         (产品bug/选择器     latency
                                                       /环境数据)
```

| 模块 | 职责 | 关键设计 |
|------|------|---------|
| ① Generator | 自然语言 → `Step[]` | pydantic 校验 + 非法步骤打回重试（≤3 次）；定位器规则固化进 prompt |
| ② Runner | Playwright 执行 | 失败分流：定位失败→Healer，断言失败→Analyzer；断言用 `expect` 自动等待跳转 |
| ③ Healer | 分层自愈 | L1 免费预检 → L2 LLM 重定位；五条护栏防误修 |
| ④ Analyzer | 判定失败性质 | 文本 LLM + 视觉模型双通道，结论一致才高置信 |
| ⑤ Metrics | 量化 | token 成本 / heal rate / false-heal / 各阶段耗时，全部真实统计 |

## 自愈机制（面试核心）

**分层策略**：先用免费手段，救不回再花钱上 LLM。

- **L1 免费预检**：从失败元素的 HTML 里提取稳定属性候选（`id` / `data-testid` / `aria-label` / `data-action` / `name` / `placeholder`），逐个验证。针对平台电商「同一商品多店在售、单属性不唯一」的定位难题，自动组合 `[data-store-id][data-product-id]` 复合选择器。
- **L2 LLM 重定位**：把失败信息 + 步骤意图 + 页面 HTML 喂给 `glm-4-flash`，让它输出一个新 CSS 选择器。

**五条护栏（防误修）**：

1. **只修定位器，绝不修断言** —— assert 失败由 Runner 分流，不进入 Healer。
2. **验证通过才应用** —— 新选择器必须在真实 DOM 满足 `count == 1` 且可见，否则丢弃。
3. **关键流程不自动修** —— page_url 命中 `login` / `payment` / `checkout` 时，只记审计、不自动应用。
4. **同一选择器失败超上限停止** —— 超过 3 次不再尝试。
5. **重试上限由编排层控制** —— 单用例自愈重跑最多 3 轮。

每次自愈（成功/失败/触栏）都追加一行到 `heals.jsonl`，是 **false-heal count** 与 **heal rate** 的唯一数据源。

## 失败分析（双通道）

- **文本 LLM**（`glm-4-flash`）：读 error + element_html + 页面上下文，判断「元素确实存在但选择器失效」vs「元素根本不存在（功能/数据缺失）」。
- **视觉模型**（`glm-4v-flash`）：读截图，判断页面是否正常渲染、目标元素是否可见。

两者结论一致 → 高置信度；不一致或缺截图 → 标记「需人工复核」。

分类结果：`产品 bug` / `选择器问题` / `环境/数据问题`，附一句证据 + 一句修复建议。

## 项目结构

```
ai-test-agent/
├── agent/
│   ├── generator.py      # ① 自然语言 → 步骤
│   ├── runner.py         # ② Playwright 执行 + 失败分流
│   ├── healer.py         # ③ 分层自愈 + 护栏 + 审计
│   ├── analyzer.py       # ④ 失败分析（文本+视觉）
│   └── metrics.py        # ⑤ 量化报告
├── llm/
│   └── client.py         # 智谱统一封装（文本/视觉 + token 记账）
├── schemas/
│   └── step.py           # Step / FailureDetail 数据模型
├── config/
│   ├── settings.py       # 集中配置
│   ├── .env              # API key（不入库）
│   └── .env.example
├── main.py               # 编排入口：生成→执行→自愈→分析→报告
├── demo_healing.py       # 自愈有效性演示
├── demo_analyzer.py      # 失败分析演示
├── tests/                # 32 个单元测试
├── reports/              # 每次运行的报告 JSON
└── heals.jsonl           # 自愈审计日志
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. 配置

复制 `config/.env.example` 为 `config/.env`，填入智谱 API Key：

```bash
cp config/.env.example config/.env
# 编辑 .env，填入 ZHIPU_API_KEY
```

### 3. 启动被测系统（SUT）

被测对象是电商平台前端（登录/商品列表/购物车/订单/卖家后台），`SUT_BASE_URL` 默认 `http://127.0.0.1:8000`。

```bash
cd <被测系统目录> && python -m http.server 8000  # 或 FastAPI serve
```

### 4. 运行完整流水线

```bash
python main.py "打开登录页 http://127.0.0.1:8000/login.html，输入账号 alice 密码 alice123，点击登录，断言进入首页"
```

不带参数时用内置默认指令。运行结果打印到终端，并保存到 `reports/report-<run_id>.json`。

### 5. 跑测试

```bash
pytest
```

## 报告示例

```json
{
  "token_cost": {
    "by_model": [
      {"model": "glm-4-flash", "calls": 5, "input_tokens": 1234, "output_tokens": 456, "cost_yuan": 0.0034}
    ],
    "total_yuan": 0.0034
  },
  "healing": {
    "heal_triggered": 1,
    "heal_success": 1,
    "heal_rate": 1.0,
    "false_heal_count": 0
  },
  "latency_seconds": {
    "generation": 1.23,
    "execution": 2.45,
    "healing": 0.31,
    "analysis": 1.02
  },
  "steps_generated": 5,
  "step_results": [
    {"index": 0, "action": "goto", "status": "passed"},
    {"index": 1, "action": "fill", "status": "passed"},
    {"index": 2, "action": "click", "status": "healed"}
  ]
}
```

## 技术栈

- **Python 3.10+**，Playwright（同步 API），pydantic v2
- **LLM**：智谱 `glm-4-flash`（文本，0.002 元/千 tokens）+ `glm-4v-flash`（视觉，免费）
- **测试**：pytest（32 个单元测试，覆盖 schema / LLM 封装 / generator / healer / analyzer / metrics）
