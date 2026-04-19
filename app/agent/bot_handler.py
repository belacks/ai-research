"""
Ceros Research Node — Modular Agent Listener
===================================================
A Telegram Bot API listener using python-telegram-bot to orchestrate
interactive modules, inline keyboards, and scheduled runs.
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from app.core.config import settings
from app.core.notifier import escape_html
from app.agent.claw_logic import execute_research_agent
from app.agent.prompts import TARGET_URLS

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Globals & State Locks
# ---------------------------------------------------------------------------
IS_CRAWLING = False

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "shared_workspace"
_TRIGGER_FILE = _WORKSPACE_DIR / "trigger.txt"
_LAST_RUN_FILE = _WORKSPACE_DIR / "last_run.json"


def _write_last_run() -> None:
    """Persist the current UTC timestamp to last_run.json."""
    payload = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
    }
    _LAST_RUN_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Updated last_run.json → %s", payload["last_run_utc"])


# ---------------------------------------------------------------------------
# Interactive Menu UI
# ---------------------------------------------------------------------------
def get_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🕸️ Run Deep Crawl", callback_data="run_deep_crawl")],
        [InlineKeyboardButton("⏸️ Skip Scheduled Run", callback_data="skip_run")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_crawl_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌐 Use Default Websites", callback_data="crawl_default")],
        [InlineKeyboardButton("🔗 Enter Custom URLs", callback_data="crawl_custom")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the Interactive Menu directly when user types /menu."""
    await update.message.reply_text(
        "<b>Ceros Central Command</b>\nStandby. Select a modular tool:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )

# ---------------------------------------------------------------------------
# Scope-filtered CLI Commands
# ---------------------------------------------------------------------------
async def briefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run a specific scope-filtered deep crawl."""
    global IS_CRAWLING
    
    if not context.args or context.args[0].lower() not in ["local", "global", "all"]:
        await update.message.reply_text(
            "ℹ️ Usage: /briefing [local|global|all]\n"
            "Example: /briefing local — runs only Indonesia/SEA sources"
        )
        return
        
    if IS_CRAWLING:
        await update.message.reply_text("⏳ Agent is already running. Please wait for the current cycle to finish.")
        return
        
    scope_arg = context.args[0].lower()
    
    if scope_arg == "local":
        filtered_entries = [e for e in TARGET_URLS if e["scope"] == "local"]
    elif scope_arg == "global":
        filtered_entries = [e for e in TARGET_URLS if e["scope"] == "global"]
    else:  # all
        filtered_entries = list(TARGET_URLS)
        
    IS_CRAWLING = True
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
        IS_CRAWLING = False


# ---------------------------------------------------------------------------
# Button Router
# ---------------------------------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches button clicks and routes them to the correct Async Python Function."""
    global IS_CRAWLING
    query = update.callback_query
    await query.answer()

    if query.data == "skip_run":
        await query.edit_message_text(text="✅ Scheduled run skipped. System returning to standby.")
        return

    if query.data == "run_deep_crawl":
        await query.edit_message_text(text="Select deep crawl source:", reply_markup=get_crawl_submenu())
        return

    if query.data == "back_to_menu":
        await query.edit_message_text(text="Standby. Select a modular tool:", reply_markup=get_main_menu())
        return

    if query.data == "crawl_custom":
        if IS_CRAWLING:
            await query.edit_message_text(text="⏳ Crawler is already currently running. Please wait.")
            return
        context.user_data["awaiting_custom_url"] = True
        await query.edit_message_text(text="Please reply to this message with a comma-separated list of URLs to crawl (e.g., https://news.ycombinator.com).")
        return

    if query.data == "crawl_default":
        if IS_CRAWLING:
            await query.edit_message_text(text="⏳ Crawler is already currently running. Please wait.")
            return

        IS_CRAWLING = True
        await query.edit_message_text(text="⏳ Spacing up Deep Crawl module on Default Targets. This might take a few minutes...")

        try:
            # We use to_thread to offload the heavy synchronous web-crawling off the PTB async loop
            await asyncio.to_thread(execute_research_agent, "manual", None)
            _write_last_run()
            await query.edit_message_text(text="✅ Deep Crawl Module finished safely in the background.")
        except Exception as exc:
            logger.error("Crawler crash: %s", exc)
            await query.edit_message_text(text=f"❌ Deep Crawl Module crashed: {exc}")
        finally:
            IS_CRAWLING = False


# ---------------------------------------------------------------------------
# Text Router
# ---------------------------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture free-text replies when expecting custom URLs."""
    global IS_CRAWLING
    if not context.user_data.get("awaiting_custom_url"):
        return

    context.user_data["awaiting_custom_url"] = False
    raw_text = update.message.text
    
    import re
    urls = re.findall(r'https?://[^\s,]+', raw_text)
    if not urls:
        await update.message.reply_text("❌ No valid URLs (starting with http:// or https://) detected. Operation cancelled.")
        return

    if IS_CRAWLING:
        await update.message.reply_text("⏳ Crawler is already currently running. Please wait.")
        return

    IS_CRAWLING = True
    msg = await update.message.reply_text("⏳ Spacing up Deep Crawl module for custom URLs. This might take a few minutes...")

    try:
        await asyncio.to_thread(execute_research_agent, "custom", None, urls)
        _write_last_run()
        await msg.edit_text("✅ Custom Deep Crawl Module finished safely in the background.")
    except Exception as exc:
        logger.error("Crawler crash: %s", exc)
        await msg.edit_text(f"❌ Custom Deep Crawl Module crashed: {exc}")
    finally:
        IS_CRAWLING = False


# ---------------------------------------------------------------------------
# Background Jobs
# ---------------------------------------------------------------------------
async def check_trigger_file(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB background job: runs every 60s looking for Streamlit Dashboard trigger."""
    global IS_CRAWLING
    if _TRIGGER_FILE.exists():
        if IS_CRAWLING:
            logger.warning("Trigger active, but agent is busy. Waiting for next cycle.")
            return

        try:
            model_override = _TRIGGER_FILE.read_text(encoding="utf-8").strip()
            if not model_override:
                model_override = None
        except Exception:
            model_override = None

        _TRIGGER_FILE.unlink(missing_ok=True)
        logger.info("🚀 Dashboard trigger file consumed. Starting background job with model=%s", model_override)

        IS_CRAWLING = True
        try:
            await context.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=f"🚀 Manual Command Center trigger consumed. Starting Engine (Model: {model_override or settings.target_model})."
            )
            await asyncio.to_thread(execute_research_agent, "manual", model_override)
            _write_last_run()
        except Exception as exc:
            logger.error(exc)
            error_msg = escape_html(str(exc))[:200]
            try:
                await context.bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text=f"⚠️ <b>CRN Scheduled Crawl Failed</b>\nError: {type(exc).__name__}: <code>{error_msg}</code>",
                    parse_mode="HTML"
                )
            except Exception as notify_exc:
                logger.error("Failed to push crash alert to Telegram: %s", notify_exc)
        finally:
            IS_CRAWLING = False


async def cron_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB background job: fires occasionally asking the user for authorization."""
    await context.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=f"⏰ <b>Scheduled Run Routine</b>\nTime for a routine crawl ({settings.schedule_interval_hours}h interval). Action required:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )


# ---------------------------------------------------------------------------
# Boot Sequence
# ---------------------------------------------------------------------------
def main() -> None:
    logger.info("Initializing Ceros Modular Agentic Listener...")
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("briefing", briefing_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # JobQueue Integration
    app.job_queue.run_repeating(check_trigger_file, interval=60, first=10)
    
    # Run the cron reminder every N hours
    # First argument is callback, second is interval in seconds
    app.job_queue.run_repeating(cron_reminder, interval=settings.schedule_interval_hours * 3600, first=settings.schedule_interval_hours * 3600)

    logger.info("Listener online and Polling active.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
