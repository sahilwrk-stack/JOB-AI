"""
Database Adapter — SQLite <-> PostgreSQL unified interface.

Set DATABASE_URL env var to use PostgreSQL (Render / Railway).
Leave it unset to use SQLite (local dev / free tier).

Usage (identical to the sqlite3 pattern):
    from core.db_adapter import _get_conn

    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM applications WHERE id = ?", (1,)).fetchall()
"""

import os
import re
import sqlite3
from typing import Any, Optional

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH      = os.getenv("DB_PATH", "db/agent_memory.db")

# Render / Railway may provide "postgres://" — psycopg2 requires "postgresql://"
_PG_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL else ""

IS_POSTGRES: bool = _PG_URL.startswith("postgresql://")
P: str = "%s" if IS_POSTGRES else "?"    # SQL placeholder symbol


# ── Schema definitions ─────────────────────────────────────────────────────────
# Both schemas define identical tables; only the column types differ.

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_title           TEXT,
    company             TEXT,
    job_url             TEXT UNIQUE,
    location            TEXT,
    match_score         INTEGER,
    confidence_score    INTEGER,
    match_quality       TEXT,
    candidate_tier      TEXT,
    execution_status    TEXT,
    risk_level          TEXT,
    form_mapping        TEXT,
    applied_at          DATETIME,
    outcome             TEXT DEFAULT 'pending',
    callback_received   INTEGER DEFAULT 0,
    retry_count         INTEGER DEFAULT 0,
    last_checked        DATETIME,
    notes               TEXT,
    source              TEXT,
    salary              TEXT,
    application_channel TEXT DEFAULT 'manual',
    applied_via_click_at DATETIME,
    interview_round     INTEGER DEFAULT 0,
    rejection_reason    TEXT,
    outcome_updated_at  DATETIME,
    skills_matched      TEXT,
    skills_missing      TEXT
);

CREATE TABLE IF NOT EXISTS skill_performance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill       TEXT UNIQUE,
    appearances INTEGER DEFAULT 0,
    callbacks   INTEGER DEFAULT 0,
    interviews  INTEGER DEFAULT 0,
    offers      INTEGER DEFAULT 0,
    updated_at  DATETIME
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    job_id      INTEGER,
    job_title   TEXT,
    company     TEXT,
    old_value   TEXT,
    new_value   TEXT,
    detail      TEXT,
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES applications(id)
);

CREATE TABLE IF NOT EXISTS strategy_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at DATETIME,
    insights  TEXT,
    strategy  TEXT
);

