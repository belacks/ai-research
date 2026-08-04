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
import asyncio
from playwright.async_api import async_playwright
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
_CONCURRENT_CRAWL_LIMIT = 3         # Concurrent Playwright browser instances


# ---------------------------------------------------------------------------
# 1. Web Crawling (Async Playwright + Semaphore)
# ---------------------------------------------------------------------------

async def crawl_and_extract_async(url: str, semaphore: asyncio.Semaphore) -> str:
    """
    Navigate to *url* using Playwright async_api with concurrency control via *semaphore*.
    Uses trafilatura to strip navigation, sidebars, and boilerplate.
    Falls back to inner_text if trafilatura returns None.
    """
    async with semaphore:
        logger.info("Async Crawling (Semaphore active) → %s", url)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=_CRAWL_TIMEOUT_MS)

                page_html = await page.content()
                text = trafilatura.extract(page_html)

                if not text:
                    logger.info("Trafilatura returned None for %s, falling back to inner_text.", url)
                    text = await page.inner_text("body")

                await browser.close()
                extracted_str = text or ""
                logger.info("Extracted %d characters from %s", len(extracted_str), url)
                return extracted_str
        except Exception as exc:
            logger.error("Async Crawl failed for %s: %s", url, exc)
            return ""


def crawl_and_extract(url: str) -> str:
    """Synchronous wrapper around crawl_and_extract_async for single-target calls."""
    try:
        return asyncio.run(crawl_and_extract_async(url, asyncio.Semaphore(1)))
    except Exception as exc:
        logger.error("Synchronous crawl fallback failed for %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# 2. LLM Reasoning (Gemini Cloud API & Local Ollama Fallback Cascade)
# ---------------------------------------------------------------------------

def summarize_with_gemini(
    text: str,
    url: str,
    model_name: str = "gemini-3.5-flash-lite",
    lens: str = "executive",
    custom_query: str = ""
) -> str:
    """Send *text* to Google AI Studio Gemini API and return raw response text."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")

    from google import genai
    if url in ["raw_prompt", "daily_digest"] or url.startswith("raw_"):
        prompt = text
    else:
        prompt = get_prompt_for_url(url, text, settings.researcher_profile, lens=lens, custom_query=custom_query)
    
    logger.info("Requesting cloud summary from Gemini API (model: %s, lens: %s) …", model_name, lens)
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    
    result = response.text.strip() if response.text else ""
    if not result:
        raise ValueError(f"Gemini API ({model_name}) returned an empty response.")
    
    logger.info("Gemini summary received (%d chars) via %s.", len(result), model_name)
    return result


def summarize_with_ollama(
    text: str,
    url: str,
    model_override: str = None,
    lens: str = "executive",
    custom_query: str = ""
) -> str:
    """Send *text* to local Ollama instance and return summary using selected Analytical Lens."""
    import time as _time

    target_model = model_override if model_override else settings.target_model
    if url in ["raw_prompt", "daily_digest"] or url.startswith("raw_"):
        prompt = text
    else:
        prompt = get_prompt_for_url(url, text, settings.researcher_profile, lens=lens, custom_query=custom_query)

    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": True,
        "think": False,
    }

    api_url = f"{settings.ollama_base_url}/api/generate"
    logger.info(
        "Requesting summary from %s (model: %s, lens: %s)",
        api_url, target_model, lens,
    )

    try:
        resp = requests.post(
            api_url, json=payload,
            stream=True,
            timeout=(_OLLAMA_CONNECT_TIMEOUT, _OLLAMA_REQUEST_TIMEOUT),
        )
        resp.raise_for_status()

        chunks = []
        thinking_count = 0
        token_count = 0
        start_t = _time.time()

        for line in resp.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            chunk = data.get("response", "")

            if "<think>" in chunk or thinking_count > 0:
                thinking_count += 1
                if "</think>" in chunk:
                    thinking_count = 0
                continue

            chunks.append(chunk)
            token_count += 1

            if token_count % 50 == 0:
                elapsed = _time.time() - start_t
                tok_s = token_count / elapsed if elapsed > 0 else 0
                logger.info(
                    "Ollama generating … %d tokens (%.1f tok/s)",
                    token_count, tok_s,
                )

            if data.get("done", False):
                total_dur = data.get("total_duration", 0) / 1e9
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


def summarize_with_cascade(
    text: str,
    url: str,
    model_override: str = None,
    lens: str = "executive",
    custom_query: str = ""
) -> str:
    """Execute model cascade supporting Analytical Lens choice."""
    default_cascade = [
        ("gemini", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.5-flash"),
        ("gemini", "gemini-3.6-flash"),
        ("gemini", "gemma-4-31b-it"),
        ("gemini", "gemma-4-26b-a4b-it"),
        ("ollama", None),
    ]

    if model_override and model_override != "auto":
        if model_override == "ollama":
            cascade = [("ollama", None)]
        else:
            cascade = [("gemini", model_override)] + [m for m in default_cascade if m[1] != model_override]
    else:
        cascade = default_cascade

    for backend, model_name in cascade:
        try:
            if backend == "gemini":
                logger.info("Attempting cascade step: Gemini (%s)...", model_name)
                return summarize_with_gemini(text, url, model_name=model_name, lens=lens, custom_query=custom_query)
            elif backend == "ollama":
                logger.info("Attempting cascade step: Local Ollama...")
                return summarize_with_ollama(text, url, model_override=model_override, lens=lens, custom_query=custom_query)
        except Exception as exc:
            logger.warning("Cascade step failed (%s / %s): %s. Falling to next model.", backend, model_name, exc)
            continue

    return "_⚠️ All LLM cascade models failed to synthesize response._"


# ---------------------------------------------------------------------------
# 2b. JSON Response Parser
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATE = {
    "title": "",
    "one_line_brief": "",
    "relevance_tags": [],
    "relevance_score": 5,
    "global_local": "global",
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
        fallback["one_line_brief"] = raw
        return fallback


def format_telegram_message(parsed: dict, url: str, category: str, scope: str, lens: str = "executive") -> str:
    """
    Render a parsed structured dict into a rich, substantive Telegram HTML message.
    Includes the detailed analytical_insight section generated for the selected Analytical Lens.
    """
    title = parsed.get("title", "")
    brief = parsed.get("one_line_brief", "")
    insight = parsed.get("analytical_insight", "")
    tags = parsed.get("relevance_tags", [])
    score = parsed.get("relevance_score", 5)
    gl = parsed.get("global_local", scope)

    scope_icon = {"global": "🌍", "local": "🇮🇩", "both": "🌐"}.get(gl, "🌍")
    tags_str = " ".join(f"#{t.replace(' ', '')}" for t in tags) if tags else f"#{category}"

    lens_headers = {
        "executive": "🎯 Executive Insight",
        "technical": "📊 Technical & Architecture Breakdown",
        "risk": "⚠️ Risk & Vulnerability Audit",
        "custom": "💬 Custom Query Breakdown"
    }
    header_text = lens_headers.get(lens, "💡 Analytical Breakdown")

    parts = [
        f"{scope_icon} <b>{escape_html(category)} Briefing</b> — <code>[{gl.upper()}]</code>",
        f"<b><a href=\"{url}\">{escape_html(title)}</a></b>" if title else "",
        f"🎯 <b>Brief:</b> {escape_html(brief)}" if brief else "",
    ]

    if insight:
        parts.append(f"<b>{header_text}:</b>\n{escape_html(insight)}")

    parts.append(f"📊 <b>Relevance:</b> {score}/10 | {escape_html(tags_str)}")

    return "\n\n".join(p for p in parts if p)


def is_eligible(parsed: dict, raw_text: str) -> tuple[bool, str]:
    """Check if the extracted text and parsed JSON output are usable."""
    if len(raw_text) < _MIN_ELIGIBLE_CHARS:
        return False, "raw text too short"
        
    brief = parsed.get("one_line_brief", "")
    if not brief or brief.startswith("_\u26a0\ufe0f"):
        return False, "LLM error in one_line_brief"
        
    title = parsed.get("title", "")
    if not title and not brief:
        return False, "JSON parse failed (fallback dict)"
        
    return True, "ok"

# ---------------------------------------------------------------------------
# 3. Orchestrator
# ---------------------------------------------------------------------------

def execute_research_agent(
    trigger: str = "scheduled",
    model_override: str = None,
    custom_urls: list[str] = None,
    target_entries: list[dict] = None,
    lens: str = "executive",
    custom_query: str = "",
) -> None:
    """
    Full research cycle:
      1. Crawl the target URL.
      2. Summarise with local/cloud LLM using selected Analytical Lens.
      3. Write a timestamped briefing to shared_workspace/.
      4. Push a Telegram notification.
      5. Consume manual trigger file if it exists.
    """
    logger.info("--- Research Agent cycle start (lens: %s) ---", lens)
    if model_override:
        logger.info("Using target model override: %s", model_override)

    # Ensure output directory exists
    _WORKSPACE_DIR.mkdir(exist_ok=True)

    aggregated_summaries = []
    sent_count = 0

    # DB: create a run record
    model_used = model_override if model_override else settings.target_model
    run_id = insert_run(trigger=trigger, model_used=model_used)

    # Build target list: pre-filtered entries > custom URL stubs > default TARGET_URLS
    if target_entries:
        entries = target_entries
    elif custom_urls:
        entries = [{"url": u, "category": "Custom", "scope": "global"} for u in custom_urls]
    else:
        entries = TARGET_URLS

    for entry in entries:
        url = entry["url"]
        category = entry["category"]
        scope = entry["scope"]
        try:
            logger.info("Processing target: %s [%s / %s]", url, category, scope)
            
            # 1. Crawl
            raw_text = crawl_and_extract(url)
            if not raw_text:
                logger.warning("Crawl returned no data for %s. Recording failure.", url)
                failed_parsed = {"title": "", "one_line_brief": "", "relevance_tags": [],
                                 "relevance_score": 0, "global_local": scope}
                aggregated_summaries.append({
                    "url": url, "category": category, "scope": scope,
                    "parsed": failed_parsed, "summary": "",
                    "raw_char_count": 0, "was_notified": False,
                })
                if run_id != -1:
                    insert_item(run_id, aggregated_summaries[-1])
                continue

            # Limit text input to save reasoning time and LLM Context Window
            raw_text = raw_text[:4000]

            # 2. Summarise with cascade supporting Analytical Lens choice
            summary_raw = summarize_with_cascade(
                raw_text, url, model_override=model_override, lens=lens, custom_query=custom_query
            )

            # 3. Parse structured JSON from LLM output
            parsed = parse_llm_json_response(summary_raw)
            
            # Store parsed data, URL, category, and scope for briefing & notification
            brief_text = parsed.get("one_line_brief", "")
            insight_text = parsed.get("analytical_insight", "")
            full_summary_str = f"{brief_text}\n\n{insight_text}".strip() if insight_text else brief_text

            aggregated_summaries.append({
                "url": url,
                "category": category,
                "scope": scope,
                "parsed": parsed,
                "summary": full_summary_str if full_summary_str else summary_raw,
                "raw_char_count": len(raw_text),
            })

            # Eligibility check: only notify if extraction + parse produced usable content
            eligible, reason = is_eligible(parsed, raw_text)
            was_notified = False
            if not eligible:
                logger.warning("Ineligible %s — %s. Skipping Telegram notification.", url, reason)
            else:
                # 4. Notify IMMEDIATELY via Telegram with Analytical Lens Breakdown
                tg_msg = format_telegram_message(parsed, url, category, scope, lens=lens)
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

    # 3. Write briefing (Markdown for dashboard & Obsidian vault)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    briefing_filename = f"briefing_{ts}.md"
    briefing_path = _WORKSPACE_DIR / briefing_filename
    
    wib = timezone(timedelta(hours=7), name="WIB")
    today_date = datetime.now(wib).strftime("%Y-%m-%d")
    md_header = f"# 🔬 CRN Research Briefing — {datetime.now(wib).strftime('%Y-%m-%d %H:%M WIB')}\n\n"
    md_blocks = [
        f"**Source:** [{item['url']}]({item['url']})\n\n{item['summary']}" 
        for item in aggregated_summaries
    ]
    md_body = "\n\n---\n\n".join(md_blocks)
    full_md_content = md_header + md_body + "\n"
    
    briefing_path.write_text(full_md_content, encoding="utf-8")
    logger.info("Briefing saved → %s", briefing_path)

    # Vault Auto-Ingestion: Copy note to Obsidian vault /raw/ directory if configured
    if settings.vault_raw_dir:
        try:
            vault_dir = Path(settings.vault_raw_dir)
            vault_dir.mkdir(parents=True, exist_ok=True)
            vault_note_path = vault_dir / f"crn_{ts}.md"
            
            frontmatter = f"---\ntags: [raw, crn, intelligence, briefing]\nsource: crn-agent\nupdated: {today_date}\n---\n\n"
            vault_note_path.write_text(frontmatter + full_md_content, encoding="utf-8")
            logger.info("Vault note synced → %s", vault_note_path)
        except Exception as exc:
            logger.warning("Failed to save vault note to %s: %s", settings.vault_raw_dir, exc)

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
