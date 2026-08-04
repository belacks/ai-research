"""
CRN Finance Intelligence Module — Obsidian Vault Synchronizer
===============================================================
Syncs current SQLite financial health and monthly cashflow metrics
directly into vault at raw/financial/financial_health_YYYY-MM.md.
"""

import logging
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

from app.core.config import settings
from app.finance.finance_db import get_finance_summary, _DB_PATH

logger = logging.getLogger(__name__)


def sync_finance_to_obsidian() -> str | None:
    """Generate and write a monthly financial report markdown note directly into vault."""
    fin = get_finance_summary()
    if not fin:
        logger.error("Cannot sync finance to Obsidian: empty summary.")
        return None

    now = datetime.now(timezone.utc)
    ym_str = now.strftime("%Y-%m")
    date_str = now.strftime("%Y-%m-%d")

    try:
        target_dir = Path(settings.vault_raw_dir) / "financial"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"financial_health_{ym_str}.md"
    except Exception as exc:
        logger.warning("Finance vault dir %s not writable: %s. Using workspace fallback.", settings.vault_raw_dir, exc)
        from app.finance.finance_db import _PROJECT_ROOT
        target_dir = _PROJECT_ROOT / "shared_workspace" / "financial"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"financial_health_{ym_str}.md"

    # Fetch recent transactions
    recent_txs = []
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            recent_txs = [
                dict(r)
                for r in conn.execute(
                    "SELECT timestamp, amount, account_name, payment_method, merchant, category, ingestion_source FROM fin_transactions ORDER BY id DESC LIMIT 15"
                ).fetchall()
            ]
    except Exception as exc:
        logger.error("Failed to fetch transactions for vault sync: %s", exc)

    tx_rows = ""
    for tx in recent_txs:
        t_short = tx["timestamp"][:10] if tx.get("timestamp") else date_str
        amt_str = f"+Rp {tx['amount']:,.0f}" if tx['amount'] > 0 else f"-Rp {abs(tx['amount']):,.0f}"
        tx_rows += f"| {t_short} | {tx['account_name']} | {tx['payment_method']} | {tx['merchant']} | **{amt_str}** | {tx['ingestion_source']} |\n"

    md_content = f"""---
tags: [finance, cashflow, knowledge-vault]
related: ["[[financial-health]]", "[[cashflow-summary]]"]
updated: {date_str}
source: shared_workspace/crn_intelligence.db
---

# Financial Health & Cashflow Dashboard — {now.strftime('%B %Y')}

---

## 1. Executive Cashflow Outlook

| Metric | Amount | Description |
| :--- | :--- | :--- |
| **Total Liquid Cash Assets** | `Rp {fin.get('total_assets', 0.0):,.0f}` | Accounts & E-Wallets |
"""

    if 'total_liabilities' in fin:
        md_content += f"""| **Total Combined Obligations** | `Rp {fin.get('total_liabilities', 0.0):,.0f}` | Total Outstanding Principal |
| **Personal Share** | `Rp {fin.get('personal_liabilities', 0.0):,.0f}` | Personal Obligations |
| **Current Installment Due** | `Rp {fin.get('total_current_due', 0.0):,.0f}` | Current Period Dues |
"""

    table_body = tx_rows if tx_rows else "| - | - | - | No transactions logged yet | - | - |\n"

    md_content += f"""
---

## 2. Recent Real-Time Transactions

| Date | Account | Method | Merchant / Description | Amount | Source |
| :--- | :--- | :--- | :--- | :--- | :--- |
{table_body}---

*Synced automatically by CRN Finance Engine on {now.strftime('%Y-%m-%d %H:%M:%S UTC')}*
"""

    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info("Synced financial health note to Obsidian vault: %s", target_file)
        return str(target_file)
    except Exception as exc:
        logger.error("Failed to write financial note to Obsidian: %s", exc)
        return None
