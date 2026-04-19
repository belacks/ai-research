"""
OpenClaw Research Node — Core Agent Logic
==========================================
Orchestrates: Web Crawl → LLM Summarisation → Briefing Output → Notification.
"""

import copy
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
import trafilatura

from app.agent.prompts import get_prompt_for_url, TARGET_URLS
from app.core.config import settings
from app.core.database import insert_run, insert_item, update_run_totals
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
_CRAWL_TIMEOUT_MS = 30_000          # 30 s — increased for heavy pages
_OLLAMA_CONNECT_TIMEOUT = 10        # fail fast if Ollama is unreachable
_OLLAMA_REQUEST_TIMEOUT = 900       # 15 min — >10mins for think mode on CPU
_MIN_ELIGIBLE_CHARS = 20            # Summary must have at least this many chars to be eligible


# ---------------------------------------------------------------------------
# 1. Web Crawling (Playwright)
# ---------------------------------------------------------------------------

def crawl_and_extract(url: str) -> str:
    """
    Navigate to *url* in headless Chromium and return clean article text.

    Uses trafilatura to strip navigation, sidebars, and boilerplate.
    Falls back silently to ``page.inner_text("body")`` if trafilatura
    returns ``None``.

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

        page.goto(url, wait_until="domcontentloaded", timeout=_CRAWL_TIMEOUT_MS)

        # Attempt clean extraction via trafilatura on full HTML
        page_html = page.content()
        text = trafilatura.extract(page_html)

        # Silent fallback: if trafilatura finds no article, use raw body text
        if not text:
            logger.info("Trafilatura returned None for %s, falling back to inner_text.", url)
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

def summarize_with_ollama(text: str, url: str, model_override: str = None) -> str:
    """
    Send *text* to the local Ollama instance and return a Markdown summary.
    Uses dynamic prompts tailored to the specific *url* source.

    Uses streaming mode so we can log progress while the model generates.
    Returns a fallback error string if the LLM is unreachable or fails.
    """
    import time as _time

    target_model = model_override if model_override else settings.target_model
    prompt = get_prompt_for_url(url, text, settings.researcher_profile)
    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": True,
        "think": False,         # disable reasoning — CPU is too slow for CoT on large multi-url texts
    }

    api_url = f"{settings.ollama_base_url}/api/generate"
    logger.info(
        "Requesting summary from %s (model: %s, timeout: %ds)",
        api_url, target_model, _OLLAMA_REQUEST_TIMEOUT,
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
# 2b. JSON Response Parser
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATE = {
    "title": "",
    "summary": "",
    "key_insights": [],
    "relevance_tags": [],
    "global_local": "global",
    "action_for_researcher": "",
}


def parse_llm_json_response(raw: str) -> dict:
    """
    Parse the LLM's raw text into a structured dict.

    1. Strip markdown code fences (```json ... ```) if present.
    2. Attempt ``json.loads()``.
    3. On failure, return a safe fallback dict with the raw text
       stuffed into "summary" so no data is lost.

    This is a standalone named function for unit-testability.
    """
    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"```\s*$", "", cleaned.strip())

    try:
        parsed = json.loads(cleaned)
        logger.info("LLM response parsed as valid JSON.")
        return parsed
    except json.JSONDecodeError:
        logger.warning("LLM response is not valid JSON. Using fallback dict.")
        fallback = copy.deepcopy(_FALLBACK_TEMPLATE)
        fallback["summary"] = raw
        return fallback


def format_telegram_message(parsed: dict, url: str, category: str, scope: str) -> str:
    """
    Render a parsed structured dict into a rich Telegram HTML message.
    Gracefully handles missing fields by falling back to empty defaults.
    """
    title = parsed.get("title", "")
    summary = parsed.get("summary", "")
    insights = parsed.get("key_insights", [])
    tags = parsed.get("relevance_tags", [])
    gl = parsed.get("global_local", scope)
    action = parsed.get("action_for_researcher", "")

    scope_icon = {"global": "\U0001f30d", "local": "\U0001f1ee\U0001f1e9", "both": "\U0001f310"}.get(gl, "\U0001f30d")

    parts = [f"<b>\U0001f52c Ceros Research Briefing</b>"]

    if title:
        parts.append(f"\n\U0001f4f0 <b>{escape_html(title)}</b>")

    tag_line = " \u2022 ".join(escape_html(t) for t in tags[:5]) if tags else category
    parts.append(f"\U0001f3f7\ufe0f {tag_line}  |  {scope_icon} {gl.capitalize()}")

    if summary:
        parts.append(f"\n{escape_html(summary)}")

    if insights:
        parts.append("\n\U0001f4a1 <b>Key Insights:</b>")
        for insight in insights[:3]:
            parts.append(f"\u2022 {escape_html(insight)}")

    if action:
        parts.append(f"\n\U0001f3af <b>Action:</b> {escape_html(action)}")

    parts.append(f"\n<code>{escape_html(url)}</code>  |  {escape_html(category)}")

    return "\n".join(parts)


def is_eligible(parsed: dict, raw_text: str) -> tuple[bool, str]:
    """Check if the extracted text and parsed JSON output are usable."""
    if len(raw_text) < _MIN_ELIGIBLE_CHARS:
        return False, "raw text too short"
        
    summary = parsed.get("summary", "")
    if not summary or summary.startswith("_⚠️"):
        return False, "LLM error in summary"
        
    title = parsed.get("title", "")
    insights = parsed.get("key_insights", [])
    if not title and not insights:
        return False, "JSON parse failed (fallback dict)"
        
    return True, "ok"

# ---------------------------------------------------------------------------
# 3. Orchestrator
# ---------------------------------------------------------------------------

def execute_research_agent(model_override: str = None, custom_urls: list[str] = None) -> None:
    """
    Full research cycle:
      1. Crawl the target URL.
      2. Summarise with the local LLM.
      3. Write a timestamped briefing to shared_workspace/.
      4. Push a Telegram notification.
      5. Consume the manual trigger file if it exists.
    """
    logger.info("--- Research Agent cycle start ---")
    if model_override:
        logger.info("Using target model override: %s", model_override)

    # Ensure output directory exists
    _WORKSPACE_DIR.mkdir(exist_ok=True)

    aggregated_summaries = []
    sent_count = 0

    # DB: create a run record
    trigger_type = "custom" if custom_urls else "scheduled"
    model_used = model_override if model_override else settings.target_model
    run_id = insert_run(trigger=trigger_type, model_used=model_used)

    # Build target list: custom URLs as plain dicts or structured TARGET_URLS
    if custom_urls:
        target_entries = [{"url": u, "category": "Custom", "scope": "global"} for u in custom_urls]
    else:
        target_entries = TARGET_URLS

    for entry in target_entries:
        url = entry["url"]
        category = entry["category"]
        scope = entry["scope"]
        try:
            logger.info("Processing target: %s [%s / %s]", url, category, scope)
            
            # 1. Crawl
            raw_text = crawl_and_extract(url)
            if not raw_text:
                logger.warning("Crawl returned no data for %s. Skipping.", url)
                continue

            # Limit text input massive to save CPU reasoning time and LLM Context Window
            raw_text = raw_text[:4000]

            # 2. Summarise
            summary_raw = summarize_with_ollama(raw_text, url, model_override=model_override)

            # 3. Parse structured JSON from LLM output
            parsed = parse_llm_json_response(summary_raw)
            
            # Store parsed data, URL, category, and scope for briefing & notification
            aggregated_summaries.append({
                "url": url,
                "category": category,
                "scope": scope,
                "parsed": parsed,
                "summary": parsed.get("summary", summary_raw),
                "raw_char_count": len(raw_text),
            })

            # Eligibility check: only notify if extraction + parse produced usable content
            eligible, reason = is_eligible(parsed, raw_text)
            was_notified = False
            if not eligible:
                logger.warning("Ineligible %s — %s. Skipping Telegram notification.", url, reason)
            else:
                # 4. Notify IMMEDIATELY via Telegram (prevents "message too long" API error)
                tg_msg = format_telegram_message(parsed, url, category, scope)
                send_telegram_alert(tg_msg)
                was_notified = True
                sent_count += 1

            # DB: persist this item
            aggregated_summaries[-1]["was_notified"] = was_notified
            if run_id != -1:
                insert_item(run_id, aggregated_summaries[-1])

        except Exception as exc:
            logger.error("Failed to process %s: %s", url, exc)
            continue

    if not aggregated_summaries:
        msg = "⚠️ All crawls failed. Skipping this cycle."
        logger.warning(msg)
        send_telegram_alert(msg)
        _consume_trigger()
        return

    # 3. Write briefing (Markdown for the dashboard)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    briefing_path = _WORKSPACE_DIR / f"briefing_{ts}.md"
    
    wib = timezone(timedelta(hours=7), name="WIB")
    md_header = f"# 🔬 Ceros Research Briefing — {datetime.now(wib).strftime('%Y-%m-%d %H:%M WIB')}\n\n"
    md_blocks = [
        f"**Source:** [{item['url']}]({item['url']})\n\n{item['summary']}" 
        for item in aggregated_summaries
    ]
    md_body = "\n\n---\n\n".join(md_blocks)
    
    briefing_path.write_text(md_header + md_body + "\n", encoding="utf-8")
    logger.info("Briefing saved → %s", briefing_path)

    # 4. Notify (HTML for Telegram)
    # Skipped: Notifications are now sent asynchronously in the loop above to avoid hitting the 4096 char limit.
    logger.info("All Telegram notifications dispatched.")

    # 5. Consume trigger
    _consume_trigger()

    # DB: finalise run totals
    if run_id != -1:
        update_run_totals(run_id, total=len(aggregated_summaries), sent=sent_count)

    logger.info("--- Research Agent cycle complete ---")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _consume_trigger() -> None:
    """Delete the manual trigger file if it exists."""
    if _TRIGGER_FILE.exists():
        _TRIGGER_FILE.unlink()
        logger.info("Manual trigger consumed (deleted %s).", _TRIGGER_FILE.name)
