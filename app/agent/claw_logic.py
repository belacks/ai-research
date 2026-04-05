"""
OpenClaw Research Node — Core Agent Logic
==========================================
Orchestrates: Web Crawl → LLM Summarisation → Briefing Output → Notification.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from app.agent.prompts import SUMMARIZE_PROMPT
from app.core.config import settings
from app.core.notifier import escape_html, md_to_telegram_html, send_telegram_alert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_TRIGGER_FILE = _WORKSPACE_DIR / "trigger.txt"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CRAWL_TIMEOUT_MS = 15_000          # 15 s — Playwright uses milliseconds
_OLLAMA_CONNECT_TIMEOUT = 10        # fail fast if Ollama is unreachable
_OLLAMA_REQUEST_TIMEOUT = 300       # 5 min — 2B model on CPU can be slow
_TARGET_URL = "https://github.com/trending/python"


# ---------------------------------------------------------------------------
# 1. Web Crawling (Playwright)
# ---------------------------------------------------------------------------

def crawl_and_extract(url: str) -> str:
    """
    Navigate to *url* in headless Chromium and return the visible body text.

    Returns an empty string on any failure (timeout, crash, etc.).
    The browser is always closed in a ``finally`` block.
    """
    logger.info("Crawling → %s", url)
    playwright = None
    browser = None

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, wait_until="networkidle", timeout=_CRAWL_TIMEOUT_MS)
        text = page.inner_text("body")

        logger.info("Extracted %d characters from %s", len(text), url)
        return text

    except Exception as exc:
        logger.error("Crawl failed for %s: %s", url, exc)
        return ""

    finally:
        if browser:
            browser.close()
        if playwright:
            playwright.stop()


# ---------------------------------------------------------------------------
# 2. Local LLM Reasoning (Ollama)
# ---------------------------------------------------------------------------

def summarize_with_ollama(text: str) -> str:
    """
    Send *text* to the local Ollama instance and return a Markdown summary.

    Uses streaming mode so we can log progress while the model generates.
    Returns a fallback error string if the LLM is unreachable or fails.
    """
    import time as _time

    prompt = SUMMARIZE_PROMPT.format(text=text)
    payload = {
        "model": settings.target_model,
        "prompt": prompt,
        "stream": True,
        "think": False,         # disable reasoning — summarisation doesn't need CoT
    }

    api_url = f"{settings.ollama_base_url}/api/generate"
    logger.info(
        "Requesting summary from %s (model: %s, timeout: %ds)",
        api_url, settings.target_model, _OLLAMA_REQUEST_TIMEOUT,
    )

    try:
        resp = requests.post(
            api_url, json=payload,
            stream=True,
            timeout=(_OLLAMA_CONNECT_TIMEOUT, _OLLAMA_REQUEST_TIMEOUT),
        )
        resp.raise_for_status()

        # Stream tokens and log progress periodically.
        # NOTE: qwen3.5 is a thinking/reasoning model — it emits tokens in
        # the "thinking" field first, then "response". We track both.
        chunks: list[str] = []
        token_count = 0
        thinking_count = 0
        phase = "loading"
        start_time = _time.monotonic()
        last_log = start_time

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            import json as _json
            data = _json.loads(line)

            # Thinking tokens (reasoning phase)
            thinking_token = data.get("thinking", "")
            if thinking_token:
                thinking_count += 1
                if phase != "thinking":
                    phase = "thinking"
                    logger.info("🧠 LLM entered thinking/reasoning phase …")

            # Response tokens (output phase)
            token = data.get("response", "")
            if token:
                chunks.append(token)
                token_count += 1
                if phase != "responding":
                    phase = "responding"
                    logger.info("✍️  LLM now generating response …")

            # Progress log every 10 seconds
            now = _time.monotonic()
            elapsed = now - start_time
            if now - last_log >= 10:
                logger.info(
                    "⏳ LLM %s … thinking=%d  response=%d tokens (%.0fs elapsed)",
                    phase, thinking_count, token_count, elapsed,
                )
                last_log = now

            # Ollama sends {"done": true} on the final chunk
            if data.get("done"):
                total_dur = data.get("total_duration", 0) / 1e9  # ns → s
                eval_count = data.get("eval_count", token_count)
                logger.info(
                    "✅ LLM finished — %d thinking + %d response tokens in %.1fs",
                    thinking_count, eval_count, total_dur,
                )
                break

        result = "".join(chunks).strip()
        if not result:
            logger.warning("Ollama returned an empty response.")
            return "_⚠️ LLM returned an empty response._"

        logger.info("Summary received (%d chars).", len(result))
        return result

    except requests.exceptions.Timeout:
        logger.error("Ollama request timed out after %ds.", _OLLAMA_REQUEST_TIMEOUT)
        return "_⚠️ Local LLM timed out — is Ollama running?_"

    except requests.exceptions.ConnectionError:
        logger.error("Cannot reach Ollama at %s.", api_url)
        return "_⚠️ Cannot connect to Ollama — check OLLAMA_BASE_URL._"

    except requests.exceptions.RequestException as exc:
        logger.error("Ollama request failed: %s", exc)
        return f"_⚠️ LLM error: {exc}_"


# ---------------------------------------------------------------------------
# 3. Orchestrator
# ---------------------------------------------------------------------------

def execute_research_agent() -> None:
    """
    Full research cycle:
      1. Crawl the target URL.
      2. Summarise with the local LLM.
      3. Write a timestamped briefing to shared_workspace/.
      4. Push a Telegram notification.
      5. Consume the manual trigger file if it exists.
    """
    logger.info("--- Research Agent cycle start ---")

    # Ensure output directory exists
    _WORKSPACE_DIR.mkdir(exist_ok=True)

    # 1. Crawl
    raw_text = crawl_and_extract(_TARGET_URL)

    if not raw_text:
        msg = f"⚠️ Crawl returned no data for <code>{escape_html(_TARGET_URL)}</code>. Skipping this cycle."
        logger.warning(msg)
        send_telegram_alert(msg)
        _consume_trigger()
        return

    # 2. Summarise
    summary = summarize_with_ollama(raw_text)

    # 3. Write briefing (Markdown for the dashboard)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    briefing_path = _WORKSPACE_DIR / f"briefing_{ts}.md"
    header = (
        f"# 🔬 OpenClaw Briefing — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"**Source:** [{_TARGET_URL}]({_TARGET_URL})\n\n---\n\n"
    )
    briefing_path.write_text(header + summary + "\n", encoding="utf-8")
    logger.info("Briefing saved → %s", briefing_path)

    # 4. Notify (HTML for Telegram)
    tg_msg = (
        f"<b>🔬 New OpenClaw Briefing</b>\n\n"
        f"Source: <code>{escape_html(_TARGET_URL)}</code>\n\n"
        f"{md_to_telegram_html(summary)}"
    )
    send_telegram_alert(tg_msg)

    # 5. Consume trigger
    _consume_trigger()

    logger.info("--- Research Agent cycle complete ---")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _consume_trigger() -> None:
    """Delete the manual trigger file if it exists."""
    if _TRIGGER_FILE.exists():
        _TRIGGER_FILE.unlink()
        logger.info("Manual trigger consumed (deleted %s).", _TRIGGER_FILE.name)
