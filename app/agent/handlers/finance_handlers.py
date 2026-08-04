"""
CRN Research Node — Financial Telegram Handlers
================================================
Handles /finance, /exp, /in, quick logs, and PDF statement document uploads.
"""

import re
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from app.core.notifier import escape_html
from app.finance.finance_db import get_finance_summary, insert_transaction
from app.finance.statement_parser import parse_statement_pdf
from app.finance.vault_sync import sync_finance_to_obsidian

logger = logging.getLogger(__name__)


async def handle_finance_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display real-time financial health summary & cashflow status."""
    fin = get_finance_summary()
    if not fin:
        await update.message.reply_text("❌ Finance database not available.")
        return

    now = datetime.now()
    res = (
        f"💳 <b>CRN Financial Health & Cashflow Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Total Liquid Assets</b>: Rp {fin.get('total_assets', 0.0):,.0f}\n"
    )
    if 'total_liabilities' in fin:
        res += (
            f"• <b>Personal Obligations</b>: Rp {fin.get('personal_liabilities', 0.0):,.0f}\n"
            f"• <b>Shared Obligations</b>: Rp {fin.get('shared_liabilities', 0.0):,.0f}\n"
            f"• <b>Total Combined Obligations</b>: Rp {fin.get('total_liabilities', 0.0):,.0f}\n"
            f"• <b>Current Installment Due</b>: Rp {fin.get('total_current_due', 0.0):,.0f}\n"
        )
    res += f"\n📅 <b>Status Period: {now.strftime('%B %Y')}</b>"
    
    await update.message.reply_text(res, parse_mode="HTML")
    await asyncio.to_thread(sync_finance_to_obsidian)


async def handle_quick_log(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str = "") -> None:
    """Log expense or income manually, updating liquid bank balance in SQLite."""
    user_txt = update.message.text.strip() if update.message else ""
    raw_text = param if param else user_txt
    raw_lower = raw_text.lower()
    
    # Fast path for simple command shapes: /exp 15k coffee or /in 500k
    is_simple_cmd = raw_text.startswith("/exp") or raw_text.startswith("/in")
    if is_simple_cmd:
        is_income = raw_lower.startswith("/in")
        amt_match = re.search(r"(\d+(?:\.\d+)?)\s*(k)?", raw_lower)
        if amt_match:
            val = float(amt_match.group(1))
            if amt_match.group(2) == "k":
                val *= 1000
            amount = val if is_income else -abs(val)
            merchant = "Manual Quick Log"
            method = "Bank Transfer"
            acc_name = "Primary Bank Account"
            
            success = insert_transaction(amount, acc_name, method, merchant, ingestion_source="telegram_manual")
            if success:
                prefix = "📈 Income" if is_income else "📉 Expense"
                await update.message.reply_text(f"✅ {prefix} of <b>Rp {abs(amount):,.0f}</b> logged cleanly under {acc_name}.", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ Failed to log transaction.", parse_mode="HTML")
            return

    # Advanced Natural Language & Multi-Transaction Parsing via Gemini LLM
    try:
        from app.agent.claw_logic import summarize_with_cascade
        parse_prompt = f"""Extract financial transactions from this user input into valid JSON:
User Input: "{raw_text}"

