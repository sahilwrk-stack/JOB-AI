"""
Component 5 — Memory System
Self-learning store: tracks applications, outcomes, patterns.
Supports SQLite (local) and PostgreSQL (production) via db_adapter.
"""

import os
import json
import hashlib
from datetime import datetime
from typing import Optional

from core.db_adapter import _get_conn, IS_POSTGRES, DB_PATH, init_schema, build_application_upsert
from utils.helpers import logger


def _make_fingerprints(job: dict) -> tuple[str, str]:
    """Build (job_fingerprint, url_fingerprint) for duplicate detection."""
    raw = (
        (job.get("title",   "") or "").strip().lower() + "|" +
        (job.get("company", "") or "").strip().lower() + "|" +
        (job.get("location","") or "").strip().lower()
    )
    url = (job.get("apply_link","") or job.get("url","") or "").strip().lower()
    for p in ("?utm_", "&utm_", "?ref=", "&ref=", "?source="):
        if p in url:
            url = url[:url.index(p)]
    return hashlib.md5(raw.encode()).hexdigest(), hashlib.md5(url.encode()).hexdigest()


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    """Initialize the database schema and run migrations (idempotent)."""
    with _get_conn() as conn:
        init_schema(conn)
    backend = "PostgreSQL" if IS_POSTGRES else f"SQLite @ {DB_PATH}"
    logger.info(f"[Memory] Database initialized ({backend})")


# ── Application CRUD ──────────────────────────────────────────────────────────

def save_application(
    job: dict,
    match_result: dict,
    apply_decision: dict,
    candidate_tier: str = "",
) -> int:
    """
    Persist a job application record.

    Returns:
        Row ID of the inserted / replaced record.
    """
    match_score = match_result.get("match_score", 0)
    confidence  = match_result.get("confidence_score", 0)

    if match_score >= 85:
        quality = "Excellent"
    elif match_score >= 70:
        quality = "Good"
    elif match_score >= 50:
        quality = "Fair"
    else:
        quality = "Poor"

    sql = build_application_upsert()
    skills_matched = json.dumps(match_result.get("matched_skills", []))
    skills_missing = json.dumps(match_result.get("missing_critical_skills", []))
    job_fp, url_fp = _make_fingerprints(job)

    smart_action = apply_decision.get("smart_action", "") or (
        (match_result.get("smart_apply") or {}).get("action", "")
    )
    ai_decision_label = apply_decision.get("ai_decision_label", "") or ""

    ai_fit = match_result.get("ai_fit") or {}
    try:
        ai_fit_conf = int(ai_fit.get("confidence") or 0)
    except (TypeError, ValueError):
        ai_fit_conf = 0
    ai_fit_decision = str(ai_fit.get("decision") or "").strip()[:64]
    ai_fit_reason = str(ai_fit.get("reason") or "").strip()[:2000]
    pipeline_status = str(match_result.get("pipeline_decision_status") or "").strip()[:32]

    with _get_conn() as conn:
        cur = conn.execute(sql, (
            job.get("title", ""),
            job.get("company", ""),
            job.get("url", "") or job.get("apply_link", ""),
            job.get("location", ""),
            match_score,
            confidence,
            quality,
            candidate_tier,
            apply_decision.get("execution_status", ""),
            apply_decision.get("risk_level", ""),
            json.dumps(apply_decision.get("form_mapping", {})),
            datetime.utcnow().isoformat(),
            datetime.utcnow().isoformat(),
            job.get("_source", ""),
            job.get("salary", ""),
            skills_matched,
            skills_missing,
            job_fp,
            url_fp,
            smart_action,
            ai_decision_label,
            ai_fit_decision,
            ai_fit_conf,
            ai_fit_reason,
            pipeline_status,
        ))
        row_id = cur.lastrowid

    logger.debug(f"[Memory] Saved application id={row_id}: {job.get('title')} @ {job.get('company')}")
    return row_id


def update_outcome(job_url: str, outcome: str, callback: bool = False):
    """Update the result of an application (e.g. 'rejected', 'interview', 'offer')."""
    sql = """
    UPDATE applications
    SET outcome=?, callback_received=?, last_checked=?
    WHERE job_url=?
    """
    with _get_conn() as conn:
        conn.execute(sql, (outcome, int(callback), datetime.utcnow().isoformat(), job_url))
    logger.info(f"[Memory] Outcome updated for {job_url}: {outcome}")


def mark_retry(job_url: str):
    """Increment retry counter for a failed application."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE applications SET retry_count = retry_count + 1, last_checked=? WHERE job_url=?",
            (datetime.utcnow().isoformat(), job_url),
        )


def get_all_applications(limit: int = 100) -> list[dict]:
    """Fetch recent applications."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM applications ORDER BY applied_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_failed_retryable(max_retries: int = 3) -> list[dict]:
    """Return failed applications eligible for retry."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM applications
            WHERE outcome IN ('failed', 'error') AND retry_count < ?
            ORDER BY match_score DESC
            """,
            (max_retries,),
        ).fetchall()
    return [dict(r) for r in rows]


def already_applied(job_url: str) -> bool:
    """Check if we have already attempted this job URL."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE job_url=?", (job_url,)
        ).fetchone()
    return row is not None


# ── Skill Tracking ────────────────────────────────────────────────────────────

def record_skill_appearance(skills: list[str]):
    """Increment appearance count for each skill seen in a job match."""
    with _get_conn() as conn:
        for skill in skills:
            conn.execute(
                """
                INSERT INTO skill_performance (skill, appearances, callbacks, updated_at)
                VALUES (?, 1, 0, ?)
                ON CONFLICT(skill) DO UPDATE SET
                    appearances = appearances + 1,
                    updated_at = excluded.updated_at
                """,
                (skill.lower(), datetime.utcnow().isoformat()),
            )


def record_skill_callback(skills: list[str]):
    """Increment callback count for skills that led to a callback."""
    with _get_conn() as conn:
        for skill in skills:
            conn.execute(
                "UPDATE skill_performance SET callbacks = callbacks + 1 WHERE skill=?",
                (skill.lower(),),
            )


def get_skill_performance() -> list[dict]:
    """Return all skill performance stats, ordered by callback rate."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT skill, appearances, callbacks,
                   ROUND(CAST(callbacks AS FLOAT) / MAX(appearances, 1) * 100, 1) AS callback_rate
            FROM skill_performance
            ORDER BY callback_rate DESC, callbacks DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ── Strategy Log ──────────────────────────────────────────────────────────────

def save_strategy(insights: list[str], strategy: str):
    """Persist a self-improvement strategy snapshot."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO strategy_log (logged_at, insights, strategy) VALUES (?, ?, ?)",
            (datetime.utcnow().isoformat(), json.dumps(insights), strategy),
        )
    logger.info("[Memory] Strategy snapshot saved.")


def get_latest_strategy() -> Optional[dict]:
    """Fetch the most recent strategy log entry."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_log ORDER BY logged_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
