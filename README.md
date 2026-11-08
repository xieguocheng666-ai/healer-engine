# Healer Engine

**AI 增强的 UI 自动化与安全自愈工具。** 用页面证据辅助模型生成测试计划，以独立目标约束检查执行和修复，最后用原业务断言验证结果；每次运行留存报告、审计和浏览器回放。

定位器是寻找页面元素的表达式；自愈是定位失效后尝试换一种寻找方式。按钮点得动不代表点对了，因此本项目将“目标身份正确”“操作恢复”“业务断言通过”分开记录。

## 三分钟运行

Python 3.10+，在仓库根目录：

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium
healer doctor
healer demo
```

演示自动启动临时本地商店，不需要模型密钥、数据库或其他项目。旧按钮编号失效后，按店铺、商品和文字恢复定位，并验证购物车里的商品与数量。终端返回报告路径；打开同目录的 `report.html` 查看结果。

这是合成示例，真实 ShopHub 前后端验证见下方命令。安装包包含示例页面和计划，安装后可从其他目录运行。

## 使用入口

| 命令 | 用途 |
|---|---|
| `healer demo` | 一次完成本地示例、修复和业务验证 |
| `healer run --plan plan.json` | 执行已保存计划，默认纯规则，无需模型 |
| `healer generate "任务" --url 页面地址 --plan-output plan.json` | 读取当前页面快照，调用模型生成计划，不执行计划 |
| `healer run --plan plan.json --mode hybrid --analyze` | 允许模型候选与失败分析，共享有界请求预算 |
| `healer benchmark --with-model --repeats 3` | 固定场景三模式对照，不假定模型一定有收益 |
| `healer shophub --source D:/my-test-framework` | 独立临时数据库中的真实 ShopHub 验证 |

不安装命令入口时可使用 `python -m healer_cli`，例如 `python -m healer_cli demo`。完整参数见 `healer --help` 和各子命令的 `--help`。

运行返回码：业务验证通过为 0；执行失败、计划非法、模型不可用或最终业务未验证为非零，便于自动检查识别失败。

## 测试计划

计划是 JSON 步骤数组；也接受 `{"schema_version":1,"steps":[...]}`。示例：

```json
[
  {"action":"goto","url":"/products.html"},
  {
    "action":"click",
    "selector":"#old-add-button",
    "target":{
      "tag":"button",
      "text":"加入购物车",
      "attributes":{"data-store-id":"A","data-product-id":"2"}
    }
  },
  {"action":"assert","selector":"#cart","assert_type":"text_equals","value":"商品 2 × 1"}
]
```

`selector` 表示如何找；`target` 表示本来要找谁。标签、精确文字和全部属性必须同时满足。即使原定位器能匹配元素，也不能绕过目标检查。缺少目标约束的旧步骤仍可正常执行，但失败时不自动修复。约束属性目前要求位于目标元素自身，不支持跨页面推理或祖先容器身份。

支持打开页面、填写、点击、等待和断言。断言包括 URL 包含、精确文字可见、元素完整文字、数量、输入值和属性相等；精确断言用 `text_equals/count/value_equals/attribute_equals`，属性断言还需 `attribute`。涉及删除、支付、下单等步骤可显式设置 `sensitive:true`，这些步骤禁止自动修复，但正确的原步骤仍按计划执行。

**修复永不改原断言，也不覆盖输入计划。** 执行的是计划副本，修复后计划只另存为建议。

## 安全闭环与模型位置

```text
页面快照 → 模型生成计划 → 结构与页面证据校验 → 保存计划
                                                      ↓
业务断言 ← 操作重跑 ← 统一候选核验 ← 规则候选 / 可选模型候选
   ↓
