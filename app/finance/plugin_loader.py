"""
CRN Finance Intelligence — Dynamic Plugin Loader
=================================================
Discovers and loads optional local extensions from `app/finance/plugins/`.
Keeps the public core CRN financial engine clean and decoupled from local domain extensions.
"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"

_LOADED_PLUGINS: List[Any] = []


def load_finance_plugins() -> List[Any]:
    """Dynamically import all python packages/modules inside app/finance/plugins/."""
    global _LOADED_PLUGINS
    if _LOADED_PLUGINS:
        return _LOADED_PLUGINS

    loaded = []
    if not _PLUGINS_DIR.exists():
        return loaded

    for _, name, ispkg in pkgutil.iter_modules([str(_PLUGINS_DIR)]):
        if name.startswith("__") or name.startswith("."):
            continue
        try:
            mod = importlib.import_module(f"app.finance.plugins.{name}")
            loaded.append(mod)
            logger.info("Loaded finance plugin: %s", name)
        except Exception as exc:
            logger.warning("Failed to load finance plugin '%s': %s", name, exc)

    _LOADED_PLUGINS = loaded
    return loaded


def trigger_plugin_db_init(conn: Any) -> None:
    """Trigger DB schema creation and seeding hooks for loaded plugins."""
    plugins = load_finance_plugins()
    for plugin in plugins:
        if hasattr(plugin, "init_plugin_db") and callable(plugin.init_plugin_db):
            try:
                plugin.init_plugin_db(conn)
            except Exception as exc:
                logger.error("Error running DB init for plugin %s: %s", getattr(plugin, "__name__", "unknown"), exc)


def trigger_plugin_transaction_hook(conn: Any, amount: float, account_name: str, payment_method: str, merchant: str, category: str) -> None:
    """Notify plugins when a transaction is recorded (e.g. for smart balance adjustment)."""
    plugins = load_finance_plugins()
    for plugin in plugins:
        if hasattr(plugin, "on_transaction_inserted") and callable(plugin.on_transaction_inserted):
            try:
                plugin.on_transaction_inserted(conn, amount, account_name, payment_method, merchant, category)
            except Exception as exc:
                logger.error("Error executing transaction hook for plugin %s: %s", getattr(plugin, "__name__", "unknown"), exc)


def get_plugin_summary_metrics(conn: Any) -> Dict[str, Any]:
    """Collect summary metrics from loaded plugins to enrich core finance summary."""
    metrics: Dict[str, Any] = {}
    plugins = load_finance_plugins()
    for plugin in plugins:
        if hasattr(plugin, "get_summary_metrics") and callable(plugin.get_summary_metrics):
            try:
                res = plugin.get_summary_metrics(conn)
                if isinstance(res, dict):
                    metrics.update(res)
            except Exception as exc:
                logger.error("Error getting metrics from plugin %s: %s", getattr(plugin, "__name__", "unknown"), exc)
    return metrics


def register_plugin_telegram_commands(app: Any) -> None:
    """Dynamically register Telegram command handlers for all loaded plugins."""
    plugins = load_finance_plugins()
    for plugin in plugins:
        if hasattr(plugin, "register_telegram_commands") and callable(plugin.register_telegram_commands):
            try:
                plugin.register_telegram_commands(app)
            except Exception as exc:
                logger.error("Error registering Telegram commands for plugin %s: %s", getattr(plugin, "__name__", "unknown"), exc)
