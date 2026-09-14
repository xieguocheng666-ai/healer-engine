# AI 测试智能体 · M1 实施计划（骨架 + LLM 客户端 + Step Schema）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 subagent-driven-development 或 executing-plans 逐任务执行。步骤用 `- [ ]` 勾选追踪。

**Goal:** 搭起项目骨架，跑通「LLM 客户端能调智谱 + Step schema 能校验 JSON」这条最小链路，为 M2 的 Generator/Runner 打地基。

**Architecture:** 三个模块先行落地——`config/settings.py`（读 .env 配置）、`schemas/step.py`（pydantic 校验步骤 JSON）、`llm/client.py`（统一封装智谱调用 + token 记账）。最后用 `main.py` 做一次真实冒烟：一句自然语言 → 调 LLM 生成步骤 JSON → pydantic 校验通过。

**Tech Stack:** Python 3.11、requests、pydantic v2、python-dotenv、pytest。

---

## Roadmap 总览（M1–M6，每个里程碑单独出计划）

| 里程碑 | 内容 | 交付物 / 验收 |
|--------|------|--------------|
| **M1**（本计划） | 骨架 + LLM 客户端 + Step schema | 调通智谱、能校验 JSON |
| M2 | Generator + Runner | 自然语言 → 执行真测试 |
| M3 | Healer（分层自愈 + 护栏 + 重试） | 选择器失效能自愈 |
| M4 | Analyzer（文本 + 视觉） | 区分产品 bug vs 选择器问题 |
| M5 | Metrics + 报告（含 cost 定价、heal rate、false-heal count） | 四项量化指标真实跑出 |
| M6 | 自身测试 + README + 串讲 | 每个模块可脱稿讲清 |

> 本计划的三个模块（settings / step / client）是 M2–M5 所有模块的共同地基，接口保持稳定，后续只增不改。

---

## 目录结构（本计划要建的文件）

```
D:\ai-test-agent\
├── main.py                  # Task 4：真实冒烟入口
├── pytest.ini               # Task 1：pytest 配置（pythonpath = .）
├── requirements.txt         # Task 1：依赖
├── .gitignore               # Task 1：排除 .env / __pycache__
├── config/
│   ├── __init__.py
│   ├── settings.py          # Task 1：读 .env 配置
│   ├── .env.example         # Task 1：配置模板
│   └── .env                 # Task 1：真实配置（不进 git，用户填 key）
├── schemas/
│   ├── __init__.py
│   └── step.py              # Task 2：Step + FailureDetail
├── llm/
│   ├── __init__.py
│   └── client.py            # Task 3：LLMClient + token 记账
└── tests/
    ├── test_schema.py       # Task 2：schema 校验测试
    └── test_llm_client.py   # Task 3：LLM 客户端测试（mock）
```

---

## Task 1: 项目骨架 + 配置

**Files:**
- Create: `requirements.txt`, `.gitignore`, `pytest.ini`
- Create: `config/__init__.py`, `config/settings.py`, `config/.env.example`, `config/.env`

- [ ] **Step 1: 初始化 git 仓库**

```bash
cd /d/ai-test-agent
git init
```

- [ ] **Step 2: 写 `requirements.txt`**

```
playwright>=1.40
requests>=2.31
pydantic>=2.5
python-dotenv>=1.0
pytest>=7.4
```

- [ ] **Step 3: 写 `.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: 写 `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
```

> `pythonpath = .` 让 pytest 把项目根加进 `sys.path`，这样 `tests/` 里能 `from schemas.step import Step`、`from llm.client import LLMClient`。

- [ ] **Step 5: 写 `config/.env.example`**

```
ZHIPU_API_KEY=your_api_key_here
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
TEXT_MODEL=glm-4-flash
VISION_MODEL=glm-4v-flash
SUT_BASE_URL=http://127.0.0.1:8000
```

- [ ] **Step 6: 复制成 `config/.env` 并填入真实 key**

```bash
cp config/.env.example config/.env
```

然后编辑 `config/.env`，把 `ZHIPU_API_KEY` 换成你已有的智谱 key（与之前验证视觉模型时用的是同一个）。其余字段用默认值即可。

- [ ] **Step 7: 写 `config/__init__.py`（空文件）**

```bash
touch config/__init__.py
```

- [ ] **Step 8: 写 `config/settings.py`**

```python
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / "config" / ".env")

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_BASE_URL = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
TEXT_MODEL = os.getenv("TEXT_MODEL", "glm-4-flash")
VISION_MODEL = os.getenv("VISION_MODEL", "glm-4v-flash")
SUT_BASE_URL = os.getenv("SUT_BASE_URL", "http://127.0.0.1:8000")
```

- [ ] **Step 9: 安装依赖并验证配置可读**

```bash
pip install -r requirements.txt
python -c "from config.settings import ZHIPU_API_KEY, TEXT_MODEL; print(bool(ZHIPU_API_KEY), TEXT_MODEL)"
```

Expected: `True glm-4-flash`（`True` 说明 key 已从 .env 读到）。

- [ ] **Step 10: 首次提交**

```bash
git add -A
git commit -m "chore: scaffold ai-test-agent project + config"
```

---

## Task 2: Step schema（pydantic）+ 测试

**Files:**
- Create: `schemas/__init__.py`, `schemas/step.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: 写失败测试 `tests/test_schema.py`**

