"""
OpenClaw Research Node — Stateful Cron Manager
================================================
Entry-point for the `research-agent` Docker service
(see docker-compose.yml → command: python app/scheduler/cron_manager.py).

Responsibilities:
  1. On startup, check last_run.json for catch-up logic.
     If the file is missing or stale (older than SCHEDULE_INTERVAL_HOURS),
     run the research agent immediately so we never miss a cycle.
  2. Schedule recurring runs via the `schedule` library.
  3. Keep the process alive with a while-True / sleep(60) heartbeat.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule

from app.core.config import settings

# ---------------------------------------------------------------------------
# Logging — timestamps are essential for Docker log tailing
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_LAST_RUN_FILE = _PROJECT_ROOT / "shared_workspace" / "last_run.json"
_TRIGGER_FILE = _PROJECT_ROOT / "shared_workspace" / "trigger.txt"

# How often (in heartbeat ticks) to print a status line.
# 1 tick = 60 s sleep, so 10 ticks ≈ every 10 minutes.
_HEARTBEAT_LOG_EVERY = 10

# ---------------------------------------------------------------------------
# Agent entry-point
# ---------------------------------------------------------------------------
from app.agent.claw_logic import execute_research_agent  # noqa: E402


# ---------------------------------------------------------------------------
# last_run.json helpers
# ---------------------------------------------------------------------------

def _read_last_run() -> datetime | None:
    """
    Read the last successful run timestamp from last_run.json.

    Returns None if the file doesn't exist, is corrupt, or unreadable.
    """
    if not _LAST_RUN_FILE.exists():
        logger.info("No last_run.json found — first run on this node.")
        return None

    try:
        data = json.loads(_LAST_RUN_FILE.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["last_run_utc"])
        # Ensure the parsed timestamp is timezone-aware (UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Corrupt last_run.json, treating as first run: %s", exc)
        return None


def _write_last_run() -> None:
    """Persist the current UTC timestamp to last_run.json."""
    payload = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
    }
    _LAST_RUN_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Updated last_run.json → %s", payload["last_run_utc"])


# ---------------------------------------------------------------------------
# Wrapped execution (run agent + persist timestamp)
# ---------------------------------------------------------------------------

def _run_and_record() -> None:
    """Execute the research agent, then stamp last_run.json on success."""
    logger.info("=" * 60)
    logger.info("Research cycle starting")
    logger.info("=" * 60)

    try:
        execute_research_agent()
        _write_last_run()
        logger.info("Research cycle completed successfully.")
    except Exception:
        logger.exception("Research cycle FAILED — last_run.json NOT updated.")


# ---------------------------------------------------------------------------
# Manual trigger detection
# ---------------------------------------------------------------------------

def _check_manual_trigger() -> bool:
    """
    Check if the dashboard dropped a trigger.txt signal file.
    If found, run the agent immediately and return True.
    """
    if _TRIGGER_FILE.exists():
        logger.info("🚀 Manual trigger detected (trigger.txt found). Running agent now …")
        _run_and_record()
        return True
    return False


# ---------------------------------------------------------------------------
# Catch-up logic
# ---------------------------------------------------------------------------

def _maybe_catch_up() -> None:
    """
    Run the agent immediately if we are overdue.

    'Overdue' means:
      • last_run.json does not exist, OR
      • (now − last_run) > SCHEDULE_INTERVAL_HOURS
    """
    interval_hours = settings.schedule_interval_hours
    last_run = _read_last_run()

    if last_run is None:
        logger.info("Catch-up triggered — no previous run recorded.")
        _run_and_record()
        return

    elapsed_hours = (
        datetime.now(timezone.utc) - last_run
    ).total_seconds() / 3600

    if elapsed_hours >= interval_hours:
        logger.info(
            "Catch-up triggered — last run was %.1fh ago (threshold: %dh).",
            elapsed_hours,
            interval_hours,
        )
        _run_and_record()
    else:
        remaining = interval_hours - elapsed_hours
        logger.info(
            "No catch-up needed — last run was %.1fh ago, next in ~%.1fh.",
            elapsed_hours,
            remaining,
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry-point: catch-up → schedule → heartbeat loop."""
    logger.info("OpenClaw Cron Manager starting  ·  interval=%dh", settings.schedule_interval_hours)

    # 1. Catch-up check on boot
    _maybe_catch_up()

    # 2. Register recurring job
    schedule.every(settings.schedule_interval_hours).hours.do(_run_and_record)
    logger.info(
        "Scheduled recurring job: every %dh. Entering heartbeat loop …",
        settings.schedule_interval_hours,
    )

    # 3. Heartbeat — check scheduled jobs + manual triggers every 60 s
    tick = 0
    try:
        while True:
            # Check for manual trigger from dashboard
            _check_manual_trigger()

            # Check scheduled recurring jobs
            schedule.run_pending()

            # Periodic heartbeat so `docker logs -f` shows signs of life
            tick += 1
            if tick % _HEARTBEAT_LOG_EVERY == 0:
                next_run = schedule.next_run()
                next_str = next_run.strftime("%H:%M:%S") if next_run else "—"
                logger.info(
                    "♥ heartbeat  ·  trigger=%s  ·  next_scheduled=%s",
                    "PENDING" if _TRIGGER_FILE.exists() else "idle",
                    next_str,
                )

            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Cron Manager stopped by user (KeyboardInterrupt).")


if __name__ == "__main__":
    main()
