"""
CRN Research Node — Second Brain RAG Telegram Handlers
=======================================================
Handles /ask command and Second Brain / Web RAG searches.
"""

import logging
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from app.core.notifier import escape_html
from app.agent.rag_logic import ask_second_brain

logger = logging.getLogger(__name__)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /ask command for RAG search.
    Usage:
      /ask web <query>   - Search Web Intelligence (CRN DB)
      /ask vault <query> - Search Personal Obsidian Vault (wiki/journal/outputs)
      /ask <query>       - Search Web Intelligence (default)
    """
    if not context.args:
        await update.message.reply_text(
            "🔍 <b>CRN Second Brain RAG Usage:</b>\n\n"
            "• <code>/ask web &lt;query&gt;</code> - Search Web Intelligence\n"
            "• <code>/ask vault &lt;query&gt;</code> - Search Personal Vault (wiki/journal/outputs)\n"
            "• Or tap <b>🔍 Ask Second Brain</b> in /menu",
            parse_mode="HTML"
        )
        return

    first_arg = context.args[0].lower()
    if first_arg in ["web", "vault"]:
        scope = first_arg
        query_text = " ".join(context.args[1:])
    else:
        scope = "web"
        query_text = " ".join(context.args)

    if not query_text.strip():
        await update.message.reply_text("❌ Please enter your search question. Example: <code>/ask vault thesis joint loss</code>", parse_mode="HTML")
        return

    scope_label = "Personal Vault" if scope == "vault" else "Web Intelligence"
    msg = await update.message.reply_text(
        f"⏳ Searching [{scope_label}] for: <i>\"{escape_html(query_text)}\"</i>...",
        parse_mode="HTML"
    )
    try:
        active_model = context.user_data.get("selected_model", "auto")
        res_html = await asyncio.to_thread(ask_second_brain, query_text, scope, active_model)
        await msg.edit_text(res_html, parse_mode="HTML")
    except Exception as exc:
        logger.error("RAG search crash: %s", exc)
        await msg.edit_text(f"❌ RAG search crashed: {exc}")