Respond ONLY in valid JSON format:
{{
  "transactions": [
    {{
      "type": "expense" | "income",
      "amount": <number>,
      "account_name": "<account_name>",
      "payment_method": "<method>",
      "merchant": "<merchant/description>",
      "category": "<category>"
    }}
  ]
}}"""
        active_model = context.user_data.get("selected_model", "auto")
        raw_res = await asyncio.to_thread(summarize_with_cascade, parse_prompt, "raw_prompt", active_model)
        
        start_idx = raw_res.find("{")
        end_idx = raw_res.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(raw_res[start_idx:end_idx])
            txs = data.get("transactions", [])
            if txs:
                logged_msgs = []
                for tx in txs:
                    amt = float(tx.get("amount", 0))
                    tx_type = tx.get("type", "expense")
                    final_amt = abs(amt) if tx_type == "income" else -abs(amt)
                    acc = tx.get("account_name", "Primary Bank Account")
                    method = tx.get("payment_method", "Bank Transfer")
                    merchant = tx.get("merchant", "Manual Log")
                    cat = tx.get("category", "General")
                    
                    suc = insert_transaction(final_amt, acc, method, merchant, category=cat, ingestion_source="telegram_manual")
                    if suc:
                        icon = "📈 Income" if final_amt > 0 else "📉 Expense"
                        logged_msgs.append(f"• <b>{icon}</b>: Rp {abs(final_amt):,.0f} ({acc} — {merchant})")
                
                if logged_msgs:
                    sync_finance_to_obsidian()
                    resp_html = "✅ <b>Transaction(s) Logged Cleanly</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(logged_msgs)
                    await update.message.reply_text(resp_html, parse_mode="HTML")
                    return
    except Exception as exc:
        logger.warning("LLM transaction parsing fallback triggered regex: %s", exc)

    # Regex Fallback
    is_income = any(kw in raw_lower for kw in ["income", "masuk", "top up", "topup", "topped up", "terima", "got"])
    amt_match = re.search(r"(\d+(?:\.\d+)?)\s*(k)?", raw_lower)
    if amt_match:
        val = float(amt_match.group(1))
        if amt_match.group(2) == "k":
            val *= 1000
        amount = val if is_income else -abs(val)
        merchant = "Manual Log"
        acc_name = "Primary Bank Account"
        
        success = insert_transaction(amount, acc_name, "Manual", merchant, ingestion_source="telegram_manual")
        if success:
            sync_finance_to_obsidian()
            prefix = "📈 Income" if is_income else "📉 Expense"
            await update.message.reply_text(f"✅ {prefix} of <b>Rp {abs(amount):,.0f}</b> logged cleanly under {acc_name}.", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Failed to log transaction.", parse_mode="HTML")
    else:
        await update.message.reply_text("💡 <b>Quick Log Usage:</b> <code>/exp 15k coffee</code> or <code>/in 500k refund</code>", parse_mode="HTML")


async def handle_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uploaded PDF statement documents from Telegram."""
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".pdf"):
        return

    msg = await update.message.reply_text("📥 Receiving PDF statement document...")
    try:
        dest_dir = Path("/app/shared_workspace/statements")
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_path = dest_dir / doc.file_name
        
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(target_path)
        
        res = await asyncio.to_thread(parse_statement_pdf, str(target_path))
        
        if res.get("success"):
            out_txt = (
                f"✅ <b>PDF Statement Processed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>File</b>: <code>{escape_html(res['file_name'])}</code>\n"
                f"• <b>Document Type</b>: {escape_html(res['document_type'])}\n"
                f"• <b>Lines Parsed</b>: {res['line_items_count']}\n"
            )
            if res.get("closing_balance") is not None:
                out_txt += f"• <b>Closing Balance</b>: <b>Rp {res['closing_balance']:,.0f}</b>\n"
            out_txt += "\nDatabase records updated cleanly."
            await msg.edit_text(out_txt, parse_mode="HTML")
        else:
            await msg.edit_text(f"❌ Failed to parse PDF statement: {res.get('error')}")
    except Exception as exc:
        logger.error("PDF upload handler crashed: %s", exc)
        await msg.edit_text(f"❌ PDF processing crashed: {exc}")

