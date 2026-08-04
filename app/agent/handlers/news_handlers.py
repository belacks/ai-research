"""
CRN Research Node — News & Deep Crawl Telegram Handlers
========================================================
Handles /briefing, /news, /digest commands and Excel/SQLite database exports.
"""

import logging
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app.agent.claw_logic import execute_research_agent
from app.agent.news_logic import execute_news_agent
from app.agent.digest_logic import generate_daily_digest
from app.agent.prompts import TARGET_URLS
from app.agent.scheduler import _write_last_run

logger = logging.getLogger(__name__)


async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run a specific scope-filtered deep crawl."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    
    if not context.args or context.args[0].lower() not in ["local", "global", "all"]:
        await update.message.reply_text(
            "ℹ️ Usage: /briefing [local|global|all]\n"
            "Example: /briefing local — runs only Indonesia/SEA sources"
        )
        return
        
    if get_is_crawling():
        await update.message.reply_text("⏳ Agent is already running. Please wait for the current cycle to finish.")
        return
        
    scope_arg = context.args[0].lower()
    
    if scope_arg == "local":
        filtered_entries = [e for e in TARGET_URLS if e["scope"] == "local"]
    elif scope_arg == "global":
        filtered_entries = [e for e in TARGET_URLS if e["scope"] == "global"]
    else:
        filtered_entries = list(TARGET_URLS)
        
    set_is_crawling(True)
    msg = await update.message.reply_text(f"🔍 Starting {scope_arg} briefing — {len(filtered_entries)} sources queued.")
    
    try:
        trigger_value = f"briefing_{scope_arg}"
        await asyncio.to_thread(execute_research_agent, trigger_value, None, None, filtered_entries)
        _write_last_run()
        await msg.edit_text(f"✅ {scope_arg.capitalize()} briefing finished safely in the background.")
    except Exception as exc:
        logger.error("Crawler crash: %s", exc)
        await msg.edit_text(f"❌ {scope_arg.capitalize()} briefing crashed: {exc}")
    finally:
        set_is_crawling(False)


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the live news sentiment scanner manually."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    if get_is_crawling():
        await update.message.reply_text("⏳ Agent is busy. Please wait for the current cycle to finish.")
        return

    set_is_crawling(True)
    msg = await update.message.reply_text("📈 Launching News Sentiment Agent...")
    try:
        signals = await asyncio.to_thread(execute_news_agent)
        await msg.edit_text(f"✅ News Sentiment Agent complete. Dispatched {signals} signals.")
    except Exception as exc:
        logger.error("News agent crash: %s", exc)
        await msg.edit_text(f"❌ News Sentiment Agent crashed: {exc}")
    finally:
        set_is_crawling(False)


async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate or retrieve the Daily Morning Coffee Digest."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    if get_is_crawling():
        await update.message.reply_text("⏳ Agent is busy. Please wait for the current cycle to finish.")
        return

    set_is_crawling(True)
    force_flag = bool(context.args and context.args[0].lower() in ["force", "refresh"])
    status_text = "🔄 Force re-synthesizing Daily Coffee Digest..." if force_flag else "☕ Processing Daily Coffee Digest..."
    msg = await update.message.reply_text(status_text)
    try:
        active_model = context.user_data.get("selected_model", "auto")
        lang = context.user_data.get("digest_lang", "id")
        await asyncio.to_thread(generate_daily_digest, active_model, lang, force_flag)
        await msg.edit_text("✅ Daily Morning Coffee Digest complete & saved to Obsidian vault.")
    except Exception as exc:
        logger.error("Digest generation crash: %s", exc)
        await msg.edit_text(f"❌ Digest crashed: {exc}")
    finally:
        set_is_crawling(False)
