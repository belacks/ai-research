"""
CRN — Target Source Loader
============================
Loads crawl targets from ``targets.yaml`` at the project root.
Returns a validated list[dict] with keys: url, category, scope.
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = {"url", "category", "scope"}
_VALID_SCOPES = {"global", "local"}

# Resolve relative to project root: app/core/targets_loader.py → ../../targets.yaml
_TARGETS_FILE = Path(__file__).resolve().parent.parent.parent / "targets.yaml"


def load_targets() -> list[dict]:
    """Load and validate crawl targets from targets.yaml.

    Returns an empty list (never raises) if the file is missing or
    entirely malformed, so the import-time call in prompts.py is safe.
    """
    if not _TARGETS_FILE.exists():
        logger.error(
            "FATAL: targets.yaml not found at %s — returning empty target list.",
            _TARGETS_FILE,
        )
        return []

    try:
        raw = yaml.safe_load(_TARGETS_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.error("FATAL: Failed to parse targets.yaml — %s", exc)
        return []

    if not isinstance(raw, dict) or "targets" not in raw:
        logger.error("FATAL: targets.yaml must contain a top-level 'targets' key.")
        return []

    entries = raw["targets"]
    if not isinstance(entries, list):
        logger.error("FATAL: 'targets' must be a list of dicts.")
        return []

    cleaned: list[dict] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning("Skipping target #%d — not a dict: %s", i, entry)
            continue

        missing = _REQUIRED_KEYS - entry.keys()
        if missing:
            logger.warning(
                "Skipping target #%d — missing keys %s: %s", i, missing, entry
            )
            continue

        scope = entry["scope"]
        if scope not in _VALID_SCOPES:
            logger.warning(
                "Skipping target #%d — invalid scope '%s' (must be global|local): %s",
                i,
                scope,
                entry,
            )
            continue

        cleaned.append({
            "url": str(entry["url"]),
            "category": str(entry["category"]),
            "scope": scope,
        })

    logger.info(
        "Loaded %d targets from targets.yaml (%d global, %d local).",
        len(cleaned),
        sum(1 for e in cleaned if e["scope"] == "global"),
        sum(1 for e in cleaned if e["scope"] == "local"),
    )
    return cleaned
