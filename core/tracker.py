"""
Application Tracking AI
Full lifecycle tracker: log → update → analyze → report.

Output schema:
{
  "total_applied":    int,
  "success_rate":     float,
  "top_skills":       list[dict],
  "recent_activity":  list[dict]
}
"""

import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.db_adapter import _get_conn
from core.memory import init_db, record_skill_callback
from utils.helpers import logger

# ── Outcome constants ─────────────────────────────────────────────────────────

class Status:
    APPLIED  = "MANUAL_APPLIED"
    REVIEW   = "PENDING_REVIEW"
    SKIPPED  = "SKIP"

class Outcome:
    PENDING   = "pending"
    INTERVIEW = "interview"
    OFFER     = "offer"
    REJECTED  = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED   = "ghosted"

ALL_OUTCOMES = [
    Outcome.PENDING, Outcome.INTERVIEW, Outcome.OFFER,
    Outcome.REJECTED, Outcome.WITHDRAWN, Outcome.GHOSTED,
]

# Outcomes counted as "positive" (got a response / advanced)
POSITIVE_OUTCOMES = {Outcome.INTERVIEW, Outcome.OFFER}
SUCCESS_OUTCOMES  = {Outcome.OFFER}

# ── Activity log helpers ──────────────────────────────────────────────────────

