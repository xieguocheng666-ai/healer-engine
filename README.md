# Healer 自愈引擎

Python + Playwright UI 测试原型：自然语言生成步骤，执行失败后尝试修复定位器，保留原断言并输出审计与报告。

当前重点是可验证的保守修复。**定位到唯一可见元素，不代表找到了正确业务目标。** 本项目用独立目标约束检查候选；证据不足就拒绝。

## 本地复现

需要 Python 3.10+。在项目根目录运行：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
python demo_healing.py
```

测试包含真实 Chromium 浏览器场景，需要安装浏览器；不需要 API key。离线演示覆盖属性变化、错误按钮排在前面、同名目标歧义、目标删除、禁用、缺少目标信息、同店不同商品。商品场景还验证操作后的商品编号和数量。

演示报告保存至 `reports/offline-validation/result-<run_id>.json`，审计保存至同目录的 `heals-<run_id>.jsonl`。每次运行独立输出，目录不入 Git。这些是人工标注的小规模本地场景，不代表线上准确率；离线模式没有评估模型收益。

## 执行过程

新增对照评估命令：

```powershell
python -m evaluation.benchmark
python -m evaluation.benchmark --with-model --repeats 3 --max-model-calls 12
python -m evaluation.shophub --source D:/my-test-framework
```

第一条比较关闭自愈与纯规则，不调用模型；第二条增加真实模型对照并限制最多 12 次模型请求；第三条在临时数据库中运行真实 ShopHub 加购核验，自动回收服务与数据库。需要 ShopHub 源码、已安装依赖的虚拟环境以及可建库的本地 MySQL 账号。

21 个固定场景、三轮对照的实际数据与边界见 [评估报告](docs/evaluation-2026-11-08.md)：扣除原本成功的场景后，规则恢复 24/33，规则加模型恢复 30/33。额外收益主要来自规则尚未覆盖的定位方式，不代表模型不可替代。

```text
自然语言 → 生成结构化步骤 → 执行
                              ├─ 正常 → 下一步
                              ├─ 定位失败 → 核对目标约束 → 规则候选 → 模型候选 → 统一核验 → 重跑
                              └─ 断言/导航等失败 → 分析并停止当前用例
```

首次失败无法恢复后，后续步骤标记 `blocked`（因前置失败而未执行）。整个用例最多尝试自愈 3 次，所有步骤共享额度。

## 目标约束是什么

`selector`（定位器）描述如何找到元素；`target`（目标约束）描述原本要操作哪个元素。例如给店铺 a 的商品 2 加购：

```json
{
  "action": "click",
  "selector": "#old-add-button",
  "target": {
    "tag": "button",
    "text": "Add",
    "attributes": {"data-store-id": "a", "data-product-id": "2"}
  }
}
```

`tag` 是元素标签；`text` 是合并空白后的精确文字；`attributes` 是必须全部匹配的属性。必须提供标签，以及文字或属性中的至少一项。属性目前要求在目标元素本身，暂不支持祖先容器约束。

目标必须在当前页面唯一匹配；隐藏、禁用、遮挡等点击不可操作情况拒绝修复。规则和模型给出的候选必须对应同一个目标元素。没有 target 的旧步骤仍可执行，但失败后拒绝自动修复；登录、支付、结算页面在调用模型前拒绝自动修复。断言不能通过自愈改写。

这些条件仅核对提供的约束，不能证明约束本身正确。生成器被要求不猜测目标属性，生成结果仍需人工审核；目标属性也变化时，本轮实现选择拒绝。

## 真实模型流水线

复制 `config/.env.example` 为 `config/.env`，配置 `ZHIPU_API_KEY`，启动你自己的被测电商服务，再运行：

```powershell
python main.py "打开 http://127.0.0.1:8000/login.html，输入账号 alice 密码 alice123，点击登录，断言 URL 包含 index.html"
```

仓库不包含完整电商服务。默认模型、接口地址见 `config/settings.py`；已单独验证真实模型自愈调用和 ShopHub 登录、加购、购物车链路，尚未实测自然语言生成的完整流水线或当前价格。生成器没有浏览器探索能力，无法仅凭自然语言可靠获知页面真实属性。

## 如何读报告

| 字段 | 含义 |
|---|---|
| `heal_triggered` / `healing_attempts` | 自愈调用次数，含被拒绝的尝试 |
| `heal_success` | 自愈后操作重跑通过的步骤数 |
| `steps_attempted` | 曾尝试自愈的不同步骤数 |
| `heal_rate` | 操作恢复步骤数 / 尝试自愈步骤数；不是业务正确率 |
| `guardrail_rejections` | 安全检查拒绝次数，不是误修数 |
| `false_heal_count` | 普通流水线无人工标注，值为 null；离线演示有已知正确目标，可单独统计 |
| `business_validation` | 全部步骤成功且最后一次导航/点击/填写后存在断言才为 passed；失败为 failed；缺少最终断言为 unverified |
| `healing.business_recovery` | 曾有操作恢复时采用业务验证结果，否则 not_applicable |

业务验证只表示提供的断言通过。例如只断言页面 URL，无法证明购物车商品正确，仍需用例设计者提供合适的断言。文本和视觉分析结论属于辅助建议，所谓置信度是现有规则分级，并未经过准确率校准。

模型 token（模型处理的文本单位）用量来自接口响应；费用按代码中的历史价格表估算，不是账单，未知模型当前会按零计算，因此不能用它判断实际成本。`execution` 耗时包含浏览器启动、执行、自愈和分析，不能与子阶段相加。

## 文件入口

- `schemas/step.py`：步骤、目标约束和失败信息。
- `agent/healer.py`：候选生成、目标核验、安全拒绝和审计。
- `agent/runner.py`：浏览器执行、断言及失败采集。
- `main.py`：用例编排、停止规则和最终报告。
- `demo_healing.py`：带预期结果的离线浏览器验证。
- `evaluation/`：固定场景对照、有限真实模型调用与真实 ShopHub 临时实例验证。
- `docs/进度.md`：本项目唯一进度记录。

设计依据：[Playwright 定位建议](https://playwright.dev/python/docs/locators)、[可操作性检查](https://playwright.dev/python/docs/actionability)。历史设计保留在 `docs/`，当前行为以代码、测试和本文为准。
