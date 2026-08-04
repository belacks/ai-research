"""
Crawl Research Node (CRN) — Modular Agent Listener
===================================================
Asynchronous Telegram Listener & JobQueue Dispatcher
"""

import logging
import asyncio
from datetime import timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from app.core.config import settings
from app.core.notifier import escape_html
from app.agent.router import check_user_rate_limit, classify_multi_intent_pipeline
from app.agent.scheduler import (
    check_trigger_file,
    scheduled_news_check,
    scheduled_daily_digest,
    cron_reminder,
    scheduled_bni_email_check,
    scheduled_monthly_statement_reminder,
)
from app.agent.handlers.menu_handlers import cmd_menu, health_command, handle_callback
from app.agent.handlers.news_handlers import briefing_command, news_command, digest_command
from app.agent.handlers.job_handlers import cmd_jobs, handle_job_status_intent, handle_job_eval_intent
from app.agent.handlers.rag_handlers import cmd_ask
from app.agent.job_crawler import run_autonomous_job_scan
from app.agent.news_logic import execute_news_agent
from app.agent.handlers.finance_handlers import (
    handle_finance_status,
    handle_quick_log,
    handle_pdf_document,
    handle_financial_query,
    handle_sync_email,
    handle_export_finance,
)
from app.agent.rag_logic import ask_second_brain
from app.agent.digest_logic import generate_daily_digest
from app.agent.news_logic import execute_news_agent
from app.agent.claw_logic import execute_research_agent
from app.agent.job_crawler import scrape_custom_job_url
from app.agent.job_logic import format_job_digest_html, get_top_job_opportunities, generate_job_pipeline_pdf
from app.agent.prompts import TARGET_URLS

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Global State Lock
IS_CRAWLING = False


def get_is_crawling() -> bool:
    return IS_CRAWLING