CREATE TABLE IF NOT EXISTS analytics_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_analyzed       INTEGER DEFAULT 0,
    insights             TEXT,
    skill_gap_analysis   TEXT,
    recommended_learning TEXT,
    strategy_update      TEXT,
    target_roles         TEXT,
    callback_patterns    TEXT,
    weak_areas           TEXT,
    run_duration_ms      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    message           TEXT,
    priority          TEXT DEFAULT 'Low',
    channel           TEXT,
    recipient         TEXT,
    subject           TEXT,
    status            TEXT DEFAULT 'sent',
    error_message     TEXT,
    job_id            INTEGER,
    job_title         TEXT,
    company           TEXT,
    match_score       INTEGER DEFAULT 0,
    sent_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safety_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    category    TEXT,
    detail      TEXT,
    job_title   TEXT,
    company     TEXT,
    job_id      TEXT,
    apply_link  TEXT,
    logged_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_learning (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id    INTEGER,
    decision          TEXT,
    result            TEXT,
    confidence        INTEGER,
    reason            TEXT,
    job_title         TEXT,
    company           TEXT,
    logged_at         TEXT
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS applications (
    id                  BIGSERIAL PRIMARY KEY,
    job_title           TEXT,
    company             TEXT,
    job_url             TEXT UNIQUE,
    location            TEXT,
    match_score         INTEGER,
    confidence_score    INTEGER,
    match_quality       TEXT,
    candidate_tier      TEXT,
    execution_status    TEXT,
    risk_level          TEXT,
    form_mapping        TEXT,
    applied_at          TIMESTAMPTZ,
    outcome             TEXT DEFAULT 'pending',
    callback_received   INTEGER DEFAULT 0,
    retry_count         INTEGER DEFAULT 0,
    last_checked        TIMESTAMPTZ,
    notes               TEXT,
    source              TEXT,
    salary              TEXT,
    application_channel TEXT DEFAULT 'manual',
    applied_via_click_at TIMESTAMPTZ,
    interview_round     INTEGER DEFAULT 0,
    rejection_reason    TEXT,
    outcome_updated_at  TIMESTAMPTZ,
    skills_matched      TEXT,
    skills_missing      TEXT
);

CREATE TABLE IF NOT EXISTS skill_performance (
    id          BIGSERIAL PRIMARY KEY,
    skill       TEXT UNIQUE,
    appearances INTEGER DEFAULT 0,
    callbacks   INTEGER DEFAULT 0,
    interviews  INTEGER DEFAULT 0,
    offers      INTEGER DEFAULT 0,
    updated_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS activity_log (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    job_id      BIGINT REFERENCES applications(id) ON DELETE SET NULL,
    job_title   TEXT,
    company     TEXT,
    old_value   TEXT,
    new_value   TEXT,
    detail      TEXT,
    logged_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_log (
    id        BIGSERIAL PRIMARY KEY,
    logged_at TIMESTAMPTZ,
    insights  TEXT,
    strategy  TEXT
);

CREATE TABLE IF NOT EXISTS analytics_log (
    id                   BIGSERIAL PRIMARY KEY,
    logged_at            TIMESTAMPTZ DEFAULT NOW(),
    total_analyzed       INTEGER DEFAULT 0,
    insights             TEXT,
    skill_gap_analysis   TEXT,
    recommended_learning TEXT,
    strategy_update      TEXT,
    target_roles         TEXT,
    callback_patterns    TEXT,
    weak_areas           TEXT,
    run_duration_ms      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications_log (
    id                BIGSERIAL PRIMARY KEY,
    notification_type TEXT NOT NULL,
    message           TEXT,
    priority          TEXT DEFAULT 'Low',
    channel           TEXT,
    recipient         TEXT,
    subject           TEXT,
    status            TEXT DEFAULT 'sent',
    error_message     TEXT,
    job_id            BIGINT,
    job_title         TEXT,
    company           TEXT,
    match_score       INTEGER DEFAULT 0,
    sent_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS safety_log (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    category    TEXT,
    detail      TEXT,
    job_title   TEXT,
    company     TEXT,
    job_id      TEXT,
    apply_link  TEXT,
    logged_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_learning (
    id                BIGSERIAL PRIMARY KEY,
    application_id    BIGINT,
    decision          TEXT,
    result            TEXT,
    confidence        INTEGER,
    reason            TEXT,
    job_title         TEXT,
    company           TEXT,
    logged_at         TIMESTAMPTZ DEFAULT NOW()
);
"""

# Column migrations (safe to re-run — errors are silently ignored)
MIGRATIONS = [
    "ALTER TABLE applications ADD COLUMN source TEXT",
    "ALTER TABLE applications ADD COLUMN salary TEXT",
    "ALTER TABLE applications ADD COLUMN interview_round INTEGER DEFAULT 0",
    "ALTER TABLE applications ADD COLUMN rejection_reason TEXT",
    "ALTER TABLE applications ADD COLUMN outcome_updated_at TEXT",
    "ALTER TABLE applications ADD COLUMN skills_matched TEXT",
    "ALTER TABLE applications ADD COLUMN skills_missing TEXT",
    "ALTER TABLE applications ADD COLUMN application_channel TEXT DEFAULT 'manual'",
    "ALTER TABLE applications ADD COLUMN applied_via_click_at TEXT",
    "ALTER TABLE skill_performance ADD COLUMN interviews INTEGER DEFAULT 0",
    "ALTER TABLE skill_performance ADD COLUMN offers INTEGER DEFAULT 0",
    # safety guard deduplication columns
    "ALTER TABLE applications ADD COLUMN job_fingerprint TEXT",
    "ALTER TABLE applications ADD COLUMN url_fingerprint TEXT",
    "ALTER TABLE applications ADD COLUMN smart_action TEXT",
    "ALTER TABLE applications ADD COLUMN ai_decision_label TEXT",
    "ALTER TABLE applications ADD COLUMN ai_fit_decision TEXT",
    "ALTER TABLE applications ADD COLUMN ai_fit_confidence INTEGER",
    "ALTER TABLE applications ADD COLUMN ai_fit_reason TEXT",
    "ALTER TABLE applications ADD COLUMN pipeline_decision_status TEXT",
]


# ── Row wrapper ────────────────────────────────────────────────────────────────

class _Row(dict):
    """
    Dict-subclass that also supports integer index access,
    making psycopg2 rows behave exactly like sqlite3.Row.
    """
    def __getitem__(self, key: Any):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        return super().get(key, default)


# ── Cursor wrapper ─────────────────────────────────────────────────────────────

class _CursorWrap:
    def __init__(self, cursor, is_pg: bool, conn_ref=None):
        self._cur     = cursor
        self._is_pg   = is_pg
        self._conn    = conn_ref
        self._last_id: Optional[int] = None

    # ── Fetch helpers ─────────────────────────────────────────────

    def _pg_row(self, raw_row) -> _Row:
        cols = [d[0] for d in (self._cur.description or [])]
        return _Row(zip(cols, raw_row))

    def fetchall(self) -> list[_Row]:
        rows = self._cur.fetchall()
        if self._is_pg:
            return [self._pg_row(r) for r in rows]
        return [_Row(dict(r)) for r in rows]

    def fetchone(self) -> Optional[_Row]:
        row = self._cur.fetchone()
        if row is None:
            return None
        if self._is_pg:
            return self._pg_row(row)
        return _Row(dict(row))

    def __iter__(self):
        return iter(self.fetchall())

    def __len__(self):
        return self._cur.rowcount or 0

    # ── lastrowid ─────────────────────────────────────────────────

    @property
    def lastrowid(self) -> Optional[int]:
        if self._is_pg:
            # Captured from RETURNING id clause appended to INSERT
            if self._last_id is not None:
                return self._last_id
            # Fallback: SELECT lastval() in same session
            try:
                c = self._conn.cursor()
                c.execute("SELECT lastval()")
                r = c.fetchone()
                return r[0] if r else None
            except Exception:
                return None
        return self._cur.lastrowid


# ── Connection wrapper ─────────────────────────────────────────────────────────

class _ConnWrap:
    """
    Unified connection object that wraps sqlite3.Connection or psycopg2.connection.
    Supports: with _get_conn() as conn:  →  conn.execute(sql, params)
    """

    def __init__(self, raw_conn, is_pg: bool):
        self._conn  = raw_conn
        self._is_pg = is_pg

    # Context manager protocol
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                self._conn.rollback()
            except Exception:
                pass
        else:
            try:
                self._conn.commit()
            except Exception:
                pass
        try:
            self._conn.close()
        except Exception:
            pass
        return False  # Never suppress exceptions

    # ── SQL translation helpers ────────────────────────────────────

    @staticmethod
    def _sub_placeholders(sql: str) -> str:
        """Replace ? with %s for PostgreSQL."""
        return re.sub(r"\?", "%s", sql)

    # ── execute ───────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> _CursorWrap:
        if self._is_pg:
            sql_t = self._sub_placeholders(sql)
            # Append RETURNING id to plain INSERT statements so we can get lastrowid
            stripped = sql_t.lstrip().upper()
            is_insert = stripped.startswith("INSERT")
            has_returning = "RETURNING" in stripped

            if is_insert and not has_returning:
                sql_t = sql_t.rstrip("; \t\n") + " RETURNING id"

            cur = self._conn.cursor()
            cur.execute(sql_t, params or None)
            wrap = _CursorWrap(cur, is_pg=True, conn_ref=self._conn)

            if is_insert:
                try:
                    row = cur.fetchone()
                    if row:
                        wrap._last_id = row[0]
                except Exception:
                    pass
            return wrap
        else:
            cur = self._conn.execute(sql, params)
            return _CursorWrap(cur, is_pg=False)

    # ── executescript (multi-statement schema init) ────────────────

    def executescript(self, sql: str):
        """Execute multiple ; separated SQL statements (schema init)."""
        if self._is_pg:
            cur = self._conn.cursor()
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        cur.execute(stmt)
                        self._conn.commit()
                    except Exception:
                        self._conn.rollback()
        else:
            self._conn.executescript(sql)


# ── Connection factory ─────────────────────────────────────────────────────────

def _get_conn() -> _ConnWrap:
    """
    Return a database connection wrapper.

    Usage:
        with _get_conn() as conn:
            rows = conn.execute("SELECT 1").fetchall()
    """
    if IS_POSTGRES:
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2-binary is required for PostgreSQL. "
                "Run: pip install psycopg2-binary"
            ) from exc

        raw = psycopg2.connect(_PG_URL)
        raw.autocommit = False
        return _ConnWrap(raw, is_pg=True)
    else:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        raw = sqlite3.connect(DB_PATH)
        raw.row_factory = sqlite3.Row
        return _ConnWrap(raw, is_pg=False)


# ── Schema initializer ─────────────────────────────────────────────────────────

def init_schema(conn: _ConnWrap):
    """
    Create all tables and run migrations.
    Safe to call multiple times — idempotent.
    """
    schema = SCHEMA_POSTGRES if IS_POSTGRES else SCHEMA_SQLITE
    conn.executescript(schema)

    for migration in MIGRATIONS:
        try:
            conn.execute(migration)
            if IS_POSTGRES:
                conn._conn.commit()
        except Exception:
            if IS_POSTGRES:
                try:
                    conn._conn.rollback()
                except Exception:
                    pass


# ── PostgreSQL-aware UPSERT helper ─────────────────────────────────────────────

def build_application_upsert() -> str:
    """
    Return the correct SQL to insert-or-update an application.
    Both SQLite 3.24+ and PostgreSQL 9.5+ support ON CONFLICT syntax.
    """
    return """
    INSERT INTO applications
        (job_title, company, job_url, location, match_score, confidence_score,
         match_quality, candidate_tier, execution_status, risk_level,
         form_mapping, applied_at, last_checked,
         source, salary, skills_matched, skills_missing,
         job_fingerprint, url_fingerprint,
         smart_action, ai_decision_label,
         ai_fit_decision, ai_fit_confidence, ai_fit_reason, pipeline_decision_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(job_url) DO UPDATE SET
        job_title        = EXCLUDED.job_title,
        company          = EXCLUDED.company,
        location         = EXCLUDED.location,
        match_score      = EXCLUDED.match_score,
        confidence_score = EXCLUDED.confidence_score,
        match_quality    = EXCLUDED.match_quality,
        candidate_tier   = EXCLUDED.candidate_tier,
        execution_status = EXCLUDED.execution_status,
        risk_level       = EXCLUDED.risk_level,
        form_mapping     = EXCLUDED.form_mapping,
        last_checked     = EXCLUDED.last_checked,
        source           = EXCLUDED.source,
        salary           = EXCLUDED.salary,
        skills_matched   = EXCLUDED.skills_matched,
        skills_missing   = EXCLUDED.skills_missing,
        job_fingerprint  = EXCLUDED.job_fingerprint,
        url_fingerprint  = EXCLUDED.url_fingerprint,
        smart_action     = EXCLUDED.smart_action,
        ai_decision_label= EXCLUDED.ai_decision_label,
        ai_fit_decision  = EXCLUDED.ai_fit_decision,
        ai_fit_confidence= EXCLUDED.ai_fit_confidence,
        ai_fit_reason    = EXCLUDED.ai_fit_reason,
        pipeline_decision_status = EXCLUDED.pipeline_decision_status
    """
