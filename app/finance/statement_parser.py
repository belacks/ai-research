"""
CRN Finance Intelligence Module — PDF Statement Ingester
==========================================================
Parses uploaded bank and e-wallet E-Statement PDFs.
Extracts transaction summaries and closing balances, updating SQLite database.
"""

import os
import re
import logging
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

from app.finance.finance_db import _DB_PATH

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: str, password: str | None = None) -> str:
    """Extract full text from a PDF file using pypdf or pdfplumber, handling encryption."""
    full_text = ""
    
    # Try pypdf first
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        if reader.is_encrypted:
            if password:
                reader.decrypt(password)
            else:
                raise ValueError("PDF is password protected.")
        
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                full_text += txt + "\n"
        if full_text.strip():
            return full_text
    except Exception as exc:
        logger.debug("pypdf extraction attempt: %s", exc)

    # Fallback to pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path, password=password) as pdf:
            for page in pdf.pages:
                txt = page.extract_text()
                if txt:
                    full_text += txt + "\n"
        return full_text
    except Exception as exc:
        logger.error("pdfplumber extraction failed: %s", exc)
        raise exc


def parse_statement_pdf(pdf_path: str, password: str | None = None) -> dict:
    """
    Parse a statement PDF and return summary dictionary.
    Supports BNI E-Statement and E-Wallets.
    """
    text = extract_text_from_pdf(pdf_path, password=password)
    if not text:
        return {"success": False, "error": "Empty or unreadable PDF text."}

    lines = text.split("\n")
    doc_type = "Unknown"
    text_lower = text.lower()

    if "bni" in text_lower or "bank negara indonesia" in text_lower or "rekening koran" in text_lower:
        doc_type = "BNI E-Statement"
    elif "shopee" in text_lower:
        doc_type = "Shopee Statement"
    elif "gopay" in text_lower:
        doc_type = "GoPay Statement"

    # Extract BNI E-Statement Metrics
    summary = {
        "success": True,
        "document_type": doc_type,
        "file_name": Path(pdf_path).name,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "total_debit": 0.0,
        "total_credit": 0.0,
        "closing_balance": None,
        "line_items_count": len(lines),
    }

    # BNI Closing Balance Extraction Regex Pattern
    balance_match = re.search(r"(?:saldo akhir|closing balance)\s*:?\s*(?:rp)?\s*([\d\.,]+)", text_lower)
    if balance_match:
        val_str = balance_match.group(1).replace(".", "").replace(",", ".")
        try:
            summary["closing_balance"] = float(val_str)
        except ValueError:
            pass

    # Update SQLite BNI Account current_balance if parsed cleanly
    if doc_type == "BNI E-Statement" and summary["closing_balance"] is not None:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.execute(
                    "UPDATE fin_accounts SET current_balance = ?, updated_at = ? WHERE name LIKE '%BNI%'",
                    (summary["closing_balance"], summary["parsed_at"]),
                )
            logger.info("Updated BNI closing balance from PDF: Rp %.2f", summary["closing_balance"])
        except Exception as exc:
            logger.error("Failed to update BNI balance from PDF: %s", exc)

    return summary