def _log_activity(
    conn: sqlite3.Connection,
    event_type: str,
    job_id: int,
    job_title: str,
    company: str,
    old_value: str = "",
    new_value: str = "",
    detail: str = "",
):
    conn.execute(
        """INSERT INTO activity_log
           (event_type, job_id, job_title, company, old_value, new_value, detail, logged_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_type, job_id, job_title, company, old_value, new_value, detail,
         datetime.now(timezone.utc).isoformat()),
    )


# ── Core Tracker API ──────────────────────────────────────────────────────────

def log_application(
    job_id_str: str,
    title: str,
    company: str,
    url: str,
    status: str,
    match_score: int = 0,
    skills: Optional[list[str]] = None,
    location: str = "",
    salary: str = "",
    source: str = "",
) -> int:
    """
    Manually log a job application (useful for jobs applied outside the agent).

    Args:
        job_id_str:  External job ID string (e.g. from job board).
        title:       Job title.
        company:     Company name.
        url:         Application URL.
        status:      "MANUAL_APPLIED" | "PENDING_REVIEW" | "SKIP".
        match_score: 0-100.
        skills:      List of matched skills.
        location:    Job location.
        salary:      Salary string.
        source:      Job board source.

    Returns:
        DB row ID.
    """
    init_db()
    skills_str = json.dumps(skills or [])
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO applications
               (job_title, company, job_url, location, match_score,
                execution_status, applied_at, last_checked,
                source, salary, skills_matched, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, company, url, location, match_score,
             status, now, now, source, salary, skills_str, Outcome.PENDING),
        )
        row_id = cur.lastrowid
        _log_activity(conn, "APPLIED", row_id, title, company,
                      new_value=status,
                      detail=f"Match score: {match_score}")

    logger.info(f"[Tracker] Logged: {title} @ {company} [{status}] id={row_id}")
    return row_id


def update_status(
    identifier: str | int,
    new_status: str,
    notes: str = "",
) -> bool:
    """
    Update the execution_status of an application (Applied / Review / Skipped).

    Args:
        identifier: DB row id (int) OR job_url (str).
        new_status: New status string.
        notes:      Optional note.

    Returns:
        True if a row was updated.
    """
    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        if isinstance(identifier, int):
            row = conn.execute(
                "SELECT id, job_title, company, execution_status FROM applications WHERE id=?",
                (identifier,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, job_title, company, execution_status FROM applications WHERE job_url=?",
                (identifier,)
            ).fetchone()

        if not row:
            logger.warning(f"[Tracker] Application not found: {identifier}")
            return False

        old_status = row["execution_status"]
        conn.execute(
            "UPDATE applications SET execution_status=?, last_checked=?, notes=? WHERE id=?",
            (new_status, now, notes or row["notes"] if hasattr(row, "notes") else notes, row["id"]),
        )
        _log_activity(conn, "STATUS_CHANGE", row["id"], row["job_title"], row["company"],
                      old_value=old_status, new_value=new_status, detail=notes)

    logger.info(f"[Tracker] Status updated id={row['id']}: {old_status} -> {new_status}")
    return True


def update_outcome(
    identifier: str | int,
    outcome: str,
    interview_round: int = 0,
    rejection_reason: str = "",
    notes: str = "",
) -> bool:
    """
    Update the outcome of an application.

    Args:
        identifier:       DB row id (int) OR job_url (str).
        outcome:          pending | interview | offer | rejected | withdrawn | ghosted
        interview_round:  Interview round number (1, 2, 3...).
        rejection_reason: Optional rejection reason.
        notes:            Optional notes.

    Returns:
        True if a row was updated.
    """
    if outcome not in ALL_OUTCOMES:
        logger.warning(f"[Tracker] Unknown outcome: '{outcome}'. Use: {ALL_OUTCOMES}")
        return False

    init_db()
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        if isinstance(identifier, int):
            row = conn.execute(
                "SELECT id, job_title, company, outcome, skills_matched FROM applications WHERE id=?",
                (identifier,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, job_title, company, outcome, skills_matched FROM applications WHERE job_url=?",
                (identifier,)
            ).fetchone()

        if not row:
            logger.warning(f"[Tracker] Application not found: {identifier}")
            return False

        old_outcome = row["outcome"]
        callback    = outcome in POSITIVE_OUTCOMES

        conn.execute(
            """UPDATE applications
               SET outcome=?, callback_received=?, interview_round=?,
                   rejection_reason=?, last_checked=?, outcome_updated_at=?, notes=?
               WHERE id=?""",
            (outcome, int(callback), interview_round,
             rejection_reason, now, now, notes, row["id"]),
        )
        _log_activity(conn, "OUTCOME_UPDATE", row["id"], row["job_title"], row["company"],
                      old_value=old_outcome, new_value=outcome,
                      detail=rejection_reason or f"Round {interview_round}" if interview_round else "")

        # Update skill performance counters
        skills_raw = row["skills_matched"] or "[]"
        try:
            skills = json.loads(skills_raw)
        except Exception:
            skills = []

        if outcome == Outcome.INTERVIEW and skills:
            conn.execute(
                f"UPDATE skill_performance SET interviews = interviews + 1 WHERE skill IN ({','.join('?' for _ in skills)})",
                [s.lower() for s in skills],
            )
        if outcome == Outcome.OFFER and skills:
            conn.execute(
                f"UPDATE skill_performance SET offers = offers + 1 WHERE skill IN ({','.join('?' for _ in skills)})",
                [s.lower() for s in skills],
            )

    logger.info(f"[Tracker] Outcome updated id={row['id']} ({row['job_title']}): {old_outcome} -> {outcome}")

    # Fire callback notification for positive outcomes (async, non-blocking)
    if outcome in POSITIVE_OUTCOMES or outcome == Outcome.OFFER:
        try:
            from core.notifier import notify_callback_received
            notify_callback_received(
                job_title       = row["job_title"] or "",
                company         = row["company"]   or "",
                outcome         = outcome,
                interview_round = interview_round,
            )
        except Exception as _ne:
            logger.debug(f"[Tracker] Notification error (non-fatal): {_ne}")

    return True


def get_application(identifier: str | int) -> Optional[dict]:
    """Fetch a single application by ID or URL."""
    init_db()
    with _get_conn() as conn:
        if isinstance(identifier, int):
            row = conn.execute("SELECT * FROM applications WHERE id=?", (identifier,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM applications WHERE job_url=?", (identifier,)).fetchone()
    return dict(row) if row else None


def list_applications(
    status: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    List applications with optional filters.

    Args:
        status:  Filter by execution_status (MANUAL_APPLIED / PENDING_REVIEW / SKIP).
        outcome: Filter by outcome (pending / interview / offer / rejected / ...).
        limit:   Max records.
        offset:  Pagination offset.
    """
    init_db()
    where, params = [], []
    if status:
        where.append("execution_status=?")
        params.append(status)
    if outcome:
        where.append("outcome=?")
        params.append(outcome)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]

    with _get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM applications {clause} ORDER BY applied_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ── Stats Engine ──────────────────────────────────────────────────────────────

