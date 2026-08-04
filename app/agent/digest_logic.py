"""
app/agent/digest_logic.py
==========================
Morning Executive Coffee Digest Generator for CRN.

Queries past 24 hours of market news (processed_news) and research briefings (briefing_items),
synthesizes an Executive Morning Digest using the 6-tier LLM cascade,
sends a Telegram summary, and writes a vault note into
raw/crn_daily_digest_<YYYYMMDD>.md.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.claw_logic import summarize_with_cascade
from app.core.config import settings
from app.core.notifier import send_telegram_alert

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_DB_PATH = _WORKSPACE_DIR / "crn_intelligence.db"


def generate_daily_digest(model_override: str = None, language: str = "id", force: bool = False) -> str:
    """
    Query past 24h SQLite intelligence items, run 6-tier LLM cascade synthesis,
    write Obsidian note to raw/crn/crn_daily_digest_<date>.md, and send Telegram digest.
    If today's note already exists and force is False, returns the cached digest.
    """
    logger.info("Starting Daily Morning Coffee Digest generation (lang=%s, force=%s)...", language, force)

    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")

    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    workspace_cache_file = _WORKSPACE_DIR / f"daily_digest_{date_str}_{language}.md"

    vault_file = None
    if settings.vault_raw_dir:
        try:
            vault_path = Path(settings.vault_raw_dir)
            vault_path.mkdir(parents=True, exist_ok=True)
            vault_file = vault_path / f"crn_daily_digest_{date_str}.md"
        except Exception as exc:
            logger.warning("Vault raw dir %s not writable: %s. Defaulting vault sync to workspace.", settings.vault_raw_dir, exc)
            vault_file = None

    # ---------------------------------------------------------------------------
    # Same-Day Cache Check: Avoid redundant LLM calls and vault file spam
    # ---------------------------------------------------------------------------
    if not force and workspace_cache_file.exists():
        logger.info("Today's Daily Morning Coffee Digest already exists (%s). Returning cached digest.", workspace_cache_file.name)
        cached_digest = workspace_cache_file.read_text(encoding="utf-8")

        from app.core.notifier import send_telegram_alert, md_to_telegram_html
        header = f"☕ **CRN Morning Coffee Digest — {date_str} (Cached)**\n\n"
        send_telegram_alert(md_to_telegram_html(header + cached_digest))
        return cached_digest

    if not _DB_PATH.exists():
        logger.warning("Database file %s does not exist. Cannot generate digest.", _DB_PATH)
        return "❌ Database file does not exist yet."

    cutoff_24h = (now_utc - timedelta(hours=24)).isoformat()
    news_items = []
    briefing_items = []

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        
        # 1. Fetch news sentiment records from past 24h (or latest 20 fallback)
        cursor.execute(
            """SELECT url, title, sentiment, confidence, ticker, processed_at
               FROM processed_news
               WHERE processed_at >= ?
               ORDER BY processed_at DESC""",
            (cutoff_24h,)
        )
        news_items = cursor.fetchall()

        if not news_items:
            cursor.execute(
                """SELECT url, title, sentiment, confidence, ticker, processed_at
                   FROM processed_news
                   ORDER BY processed_at DESC LIMIT 20"""
            )
            news_items = cursor.fetchall()

        # 2. Fetch deep crawl briefing items from past 24h (or latest 10 fallback)
        try:
            cursor.execute(
                """SELECT title, url, summary, key_insights, relevance_score, category
                   FROM briefing_items
                   WHERE crawled_at >= ?
                   ORDER BY crawled_at DESC""",
                (cutoff_24h,)
            )
            briefing_items = cursor.fetchall()

            if not briefing_items:
                cursor.execute(
                    """SELECT title, url, summary, key_insights, relevance_score, category
                       FROM briefing_items
                       ORDER BY crawled_at DESC LIMIT 10"""
                )
                briefing_items = cursor.fetchall()
        except sqlite3.OperationalError:
            logger.info("briefing_items table empty or missing.")

    if not news_items and not briefing_items:
        logger.info("No intelligence items found in database for digest.")
        return "ℹ️ No market news or briefing data found in database."

    # Format raw text context for LLM synthesis
    context_lines = [f"=== DAILY INTELLIGENCE SNAPSHOT ({date_str}) ==="]
    
    if news_items:
        context_lines.append("\n--- MARKET NEWS SENTIMENT ITEMS ---")
        for item in news_items:
            url, title, sentiment, confidence, ticker, proc_at = item
            conf_pct = f"{confidence:.0%}" if isinstance(confidence, float) else str(confidence)
            context_lines.append(f"• [{ticker}] [{sentiment.upper()} {conf_pct}] {title} ({url})")

    if briefing_items:
        context_lines.append("\n--- DEEP RESEARCH BRIEFINGS ---")
        for b in briefing_items:
            btitle, burl, bsummary, bkey, bscore, bcat = b
            context_lines.append(f"• [{bcat}] {btitle} (Score: {bscore})\n  Summary: {bsummary}\n  Insights: {bkey}")

    raw_context = "\n".join(context_lines)

    lang_instruction = "in Indonesian" if language == "id" else "in English"

    # Prompt for Daily Digest Synthesis
    prompt = f"""You are an AI Executive Intelligence Lead. Below is a raw snapshot of market news, ticker sentiment, and research briefings gathered by CRN over the past 24 hours.

