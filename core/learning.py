"""
Agent behavior learning — track AI decisions and adapt strictness over time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.db_adapter import _get_conn
from utils.helpers import logger

_STRATEGY_FILE = Path(__file__).resolve().parent.parent / "db" / "learning_strategy.json"


def record_learning_event(
    application_id: int | None,
    decision: str,
    result: str,
    confidence: int,
    reason: str = "",
    job_title: str = "",
    company: str = "",
) -> None:
    """Insert a row into agent_learning."""
    ts = datetime.now(timezone.utc).isoformat()
    reason = (reason or "")[:2000]
    jt = (job_title or "")[:500]
    co = (company or "")[:500]
    sql = """
    INSERT INTO agent_learning
        (application_id, decision, result, confidence, reason, job_title, company, logged_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    try:
        with _get_conn() as conn:
            conn.execute(
                sql,
                (application_id, decision, result, int(confidence), reason, jt, co, ts),
            )
    except Exception as exc:
        logger.warning(f"[Learning] record_learning_event failed: {exc}")


def update_strategy() -> dict[str, Any]:
    """
    Analyze recent agent_learning + applications pipeline labels.
    Adjust min_score delta (stricter if many rejections).
    """
    stats = {
        "samples": 0,
        "rejected": 0,
        "auto_applied": 0,
        "recommended": 0,
        "avg_confidence": 0.0,
        "min_score_delta": 0,
        "learning_status": "idle",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT decision, result, confidence FROM agent_learning ORDER BY id DESC LIMIT 500"
            ).fetchall()
    except Exception as exc:
        logger.debug(f"[Learning] update_strategy read failed: {exc}")
        stats["learning_status"] = f"db_error: {exc}"
        _write_strategy(stats)
        return stats

    samples = [dict(r) for r in rows] if rows else []
    stats["samples"] = len(samples)
    if not samples:
        stats["learning_status"] = "collecting_data"
        _write_strategy(stats)
        return stats

    confs: list[int] = []
    rejected = 0
    auto_applied = 0
    recommended = 0
    for r in samples:
        try:
            confs.append(int(r.get("confidence") or 0))
        except (TypeError, ValueError):
            pass
        dec = (r.get("decision") or "").lower()
        res = (r.get("result") or "").lower()
        if res == "rejected" or dec == "reject":
            rejected += 1
        elif res == "auto_applied":
            auto_applied += 1
        elif res == "recommended":
            recommended += 1

    stats["rejected"] = rejected
    stats["auto_applied"] = auto_applied
    stats["recommended"] = recommended
    stats["avg_confidence"] = round(sum(confs) / max(len(confs), 1), 1)

    n = len(samples)
    reject_rate = rejected / max(n, 1)
    # Stricter matching if rejections dominate; relax slightly if few rejects
    delta = 0
    if reject_rate > 0.55 and n >= 5:
        delta = min(15, 5 + int((reject_rate - 0.55) * 30))
        stats["learning_status"] = "strictening"
    elif reject_rate < 0.25 and n >= 8:
        delta = -3
        stats["learning_status"] = "broadening"
    else:
        stats["learning_status"] = "stable"

    stats["min_score_delta"] = delta
    _write_strategy(stats)
    logger.info(
        f"[Learning] strategy update | n={n} reject_rate={reject_rate:.2f} → min_score_delta={delta}"
    )
    return stats


def get_learning_min_score_delta() -> int:
    """Positive = stricter (raise bar for rank_jobs)."""
    try:
        if _STRATEGY_FILE.exists():
            data = json.loads(_STRATEGY_FILE.read_text(encoding="utf-8"))
            return int(data.get("min_score_delta") or 0)
    except Exception:
        pass
    return 0


def get_learning_status_snapshot() -> dict[str, Any]:
    """Lightweight read for diagnostics."""
    snap = dict(_read_strategy())
    snap.setdefault("learning_status", "unknown")
    return snap


def _write_strategy(obj: dict) -> None:
    try:
        _STRATEGY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STRATEGY_FILE.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"[Learning] could not write strategy file: {exc}")


def _read_strategy() -> dict:
    try:
        if _STRATEGY_FILE.exists():
            return json.loads(_STRATEGY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def aggregate_application_ai_stats() -> dict[str, Any]:
    """Counts from applications table for diagnostics."""
    out = {
        "total_auto_applied": 0,
        "total_rejected": 0,
        "avg_confidence": None,
        "learning_status": get_learning_status_snapshot().get("learning_status", "—"),
    }
    try:
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN pipeline_decision_status = 'auto_applied' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN pipeline_decision_status = 'rejected' THEN 1 ELSE 0 END),
                  AVG(CAST(ai_fit_confidence AS REAL))
                FROM applications
                """
            ).fetchone()
        if row:
            vals = list(row.values()) if hasattr(row, "values") else [row[0], row[1], row[2]]
            out["total_auto_applied"] = int(vals[0] or 0)
            out["total_rejected"] = int(vals[1] or 0)
            ac = vals[2]
            out["avg_confidence"] = round(float(ac), 1) if ac is not None else None
    except Exception as exc:
        logger.debug(f"[Learning] aggregate_application_ai_stats: {exc}")
        out["learning_status"] = f"stats_error: {exc}"
    return out


__all__ = [
    "record_learning_event",
    "update_strategy",
    "get_learning_min_score_delta",
    "get_learning_status_snapshot",
    "aggregate_application_ai_stats",
]