def _safe_rate(numerator: int, denominator: int, decimals: int = 1) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, decimals)


def get_stats() -> dict:
    """
    Generate comprehensive application statistics.

    Returns:
        Full stats dict matching the tracker output schema (extended).
    """
    init_db()
    with _get_conn() as conn:

        # ── Counts by status ───────────────────────────────────────────────
        status_rows = conn.execute(
            "SELECT execution_status, COUNT(*) as cnt FROM applications GROUP BY execution_status"
        ).fetchall()
        status_counts = {r["execution_status"]: r["cnt"] for r in status_rows}

        # ── Counts by outcome ──────────────────────────────────────────────
        outcome_rows = conn.execute(
            "SELECT outcome, COUNT(*) as cnt FROM applications GROUP BY outcome"
        ).fetchall()
        outcome_counts = {r["outcome"]: r["cnt"] for r in outcome_rows}

        total       = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        applied     = status_counts.get("MANUAL_APPLIED", 0) + status_counts.get("AUTO_APPLY", 0)
        reviewed    = status_counts.get("PENDING_REVIEW", 0) + status_counts.get("REVIEW", 0)
        skipped     = status_counts.get("SKIP", 0)

        interviews  = outcome_counts.get("interview", 0)
        offers      = outcome_counts.get("offer", 0)
        rejections  = outcome_counts.get("rejected", 0)
        pending     = outcome_counts.get("pending", 0)
        withdrawn   = outcome_counts.get("withdrawn", 0)
        ghosted     = outcome_counts.get("ghosted", 0)

        # ── Score stats ────────────────────────────────────────────────────
        score_row = conn.execute(
            "SELECT AVG(match_score) as avg, MAX(match_score) as max, MIN(match_score) as min FROM applications WHERE execution_status='AUTO_APPLY'"
        ).fetchone()
        avg_score = round(score_row["avg"] or 0, 1)
        max_score = score_row["max"] or 0
        min_score = score_row["min"] or 0

        # ── Top skills (by callback rate, then interviews) ─────────────────
        skill_rows = conn.execute(
            """
            SELECT skill, appearances, callbacks, interviews, offers,
                   ROUND(CAST(callbacks  AS FLOAT) / MAX(appearances,1) * 100, 1) AS callback_rate,
                   ROUND(CAST(interviews AS FLOAT) / MAX(appearances,1) * 100, 1) AS interview_rate,
                   ROUND(CAST(offers     AS FLOAT) / MAX(appearances,1) * 100, 1) AS offer_rate
            FROM skill_performance
            WHERE appearances > 0
            ORDER BY interview_rate DESC, callback_rate DESC, appearances DESC
            LIMIT 15
            """
        ).fetchall()
        top_skills = [dict(r) for r in skill_rows]

        # ── Daily trend (last 30 days) ─────────────────────────────────────
        thirty_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        trend_rows = conn.execute(
            """
            SELECT DATE(applied_at) as day, COUNT(*) as count
            FROM applications
            WHERE applied_at >= ?
            GROUP BY DATE(applied_at)
            ORDER BY day
            """,
            (thirty_ago,),
        ).fetchall()
        daily_trend = [{"date": r["day"], "count": r["count"]} for r in trend_rows]

        # ── Top companies applied to ────────────────────────────────────────
        company_rows = conn.execute(
            """
            SELECT company, COUNT(*) as count,
                   SUM(CASE WHEN outcome='interview' THEN 1 ELSE 0 END) as interviews,
                   MAX(match_score) as best_score
            FROM applications
            WHERE company != ''
            GROUP BY company
            ORDER BY count DESC
            LIMIT 10
            """
        ).fetchall()
        top_companies = [dict(r) for r in company_rows]

        # ── Score distribution buckets ─────────────────────────────────────
        score_dist = {}
        for label, lo, hi in [("90-100",90,101),("80-89",80,90),("70-79",70,80),("60-69",60,70),("50-59",50,60),("<50",0,50)]:
            count = conn.execute(
                "SELECT COUNT(*) FROM applications WHERE match_score >= ? AND match_score < ?",
                (lo, hi)
            ).fetchone()[0]
            score_dist[label] = count

        # ── Recent activity feed ───────────────────────────────────────────
        activity_rows = conn.execute(
            """
            SELECT * FROM activity_log
            ORDER BY logged_at DESC LIMIT 20
            """
        ).fetchall()
        recent_activity = [dict(r) for r in activity_rows]

        # ── Source breakdown ────────────────────────────────────────────────
        source_rows = conn.execute(
            "SELECT source, COUNT(*) as count FROM applications WHERE source != '' GROUP BY source ORDER BY count DESC"
        ).fetchall()
        source_breakdown = [dict(r) for r in source_rows]

    # ── Derived rates ──────────────────────────────────────────────────────────
    success_rate    = _safe_rate(offers, applied)
    interview_rate  = _safe_rate(interviews, applied)
    response_rate   = _safe_rate(interviews + offers + rejections, applied)
    rejection_rate  = _safe_rate(rejections, applied)

    return {
        # ── Core output schema ────────────────────────────────────────────
        "total_applied":    applied,
        "success_rate":     success_rate,
        "top_skills":       top_skills,
        "recent_activity":  recent_activity,

        # ── Extended stats ────────────────────────────────────────────────
        "counts": {
            "total":       total,
            "applied":     applied,
            "reviewed":    reviewed,
            "skipped":     skipped,
            "pending":     pending,
            "interviews":  interviews,
            "offers":      offers,
            "rejections":  rejections,
            "withdrawn":   withdrawn,
            "ghosted":     ghosted,
        },
        "rates": {
            "success_rate":    success_rate,
            "interview_rate":  interview_rate,
            "response_rate":   response_rate,
            "rejection_rate":  rejection_rate,
        },
        "score_stats": {
            "average": avg_score,
            "maximum": max_score,
            "minimum": min_score,
        },
        "score_distribution": score_dist,
        "daily_trend":        daily_trend,
        "top_companies":      top_companies,
        "source_breakdown":   source_breakdown,
    }