```python
import pytest
from pydantic import ValidationError

from schemas.step import Step, FailureDetail


def test_valid_goto_step():
    s = Step(action="goto", url="http://127.0.0.1:8000/login.html")
    assert s.action == "goto"
    assert s.url == "http://127.0.0.1:8000/login.html"


def test_valid_fill_step():
    s = Step(action="fill", selector="[data-action='username']", value="alice")
    assert s.value == "alice"


def test_valid_assert_step():
    s = Step(action="assert", assert_type="url_contains", value="index.html")
    assert s.assert_type == "url_contains"


def test_invalid_action_rejected():
    with pytest.raises(ValidationError):
        Step(action="hover")


def test_invalid_assert_type_rejected():
    with pytest.raises(ValidationError):
        Step(action="assert", assert_type="element_visible", value="x")


def test_goto_without_url_rejected():
    with pytest.raises(ValidationError):
        Step(action="goto")


def test_fill_without_selector_rejected():
    with pytest.raises(ValidationError):
        Step(action="fill", value="alice")


def test_failure_detail_holds_step_and_error():
    step = Step(action="click", selector="[data-action='submit']")
    fd = FailureDetail(step=step, error="TimeoutError", page_url="http://x")
    assert fd.step is step
    assert fd.error == "TimeoutError"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_schema.py -v
```

Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'schemas'`）。

- [ ] **Step 3: 写 `schemas/__init__.py`（空文件）**

```bash
touch schemas/__init__.py
```

- [ ] **Step 4: 写 `schemas/step.py`**

```python
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, model_validator


class Step(BaseModel):
    """一条 UI 测试步骤。action 集合封闭，非法 action 直接校验拒绝。"""

    action: Literal["goto", "fill", "click", "assert", "wait"]
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    assert_type: Literal["url_contains", "text_visible"] | None = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> "Step":
        if self.action == "goto" and not self.url:
            raise ValueError("goto 步骤必须提供 url")
        if self.action in ("fill", "click", "wait") and not self.selector:
            raise ValueError(f"{self.action} 步骤必须提供 selector")
        if self.action == "fill" and self.value is None:
            raise ValueError("fill 步骤必须提供 value")
        if self.action == "assert" and (not self.assert_type or self.value is None):
            raise ValueError("assert 步骤必须提供 assert_type 和 value")
        return self


@dataclass
class FailureDetail:
    """Runner 捕获的失败详情，是 Healer 与 Analyzer 的输入。"""

    step: Step
    error: str
    page_url: str
    element_html: str | None = None
    screenshot: bytes | None = None
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_schema.py -v
```

Expected: 8 passed。

- [ ] **Step 6: 提交**

```bash
git add schemas/ tests/test_schema.py
git commit -m "feat: add Step schema with pydantic validation"
```

---

## Task 3: LLM 客户端 + 测试

**Files:**
- Create: `llm/__init__.py`, `llm/client.py`
- Test: `tests/test_llm_client.py`

> 本 Task 只做**文本调用**（glm-4-flash）。视觉调用（glm-4v-flash）在 M4 Analyzer 才用到，届时再扩展，避免 YAGNI。

- [ ] **Step 1: 写失败测试 `tests/test_llm_client.py`**

```python
import pytest
from unittest.mock import Mock, patch

from llm.client import LLMClient


def _mock_response(content, usage):
    resp = Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }
    return resp


def test_chat_returns_content_and_records_usage():
    resp = _mock_response('{"action": "goto"}', {"prompt_tokens": 12, "completion_tokens": 5})
    client = LLMClient(api_key="test-key")
    with patch("requests.post", return_value=resp):
        content = client.chat([{"role": "user", "content": "hi"}], stage="generation")
    assert content == '{"action": "goto"}'
    assert len(client.calls) == 1
    assert client.calls[0].input_tokens == 12
    assert client.calls[0].output_tokens == 5
    assert client.calls[0].stage == "generation"


def test_chat_sends_bearer_header_and_model():
    resp = _mock_response("ok", {})
    client = LLMClient(api_key="secret", model="glm-4-flash")
    with patch("requests.post", return_value=resp) as post:
        client.chat([{"role": "user", "content": "hi"}])
    _, kwargs = post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["model"] == "glm-4-flash"


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        LLMClient(api_key="")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_llm_client.py -v
```

Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'llm'`）。

