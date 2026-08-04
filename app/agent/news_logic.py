"""
CRN — Trading & Market Sentiment Agent
======================================
Sync-based orchestrator: Scrape Headlines → LLM Sentiment Analysis (Ollama / Gemini) → Telegram Alert
Uses a local SQLite table to track processed articles to prevent duplicate notifications.
"""

import copy
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from app.agent.claw_logic import summarize_with_cascade, parse_llm_json_response
from app.core.config import settings
from app.core.notifier import send_telegram_alert, escape_html

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths and Database Settings
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_DB_PATH = _WORKSPACE_DIR / "crn_intelligence.db"

# ---------------------------------------------------------------------------
# Prompt for Financial & Commodity Sentiment Analysis
# ---------------------------------------------------------------------------
_SENTIMENT_SYSTEM_PROMPT = """You are a senior financial analyst focusing on Indonesian markets (IDX stocks) and global gold (XAUUSD).
Analyze the following market headline and article snippet.

Extract and output ONLY valid JSON matching this exact structure:
{
  "ticker": "XAUUSD, BBCA, TLKM, GOTO, or GENERAL",
  "sentiment": "bullish, bearish, or neutral",
  "confidence": 0.85,
  "one_line_reason": "Concise 1-sentence financial impact"
}

Article:
"""

# ---------------------------------------------------------------------------
# Deduplication SQLite Helpers
# ---------------------------------------------------------------------------
def _init_news_db() -> None:
    """Ensure the processed_news table exists in the CRN database."""
    try:
        _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS processed_news (
                    url           TEXT PRIMARY KEY,
                    title         TEXT NOT NULL,
                    sentiment     TEXT,
                    confidence    REAL,
                    ticker        TEXT,
                    processed_at  TEXT NOT NULL
                )"""
            )
    except Exception as exc:
        logger.error("Failed to initialize processed_news table: %s", exc)

def _is_processed(url: str) -> bool:
    """Check if the news URL has already been processed."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cursor = conn.execute("SELECT 1 FROM processed_news WHERE url = ?", (url,))
            return cursor.fetchone() is not None
    except Exception as exc:
        logger.error("Failed to check duplicate URL %s: %s", url, exc)
        return False