结果 JSON + HTML + 修复审计 + 原计划 / 建议计划 + 浏览器回放
```

- 规则支持稳定属性、精确文字和嵌套文字，不为了调用模型故意遗漏可确定的定位方式。
- 歧义、目标缺失、不可操作、敏感步骤及登录/支付/结算页面拒绝自动修复。
- 所有候选都必须匹配同一个独立目标；失败后停止当前用例，剩余步骤标记未执行。
- 单用例最多 3 次自愈尝试。模型客户端默认单次超时 15 秒、最多 20 次 HTTP 尝试，瞬态错误最多重试 1 次；鉴权或响应结构错误不重试。
- 生成器读取的是当前页的有界快照，不是全站自主探索。页面内容按数据处理；目标属性未出现在有效快照中则反馈错误重试，空或损坏快照不会绕过校验。生成计划仍需核对业务意图。
- 模型分析只是辅助意见，不会把失败用例改成通过；文字和视觉结论一致也不代表经统计校准的准确率。

## 模型配置

开发 checkout 中复制 `config/.env.example` 为 `config/.env` 并配置；非源码安装可直接设置同名环境变量。变量包括 `ZHIPU_API_KEY`、`ZHIPU_BASE_URL`、`TEXT_MODEL`、`VISION_MODEL`、`SUT_BASE_URL`。接口采用兼容的对话补全格式，目前实际验证的是智谱接口，不声称所有供应商均兼容。

模型请求次数与成功返回次数分开；缺少 token 用量、存在无法核算的失败尝试或没有可信价格时，费用为 `null`（未知），不会当免费。`Metrics(pricing_per_1k=...)` 可显式注入价格，实际账单仍以供应商为准。

## 证据、隐私与结果口径

每次执行保存到 `reports/<run_id>/`：

| 文件 | 内容 |
|---|---|
| `report.json` / `report.html` | 执行结果、业务验证、拒绝原因、用量与成本状态 |
| `plan.json` | 输入计划副本 |
| `proposed-plan.json` | 修复后建议计划，不自动替换原文件 |
| `heals.jsonl` | 每次修复的目标、候选、层级及拒绝原因 |
| `trace.zip` | 浏览器过程回放，用 `python -m playwright show-trace 路径` 打开 |

HTML 隐藏填写值、完整指令、自由错误正文和定位器内容；JSON 摘要对本次填写值脱敏。**原计划、建议计划、快照、审计、截图与 trace 仍属于本地私有证据，可能包含表单或会话信息，不能未经检查直接公开。** 生成命令另存 `.snapshot.json` 和 `.generation.json` 作为页面依据与真实模型用量记录。

`heal_rate` 是操作恢复步骤数 / 尝试自愈步骤数；`business_validation` 只有全流程成功且最后一次导航/点击/填写后有断言才通过。断言质量由用例设计者负责。普通运行缺少人工正确目标标注，`false_heal_count` 保持未知；固定评估集才可对照已知目标统计误选。`execution` 是包含自愈及分析的总阶段耗时，不能与其子阶段相加。

## 验证与工程交付

```powershell
python -m pytest -q
healer benchmark
healer benchmark --with-model --repeats 3 --max-model-calls 12
healer shophub --source D:/my-test-framework
```

自动检查通过 GitHub Actions 安装项目和 Chromium、执行离线测试与示例，不使用真实模型或原数据库。ShopHub 命令另需该源码及已安装依赖的 `.venv`、运行中的 MySQL 和可建库权限；只新建随机专用数据库，结束后核验删除，不调用原库 reset。

最新结果与复现边界见 [v1 交付验收](docs/v1-delivery.md)；此前规则不完整时的 [历史实验](docs/evaluation-2026-11-08.md)保留，不与新版数字混用。

## 代码入口

`healer_cli.py` 提供命令入口；`main.py` 编排计划执行；`schemas/` 定义步骤与目标；`agent/` 实现生成、执行、自愈及分析；`llm/` 管理有界模型调用；`reporting/` 渲染摘要；`evaluation/` 做对照和真实业务验证。架构与取舍见 [设计说明](docs/architecture.md)，项目进度只记 [进度文档](docs/进度.md)。
