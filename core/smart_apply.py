"""
Smart Apply — safe routing for job application UX.

- ignore:       do nothing (reject / skip)
- manual_apply: show Apply Now; user clicks to open link
- auto_open:    open apply link in default browser (no form submit)
"""

from __future__ import annotations

import webbrowser
from typing import Any

from utils.helpers import logger


def get_apply_link(job: dict[str, Any]) -> str:
    """Resolve canonical apply URL from assorted job shapes (RapidAPI, scrapers)."""
    if not isinstance(job, dict):
        return ""
    for key in (
        "apply_link",
        "apply_url",
        "applyUrl",
        "job_apply_link",
        "url",
        "job_link",
        "jobLink",
        "link",
        "apply",
    ):
        v = job.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def map_execution_to_decision_label(execution_status: str) -> str:
    """
    Map pipeline execution_status to smart-apply *decision* word.

    ``reject`` → first rule in ``process_job_application`` (ignore).
    Anything else → confidence rules apply.
    """
    s = (execution_status or "").strip().upper()
    if s in ("SKIP", "ABORT", "SAFETY_BLOCKED"):
        return "reject"
    return "apply"


def process_job_application(job: dict, decision: str, confidence: int | float) -> dict:
    """
    Returns:
        {
            "action": "ignore" | "manual_apply" | "auto_open",
            "link": str,
        }

    Rules:
    - decision == "reject" → ignore
    - confidence >= 80 → auto_open
    - else → manual_apply (covers < 75 and 75–79 medium band)

    Resolves link from ``apply_link``, ``url``, ``job_link``, then ``get_apply_link`` keys.
    """
    j = job if isinstance(job, dict) else {}
    raw = j.get("apply_link") or j.get("url") or j.get("job_link")
    link = raw.strip() if isinstance(raw, str) else ""
    if not link:
        link = get_apply_link(j)

    d = str(decision or "").lower().strip()
    if d in ("reject", "skip", "aborted", "abort"):
        return {"action": "ignore", "link": j.get("apply_link") or link or None}

    try:
        c = int(float(confidence))
    except (TypeError, ValueError):
        c = 0

    if c >= 80:
        return {"action": "auto_open", "link": j.get("apply_link") or link}
    return {"action": "manual_apply", "link": j.get("apply_link") or link}


def smart_action_to_execution_status(action: str) -> str:
    if action == "ignore":
        return "SKIP"
    if action == "manual_apply":
        return "PENDING_REVIEW"
    if action == "auto_open":
        return "AUTO_APPLY"
    return "PENDING_REVIEW"


def ui_decision_label(smart_action: str) -> str:
    """Dashboard: Apply vs Reject column."""
    if smart_action == "ignore":
        return "Reject"
    return "Apply"


def ui_action_label(smart_action: str) -> str:
    if smart_action == "ignore":
        return "Ignore"
    if smart_action == "auto_open":
        return "Auto"
    return "Manual"


def open_application(link: str) -> bool:
    """Open URL in default browser only — never submits forms."""
    if not link or not str(link).strip():
        logger.warning("[SmartApply] open_application called with empty link")
        return False
    try:
        webbrowser.open(str(link).strip(), new=2)
        logger.info("[SmartApply] Opened browser tab: %s…", link[:80])
        return True
    except Exception as exc:
        print("Error:", exc)
        logger.warning("[SmartApply] webbrowser.open failed: %s", exc)
        return False
