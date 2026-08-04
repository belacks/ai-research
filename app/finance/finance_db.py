"""
CRN Finance Intelligence — Core Database & Transaction Manager
===============================================================
Manages SQLite tables for liquid accounts and financial transactions.
Integrated directly into `shared_workspace/crn_intelligence.db`.
Pluggable via `app.finance.plugin_loader` for optional local domain extensions.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.finance.plugin_loader import (
    get_plugin_summary_metrics,
    trigger_plugin_db_init,
    trigger_plugin_transaction_hook,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "shared_workspace" / "crn_intelligence.db"

_FINANCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fin_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    account_type        TEXT NOT NULL, -- 'bank' | 'ewallet' | 'cash'
    current_balance     REAL NOT NULL DEFAULT 0.0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fin_transactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    amount              REAL NOT NULL, -- Negative for expense, positive for income
    account_name        TEXT NOT NULL,
    payment_method      TEXT NOT NULL, -- 'QRIS' | 'Transfer' | 'PayLater' | 'Cash'
    merchant            TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT 'Uncategorized',
    ingestion_source    TEXT NOT NULL, -- 'bni_email' | 'statement_pdf' | 'telegram_manual'
    external_id         TEXT UNIQUE
);
"""

# Default open-source seed accounts
_BASELINE_ACCOUNTS = [
    ("Primary Bank Account", "bank", 0.0),
    ("Primary E-Wallet", "ewallet", 0.0),
    ("Cash in Hand", "cash", 0.0),
]


def init_finance_db() -> None:
    """Create core financial tables and trigger loaded plugin DB initializations."""
    try:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now(timezone.utc).isoformat()
        
        with sqlite3.connect(_DB_PATH) as conn:
            conn.executescript(_FINANCE_SCHEMA_SQL)
            
            # Seed default accounts if DB is completely empty
            cursor = conn.execute("SELECT COUNT(*) FROM fin_accounts")
            if cursor.fetchone()[0] == 0:
                for name, atype, bal in _BASELINE_ACCOUNTS:
                    conn.execute(
                        "INSERT INTO fin_accounts (name, account_type, current_balance, updated_at) VALUES (?, ?, ?, ?)",
                        (name, atype, bal, now_str),
                    )
                logger.info("Seeded %d core finance accounts into DB.", len(_BASELINE_ACCOUNTS))

            # Trigger dynamic plugin DB initialization (e.g. local domain extensions)
            trigger_plugin_db_init(conn)

        logger.info("Core Finance DB initialized successfully.")
    except Exception as exc:
        logger.error("Failed to initialize Core Finance DB: %s", exc)


def get_finance_summary() -> dict:
    """Return aggregated financial summary combining core liquid assets and plugin metrics."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            
            # Assets Total
            tot_assets = conn.execute("SELECT SUM(current_balance) FROM fin_accounts").fetchone()[0] or 0.0
            
            # Collect plugin metrics (e.g., domain extensions, pending dues)
            plugin_metrics = get_plugin_summary_metrics(conn)

            summary = {
                "total_assets": tot_assets,
            }
            summary.update(plugin_metrics)
            return summary
    except Exception as exc:
        logger.error("Failed to fetch finance summary: %s", exc)
        return {}


def insert_transaction(
    amount: float,
    account_name: str,
    payment_method: str,
    merchant: str,
    category: str = "Uncategorized",
    ingestion_source: str = "telegram_manual",
    external_id: str | None = None,
    tx_timestamp: str | None = None,
) -> bool:
    """Insert a transaction into fin_transactions. Deduplicates on external_id or amount window."""
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        final_ts = tx_timestamp if tx_timestamp else now_str
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            
            # Fuzzy Deduplication Check
            cursor = conn.execute(
                """SELECT id, ingestion_source, external_id FROM fin_transactions 
                   WHERE account_name LIKE ? AND ABS(amount - ?) < 1000.0 
                   AND timestamp >= datetime('now', '-7 days')""",
                (f"%{account_name}%", amount),
            )
            match = cursor.fetchone()
            if match:
                if ingestion_source == "bni_email" and match["ingestion_source"] == "telegram_manual":
                    conn.execute("UPDATE fin_transactions SET external_id = ?, amount = ? WHERE id = ?", (external_id, amount, match["id"]))
                    logger.info("Linked BNI email %s to existing manual transaction id %d", external_id, match["id"])
                    return True
                elif ingestion_source == "telegram_manual" and match["ingestion_source"] == "bni_email":
                    logger.info("Manual transaction matches existing BNI email transaction id %d, skipping", match["id"])
                    return True

            conn.execute(
                """INSERT INTO fin_transactions
                   (timestamp, amount, account_name, payment_method, merchant, category, ingestion_source, external_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (final_ts, amount, account_name, payment_method, merchant, category, ingestion_source, external_id),
            )
            
            # Update account balance for live transactions
            if ingestion_source in ["telegram_manual", "bni_email"]:
                conn.execute(
                    "UPDATE fin_accounts SET current_balance = current_balance + ?, updated_at = ? WHERE name LIKE ?",
                    (amount, now_str, f"%{account_name}%"),
                )
                
            # Trigger transaction hook for loaded plugins (e.g. local domain extensions)
            trigger_plugin_transaction_hook(conn, amount, account_name, payment_method, merchant, category)

        logger.info("Inserted transaction: %s Rp %.2f at %s", payment_method, amount, merchant)
        return True
    except sqlite3.IntegrityError:
        logger.debug("Duplicate transaction ignored (external_id=%s)", external_id)
        return False
    except Exception as exc:
        logger.error("Failed to insert transaction: %s", exc)
        return False


# Auto-initialize database on import
init_finance_db()