def get_recent_activity(limit: int = 20) -> list[dict]:
    """Return the most recent tracker activity events."""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY logged_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_top_skills(limit: int = 10) -> list[dict]:
    """Return top performing skills by interview rate."""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT skill, appearances, callbacks, interviews, offers,
                   ROUND(CAST(interviews AS FLOAT) / MAX(appearances,1) * 100, 1) AS interview_rate,
                   ROUND(CAST(offers     AS FLOAT) / MAX(appearances,1) * 100, 1) AS offer_rate
            FROM skill_performance
            WHERE appearances > 0
            ORDER BY interview_rate DESC, appearances DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def generate_report() -> dict:
    """
    Generate the full tracker output report.

    Returns:
        Exact output schema:
        {
            "total_applied": int,
            "success_rate":  float,
            "top_skills":    list[dict],
            "recent_activity": list[dict]
        }
        (plus extended fields)
    """
    stats = get_stats()
    logger.info(
        f"[Tracker] Report: applied={stats['counts']['applied']} | "
        f"interviews={stats['counts']['interviews']} | "
        f"offers={stats['counts']['offers']} | "
        f"success_rate={stats['rates']['success_rate']}%"
    )
    return stats


# ── Bulk seed (for testing / demo) ────────────────────────────────────────────

def seed_demo_data():
    """Insert demo applications so the tracker has data to analyze."""
    import random
    init_db()
    demo = [
        ("ML Engineer",        "DataSpark AI",       "https://ex.com/j1",  "AUTO_APPLY", 91, ["Python","TensorFlow","SQL"],       "interview", 1),
        ("Data Scientist",     "Quantify Analytics", "https://ex.com/j2",  "AUTO_APPLY", 84, ["Python","SQL","Pandas"],            "rejected",  0),
        ("NLP Engineer",       "LinguaLabs",         "https://ex.com/j3",  "AUTO_APPLY", 88, ["Python","PyTorch","HuggingFace"],   "interview", 2),
        ("AI Developer",       "TechVentures",       "https://ex.com/j4",  "REVIEW",     72, ["Python","FastAPI","Docker"],        "pending",   0),
        ("Junior ML Intern",   "StartupXYZ",         "https://ex.com/j5",  "AUTO_APPLY", 67, ["Python","scikit-learn"],            "offer",     1),
        ("Backend Developer",  "CloudStack",         "https://ex.com/j6",  "SKIP",       48, ["Django","PostgreSQL"],              "skipped",   0),
        ("Data Analyst",       "FinInsights",        "https://ex.com/j7",  "REVIEW",     75, ["SQL","Tableau","Excel"],            "interview", 1),
        ("Research Intern",    "DeepMind BLR",       "https://ex.com/j8",  "AUTO_APPLY", 91, ["Python","PyTorch","Research"],      "pending",   0),
        ("ML Platform Eng",    "CloudAI Corp",       "https://ex.com/j9",  "REVIEW",     61, ["Python","Kubernetes","MLflow"],     "rejected",  0),
        ("Python Developer",   "WebDev Inc",         "https://ex.com/j10", "AUTO_APPLY", 70, ["Python","FastAPI","REST APIs"],     "ghosted",   0),
    ]

    with _get_conn() as conn:
        for i, (title, company, url, status, score, skills, outcome, interview_round) in enumerate(demo):
            days_ago = i * 3
            ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO applications
                       (job_title, company, job_url, match_score, execution_status,
                        applied_at, last_checked, outcome, outcome_updated_at,
                        skills_matched, interview_round, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (title, company, url, score, status, ts, ts,
                     outcome, ts, json.dumps(skills), interview_round, "demo"),
                )
                row_id = conn.execute("SELECT id FROM applications WHERE job_url=?", (url,)).fetchone()[0]
                _log_activity(conn, "APPLIED", row_id, title, company,
                              new_value=status, detail=f"Score: {score}")
                if outcome != "pending":
                    _log_activity(conn, "OUTCOME_UPDATE", row_id, title, company,
                                  old_value="pending", new_value=outcome)
            except Exception:
                pass

            # Update skill_performance
            for skill in skills:
                conn.execute(
                    """INSERT INTO skill_performance (skill, appearances, callbacks, interviews, offers, updated_at)
                       VALUES (?, 1, 0, 0, 0, ?)
                       ON CONFLICT(skill) DO UPDATE SET appearances = appearances + 1""",
                    (skill.lower(), ts),
                )
                if outcome in ("interview", "offer"):
                    conn.execute(
                        "UPDATE skill_performance SET interviews = interviews + 1 WHERE skill=?",
                        (skill.lower(),),
                    )
                if outcome == "offer":
                    conn.execute(
                        "UPDATE skill_performance SET offers = offers + 1 WHERE skill=?",
                        (skill.lower(),),
                    )

    logger.success("[Tracker] Demo data seeded successfully.")
