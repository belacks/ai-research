"""
CRN Finance Intelligence — Generic Bank Parser Blueprint
=========================================================
Open-source template for parsing transaction emails (BCA, Mandiri, BRI, Bank of America, etc.)
or e-wallet statements (GoPay, OVO, ShopeePay, PayPal).

To implement a new bank adapter:
1. Copy this file to `custom_bank_parser.py` in `app/finance/parsers/`.
2. Implement `parse_email_body()` with your bank's notification email layout/regex.
3. Call `insert_transaction()` from `app.finance.finance_db` to save parsed transactions.
"""

import re
import logging
from html import unescape
from app.finance.finance_db import insert_transaction

logger = logging.getLogger(__name__)


def parse_bank_email_body(body_text: str) -> dict | None:
    """
    Extract amount, transaction_type, merchant, and payment_method from bank email text/HTML.

    Returns:
        dict: {
            "amount": float,             # Negative for expense (-25000.0), Positive for income (+50000.0)
            "account_name": str,         # e.g., "Primary Bank Account" or "GoPay Wallet"
            "payment_method": str,       # "QRIS", "Transfer", "Debit Card", etc.
            "merchant": str,             # Merchant name or recipient
            "category": str              # Expense category ("Food", "Bills", "Shopping", "General")
        }
    """
    cleaned_text = unescape(re.sub(r"<[^>]+>", " ", body_text))
    cleaned_text = re.sub(r"\s+", " ", cleaned_text)

    # Example Regex Pattern for Amount (e.g. "Rp 50.000" or "IDR 50,000")
    amt_match = re.search(r"(?:Rp|IDR)\s*([\d\.,]+)", cleaned_text, re.IGNORECASE)
    if not amt_match:
        return None

    raw_amt = amt_match.group(1).replace(".", "").replace(",", ".")
    try:
        amount = float(raw_amt)
    except ValueError:
        return None

    # Example Merchant Match
    merchant_match = re.search(r"(?:at|ke|to|di)\s+([A-Za-z0-9\s._-]+?)(?:\s+pada|\s+date|\s+ref|$)", cleaned_text, re.IGNORECASE)
    merchant = merchant_match.group(1).strip() if merchant_match else "Unknown Merchant"

    return {
        "amount": -abs(amount),  # Default to expense
        "account_name": "Primary Bank Account",
        "payment_method": "Bank Transfer",
        "merchant": merchant,
        "category": "General",
    }


def fetch_and_ingest_bank_emails(gmail_user: str, gmail_app_password: str, search_query: str = 'FROM "mybank.com"') -> int:
    """
    Example IMAP crawler connecting to Gmail to search and ingest custom bank emails.
    """
    if not gmail_user or not gmail_app_password:
        logger.warning("Gmail credentials missing. Skipping bank email sync.")
        return 0

    # Custom IMAP logic here...
    return 0