async def handle_financial_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str) -> None:
    """Analyze custom financial decision / affordability question using live DB metrics and Gemini."""
    msg = await update.message.reply_text("⏳ Analyzing financial affordability & cashflow context...", parse_mode="HTML")
    try:
        fin = get_finance_summary()
        from app.agent.claw_logic import summarize_with_cascade
        
        fin_lines = [f"- {k.replace('_', ' ').title()}: Rp {v:,.0f}" if isinstance(v, (int, float)) else f"- {k.replace('_', ' ').title()}: {v}" for k, v in fin.items()]
        fin_context = "\n".join(fin_lines)

        prompt = f"""You are a personal AI Financial Advisor & Cashflow Manager.
Below is the REAL-TIME financial status from the database:
{fin_context}

User Question: "{query_text}"

Instructions:
1. Give a direct, sharp, objective answer based on the exact numbers provided.
2. Respond using strictly standard formatting or allowed Telegram HTML tags (<b>bold</b>, <i>italic</i>, <code>code</code>). Do NOT use <p>, <div>, or <br> tags.
"""
        active_model = context.user_data.get("selected_model", "auto")
        res_text = await asyncio.to_thread(summarize_with_cascade, prompt, "raw_prompt", active_model)
        
        # Clean unsupported HTML tags for Telegram
        cleaned = re.sub(r"</?(?:p|div|section|article)[^>]*>", "\n", res_text, flags=re.IGNORECASE)
        cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        try:
            await msg.edit_text(cleaned, parse_mode="HTML")
        except Exception as html_exc:
            logger.warning("Telegram HTML parse failed (%s), falling back to plain text", html_exc)
            await msg.edit_text(cleaned, parse_mode=None)
    except Exception as exc:
        logger.error("Financial query analysis crash: %s", exc)
        await msg.edit_text(f"❌ Financial query analysis crashed: {exc}")


async def handle_sync_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually trigger BNI Wondr transaction email ingestion from Gmail IMAP."""
    from app.core.config import settings
    if not settings.gmail_user or not settings.gmail_app_password:
        await update.message.reply_text("❌ Gmail IMAP credentials not configured in environment.")
        return

    msg = await update.message.reply_text("📥 Checking Gmail IMAP for new BNI Wondr transaction emails...", parse_mode="HTML")
    try:
        from app.finance.parsers import fetch_bni_transactions_imap
        count = await asyncio.to_thread(fetch_bni_transactions_imap, settings.gmail_user, settings.gmail_app_password)
        if count > 0:
            sync_finance_to_obsidian()
            await msg.edit_text(f"✅ Auto-ingested <b>{count}</b> new BNI transaction email(s) into database.", parse_mode="HTML")
        else:
            await msg.edit_text("ℹ️ Email check completed. No new BNI transaction emails found.")
    except Exception as exc:
        logger.error("Manual BNI email sync error: %s", exc)
        await msg.edit_text(f"❌ Email sync failed: {exc}")


async def handle_export_finance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send multi-sheet Excel workbook and PDF visual report table."""
    msg = await update.message.reply_text("📊 Generating Excel financial workbook and PDF breakdown report...", parse_mode="HTML")
    try:
        from app.finance.export_engine import generate_excel_export, generate_pdf_export
        
        xlsx_path = await asyncio.to_thread(generate_excel_export)
        pdf_path = await asyncio.to_thread(generate_pdf_export)

        await msg.edit_text("✅ Financial breakdown generated! Sending documents...", parse_mode="HTML")
        
        # Send Excel file
        with open(xlsx_path, "rb") as doc_file:
            await update.message.reply_document(
                document=doc_file,
                filename=f"CRN_Financial_Breakdown_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                caption="📈 <b>CRN Multi-Tab Financial Breakdown (.xlsx)</b>\nIncludes AutoFilters by Date, Account, Category & Daily Summary.",
                parse_mode="HTML"
            )

        # Send PDF file
        with open(pdf_path, "rb") as pdf_file:
            await update.message.reply_document(
                document=pdf_file,
                filename=f"CRN_Financial_Report_{datetime.now().strftime('%Y-%m-%d')}.pdf",
                caption="📄 <b>CRN Financial Audit & Breakdown Report (.pdf)</b>\nLandscape summary table of liquid assets and ledger.",
                parse_mode="HTML"
            )
    except Exception as exc:
        logger.error("Failed to generate financial export: %s", exc)
        await msg.edit_text(f"❌ Failed to generate financial export: {exc}")


