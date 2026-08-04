"""
CRN Research Node — Universal 2-Tier Intent Pipeline & Rate Limiter
=====================================================================
Modular router parsing free-text queries, command fast-paths, and multi-intent LLM pipelines.
"""

import json
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Any
from datetime import datetime, timezone

from app.core.config import settings
from app.agent.claw_logic import summarize_with_cascade

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

USER_REQUEST_TIMESTAMPS: dict[int, list[float]] = {}


def check_user_rate_limit(user_id: int, max_req: int = 6, window_sec: int = 60) -> tuple[bool, int]:
    """
    Sliding window anti-spam rate limiter per Telegram user.
    Returns (is_limited, seconds_until_reset)
    """
    now = datetime.now(timezone.utc).timestamp()
    timestamps = USER_REQUEST_TIMESTAMPS.get(user_id, [])
    valid_timestamps = [t for t in timestamps if now - t < window_sec]
    USER_REQUEST_TIMESTAMPS[user_id] = valid_timestamps

    if len(valid_timestamps) >= max_req:
        oldest = valid_timestamps[0]
        wait_sec = int(window_sec - (now - oldest)) + 1
        return True, max(wait_sec, 1)

    USER_REQUEST_TIMESTAMPS[user_id].append(now)
    return False, 0


def classify_multi_intent_pipeline(user_text: str) -> list[dict]:
    """
    Multi-Intent Chained Router covering 100% of /menu features.
    Returns list of action step dicts: [{"intent": str, "param": str, "explanation": str}]
    """
    raw_lower = user_text.lower().strip()

    # Tier 1: Fast Regex / Pattern Matching ONLY for Standalone Simple Commands
    has_modifiers = any(
        k in raw_lower
        for k in [
            "then", "and", "english", "indonesian", "bahasa", "inggris", "using", "with",
            "model", "version", "gemma", "gemini", "ollama", "3.5", "3,5", "3.6", "3,6", "31b", "26b"
        ]
    )

    if not has_modifiers:
        if raw_lower.startswith("http://") or raw_lower.startswith("https://"):
            return [{"intent": "JOB_EVAL", "param": user_text.strip(), "explanation": "Evaluate single job URL"}]

        digest_kw = ["coffee digest", "morning digest", "morning coffee", "daily digest", "morning digestion"]
        if raw_lower in digest_kw or (any(k in raw_lower for k in digest_kw) and len(raw_lower.split()) <= 3):
            return [{"intent": "DAILY_DIGEST", "param": "", "explanation": "Run Daily Morning Coffee Digest"}]

        if any(k in raw_lower for k in ["export db", "export database", "download db", "download database"]):
            return [{"intent": "NEWS_EXPORT_DB", "param": "", "explanation": "Export SQLite database"}]

        if any(k in raw_lower for k in ["export xlsx", "export excel", "download xlsx", "download excel"]):
            return [{"intent": "NEWS_EXPORT_XLSX", "param": "", "explanation": "Export Excel spreadsheet"}]

        # Finance & Export fast paths
        is_export_action = any(k in raw_lower for k in ["export", "report", "breakdown", "download", "pdf", "excel", "xlsx"])
        is_finance_concept = any(k in raw_lower for k in ["finance", "financial", "money", "budget", "ledger", "balance", "status", "statement"])
        
        if is_export_action and is_finance_concept and raw_lower not in ["finance", "finance status"]:
            return [{"intent": "EXPORT_FINANCE", "param": "", "explanation": "Generate and send Excel breakdown and PDF report"}]

        if raw_lower.startswith("/export") or raw_lower.startswith("/report") or raw_lower.startswith("/breakdown"):
            return [{"intent": "EXPORT_FINANCE", "param": "", "explanation": "Generate and send Excel breakdown and PDF report"}]

        if raw_lower.startswith("/finance") or raw_lower in ["finance", "finance status", "my budget", "financial status"]:
            return [{"intent": "FINANCE_STATUS", "param": "", "explanation": "Display financial summary & burn rate"}]

        if raw_lower.startswith("/email") or raw_lower.startswith("/sync_email") or raw_lower in ["sync email", "check email", "check bni"]:
            return [{"intent": "SYNC_EMAIL", "param": "", "explanation": "Trigger BNI Wondr transaction email IMAP sync"}]

        # Job & News fast paths (Exact or strict 2-3 word match)
        if raw_lower in ["job scan", "do job scan", "run job scan", "scan jobs"]:
            return [{"intent": "JOB_SCAN", "param": "", "explanation": "Run autonomous job scan"}]

        if raw_lower in ["job view", "view jobs", "job pipeline", "my jobs", "show jobs", "/jobs"]:
            return [{"intent": "JOB_VIEW", "param": "", "explanation": "View job pipeline"}]

        if raw_lower in ["news scan", "run news scan", "scan news", "sentiment scan"]:
            return [{"intent": "NEWS_SCAN", "param": "", "explanation": "Run market news sentiment scan"}]

        # Model switching fast paths
        model_fast_map = {
            "gemini 3.6": ("SET_MODEL", "gemini-3.6-flash"),
            "gemma 31b": ("SET_MODEL", "gemma-4-31b-it"),
            "gemma 4 31b": ("SET_MODEL", "gemma-4-31b-it"),
            "gemma 26b": ("SET_MODEL", "gemma-4-26b-a4b-it"),
            "gemma 4 26b": ("SET_MODEL", "gemma-4-26b-a4b-it"),
            "gemma moe": ("SET_MODEL", "gemma-4-26b-a4b-it"),
            "flash lite": ("SET_MODEL", "gemini-3.5-flash-lite"),
            "gemini 3.5 flash lite": ("SET_MODEL", "gemini-3.5-flash-lite"),
            "gemini 3.5": ("SET_MODEL", "gemini-3.5-flash"),
            "use ollama": ("SET_MODEL", "ollama"),
            "auto model": ("SET_MODEL", "auto"),
        }
        for kw, (it, pr) in model_fast_map.items():
            if raw_lower == kw or (kw in raw_lower and len(raw_lower.split()) <= 4):
                return [{"intent": it, "param": pr, "explanation": f"Set model to {pr}"}]

        # Language switching fast paths
        if any(kw in raw_lower for kw in ["switch to english", "use english", "set lang en", "english output"]):
            return [{"intent": "SET_LANG", "param": "en", "explanation": "Set output language to English"}]
        if any(kw in raw_lower for kw in ["switch to indonesian", "switch to bahasa", "use indonesian", "use bahasa", "set lang id"]):
            return [{"intent": "SET_LANG", "param": "id", "explanation": "Set output language to Indonesian"}]

    # Tier 2: Cloud Gemini 3.5 Flash-Lite Multi-Intent Orchestrator
    router_prompt = f"""You are CRN's AI Intent Orchestrator for Telegram chat.
Below is the user message and current conversation context. Your job is to select the exact single target handler or classify the user's intent.

Cover 100% of Crawl Research Node (CRN) features:

Allowed Intent Codes:
- "SET_MODEL": change active AI model engine (param = target model key, e.g. "gemini-3.6-flash", "gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.5-flash-lite", "gemini-3.5-flash", "ollama", "auto").
- "SET_LANG": change output language (param = "id" or "en").
- "JOB_EVAL": evaluate fit for a job URL or list of job URLs (param = URL or comma-separated URLs).
- "JOB_STATUS": update job application status (param = raw prompt text or status update command).
- "JOB_SCAN": run autonomous job scan across job boards.
- "JOB_VIEW": ONLY for explicitly requesting the job application tracking list (e.g. "/jobs", "show job list", "view job pipeline").
- "JOB_EXPORT_PDF": export/generate styled PDF job report.
- "NEWS_SCAN": run live market news sentiment scanner.
- "NEWS_EXPORT_DB": export SQLite database (.db).
- "NEWS_EXPORT_XLSX": export processed news Excel (.xlsx).
- "CRAWL_DEFAULT": run deep crawl sweep on default target list.
- "CRAWL_LOCAL": run deep crawl sweep on local Indonesian sources.
- "CRAWL_GLOBAL": run deep crawl sweep on global research sources.
- "CRAWL_CUSTOM": deep crawl specific non-job URLs provided by user (param = comma-separated URLs).
- "DAILY_DIGEST": generate Daily Morning Coffee Digest.
- "FINANCE_QUERY": for any spending, budget allowance, or financial decision questions (e.g. "can I spend X?", "can I buy Y?", "is 60k for snacks fine?").
- "FINANCE_STATUS": ONLY for requesting static financial summary text snapshot (e.g. "/finance", "finance status", "my balance").
- "EXPORT_FINANCE": for exporting or sending Excel breakdown/PDF report documents of money/finance status (e.g. "export a report of my money status", "send pdf report", "download excel").
- "QUICK_LOG": log expense, income, or transaction manually (param = raw expense command or text).
- "RAG_ALL": for general questions, project status queries, career advice, technical questions, thesis advice, or conversational requests (e.g. "what projects should I work on?", "what is my project status?").
- "UNNECESSARY": prompt is complete gibberish, filler, or spam without actionable intent.

User Input: "{user_text}"

Respond ONLY in valid JSON format:
{{
  "pipeline": [
    {{
      "intent": "<INTENT_CODE>",
      "param": "<extracted parameter or query>",
      "explanation": "<short rationale>"
    }}
  ]
}}
"""
    try:
        raw_res = summarize_with_cascade(router_prompt, "raw_prompt", model_override="gemini-3.5-flash-lite")
        start_idx = raw_res.find("{")
        end_idx = raw_res.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(raw_res[start_idx:end_idx])
            pipeline = data.get("pipeline", [])
            if pipeline:
                return pipeline
    except Exception as exc:
        logger.warning("Multi-Intent Router error: %s", exc)

    return [{"intent": "RAG_ALL", "param": user_text, "explanation": "Default knowledge search"}]
