"""
OpenClaw Research Node — Centralized Configuration
===================================================
Single source of truth for all application settings.

Loading priority:
  1. Real environment variables (injected by Docker Compose via env_file)
  2. Fallback to .env file for local development (override=False)

Fail-fast: the process will crash immediately with a clear ValueError
if any required credential is missing — we want Docker logs to scream,
not the agent to silently malfunction mid-run.
"""

import os
import logging
from dataclasses import dataclass

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: load .env only as a local-dev fallback.
# In Docker, env vars are already injected by `docker-compose.yml → env_file`.
# override=False ensures system env always wins over the .env file.
# ---------------------------------------------------------------------------
load_dotenv(override=False)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _require_env(key: str) -> str:
    """Return the value of an environment variable or die trying."""
    value = os.getenv(key)
    if not value:
        raise ValueError(
            f"[CONFIG FATAL] Required environment variable '{key}' is missing or empty. "
            f"Check your .env file or Docker Compose env_file configuration."
        )
    return value


# ---------------------------------------------------------------------------
# Application Settings (immutable after startup)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """Immutable container for every runtime setting the app needs."""

    # -- Telegram Push Notifications --
    telegram_bot_token: str
    telegram_chat_id: str

    # -- Local LLM / SLM (Ollama) --
    ollama_base_url: str
    target_model: str

    # -- Stateful Scheduler --
    schedule_interval_hours: int

    # -- User Profile --
    researcher_profile: str

    # -- News Sentiment Agent --
    news_model: str
    news_min_confidence: float

    # -- Cloud LLM & Vault --
    gemini_api_key: str
    vault_raw_dir: str
    obsidian_vault_dir: str

    # -- Gmail BNI Parser --
    gmail_user: str
    gmail_app_password: str


def _load_settings() -> Settings:
    """
    Build a Settings instance from the environment.

    Called exactly once at module-import time so every other module
    can simply ``from app.core.config import settings``.
    """
    logger.info("Loading application settings from environment …")

    cfg = Settings(
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require_env("TELEGRAM_CHAT_ID"),
        ollama_base_url=_require_env("OLLAMA_BASE_URL"),
        target_model=_require_env("TARGET_MODEL"),
        schedule_interval_hours=int(_require_env("SCHEDULE_INTERVAL_HOURS")),
        researcher_profile=os.getenv(
            "RESEARCHER_PROFILE", 
            "A technology researcher interested in AI, Data Science, and global tech trends."
        ),
        news_model=os.getenv("NEWS_MODEL", "will702/indo-roBERTa-financial-sentiment-v2"),
        news_min_confidence=float(os.getenv("NEWS_MIN_CONFIDENCE", "0.70")),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        vault_raw_dir=os.getenv("VAULT_RAW_DIR", "./shared_workspace/vault_output"),
        obsidian_vault_dir=os.getenv("OBSIDIAN_VAULT_DIR", os.getenv("VAULT_RAW_DIR", "./shared_workspace/vault_output")),
        gmail_user=os.getenv("GMAIL_USER", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
    )

    logger.info(
        "Settings loaded  ·  model=%s  ·  ollama=%s  ·  schedule_every=%dh  ·  gemini=%s",
        cfg.target_model,
        cfg.ollama_base_url,
        cfg.schedule_interval_hours,
        "CONFIGURED" if cfg.gemini_api_key else "NOT SET",
    )
    return cfg


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere:
#   from app.core.config import settings
# ---------------------------------------------------------------------------
settings: Settings = _load_settings()
