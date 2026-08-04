"""
CRN Research Node — Menu UI & Callback Telegram Handlers
=========================================================
Handles /menu, /health, interactive inline keyboards, and button click callbacks.
"""

import logging
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.core.notifier import escape_html
from app.core.database import get_source_health
from app.agent.claw_logic import execute_research_agent
from app.agent.news_logic import execute_news_agent
from app.agent.digest_logic import generate_daily_digest
from app.agent.job_logic import (
    get_top_job_opportunities,
    format_job_digest_html,
    generate_job_pipeline_pdf,
)
from app.agent.job_crawler import run_autonomous_job_scan
from app.agent.prompts import TARGET_URLS
from app.agent.router import _PROJECT_ROOT

logger = logging.getLogger(__name__)


def get_main_menu(selected_model: str = "Auto-Cascade", lang_label: str = "🇮🇩 Bahasa") -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("☕ Morning Coffee Digest", callback_data="run_daily_digest")],
        [InlineKeyboardButton("🔍 Ask Second Brain (RAG)", callback_data="ask_rag_prompt")],
        [InlineKeyboardButton("💼 Job Intelligence Engine", callback_data="open_job_menu")],
        [InlineKeyboardButton("🕸️ Deep Crawl Module", callback_data="open_crawl_menu")],
        [InlineKeyboardButton("📈 News Sentiment Module", callback_data="open_news_menu")],
        [InlineKeyboardButton(f"⚙️ Model: {selected_model}", callback_data="open_model_menu")],
        [InlineKeyboardButton(f"🌐 Lang: {lang_label}", callback_data="toggle_digest_lang")],
        [InlineKeyboardButton("⏸️ Skip Scheduled Run", callback_data="skip_run")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_job_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚀 Run Autonomous Job Scan", callback_data="run_job_scan")],
        [InlineKeyboardButton("📊 View Matched Pipeline", callback_data="view_job_pipeline")],
        [InlineKeyboardButton("📄 Export PDF Job Report", callback_data="export_jobs_pdf")],
        [InlineKeyboardButton("🔗 Evaluate Custom Job Link", callback_data="eval_job_url_prompt")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_crawl_submenu(selected_lens: str = "🎯 Executive") -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌐 Default Targets", callback_data="crawl_default")],
        [InlineKeyboardButton("🔗 Custom URLs", callback_data="crawl_custom")],
        [InlineKeyboardButton("🇮🇩 Local Sources", callback_data="crawl_local")],
        [InlineKeyboardButton("🌍 Global Sources", callback_data="crawl_global")],
        [InlineKeyboardButton(f"🔍 Lens: {selected_lens}", callback_data="open_lens_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_lens_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🎯 Executive Briefing", callback_data="set_lens_executive")],
        [InlineKeyboardButton("📊 Technical & Architecture", callback_data="set_lens_technical")],
        [InlineKeyboardButton("⚠️ Risk & Counter-Arguments", callback_data="set_lens_risk")],
        [InlineKeyboardButton("💬 Custom Prompt Query", callback_data="set_lens_custom")],
        [InlineKeyboardButton("⬅️ Back", callback_data="open_crawl_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_rag_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌐 Web Intelligence (CRN DB)", callback_data="ask_scope_web")],
        [InlineKeyboardButton("🧠 Personal Vault (Knowledge)", callback_data="ask_scope_vault")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_news_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🚀 Run Sentiment Scan", callback_data="run_news_agent")],
        [InlineKeyboardButton("📤 Export Database (.db)", callback_data="export_db_sqlite")],
        [InlineKeyboardButton("📊 Export News (.xlsx)", callback_data="export_db_xlsx")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_model_submenu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔄 Auto-Cascade (Default)", callback_data="set_model_auto")],
        [InlineKeyboardButton("⚡ Gemini 3.6 Flash", callback_data="set_model_gemini_36_flash")],
        [InlineKeyboardButton("💎 Gemma 4 31B IT", callback_data="set_model_gemma_4_31b_it")],
        [InlineKeyboardButton("💎 Gemma 4 26B MoE IT", callback_data="set_model_gemma_4_26b_a4b_it")],
        [InlineKeyboardButton("⚡ Gemini 3.5 Flash-Lite", callback_data="set_model_gemini_35_flash_lite")],
        [InlineKeyboardButton("⚡ Gemini 3.5 Flash", callback_data="set_model_gemini_35_flash")],
        [InlineKeyboardButton("💻 Local Gemma 4 (Ollama)", callback_data="set_model_ollama")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the Interactive Menu directly when user types /menu."""
    current_model = context.user_data.get("selected_model_label", "Auto-Cascade")
    current_lang = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
    await update.message.reply_text(
        "<b>CRN Control Center</b>\nStandby. Select a modular tool:",
        reply_markup=get_main_menu(current_model, current_lang),
        parse_mode="HTML"
    )


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show per-source crawl success rates and average relevance scores."""
    rows = get_source_health()
    if not rows:
        await update.message.reply_text("No crawl data recorded yet. Run a briefing first.")
        return

    lines = ["<b>📊 Source Health Report</b>\n"]
    for r in rows:
        total = r["total_attempts"]
        ok = r["successful"] or 0
        rate = (ok / total * 100) if total else 0
        avg = r["avg_score"]

        if rate < 50:
            icon = "🔴"
        elif avg is not None and avg < 5:
            icon = "🟡"
        else:
            icon = "🟢"

        score_str = f"{avg}" if avg is not None else "n/a"
        lines.append(
            f"{icon} <b>{escape_html(r['category'])}</b>\n"
            f"    {ok}/{total} ok ({rate:.0f}%) · avg score {score_str}\n"
            f"    <code>{escape_html(r['url'])}</code>"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches button clicks and routes them to the correct Async Python Function."""
    from app.agent.bot_handler import get_is_crawling, set_is_crawling
    query = update.callback_query
    await query.answer()

    if query.data == "skip_run":
        await query.edit_message_text(text="✅ Scheduled run skipped. System returning to standby.")
        return

    if query.data == "ask_rag_prompt":
        await query.edit_message_text(
            text="🔍 <b>CRN Second Brain RAG Query Engine</b>\nSelect search scope:",
            reply_markup=get_rag_submenu(),
            parse_mode="HTML"
        )
        return

    if query.data in ["ask_scope_web", "ask_scope_vault"]:
        scope = "vault" if query.data == "ask_scope_vault" else "web"
        context.user_data["rag_scope"] = scope
        context.user_data["awaiting_rag_query"] = True
        scope_title = "🧠 Personal Vault (wiki/journal/outputs)" if scope == "vault" else "🌐 Web Intelligence (CRN DB)"
        await query.edit_message_text(
            text=f"🔍 <b>RAG Search Scope: {scope_title} Active.</b>\n\nPlease reply with your question across {scope_title}:",
            parse_mode="HTML"
        )
        return

    if query.data == "open_job_menu":
        await query.edit_message_text(
            text="💼 <b>CRN Job Intelligence & Fit Analysis Module</b>\nSelect an option below:",
            reply_markup=get_job_submenu(),
            parse_mode="HTML"
        )
        return

    if query.data in ["view_job_pipeline", "view_jobs_new", "view_jobs_applied", "view_jobs_archived"]:
        status_map = {
            "view_job_pipeline": "NEW",
            "view_jobs_new": "NEW",
            "view_jobs_applied": "APPLIED",
            "view_jobs_archived": "ARCHIVED"
        }
        st_filter = status_map.get(query.data, "NEW")
        try:
            top_jobs = get_top_job_opportunities(min_fit=50, limit=10, status_filter=st_filter)
            digest_html = format_job_digest_html(top_jobs)
            await query.edit_message_text(text=digest_html, reply_markup=get_job_submenu(), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            logger.error("Failed to view job pipeline: %s", exc)
            await query.edit_message_text(text=f"❌ Failed to load job pipeline: {escape_html(str(exc))}", reply_markup=get_job_submenu(), parse_mode="HTML")
        return

    if query.data == "export_jobs_pdf":
        await query.edit_message_text("⏳ Generating styled PDF Job Pipeline Report...", parse_mode="HTML")
        try:
            pdf_path = await asyncio.to_thread(generate_job_pipeline_pdf)
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=pdf_path,
                filename="job_pipeline_report.pdf",
                caption="📄 <b>CRN Job Intelligence Pipeline PDF Report</b>\nSynced to your Second Brain vault: <code>raw/crn/jobs/job_pipeline.md</code>.",
                parse_mode="HTML"
            )
            await query.edit_message_text(
                text="✅ <b>PDF Report Exported!</b> Document sent below.\nSelect an option to continue:",
                reply_markup=get_job_submenu(),
                parse_mode="HTML"
            )
        except Exception as exc:
            logger.error("PDF generation error: %s", exc)
            await query.edit_message_text(text=f"❌ PDF generation failed: {escape_html(str(exc))}", reply_markup=get_job_submenu(), parse_mode="HTML")
        return

    if query.data == "run_job_scan":
        await query.edit_message_text(text="⏳ Running Autonomous Job Scan across target job boards...")
        try:
            evaluated_jobs = await run_autonomous_job_scan()
            digest_html = format_job_digest_html(evaluated_jobs)
            await query.edit_message_text(text=digest_html, reply_markup=get_job_submenu(), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            logger.error("Job scan failed: %s", exc)
            await query.edit_message_text(text=f"❌ Autonomous Job Scan crashed: {exc}")
        return

    if query.data == "eval_job_url_prompt":
        context.user_data["awaiting_job_url"] = True
        await query.edit_message_text(
            text="💼 <b>Custom Job Link Evaluation Mode Active.</b>\n\nPlease reply with the URL of the job posting (e.g. LinkedIn, Glints, RemoteOK link):",
            parse_mode="HTML"
        )
        return

    if query.data == "toggle_digest_lang":
        current_lang = context.user_data.get("digest_lang", "id")
        if current_lang == "id":
            context.user_data["digest_lang"] = "en"
            context.user_data["digest_lang_label"] = "🇬🇧 English"
        else:
            context.user_data["digest_lang"] = "id"
            context.user_data["digest_lang_label"] = "🇮🇩 Bahasa"
        
        current_model = context.user_data.get("selected_model_label", "Auto-Cascade")
        new_lang_label = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
        await query.edit_message_text(
            text=f"✅ Digest Output Language updated to: <b>{new_lang_label}</b>",
            reply_markup=get_main_menu(current_model, new_lang_label),
            parse_mode="HTML"
        )
        return

    if query.data == "run_daily_digest":
        if get_is_crawling():
            await query.edit_message_text(text="⏳ Agent is busy. Please wait.")
            return
        set_is_crawling(True)
        await query.edit_message_text(text="☕ Synthesizing Daily Morning Coffee Digest...")
        try:
            active_model = context.user_data.get("selected_model", "auto")
            lang = context.user_data.get("digest_lang", "id")
            await asyncio.to_thread(generate_daily_digest, active_model, lang)
            await query.edit_message_text(text="✅ Daily Morning Coffee Digest complete & saved to Obsidian vault.")
        except Exception as exc:
            logger.error("Digest crash: %s", exc)
            await query.edit_message_text(text=f"❌ Digest crashed: {exc}")
        finally:
            set_is_crawling(False)
        return

    if query.data == "run_news_agent":
        if get_is_crawling():
            await query.edit_message_text(text="⏳ Agent is busy. Please wait.")
            return
        set_is_crawling(True)
        await query.edit_message_text(text="⏳ Running News Sentiment Agent... This might take a few moments.")
        try:
            signals = await asyncio.to_thread(execute_news_agent)
            await query.edit_message_text(text=f"✅ News Sentiment Agent complete. Sent {signals} signals.")
        except Exception as exc:
            logger.error("News agent crash: %s", exc)
            await query.edit_message_text(text=f"❌ News Sentiment Agent crashed: {exc}")
        finally:
            set_is_crawling(False)
        return

    if query.data == "open_crawl_menu":
        current_lens = context.user_data.get("selected_lens_label", "🎯 Executive")
        await query.edit_message_text(text="Select deep crawl source:", reply_markup=get_crawl_submenu(current_lens))
        return

    if query.data == "open_lens_menu":
        await query.edit_message_text(text="Select Analytical Lens focus:", reply_markup=get_lens_submenu())
        return

    if query.data.startswith("set_lens_"):
        lens_key = query.data.replace("set_lens_", "")
        lens_map = {
            "executive": "🎯 Executive",
            "technical": "📊 Technical",
            "risk": "⚠️ Risk",
            "custom": "💬 Custom Query"
        }
        context.user_data["selected_lens"] = lens_key
        context.user_data["selected_lens_label"] = lens_map.get(lens_key, "🎯 Executive")
        
        if lens_key == "custom":
            context.user_data["awaiting_custom_query"] = True
            await query.edit_message_text(
                text="💬 <b>Custom Analytical Query Mode Active.</b>\n\nPlease reply with your custom analytical question (e.g., <i>'How does this paper affect XAUUSD gold trading?'</i>):",
                parse_mode="HTML"
            )
            return

        current_lens_label = context.user_data.get("selected_lens_label", "🎯 Executive")
        await query.edit_message_text(
            text=f"✅ Analytical Lens updated to: <b>{current_lens_label}</b>",
            reply_markup=get_crawl_submenu(current_lens_label),
            parse_mode="HTML"
        )
        return

    if query.data == "open_news_menu":
        await query.edit_message_text(text="Select News Sentiment action:", reply_markup=get_news_submenu())
        return

    if query.data == "open_model_menu":
        await query.edit_message_text(text="Select active model engine:", reply_markup=get_model_submenu())
        return

    if query.data == "export_db_sqlite":
        db_path = _PROJECT_ROOT / "shared_workspace" / "crn_intelligence.db"
        if not db_path.exists():
            await query.edit_message_text("❌ Database file not found.")
            return

        await query.edit_message_text("📤 Exporting SQLite database (.db) to chat...")
        try:
            with open(db_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="crn_intelligence.db",
                    caption="📊 CRN SQLite Intelligence Database Export"
                )
            current_label = context.user_data.get("selected_model_label", "Auto-Cascade")
            current_lang = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
            await query.message.reply_text("✅ Database exported successfully.", reply_markup=get_main_menu(current_label, current_lang))
        except Exception as exc:
            logger.error("Failed to export SQLite database: %s", exc)
            await query.edit_message_text(f"❌ Export failed: {exc}")
        return

    if query.data == "export_db_xlsx":
        db_path = _PROJECT_ROOT / "shared_workspace" / "crn_intelligence.db"
        xlsx_path = _PROJECT_ROOT / "shared_workspace" / "processed_news.xlsx"
        if not db_path.exists():
            await query.edit_message_text("❌ Database file not found.")
            return

        try:
            import sqlite3
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Processed News"

            headers = ["ID", "Headline Title", "Source URL", "Published Date", "Sentiment Flag", "Analytical Summary", "Created At"]
            ws.append(headers)

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, title, url, published_date, sentiment_flag, summary, created_at FROM processed_news ORDER BY id DESC"
                )
                rows = cursor.fetchall()
                for row in rows:
                    ws.append(list(row))

            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = openpyxl.utils.get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 60)

            wb.save(xlsx_path)

            await query.edit_message_text("📤 Exporting processed_news.xlsx to chat...")
            with open(xlsx_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="processed_news_export.xlsx",
                    caption=f"📊 Processed News Excel Export ({len(rows)} records)"
                )
            current_label = context.user_data.get("selected_model_label", "Auto-Cascade")
            current_lang = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
            await query.message.reply_text("✅ News Excel spreadsheet exported successfully.", reply_markup=get_main_menu(current_label, current_lang))
        except Exception as exc:
            logger.error("Failed to export Excel spreadsheet: %s", exc)
            await query.edit_message_text(f"❌ Export failed: {exc}")
        return

    if query.data.startswith("set_model_"):
        model_map = {
            "set_model_auto": ("auto", "Auto-Cascade"),
            "set_model_gemini_36_flash": ("gemini-3.6-flash", "Gemini 3.6 Flash"),
            "set_model_gemma_4_31b_it": ("gemma-4-31b-it", "Gemma 4 31B IT"),
            "set_model_gemma_4_26b_a4b_it": ("gemma-4-26b-a4b-it", "Gemma 4 26B MoE IT"),
            "set_model_gemini_35_flash_lite": ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
            "set_model_gemini_35_flash": ("gemini-3.5-flash", "Gemini 3.5 Flash"),
            "set_model_ollama": ("ollama", "Local Gemma 4"),
        }
        val, label = model_map.get(query.data, ("auto", "Auto-Cascade"))
        context.user_data["selected_model"] = val
        context.user_data["selected_model_label"] = label
        current_lang = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
        
        await query.edit_message_text(
            text=f"✅ Active model engine updated to: <b>{label}</b>",
            reply_markup=get_main_menu(label, current_lang),
            parse_mode="HTML"
        )
        return

    if query.data == "back_to_menu":
        current_label = context.user_data.get("selected_model_label", "Auto-Cascade")
        current_lang = context.user_data.get("digest_lang_label", "🇮🇩 Bahasa")
        await query.edit_message_text(
            text="<b>CRN Control Center</b>\nStandby. Select a modular tool:",
            reply_markup=get_main_menu(current_label, current_lang),
            parse_mode="HTML"
        )
        return

    if query.data == "crawl_custom":
        if get_is_crawling():
            await query.edit_message_text(text="⏳ Crawler is already currently running. Please wait.")
            return
        context.user_data["awaiting_custom_url"] = True
        await query.edit_message_text(text="Please reply to this message with a comma-separated list of URLs to crawl (e.g., https://news.ycombinator.com).")
        return

    if query.data in ["crawl_default", "crawl_local", "crawl_global"]:
        if get_is_crawling():
            await query.edit_message_text(text="⏳ Crawler is already currently running. Please wait.")
            return

        set_is_crawling(True)
        active_model = context.user_data.get("selected_model", "auto")
        model_label = context.user_data.get("selected_model_label", "Auto-Cascade")
        active_lens = context.user_data.get("selected_lens", "executive")
        lens_label = context.user_data.get("selected_lens_label", "🎯 Executive")
        custom_q = context.user_data.get("custom_query_text", "")
        
        scope_filter = None
        if query.data == "crawl_local":
            scope_filter = [e for e in TARGET_URLS if e["scope"] == "local"]
        elif query.data == "crawl_global":
            scope_filter = [e for e in TARGET_URLS if e["scope"] == "global"]

        scope_desc = "Local Sources" if query.data == "crawl_local" else ("Global Sources" if query.data == "crawl_global" else "Default Targets")
        await query.edit_message_text(text=f"⏳ Launching Deep Crawl on {scope_desc} ({model_label} | Lens: {lens_label})...")

        try:
            await asyncio.to_thread(
                execute_research_agent,
                "manual", active_model, None, scope_filter, active_lens, custom_q
            )
            from app.agent.scheduler import _write_last_run
            _write_last_run()
            await query.edit_message_text(text=f"✅ Deep Crawl ({scope_desc}) complete using {model_label} [{lens_label}].")
        except Exception as exc:
            logger.error("Crawler crash: %s", exc)
            await query.edit_message_text(text=f"❌ Deep Crawl Module crashed: {exc}")
        finally:
            set_is_crawling(False)
        return