def set_is_crawling(value: bool) -> None:
    global IS_CRAWLING
    IS_CRAWLING = value


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture free-text replies and execute single or multi-intent chained pipelines."""
    user_id = update.effective_user.id if update.effective_user else 0
    is_limited, wait_sec = check_user_rate_limit(user_id)
    if is_limited:
        await update.message.reply_text(
            f"⚠️ <b>Rate Limit Triggered</b>\nYou've sent too many requests. Please wait <b>{wait_sec} seconds</b> before sending another message.",
            parse_mode="HTML"
        )
        return

    # Handle awaiting user states
    if context.user_data.get("awaiting_rag_query"):
        context.user_data["awaiting_rag_query"] = False
        rag_q = update.message.text.strip()
        scope = context.user_data.get("rag_scope", "web")
        scope_label = "Personal Vault" if scope == "vault" else "Web Intelligence"
        msg = await update.message.reply_text(f"⏳ Searching [{scope_label}] for: <i>\"{escape_html(rag_q)}\"</i>...", parse_mode="HTML")
        try:
            active_model = context.user_data.get("selected_model", "auto")
            res_html = await asyncio.to_thread(ask_second_brain, rag_q, scope, active_model)
            await msg.edit_text(res_html, parse_mode="HTML")
        except Exception as exc:
            logger.error("RAG search crash: %s", exc)
            await msg.edit_text(f"❌ RAG search crashed: {exc}")
        return

    if context.user_data.get("awaiting_job_url"):
        context.user_data["awaiting_job_url"] = False
        url_text = update.message.text.strip()
        msg = await update.message.reply_text(f"⏳ Fetching & evaluating job fit for: <code>{escape_html(url_text)}</code>...", parse_mode="HTML")
        try:
            eval_res = await scrape_custom_job_url(url_text)
            digest_html = format_job_digest_html([eval_res])
            await msg.edit_text(digest_html, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            logger.error("Job URL evaluation failed: %s", exc)
            await msg.edit_text(f"❌ Job URL evaluation failed: {escape_html(str(exc))}")
        return

    # Multi-Intent Chained Orchestration
    user_txt = update.message.text.strip()
    pipeline = classify_multi_intent_pipeline(user_txt)
    total_steps = len(pipeline)

    logger.info("Multi-Intent Pipeline parsed %d step(s) for query: '%s'", total_steps, user_txt)

    for idx, step in enumerate(pipeline, 1):
        intent = step.get("intent", "RAG_ALL")
        param = step.get("param", user_txt)

        if intent == "UNNECESSARY":
            await update.message.reply_text(
                "ℹ️ <b>No actionable intent recognized.</b>\n\n"
                "Please type a specific question, paste a job URL, update an application status, or open <code>/menu</code>.",
                parse_mode="HTML"
            )
            return

        elif intent in ["FINANCE_QUERY", "FINANCE_STATUS"]:
            if intent == "FINANCE_QUERY" or (param and len(param.strip()) > 0 and not user_txt.lower().startswith("/finance")):
                await handle_financial_query(update, context, user_txt)
            else:
                await handle_finance_status(update, context)

        elif intent == "SYNC_EMAIL":
            await handle_sync_email(update, context)

        elif intent == "EXPORT_FINANCE":
            await handle_export_finance(update, context)

        elif intent == "QUICK_LOG":
            await handle_quick_log(update, context, param)

        elif intent == "JOB_STATUS":
            await handle_job_status_intent(update, context, param)

        elif intent == "JOB_SCAN":
            msg = await update.message.reply_text(f"⏳ [{idx}/{total_steps}] Launching Autonomous Job Scan across target job boards...", parse_mode="HTML")
            try:
                scanned_jobs = await run_autonomous_job_scan()
                if scanned_jobs:
                    digest_html = format_job_digest_html(scanned_jobs)
                    await msg.edit_text(digest_html, parse_mode="HTML", disable_web_page_preview=True)
                else:
                    await msg.edit_text("ℹ️ Autonomous Job Scan completed. No new job postings matched min fit threshold.")
            except Exception as exc:
                logger.error("Job scan failed: %s", exc)
                await msg.edit_text(f"❌ Autonomous Job Scan failed: {escape_html(str(exc))}")

        elif intent == "NEWS_SCAN":
            msg = await update.message.reply_text(f"⏳ [{idx}/{total_steps}] Running Market News Sentiment Scanner...", parse_mode="HTML")
            try:
                cnt = await asyncio.to_thread(execute_news_agent)
                await msg.edit_text(f"✅ Market News Sentiment Scan complete. Processed {cnt} articles.", parse_mode="HTML")
            except Exception as exc:
                logger.error("News scan failed: %s", exc)
                await msg.edit_text(f"❌ Market News Scan failed: {escape_html(str(exc))}")

        elif intent == "JOB_VIEW":
            top_jobs = get_top_job_opportunities(min_fit=50, limit=10, status_filter="ALL")
            digest_html = format_job_digest_html(top_jobs)
            await update.message.reply_text(digest_html, parse_mode="HTML", disable_web_page_preview=True)

        elif intent == "JOB_EXPORT_PDF":
            msg = await update.message.reply_text(f"⏳ [{idx}/{total_steps}] Generating styled PDF Job Pipeline Report...", parse_mode="HTML")
            try:
                pdf_path = await asyncio.to_thread(generate_job_pipeline_pdf)
                await update.message.reply_document(
                    document=open(pdf_path, "rb"),
                    filename="job_pipeline_report.pdf",
                    caption="📄 <b>CRN Job Intelligence Pipeline PDF Report</b>\nSynced to Second Brain vault.",
                    parse_mode="HTML"
                )
                await msg.delete()
            except Exception as exc:
                logger.error("PDF generation error: %s", exc)
                await msg.edit_text(f"❌ PDF generation failed: {exc}")

        elif intent in ["RAG_VAULT", "RAG_WEB", "RAG_ALL"]:
            scope_map = {"RAG_VAULT": "vault", "RAG_WEB": "web", "RAG_ALL": "all"}
            target_scope = scope_map.get(intent, "all")
            scope_label = "Personal Vault" if target_scope == "vault" else ("Web Intelligence" if target_scope == "web" else "Unified Second Brain & Web")
            
            msg = await update.message.reply_text(f"⏳ [{idx}/{total_steps}] Searching [{scope_label}] for: <i>\"{escape_html(param)}\"</i>...", parse_mode="HTML")
            try:
                active_model = context.user_data.get("selected_model", "auto")
                res_html = await asyncio.to_thread(ask_second_brain, param, target_scope, active_model)
                await msg.edit_text(res_html, parse_mode="HTML")
            except Exception as exc:
                logger.error("RAG search crash: %s", exc)
                await msg.edit_text(f"❌ RAG search crashed: {exc}")

        elif intent == "SET_MODEL":
            context.user_data["selected_model"] = param
            await update.message.reply_text(f"✅ [{idx}/{total_steps}] Active model updated to: <b>{param}</b>", parse_mode="HTML")

        elif intent == "SET_LANG":
            context.user_data["digest_lang"] = param
            await update.message.reply_text(f"✅ [{idx}/{total_steps}] Output language updated to: <b>{param}</b>", parse_mode="HTML")

        elif intent == "DAILY_DIGEST":
            msg = await update.message.reply_text(f"☕ [{idx}/{total_steps}] Processing Daily Coffee Digest...", parse_mode="HTML")
            active_model = context.user_data.get("selected_model", "auto")
            lang = context.user_data.get("digest_lang", "id")
            await asyncio.to_thread(generate_daily_digest, active_model, lang, True)
            await msg.edit_text("✅ Daily Morning Coffee Digest complete & saved to Obsidian vault.")


def main() -> None:
    logger.info("Initializing CRN Modular Agentic Listener...")
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("briefing", briefing_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("digest", digest_command))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler(["finance", "exp", "in", "email", "sync_email", "export", "report", "breakdown"], handle_text))
    
    # Dynamically register local plugin commands (e.g. /debt, /debts, /payoff)
    from app.finance.plugin_loader import register_plugin_telegram_commands
    register_plugin_telegram_commands(app)

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf_document))
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    # JobQueue Integrations
    app.job_queue.run_repeating(check_trigger_file, interval=60, first=10)
    app.job_queue.run_repeating(cron_reminder, interval=settings.schedule_interval_hours * 3600, first=settings.schedule_interval_hours * 3600)
    app.job_queue.run_repeating(scheduled_news_check, interval=1800, first=30)
    app.job_queue.run_repeating(scheduled_bni_email_check, interval=1800, first=45)
    
    import datetime as dt
    app.job_queue.run_daily(scheduled_daily_digest, time=dt.time(hour=0, minute=0, tzinfo=timezone.utc))
    app.job_queue.run_daily(scheduled_monthly_statement_reminder, time=dt.time(hour=1, minute=0, tzinfo=timezone.utc))

    logger.info("Listener online and Polling active.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
