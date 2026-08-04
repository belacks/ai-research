"""
OpenClaw Research Node — Telegram Notifier
===========================================
Lightweight, single-attempt push notification via the Telegram Bot API.

Usage:
    from app.core.notifier import send_telegram_alert, escape_html
    ok = send_telegram_alert("<b>Briefing ready</b> — 3 new signals detected.")

Formatting reference (HTML mode):
    <b>bold</b>  <i>italic</i>  <code>monospace</code>  <pre>block</pre>

Design decisions (Phase 1):
  • Returns bool so callers can react to delivery failure.
  • Single attempt, no retry / backoff — keep the footprint small.
  • 10-second timeout — don't block the agent's main thread.
  • parse_mode='HTML' — far more resilient to LLM output than Markdown.
"""

import html
import logging

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TELEGRAM_API_URL = (
    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
)
_REQUEST_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def escape_html(text: str) -> str:
    """Escape ``<``, ``>``, and ``&`` so raw text is safe inside HTML messages."""
    return html.escape(text, quote=False)


def md_to_telegram_html(text: str) -> str:
    """
    Convert Markdown from LLM output into Telegram-safe HTML tags.
    Handles:
      • **bold** → <b>bold</b>
      • *italic* → <i>italic</i>
      • # Headers → <b>Headers</b>
      • [title](url) → <a href="url">title</a>
      • * bullet / - bullet → • bullet
      • `code` → <code>code</code>
    """
    import re

    # 1. Preserve Markdown links before HTML escaping
    links = []
    def link_repl(m):
        links.append((m.group(1), m.group(2)))
        return f"___LINK_{len(links)-1}___"

    text_with_placeholders = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', link_repl, text)

    # 2. Escape HTML entities
    safe = html.escape(text_with_placeholders, quote=False)

    # 3. Headers # ## ### → <b>Header</b>
    safe = re.sub(r'^(#{1,6})\s*(.+)$', r'<b>\2</b>', safe, flags=re.MULTILINE)

    # 4. **bold** → <b>bold</b>
    safe = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', safe)

    # 5. `code` → <code>code</code>
    safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)

    # 6. Bullet lists
    lines = safe.split('\n')
    formatted_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('* ') or stripped.startswith('- '):
            formatted_lines.append('• ' + line.lstrip()[2:])
        else:
            formatted_lines.append(line)
    safe = '\n'.join(formatted_lines)

    # 7. Convert single asterisk *italic* → <i>italic</i>
    safe = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', safe)

    # 8. Restore links as Telegram HTML tags
    for idx, (link_text, link_url) in enumerate(links):
        safe_link_text = html.escape(link_text, quote=False)
        safe = safe.replace(f"___LINK_{idx}___", f'<a href="{link_url}">{safe_link_text}</a>')

    return safe


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_telegram_alert(message: str) -> bool:
    """
    Send an HTML-formatted message to the configured Telegram chat.

    Parameters
    ----------
    message : str
        The alert body.  Telegram HTML tags are supported
        (e.g. <b>bold</b>, <i>italic</i>, <code>code</code>).
        Use ``escape_html()`` on any untrusted / LLM-generated text
        before embedding it in the message.

    Returns
    -------
    bool
        True if the Telegram API accepted the message (HTTP 200 + ok=True),
        False otherwise.
    """
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(
            _TELEGRAM_API_URL,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

        # Telegram returns {"ok": true, ...} on success.
        if response.ok and response.json().get("ok"):
            logger.info("Telegram alert sent successfully.")
            return True

        # If HTML parsing failed, retry without formatting
        if response.status_code == 400 and "parse entities" in response.text:
            logger.warning("HTML parse error — retrying as plain text.")
            payload.pop("parse_mode")
            response = requests.post(
                _TELEGRAM_API_URL,
                json=payload,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            if response.ok and response.json().get("ok"):
                logger.info("Telegram alert sent (plain text fallback).")
                return True

        # HTTP succeeded but Telegram rejected the request (bad token, etc.)
        logger.error(
            "Telegram API rejected the request  ·  status=%s  ·  body=%s",
            response.status_code,
            response.text,
        )
        return False

    except requests.exceptions.Timeout:
        logger.error(
            "Telegram API timed out after %ds — possible network issue.",
            _REQUEST_TIMEOUT_SECONDS,
        )
        return False

    except requests.exceptions.ConnectionError:
        logger.error(
            "Could not connect to Telegram API — check DNS / internet access."
        )
        return False

    except requests.exceptions.RequestException as exc:
        logger.error("Unexpected error sending Telegram alert: %s", exc)
        return False

