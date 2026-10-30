"""Paths, limits and environment names used by the Stage 1 service.

The existing schema and field configuration stay the source of truth; this
module only points to them and records the frozen Stage 0 limits.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SCHEMA_PATH = BASE_DIR / "loan_complaint.schema.json"
FIELD_CONFIG_PATH = BASE_DIR / "field_config.json"
STAGE0_DIR = BASE_DIR / "stage0"
FIXTURES_DIR = STAGE0_DIR / "fixtures"
CASES_DIR = FIXTURES_DIR / "cases"

TEMPLATE_ID = "private_lending_complaint"
SCHEMA_VERSION = "0.1.0"

MAX_FILE_CONTEXT_ITEMS = 20
MAX_ITEM_CHARS = 50_000
MAX_TOTAL_CHARS = 100_000

WORKERS_DEFAULT = 5
WORKERS_ALLOWED = (4, 5)
QUEUE_CAPACITY = 20
REQUEST_TIMEOUT_SECONDS = 60.0
MODEL_RETRY_LIMIT = 1

# Qwen3.8 adapter configuration is read from the environment; never hard-code
# credentials in the repository.
QWEN_BASE_URL_ENV = "QWEN_BASE_URL"
QWEN_API_KEY_ENV = "QWEN_API_KEY"
QWEN_MODEL_ENV = "QWEN_MODEL"
QWEN_TIMEOUT_ENV = "QWEN_TIMEOUT_S"
QWEN_MAX_TOKENS_ENV = "QWEN_MAX_TOKENS"
QWEN_ENABLE_THINKING_ENV = "QWEN_ENABLE_THINKING"
