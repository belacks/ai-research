"""
CRN Research Node — Background JobQueue Scheduler
==================================================
Houses all scheduled cron routines, daily digests, trigger file listeners, and periodic checks.
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telegram.ext import ContextTypes

from app.core.config import settings
from app.core.notifier import escape_html
from app.agent.claw_logic import execute_research_agent
from app.agent.news_logic import execute_news_agent
from app.agent.digest_logic import generate_daily_digest

logger = logging.getLogger(__name__)

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


async def check_trigger_file(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB background job: runs every 60s looking for Streamlit Dashboard trigger."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    if _TRIGGER_FILE.exists():
        if get_is_crawling():
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

        set_is_crawling(True)
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
            set_is_crawling(False)


async def scheduled_news_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB background job: silently scans live news sentiment every 30 minutes."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    if get_is_crawling():
        logger.info("Scheduled news sentiment check skipped: Agent busy.")
        return

    set_is_crawling(True)
    try:
        await asyncio.to_thread(execute_news_agent)
    except Exception as exc:
        logger.error("Failed running scheduled news sentiment check: %s", exc)
    finally:
        set_is_crawling(False)


async def scheduled_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled 07:00 AM WIB Job: Runs News Sentiment + Deep Crawl sweep, then generates Daily Morning Digest."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    if get_is_crawling():
        logger.info("Scheduled morning digest skipped: Agent busy.")
        return

    set_is_crawling(True)
    try:
        # Step 1: Run News Sentiment Scan
        logger.info("Executing 07:00 AM News Sentiment Scan...")
        await asyncio.to_thread(execute_news_agent)

        # Step 2: Run Deep Crawl Briefing Sweep
        logger.info("Executing 07:00 AM Deep Crawl Sweep...")
        await asyncio.to_thread(execute_research_agent, "morning_digest_cron")

        # Step 3: Synthesize Morning Coffee Digest from updated DB
        logger.info("Synthesizing Daily Morning Coffee Digest...")
        await asyncio.to_thread(generate_daily_digest)
        _write_last_run()
    except Exception as exc:
        logger.error("Scheduled morning digest failed: %s", exc)
    finally:
        set_is_crawling(False)


async def cron_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB background job: fires occasionally asking the user for authorization."""
    from app.agent.handlers.menu_handlers import get_main_menu
    await context.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=f"⏰ <b>Scheduled Run Routine</b>\nTime for a routine crawl ({settings.schedule_interval_hours}h interval). Action required:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )


async def scheduled_bni_email_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background task running every 30 mins: Auto-fetches BNI Wondr transaction emails from Gmail IMAP."""
    if not settings.gmail_user or not settings.gmail_app_password:
        return
    try:
        from app.finance.parsers import fetch_bni_transactions_imap
        count = await asyncio.to_thread(fetch_bni_transactions_imap, settings.gmail_user, settings.gmail_app_password)
        if count > 0:
            logger.info("Auto-ingested %d BNI transaction emails into DB.", count)
    except Exception as exc:
        logger.error("Scheduled BNI email check error: %s", exc)


async def scheduled_monthly_statement_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled 1st of Month Reminder at 08:00 AM WIB (01:00 UTC): Audit & E-Statement push notification."""
    import datetime as dt
    now_utc = dt.datetime.now(dt.timezone.utc)
    if now_utc.day != 1:
        return
        
    res = (
        f"📅 <b>CRN Monthly Financial Audit & E-Statement Reminder</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Today is the 1st of the month!\n\n"
        f"1. <b>Monthly Allowance</b>: Time to request your Rp 1.5M monthly allowance.\n"
        f"2. <b>BNI E-Statement</b>: Check your Gmail inbox for your official BNI E-Statement PDF.\n"
        f"3. <b>Upload Statement</b>: Send your `.pdf` statement directly to this chat to update your monthly balance.\n\n"
        f"Run <code>/finance</code> anytime to view your safe daily spend budget."
    )
    await context.bot.send_message(chat_id=settings.telegram_chat_id, text=res, parse_mode="HTML")
