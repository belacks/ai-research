"""
CRN Research Node — Job Intelligence Telegram Handlers
======================================================
Handles /jobs command, status updates, PDF report exports, and job URL fit evaluations.
"""

import logging
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app.core.notifier import escape_html
from app.agent.job_logic import (
    get_top_job_opportunities,
    format_job_digest_html,
    generate_job_pipeline_pdf,
    handle_job_status_nl_prompt,
)
from app.agent.job_crawler import run_autonomous_job_scan, scrape_custom_job_url
from app.agent.handlers.menu_handlers import get_job_submenu

logger = logging.getLogger(__name__)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /jobs command to trigger job scan, export PDF, or view job pipeline."""
    if context.args and context.args[0].lower() in ["pdf", "export"]:
        msg = await update.message.reply_text("⏳ Generating styled PDF Job Pipeline Report...", parse_mode="HTML")
        try:
            pdf_path = await asyncio.to_thread(generate_job_pipeline_pdf)
            await update.message.reply_document(
                document=open(pdf_path, "rb"),
                filename="job_pipeline_report.pdf",
                caption="📄 <b>CRN Job Intelligence Pipeline PDF Report</b>\nSynced to your Second Brain vault: <code>raw/crn/jobs/job_pipeline.md</code>.",
                parse_mode="HTML"
            )
            await msg.delete()
        except Exception as exc:
            logger.error("PDF generation error: %s", exc)
            await msg.edit_text(f"❌ PDF generation failed: {exc}")
        return

    if context.args and context.args[0].lower() in ["scan", "run"]:
        msg = await update.message.reply_text("⏳ Running Autonomous Job Scan across target job boards...", parse_mode="HTML")
        try:
            evaluated_jobs = await run_autonomous_job_scan()
            digest_html = format_job_digest_html(evaluated_jobs)
            await msg.edit_text(digest_html, reply_markup=get_job_submenu(), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            logger.error("Job scan failed: %s", exc)
            await msg.edit_text(f"❌ Autonomous Job Scan crashed: {exc}")
        return

    st_filter = "NEW"
    if context.args:
        arg = context.args[0].lower()
        if arg in ["applied", "app"]:
            st_filter = "APPLIED"
        elif arg in ["archived", "arc"]:
            st_filter = "ARCHIVED"
        elif arg in ["all"]:
            st_filter = "ALL"

    top_jobs = get_top_job_opportunities(min_fit=50, limit=10, status_filter=st_filter)
    digest_html = format_job_digest_html(top_jobs)
    await update.message.reply_text(digest_html, reply_markup=get_job_submenu(), parse_mode="HTML", disable_web_page_preview=True)


async def handle_job_status_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str) -> None:
    """Handle natural language job status update pipeline step."""
    user_txt = update.message.text.strip() if update.message else ""
    msg = await update.message.reply_text("⏳ Processing job status update...", parse_mode="HTML")
    res_text = await asyncio.to_thread(handle_job_status_nl_prompt, param if param else user_txt)
    await msg.edit_text(res_text, parse_mode="HTML")


async def handle_job_eval_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str) -> None:
    """Handle job URL evaluation pipeline step."""
    urls = [u.strip() for u in param.replace(";", ",").split() if u.strip().startswith("http")]
    target_urls = urls if urls else [param.strip()]
    for u in target_urls:
        msg = await update.message.reply_text(f"⏳ Evaluating job fit for: <code>{escape_html(u)}</code>...", parse_mode="HTML")
        try:
            eval_res = await scrape_custom_job_url(u)
            digest_html = format_job_digest_html([eval_res])
            await msg.edit_text(digest_html, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            logger.error("Job evaluation failed for %s: %s", u, exc)
            await msg.edit_text(f"❌ Job evaluation failed: {escape_html(str(exc))}")
