"""
CRN Finance Intelligence — Bank Parsers Package
=================================================
Adapter package for email and statement transaction parsers.
"""

from app.finance.parsers.bni_parser import fetch_bni_transactions_imap


def fetch_all_configured_bank_transactions(gmail_user: str, gmail_app_password: str) -> int:
    """
    Triggers all active bank email crawlers (BNI, Mandiri, BCA, etc.).
    """
    total = 0
    total += fetch_bni_transactions_imap(gmail_user, gmail_app_password)
    return total