- [ ] **Step 3: 写 `llm/__init__.py`（空文件）**

```bash
touch llm/__init__.py
```

- [ ] **Step 4: 写 `llm/client.py`**

```python
from dataclasses import dataclass, field

import requests

from config.settings import ZHIPU_API_KEY, ZHIPU_BASE_URL, TEXT_MODEL


@dataclass
class LLMUsage:
    """单次 LLM 调用的 token 记账。cost 计算归 M5（需查证官方定价后实现）。"""

    model: str
    input_tokens: int
    output_tokens: int
    stage: str


class LLMClient:
    """智谱开放平台统一封装。每次 chat 追加一条 usage 记录，供 M5 Metrics 聚合。"""

    def __init__(self, model: str = TEXT_MODEL, base_url: str = ZHIPU_BASE_URL, api_key: str = ZHIPU_API_KEY):
        if not api_key:
            raise ValueError("ZHIPU_API_KEY 未配置，请检查 config/.env")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.calls: list[LLMUsage] = []

    def chat(self, messages: list[dict], stage: str = "default") -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages}
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})
        self.calls.append(
            LLMUsage(
                model=self.model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                stage=stage,
            )
        )
        return data["choices"][0]["message"]["content"]
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_llm_client.py -v
```

Expected: 3 passed。

- [ ] **Step 6: 提交**

```bash
git add llm/ tests/test_llm_client.py
git commit -m "feat: add LLM client with token accounting"
```

---

## Task 4: 最小冒烟（真实调智谱 + 校验 JSON）

**Files:**
- Create: `main.py`

> 这一步不用 mock，真实调智谱，证明 M1 链路通：一句自然语言 → LLM 生成步骤 JSON → pydantic 校验全部通过。

- [ ] **Step 1: 写 `main.py`**

```python
import json

from llm.client import LLMClient
from schemas.step import Step

SYSTEM_PROMPT = (
    "你是 UI 测试用例生成器。根据用户指令输出一个 JSON 数组，"
    "每个元素是一条测试步骤，字段仅允许：action（取值 goto/fill/click/assert/wait）、"
    "url、selector、value、assert_type（取值 url_contains/text_visible）。"
    "定位器优先用 data-action / data-store-id / data-product-id 等语义属性。"
    "只输出 JSON，不要任何解释或代码块标记。"
)


def main() -> None:
    instruction = (
        "打开登录页 http://127.0.0.1:8000/login.html，"
        "在用户名框输入 alice，密码框输入 alice123，"
        "点击登录按钮，断言 URL 包含 index.html"
    )
    client = LLMClient()
    content = client.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ],
        stage="generation",
    )
    steps_json = json.loads(content)
    steps = [Step(**s) for s in steps_json]
    print(f"生成 {len(steps)} 步，全部通过 pydantic 校验：")
    for s in steps:
        print(f"  - {s.action}: {s.selector or s.url or s.value}")
    print(f"本次 token 记账：{client.calls}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行冒烟**

```bash
python main.py
```

Expected: 打印「生成 N 步，全部通过 pydantic 校验」及每步摘要，最后打印 token 记账。

> 若某步校验失败（`ValidationError`），说明 LLM 返回的字段不合规——这正是 M2 Generator 要做的「非法 JSON 重试」逻辑，M1 先如实暴露即可。

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: add smoke entry that generates + validates steps"
```

---

## M1 完成标准（全部满足才进 M2）

- [ ] `pytest` 全绿（8 schema 用例 + 3 client 用例 = 11 passed）
- [ ] `python main.py` 真实调智谱，输出合法步骤 JSON 且全部通过 pydantic 校验
- [ ] `git log` 有 3 个干净提交（scaffold / schema / client+main）
- [ ] `config/.env` 未进 git（`.gitignore` 生效）

---

## 后续里程碑计划（M2 起，逐个单独出计划）

- **M2 Generator + Runner**：实现「非法 JSON 重试最多 3 次」、定位器优先 `data-*`；Runner 用 Playwright 同步 API 逐条执行，捕获 `FailureDetail`（含截图）。届时 `requirements.txt` 需 `playwright install chromium`。
- **M3 Healer**：分层自愈（L1 免费预检 → L2 LLM 重定位）+ 五条护栏 + 重试，落地 `heals.jsonl` 审计日志。
- **M4 Analyzer**：文本 LLM + 视觉模型双通道判断失败性质，扩展 `client.py` 增加视觉调用。
- **M5 Metrics**：聚合 `llm_calls` 与 `heals.jsonl`，实现 heal rate / false-heal count / token cost（查证 glm 官方定价）/ 耗时。
- **M6 自身测试 + README + 串讲**。