Synthesize a high-value, direct, executive Morning Coffee Digest {lang_instruction}.

Use this exact section structure (casual but technically substantive tone, no em-dashes):

☕ **Executive Summary**
(2-3 sentences summarizing overall market atmosphere, IDX trends, and Gold/Macro direction)

📈 **Ticker & Market Snapshot**
(Bullet points grouping key tickers like XAUUSD, BBNI, GENERAL, etc., with their sentiment and impact)

💡 **Top 3 Strategic Insights**
1. [Key Insight 1]
2. [Key Insight 2]
3. [Key Insight 3]

🎯 **Action Plan / Watchlist for Today**
- [Concrete takeaway 1]
- [Concrete takeaway 2]

Raw Intelligence Data:
{raw_context[:8000]}
"""

    logger.info("Synthesizing Executive Morning Digest via 6-tier LLM cascade...")
    digest_text = summarize_with_cascade(prompt, "daily_digest", model_override=model_override)

    # Collect unique source references
    sources_set = set()
    sources_list = []
    for item in news_items:
        url, title = item[0], item[1]
        if url and url not in sources_set:
            sources_set.add(url)
            sources_list.append((title, url))

    for b in briefing_items:
        btitle, burl = b[0], b[1]
        if burl and burl not in sources_set:
            sources_set.add(burl)
            sources_list.append((btitle, burl))

    if sources_list:
        digest_text += "\n\n📌 **Source References**\n"
        for title, url in sources_list[:8]:  # Top 8 unique sources
            clean_title = title.strip() if title else url
            digest_text += f"- [{clean_title}]({url})\n"

    # Save to Streamlit workspace (canonical per date & language)
    workspace_cache_file.write_text(digest_text, encoding="utf-8")

    # Save / Append to Obsidian Vault (single canonical bilingual note per date)
    if vault_file:
        try:
            lang_title = "Bahasa Indonesia" if language == "id" else "English"
            section_content = f"## ☕ Morning Coffee Digest ({lang_title})\n\n{digest_text}"

            if vault_file.exists():
                existing_content = vault_file.read_text(encoding="utf-8")
                if f"({lang_title})" in existing_content:
                    logger.info("Obsidian vault note already contains %s section. Skipping vault edit.", lang_title)
                else:
                    updated_content = existing_content.rstrip() + f"\n\n---\n\n{section_content}\n"
                    vault_file.write_text(updated_content, encoding="utf-8")
                    logger.info("Appended %s section to Obsidian Vault note → %s", lang_title, vault_file)
            else:
                note_content = f"""---
tags: [raw, crn, intelligence, digest]
updated: {date_str}
source: CRN Daily Coffee Digest
---

# ☕ CRN Morning Coffee Digest — {date_str}

{section_content}

---
*Generated by Ceros Research Node (CRN) Daily Executive Pipeline.*
"""
                vault_file.write_text(note_content, encoding="utf-8")
                logger.info("Saved new Morning Digest note to Obsidian Vault → %s", vault_file)
        except Exception as exc:
            logger.warning("Failed writing to vault note %s: %s", vault_file, exc)

    # Dispatch to Telegram using Telegram HTML parser
    from app.core.notifier import send_telegram_alert, md_to_telegram_html
    header = f"☕ **CRN Morning Coffee Digest — {date_str}**\n\n"
    telegram_html = md_to_telegram_html(header + digest_text)
    send_telegram_alert(telegram_html)

    return digest_text
