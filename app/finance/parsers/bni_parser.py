"""
CRN Finance Intelligence — BNI Wondr Email Parser Adapter
===========================================================
Concrete bank parser adapter for BNI Wondr transaction emails via Gmail IMAP.
"""

import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header
from html import unescape
from app.finance.finance_db import insert_transaction

logger = logging.getLogger(__name__)


def parse_bni_email_body(body_text: str) -> dict | None:
    """
    Extract amount, transaction_type, merchant, and payment_method from BNI email HTML/text.
    """
    cleaned_text = unescape(re.sub(r"<[^>]+>", " ", body_text))
    cleaned_text = re.sub(r"\s+", " ", cleaned_text)

    # 1. Match Amount (e.g. Rp 18.000,00 or Rp18,000.00 or IDR 18.000)
    amt_match = re.search(r"(?:Rp|IDR)\s*([0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{2})?)", cleaned_text, re.IGNORECASE)
    if not amt_match:
        return None

    raw_amt = amt_match.group(1)
    if "," in raw_amt and "." in raw_amt:
        clean_amt_str = raw_amt.replace(".", "").replace(",", ".")
    elif "." in raw_amt:
        clean_amt_str = raw_amt.replace(".", "")
    elif "," in raw_amt:
        clean_amt_str = raw_amt.replace(",", "")
    else:
        clean_amt_str = raw_amt

    try:
        amount_val = float(clean_amt_str)
    except ValueError:
        return None

    # 2. Determine Transaction Type & Payment Method
    is_income = False
    payment_method = "Bank Transfer"
    merchant = "BNI Transaction"

    if re.search(r"QRIS|Pembayaran QR", cleaned_text, re.IGNORECASE):
        payment_method = "QRIS"
        is_income = False
    elif re.search(r"Transfer Masuk|Kredit|Dana Masuk|Incoming", cleaned_text, re.IGNORECASE):
        payment_method = "Bank Transfer"
        is_income = True
    elif re.search(r"Transfer Keluar|Debet|Transfer Out|Pembayaran", cleaned_text, re.IGNORECASE):
        payment_method = "Bank Transfer"
        is_income = False
    elif re.search(r"Kartu Debit|EDC|Atm", cleaned_text, re.IGNORECASE):
        payment_method = "Debit Card"
        is_income = False

    # 3. Extract Merchant / Receiver Name if available
    merchant_match = re.search(r"(?:di|ke|dari|merchant)\s+([A-Za-z0-9\s._\-]{3,30})", cleaned_text, re.IGNORECASE)
    if merchant_match:
        merchant = merchant_match.group(1).strip()

    final_amount = amount_val if is_income else -abs(amount_val)
    category = "Income" if is_income else ("Food & Beverage" if payment_method == "QRIS" else "Transfer")

    return {
        "amount": final_amount,
        "payment_method": payment_method,
        "merchant": merchant,
        "category": category,
    }


def fetch_bni_transactions_imap(gmail_user: str, gmail_app_password: str, max_emails: int = 20, default_account: str = "Primary Bank Account") -> int:
    """
    Connect to Gmail via IMAP, search BNI emails, parse, and insert into DB.
    """
    if not gmail_user or not gmail_app_password:
        logger.warning("Gmail credentials missing. Skipping BNI email fetch.")
        return 0

    inserted_count = 0
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_app_password)
        mail.select("inbox")

        search_query = '(OR (FROM "bni.co.id") (SUBJECT "Wondr"))'
        status, data = mail.search(None, search_query)

        if status != "OK" or not data[0]:
            logger.info("No BNI emails found on Gmail server.")
            mail.logout()
            return 0

        email_ids = data[0].split()
        recent_ids = email_ids[-max_emails:]

        for e_id in recent_ids:
            res, msg_data = mail.fetch(e_id, "(RFC822)")
            if res != "OK":
                continue

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    msg_id = msg.get("Message-ID", f"bni_{e_id.decode()}")

                    body_text = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            if content_type in ["text/plain", "text/html"]:
                                body_text = part.get_payload(decode=True).decode(errors="ignore")
                                break
                    else:
                        body_text = msg.get_payload(decode=True).decode(errors="ignore")

                    parsed_tx = parse_bni_email_body(body_text)
                    if parsed_tx:
                        success = insert_transaction(
                            amount=parsed_tx["amount"],
                            account_name=default_account,
                            payment_method=parsed_tx["payment_method"],
                            merchant=parsed_tx["merchant"],
                            category=parsed_tx["category"],
                            ingestion_source="bni_email",
                            external_id=msg_id,
                        )
                        if success:
                            inserted_count += 1

        mail.logout()
        logger.info("Successfully fetched %d new BNI transactions from Gmail.", inserted_count)
        return inserted_count
    except Exception as exc:
        logger.error("Error during BNI Gmail fetch: %s", exc)
        return 0