def _save_processed_news(url: str, title: str, sentiment: str, confidence: float, ticker: str) -> None:
    """Record processed news to avoid duplicate alerts and log history."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO processed_news 
                   (url, title, sentiment, confidence, ticker, processed_at) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (url, title, sentiment, confidence, ticker, datetime.now(timezone.utc).isoformat())
            )
    except Exception as exc:
        logger.error("Failed to save processed news entry: %s", exc)

# ---------------------------------------------------------------------------
# LLM Sentiment Analyzer (Ollama First, then Cloud Cascade)
# ---------------------------------------------------------------------------
def analyze_sentiment_with_llm(title: str, url: str) -> dict:
    """
    Run sentiment analysis using LLM model cascade (Ollama local first, then cloud API).
    """
    content = f"Title: {title}\nSource URL: {url}"
    
    # Send content through LLM cascade
    raw_response = summarize_with_cascade(content, url)
    parsed = parse_llm_json_response(raw_response)
    
    ticker = parsed.get("ticker", "GENERAL").upper()
    sentiment = str(parsed.get("sentiment", "neutral")).lower()
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (ValueError, TypeError):
        confidence = 0.5
        
    reason = parsed.get("one_line_reason", "")
    if not reason:
        reason = parsed.get("one_line_brief", title)

    return {
        "ticker": ticker,
        "sentiment": sentiment,
        "confidence": confidence,
        "reason": reason
    }

# ---------------------------------------------------------------------------
# Core Scraper and Analyzer Logic
# ---------------------------------------------------------------------------
def execute_news_agent(min_confidence: float = None) -> int:
    """
    Perform a complete news sentiment check:
      1. Scrape latest market headlines from CNBC Indonesia.
      2. Deduplicate articles using local SQLite records.
      3. Run LLM Sentiment Analysis (Ollama / Cloud Cascade).
      4. Dispatch Telegram push notifications for actionable signals.
    """
    if min_confidence is None:
        min_confidence = settings.news_min_confidence

    logger.info("Starting LLM News Sentiment Agent execution cycle...")
    _init_news_db()
    
    # 1. Sync Playwright Scrape
    playwright = None
    browser = None
    scraped_items = []
    
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        url = "https://www.cnbcindonesia.com/market"
        logger.info("Fetching market headlines from %s", url)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        articles = page.locator("article").all()
        logger.info("Found %d raw article blocks.", len(articles))
        
        for article in articles[:10]:
            title_node = article.locator("h2")
            link_node = article.locator("a")
            
            if title_node.count() > 0 and link_node.count() > 0:
                title = title_node.first.inner_text().strip()
                link = link_node.first.get_attribute("href")
                
                if link and not _is_processed(link):
                    scraped_items.append({"title": title, "link": link})
                    
        logger.info("%d new unprocessed articles identified.", len(scraped_items))
        
    except Exception as exc:
        logger.error("Failed to run Playwright news scraper: %s", exc)
        return 0
    finally:
        if browser:
            browser.close()
        if playwright:
            playwright.stop()
            
    if not scraped_items:
        logger.info("No new articles found. Exiting cycle.")
        return 0

    signals_sent = 0
    analyzed_items = []

    # 2. Model Inference & Notification Loop
    for item in scraped_items:
        title = item["title"]
        url = item["link"]
        
        # Analyze via LLM
        result = analyze_sentiment_with_llm(title, url)
        ticker = result["ticker"]
        sentiment = result["sentiment"]
        confidence = result["confidence"]
        reason = result["reason"]
        
        # Save state to SQLite
        _save_processed_news(url, title, sentiment=sentiment, confidence=confidence, ticker=ticker)
        analyzed_items.append({
            "title": title,
            "url": url,
            "ticker": ticker,
            "sentiment": sentiment,
            "confidence": confidence,
            "reason": reason
        })
        
        logger.info("Headline: '%s' -> Ticker: %s | Sentiment: %s (%.2f%%)", 
                    title, ticker, sentiment, confidence * 100)
                    
        # Signal evaluation and Alert dispatch
        if confidence >= min_confidence and sentiment in ["bullish", "bearish", "positive", "negative"]:
            is_bullish = sentiment in ["bullish", "positive"]
            trade_action = "🟢 BULLISH (BUY)" if is_bullish else "🔴 BEARISH (SELL)"
            
            html_message = (
                f"<b>📊 LLM Market Sentiment Signal</b>\n\n"
                f"<b>Asset:</b> <code>{escape_html(ticker)}</code>\n"
                f"<b>Action:</b> {trade_action}\n"
                f"<b>Confidence:</b> {confidence:.2%}\n\n"
                f"<b>Insight:</b> {escape_html(reason)}\n\n"
                f"<i>Headline:</i> \"{escape_html(title)}\"\n"
                f"🔗 <a href='{url}'>Read Source</a>"
            )
            
            success = send_telegram_alert(html_message)
            if success:
                signals_sent += 1
                logger.info("Signal notification sent successfully.")
            else:
                logger.warning("Failed to send Signal notification.")

    # 3. Write Markdown Briefing & Vault Auto-Ingestion
    if analyzed_items:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        today_date = datetime.now().strftime("%Y-%m-%d")

        md_header = f"# 📈 CRN Market Sentiment Briefing — {datetime.now().strftime('%Y-%m-%d %H:%M WIB')}\n\n"
        md_blocks = [
            f"### [{item['title']}]({item['url']})\n"
            f"- **Asset Ticker:** `{item['ticker']}`\n"
            f"- **Sentiment:** `{item['sentiment'].upper()}` ({item['confidence']:.1%})\n"
            f"- **Insight:** {item['reason']}\n"
            for item in analyzed_items
        ]
        full_md = md_header + "\n---\n\n".join(md_blocks) + "\n"

        # Save to shared_workspace
        briefing_path = _WORKSPACE_DIR / f"news_briefing_{ts}.md"
        briefing_path.write_text(full_md, encoding="utf-8")
        logger.info("News briefing saved → %s", briefing_path)

        # Sync to Obsidian vault
        if settings.vault_raw_dir:
            try:
                vault_dir = Path(settings.vault_raw_dir)
                vault_dir.mkdir(parents=True, exist_ok=True)
                vault_note_path = vault_dir / f"crn_news_{ts}.md"
                frontmatter = f"---\ntags: [raw, crn, market, sentiment]\nsource: crn-news-agent\nupdated: {today_date}\n---\n\n"
                vault_note_path.write_text(frontmatter + full_md, encoding="utf-8")
                logger.info("News vault note synced → %s", vault_note_path)
            except Exception as exc:
                logger.warning("Failed to save news vault note: %s", exc)

    return signals_sent

