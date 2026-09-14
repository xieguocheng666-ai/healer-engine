"""全局配置：从 config/.env 读取，集中暴露给各模块。

所有可变配置（API key / 模型名 / 被测系统地址）都集中在这里，
模块内部不写死，也不散落环境变量读取逻辑。
"""

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

# 关键流程页面：命中这些关键词的定位失败，自愈结果只记审计、不自动应用（护栏 #3）
CRITICAL_URL_KEYWORDS = ("login", "payment", "checkout")

# 自愈重试上限（护栏 #4 / #5）
MAX_HEAL_ATTEMPTS_PER_SELECTOR = 3
MAX_HEAL_ROUNDS_PER_RUN = 3
