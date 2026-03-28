"""
Safe Automation AI — Safety Guard
====================================
A strict multi-layer safety layer that wraps every auto-apply action.

Responsibilities:
  1. Daily application limit enforcement  (DAILY_APPLY_LIMIT, default 10)
  2. Duplicate submission detection       (URL hash + title+company hash)
  3. Platform policy compliance           (rate limiting, domain allowlist)
  4. Confidence-drop review escalation    (rolling avg confidence monitor)
  5. Consecutive-error circuit breaker    (auto-HALT on repeated failures)
  6. Human review queue                   (uncertain cases parked for review)

Output schema:
{
  "safety_status":   "SAFE | WARNING | BLOCKED | HALTED",
  "warnings":        [],
  "actions_blocked": [],
  "daily_stats":     {},
  "circuit_breaker": {}
}
"""

import os
import json
import time
import hashlib
from collections import deque
from datetime     import datetime, timezone, date, timedelta
from dataclasses  import dataclass, field, asdict
from typing       import Optional

from core.db_adapter import _get_conn
from core.memory      import init_db
from utils.helpers    import logger

# ── Config (env-driven) ───────────────────────────────────────────────────────

def _cfg(key: str, default):
    val = os.getenv(key, "")
    if isinstance(default, int):
        try:   return int(val) if val else default
        except ValueError: return default
    if isinstance(default, float):
        try:   return float(val) if val else default
        except ValueError: return default
    if isinstance(default, bool):
        return val.lower() in ("1","true","yes") if val else default
    return val or default

class _Cfg:
    DAILY_LIMIT           = _cfg("DAILY_APPLY_LIMIT",            10)
    DAILY_LIMIT_HARD_CAP  = _cfg("DAILY_APPLY_HARD_CAP",         20)
    MIN_CONFIDENCE        = _cfg("SAFETY_MIN_CONFIDENCE",         50)
    REVIEW_CONFIDENCE     = _cfg("SAFETY_REVIEW_CONFIDENCE",      70)
    ERROR_HALT_THRESHOLD  = _cfg("SAFETY_ERROR_HALT_THRESHOLD",   3)
    ERROR_WINDOW_MINUTES  = _cfg("SAFETY_ERROR_WINDOW_MINUTES",   30)
    DUPLICATE_WINDOW_DAYS = _cfg("SAFETY_DUPLICATE_WINDOW_DAYS",  30)
    DOMAIN_MIN_DELAY_S    = _cfg("SAFETY_DOMAIN_MIN_DELAY_S",     10)
    SAFETY_ENABLED        = _cfg("SAFETY_ENABLED",                True)
    HALT_ON_ERROR         = _cfg("SAFETY_HALT_ON_ERROR",          True)

# ── Platform policy tables ────────────────────────────────────────────────────

# Known job boards — allowed for automation (check each board's ToS in production)
ALLOWED_DOMAINS = {
    "linkedin.com", "indeed.com", "naukri.com", "internshala.com",
    "wellfound.com", "angel.co", "remote.co", "weworkremotely.com",
    "ycombinator.com", "lever.co", "greenhouse.io", "workday.com",
    "smartrecruiters.com", "ashbyhq.com", "himalayas.app", "remoteok.com",
    "adzuna.com", "jsearch.rapidapi.com", "glassdoor.com",
}

# Domains that explicitly disallow scraping / automation
BLOCKED_DOMAINS = {
    "dice.com",         # ToS blocks automated applications
    "ziprecruiter.com", # rate-limits heavily
    "monster.com",      # ToS blocks bots
    "careerbuilder.com",
}

# State file for circuit breaker / rate limiter persistence
SAFETY_STATE_PATH = os.path.join("db", "safety_state.json")

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BlockedAction:
    job_id:     str
    job_title:  str
    company:    str
    apply_link: str
    reason:     str
    category:   str   # duplicate | daily_limit | blocked_domain | low_confidence | circuit_breaker | policy
    blocked_at: str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class SafetyResult:
    """Returned by every safety check call."""
    allowed:         bool          = True
    safety_status:   str           = "SAFE"   # SAFE | WARNING | BLOCKED | HALTED
    warnings:        list          = field(default_factory=list)
    actions_blocked: list          = field(default_factory=list)
    daily_stats:     dict          = field(default_factory=dict)
    circuit_breaker: dict          = field(default_factory=dict)
    timestamp:       str           = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if os.path.exists(SAFETY_STATE_PATH):
            with open(SAFETY_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "halted":              False,
        "halt_reason":         "",
        "consecutive_errors":  0,
        "last_error_at":       "",
        "domain_last_access":  {},    # domain -> last access ISO timestamp
        "system_mode":         "auto",  # auto | review
        "mode_reason":         "",
        "recent_confidences":  [],    # last 10 confidence scores
        "total_blocked_today": 0,
        "date_of_stats":       "",
    }


def _save_state(state: dict):
    os.makedirs("db", exist_ok=True)
    state["_saved_at"] = datetime.now(timezone.utc).isoformat()
    with open(SAFETY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


# ── Daily Limit Guard ─────────────────────────────────────────────────────────

def _get_daily_count() -> int:
    """Count AUTO_APPLY applications submitted today (UTC)."""
    today = date.today().isoformat()
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM applications "
                "WHERE execution_status = 'AUTO_APPLY' AND DATE(applied_at) = ?",
                (today,)
            ).fetchone()
            return int(row["cnt"]) if row else 0
    except Exception as exc:
        logger.warning(f"[SafetyGuard] daily count error: {exc}")
        return 0


def _build_daily_stats(daily_count: int) -> dict:
    limit    = _Cfg.DAILY_LIMIT
    hard_cap = _Cfg.DAILY_LIMIT_HARD_CAP
    return {
        "applied_today": daily_count,
        "daily_limit":   limit,
        "hard_cap":      hard_cap,
        "remaining":     max(0, limit - daily_count),
        "utilization_pct": round(daily_count / max(limit, 1) * 100, 1),
        "date":          date.today().isoformat(),
    }


# ── Duplicate Detector ────────────────────────────────────────────────────────

def _job_fingerprint(job: dict) -> str:
    """Create a unique fingerprint from title + company + location."""
    raw = (
        (job.get("title",   "") or "").strip().lower() + "|" +
        (job.get("company", "") or "").strip().lower() + "|" +
        (job.get("location","") or "").strip().lower()
    )
    return hashlib.md5(raw.encode()).hexdigest()


def _url_fingerprint(url: str) -> str:
    """Normalize and hash a URL."""
    url = (url or "").strip().lower()
    # Strip common tracking params
    for param in ("?utm_", "&utm_", "?ref=", "&ref=", "?source="):
        if param in url:
            url = url[:url.index(param)]
    return hashlib.md5(url.encode()).hexdigest()


def _is_duplicate(job: dict) -> tuple[bool, str]:
    """
    Check if this job was already applied to within DUPLICATE_WINDOW_DAYS.
    Returns (is_duplicate, reason).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_Cfg.DUPLICATE_WINDOW_DAYS)).isoformat()
    fp     = _job_fingerprint(job)
    url    = (job.get("apply_link","") or "").strip()
    url_fp = _url_fingerprint(url) if url else None

    try:
        with _get_conn() as conn:
            # Check by fingerprint (title+company+location)
            row = conn.execute(
                "SELECT job_title, company, applied_at FROM applications "
                "WHERE job_fingerprint = ? AND applied_at >= ? LIMIT 1",
                (fp, cutoff)
            ).fetchone()
            if row:
                return True, (
                    f"Already applied to '{row['job_title']}' @ {row['company']} "
                    f"on {(row['applied_at'] or '')[:10]}"
                )

            # Check by URL fingerprint
            if url_fp:
                row2 = conn.execute(
                    "SELECT job_title, company, applied_at FROM applications "
                    "WHERE url_fingerprint = ? AND applied_at >= ? LIMIT 1",
                    (url_fp, cutoff)
                ).fetchone()
                if row2:
                    return True, (
                        f"Same URL already applied: '{row2['job_title']}' @ {row2['company']} "
                        f"on {(row2['applied_at'] or '')[:10]}"
                    )

    except Exception as exc:
        logger.warning(f"[SafetyGuard] duplicate check error: {exc}")

    return False, ""


# ── Domain Policy Checker ─────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Crude domain extractor — avoids importing urllib in hot path."""
    url = (url or "").lower().strip()
    for prefix in ("https://", "http://", "//"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    domain = url.split("/")[0].split("?")[0]
    # Remove www.
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _check_domain_policy(url: str, state: dict) -> tuple[bool, str, str]:
    """
    Returns (allowed, warning_or_empty, rate_limit_msg_or_empty).
    """
    domain = _extract_domain(url)
    if not domain:
        return True, "", ""

    # Blocked domain
    if domain in BLOCKED_DOMAINS:
        return False, f"Domain '{domain}' is on the blocked list (ToS restriction)", ""

    # Rate limiting — enforce min delay between requests to same domain
    last_access = state.get("domain_last_access", {}).get(domain)
    if last_access:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_access)).total_seconds()
        if elapsed < _Cfg.DOMAIN_MIN_DELAY_S:
            wait = round(_Cfg.DOMAIN_MIN_DELAY_S - elapsed, 1)
            return False, "", f"Rate limit: domain '{domain}' accessed {elapsed:.0f}s ago, need {_Cfg.DOMAIN_MIN_DELAY_S}s gap"

    # Not in allowed list — warn but don't block
    warning = ""
    if domain and domain not in ALLOWED_DOMAINS:
        warning = f"Domain '{domain}' not in verified allowlist — manual ToS check recommended"

    return True, warning, ""


# ── Confidence Monitor ────────────────────────────────────────────────────────

def _check_confidence(confidence: int, state: dict) -> tuple[str, list[str]]:
    """
    Returns (suggested_action, warnings_list).
    suggested_action: "auto" | "review" | "skip"
    """
    warnings = []

    # Update rolling window
    recent = state.get("recent_confidences", [])
    recent.append(confidence)
    if len(recent) > 10:
        recent = recent[-10:]
    state["recent_confidences"] = recent

    avg = sum(recent) / len(recent) if recent else confidence

    # Hard skip
    if confidence < _Cfg.MIN_CONFIDENCE:
        warnings.append(
            f"Confidence {confidence}% below minimum threshold {_Cfg.MIN_CONFIDENCE}% — skipping"
        )
        return "skip", warnings

    # Rolling average drop — escalate to review
    if avg < _Cfg.REVIEW_CONFIDENCE and len(recent) >= 3:
        warnings.append(
            f"Rolling avg confidence {avg:.0f}% below {_Cfg.REVIEW_CONFIDENCE}% — escalating to review mode"
        )
        state["system_mode"] = "review"
        state["mode_reason"] = f"Rolling confidence dropped to {avg:.0f}%"
        return "review", warnings

    # Single job borderline
    if confidence < _Cfg.REVIEW_CONFIDENCE:
        warnings.append(f"Confidence {confidence}% is borderline — recommend review before applying")
        return "review", warnings

    # Recovery: if rolling avg is good, restore auto mode
    if avg >= _Cfg.REVIEW_CONFIDENCE + 10 and state.get("system_mode") == "review":
        if "confidence" in state.get("mode_reason","").lower():
            state["system_mode"] = "auto"
            state["mode_reason"] = ""
            warnings.append(f"Confidence recovered (avg {avg:.0f}%) — auto mode restored")

    return "auto", warnings


# ── Circuit Breaker ───────────────────────────────────────────────────────────

def _check_circuit_breaker(state: dict) -> tuple[bool, str]:
    """
    Returns (is_halted, halt_reason).
    Checks consecutive errors; auto-resets after ERROR_WINDOW_MINUTES.
    """
    if state.get("halted"):
        # Check if enough time passed to auto-reset
        last_err = state.get("last_error_at","")
        if last_err:
            age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(last_err)).total_seconds() / 60
            if age_min > _Cfg.ERROR_WINDOW_MINUTES * 2:
                logger.info("[SafetyGuard] Circuit breaker auto-reset after cool-down period")
                state["halted"]             = False
                state["halt_reason"]        = ""
                state["consecutive_errors"] = 0
                return False, ""
        return True, state.get("halt_reason","System halted due to errors")

    return False, ""


def record_error(error_msg: str = ""):
    """Call this when an application attempt fails."""
    state = _load_state()
    state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
    state["last_error_at"]      = datetime.now(timezone.utc).isoformat()

    if (_Cfg.HALT_ON_ERROR and
            state["consecutive_errors"] >= _Cfg.ERROR_HALT_THRESHOLD):
        state["halted"]      = True
        state["halt_reason"] = (
            f"Circuit breaker tripped: {state['consecutive_errors']} consecutive errors. "
            f"Last error: {error_msg[:120]}"
        )
        logger.error(f"[SafetyGuard] CIRCUIT BREAKER TRIPPED: {state['halt_reason']}")
        _log_safety_event("HALT", "circuit_breaker", state["halt_reason"])

    _save_state(state)
    return state["halted"]


def record_success():
    """Call this when an application succeeds — resets error counter."""
    state = _load_state()
    if state.get("consecutive_errors", 0) > 0:
        logger.debug("[SafetyGuard] Error counter reset after successful application")
    state["consecutive_errors"] = 0
    _save_state(state)


# ── Safety Log ────────────────────────────────────────────────────────────────

def _log_safety_event(
    event_type:  str,
    category:    str,
    detail:      str,
    job_title:   str = "",
    company:     str = "",
    job_id:      str = "",
    apply_link:  str = "",
):
    """Persist safety event to the DB safety_log table."""
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO safety_log
                   (event_type, category, detail, job_title, company, job_id, apply_link, logged_at)
                   VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (event_type, category, detail, job_title, company, str(job_id), apply_link)
            )
    except Exception as exc:
        logger.debug(f"[SafetyGuard] log error (non-fatal): {exc}")


def get_safety_log(limit: int = 50) -> list[dict]:
    """Retrieve recent safety events from DB."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM safety_log ORDER BY logged_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_safety_stats() -> dict:
    """Aggregate safety stats for dashboard / API."""
    try:
        with _get_conn() as conn:
            total_blocked = conn.execute(
                "SELECT COUNT(*) as cnt FROM safety_log WHERE event_type='BLOCK'"
            ).fetchone()
            today_applied = _get_daily_count()
            by_category   = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM safety_log "
                "WHERE event_type='BLOCK' GROUP BY category"
            ).fetchall()

            state = _load_state()
            return {
                "total_blocked":    total_blocked["cnt"] if total_blocked else 0,
                "applied_today":    today_applied,
                "daily_limit":      _Cfg.DAILY_LIMIT,
                "remaining_today":  max(0, _Cfg.DAILY_LIMIT - today_applied),
                "system_halted":    state.get("halted", False),
                "halt_reason":      state.get("halt_reason", ""),
                "system_mode":      state.get("system_mode", "auto"),
                "mode_reason":      state.get("mode_reason", ""),
                "consecutive_errors": state.get("consecutive_errors", 0),
                "blocks_by_category": {r["category"]: r["cnt"] for r in by_category},
                "last_confidence_avg": (
                    round(sum(state.get("recent_confidences",[])) /
                          max(len(state.get("recent_confidences",[])),1), 1)
                    if state.get("recent_confidences") else None
                ),
            }
    except Exception as exc:
        logger.warning(f"[SafetyGuard] stats error: {exc}")
        return {}


# ── System Controls ───────────────────────────────────────────────────────────

def reset_halt(reason: str = "Manual reset by operator") -> dict:
    """Operator-triggered halt reset."""
    state = _load_state()
    state["halted"]             = False
    state["halt_reason"]        = ""
    state["consecutive_errors"] = 0
    state["system_mode"]        = "auto"
    _save_state(state)
    _log_safety_event("RESET", "manual", reason)
    logger.info(f"[SafetyGuard] System halt RESET: {reason}")
    return {"ok": True, "message": f"Halt cleared: {reason}"}


def set_review_mode(reason: str = "Manual override") -> dict:
    """Force system into review mode."""
    state = _load_state()
    state["system_mode"] = "review"
    state["mode_reason"] = reason
    _save_state(state)
    _log_safety_event("MODE_CHANGE", "manual", f"Switched to review mode: {reason}")
    logger.info(f"[SafetyGuard] Mode set to REVIEW: {reason}")
    return {"ok": True, "system_mode": "review", "reason": reason}


def set_auto_mode(reason: str = "Manual override") -> dict:
    """Force system back into auto mode."""
    state = _load_state()
    state["system_mode"] = "auto"
    state["mode_reason"] = reason
    _save_state(state)
    _log_safety_event("MODE_CHANGE", "manual", f"Switched to auto mode: {reason}")
    logger.info(f"[SafetyGuard] Mode set to AUTO: {reason}")
    return {"ok": True, "system_mode": "auto", "reason": reason}


def get_system_mode() -> str:
    """Return current system mode: 'auto' or 'review'."""
    return _load_state().get("system_mode", "auto")


def update_domain_access(url: str):
    """Record that a domain was just accessed (for rate limiting)."""
    domain = _extract_domain(url)
    if domain:
        state = _load_state()
        if "domain_last_access" not in state:
            state["domain_last_access"] = {}
        state["domain_last_access"][domain] = datetime.now(timezone.utc).isoformat()
        _save_state(state)


# ── Main Safety Check (call before every application) ─────────────────────────

def check_safe_to_apply(
    job:         dict,
    confidence:  int  = 100,
    dry_run:     bool = True,
) -> SafetyResult:
    """
    Run all safety checks for a single job.

    Args:
        job:        Job dict (needs title, company, apply_link, job_id).
        confidence: Auto-apply confidence score (0-100).
        dry_run:    If True, all checks still run but no blocking is enforced for limits.

    Returns:
        SafetyResult — check result.allowed to decide whether to proceed.
    """
    if not _Cfg.SAFETY_ENABLED:
        return SafetyResult(allowed=True, safety_status="SAFE")

    init_db()
    state    = _load_state()
    warnings = []
    blocked  = []
    status   = "SAFE"

    job_title  = job.get("title",      "") or ""
    company    = job.get("company",    "") or ""
    apply_link = job.get("apply_link", "") or ""
    job_id     = str(job.get("job_id","") or "")

    # ── CHECK 1: Circuit breaker ──────────────────────────────────────────────
    is_halted, halt_reason = _check_circuit_breaker(state)
    if is_halted:
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=halt_reason, category="circuit_breaker",
        )))
        _log_safety_event("BLOCK","circuit_breaker",halt_reason,job_title,company,job_id,apply_link)
        return SafetyResult(
            allowed=False,
            safety_status="HALTED",
            warnings=[halt_reason],
            actions_blocked=blocked,
            daily_stats=_build_daily_stats(_get_daily_count()),
            circuit_breaker={"halted":True,"reason":halt_reason,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    # ── CHECK 2: System mode override ────────────────────────────────────────
    sys_mode = state.get("system_mode", "auto")
    if sys_mode == "review":
        mode_reason = state.get("mode_reason","System in review mode")
        warnings.append(f"System in REVIEW mode: {mode_reason}")
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=f"Review mode active: {mode_reason}", category="review_mode",
        )))
        _log_safety_event("BLOCK","review_mode",mode_reason,job_title,company,job_id,apply_link)
        return SafetyResult(
            allowed=False,
            safety_status="BLOCKED",   # FIX: BLOCKED not WARNING — action is prevented
            warnings=warnings,
            actions_blocked=blocked,
            daily_stats=_build_daily_stats(_get_daily_count()),
            circuit_breaker={"halted":False,"system_mode":"review"},
        )

    # ── CHECK 3: Daily limit ──────────────────────────────────────────────────
    daily_count = _get_daily_count()
    daily_stats = _build_daily_stats(daily_count)

    hard_cap = _Cfg.DAILY_LIMIT_HARD_CAP
    soft_lim = _Cfg.DAILY_LIMIT

    if daily_count >= hard_cap and not dry_run:
        reason = f"Hard daily cap reached: {daily_count}/{hard_cap} applications today"
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=reason, category="daily_limit",
        )))
        _log_safety_event("BLOCK","daily_limit",reason,job_title,company,job_id,apply_link)
        return SafetyResult(
            allowed=False, safety_status="BLOCKED",
            warnings=[reason], actions_blocked=blocked,
            daily_stats=daily_stats,
            circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    if daily_count >= soft_lim and not dry_run:
        reason = (
            f"Daily soft limit reached: {daily_count}/{soft_lim} applications today. "
            f"Hard cap is {hard_cap}."
        )
        warnings.append(reason)
        status = "WARNING"
        _log_safety_event("WARNING","daily_limit",reason,job_title,company,job_id,apply_link)

    elif daily_count >= soft_lim * 0.8:
        warnings.append(
            f"Daily limit approaching: {daily_count}/{soft_lim} applications today "
            f"({int(daily_count/soft_lim*100)}%)"
        )
        if status == "SAFE": status = "WARNING"

    # ── CHECK 3b: Spam / junk job detection ──────────────────────────────────
    title_lower  = job_title.lower()
    desc_lower   = (job.get("description","") or "").lower()
    spam_text    = f"{title_lower} {desc_lower}"
    for signal in SPAM_SIGNALS:
        if signal in spam_text:
            reason = f"Spam signal detected: '{signal}'"
            blocked.append(asdict(BlockedAction(
                job_id=job_id, job_title=job_title, company=company,
                apply_link=apply_link, reason=reason, category="spam_detected",
            )))
            _log_safety_event("BLOCK","spam_detected",reason,job_title,company,job_id,apply_link)
            return SafetyResult(
                allowed=False, safety_status="BLOCKED",
                warnings=[reason], actions_blocked=blocked,
                daily_stats=daily_stats,
                circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
            )

    # ── CHECK 4: Duplicate detection ─────────────────────────────────────────
    is_dup, dup_reason = _is_duplicate(job)
    if is_dup:
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=dup_reason, category="duplicate",
        )))
        _log_safety_event("BLOCK","duplicate",dup_reason,job_title,company,job_id,apply_link)
        return SafetyResult(
            allowed=False, safety_status="BLOCKED",
            warnings=[dup_reason], actions_blocked=blocked,
            daily_stats=daily_stats,
            circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    # ── CHECK 5: Domain policy ────────────────────────────────────────────────
    domain_ok, domain_warn, rate_msg = _check_domain_policy(apply_link, state)
    if not domain_ok:
        reason = rate_msg or domain_warn
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=reason, category="blocked_domain",
        )))
        _log_safety_event("BLOCK","blocked_domain",reason,job_title,company,job_id,apply_link)
        return SafetyResult(
            allowed=False, safety_status="BLOCKED",
            warnings=[reason], actions_blocked=blocked,
            daily_stats=daily_stats,
            circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    if domain_warn:
        warnings.append(domain_warn)
        if status == "SAFE": status = "WARNING"

    # ── CHECK 6: Confidence monitor ───────────────────────────────────────────
    action, conf_warnings = _check_confidence(confidence, state)
    warnings.extend(conf_warnings)

    if action == "skip":
        reason = conf_warnings[0] if conf_warnings else f"Confidence {confidence}% below minimum"
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=reason, category="low_confidence",
        )))
        _log_safety_event("BLOCK","low_confidence",reason,job_title,company,job_id,apply_link)
        _save_state(state)
        return SafetyResult(
            allowed=False, safety_status="BLOCKED",
            warnings=warnings, actions_blocked=blocked,
            daily_stats=daily_stats,
            circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    if action == "review" and not dry_run:
        reason = conf_warnings[0] if conf_warnings else f"Confidence {confidence}% requires review"
        blocked.append(asdict(BlockedAction(
            job_id=job_id, job_title=job_title, company=company,
            apply_link=apply_link, reason=reason, category="review_required",
        )))
        _log_safety_event("BLOCK","review_required",reason,job_title,company,job_id,apply_link)
        _save_state(state)
        return SafetyResult(
            allowed=False, safety_status="BLOCKED",  # FIX: BLOCKED not WARNING — action is prevented
            warnings=warnings, actions_blocked=blocked,
            daily_stats=daily_stats,
            circuit_breaker={"halted":False,"consecutive_errors":state.get("consecutive_errors",0)},
        )

    # ── All checks passed ────────────────────────────────────────────────────
    _save_state(state)

    if blocked:
        status = "BLOCKED"
    elif warnings:
        status = "WARNING"
    else:
        status = "SAFE"

    logger.debug(
        f"[SafetyGuard] {status} — {job_title} @ {company} | "
        f"daily={daily_count}/{soft_lim} confidence={confidence}%"
    )

    return SafetyResult(
        allowed     = (status in ("SAFE","WARNING")),
        safety_status = status,
        warnings    = warnings,
        actions_blocked = blocked,
        daily_stats = daily_stats,
        circuit_breaker = {
            "halted":             False,
            "consecutive_errors": state.get("consecutive_errors", 0),
            "system_mode":        state.get("system_mode","auto"),
        },
    )


# ── Batch safety summary (for orchestrator runs) ──────────────────────────────

def run_safety_check_batch(
    jobs:     list[dict],
    dry_run:  bool = True,
) -> dict:
    """
    Run safety checks on a list of jobs.
    Returns the output schema plus per-job results.
    """
    init_db()
    all_warnings  = []
    all_blocked   = []
    approved_jobs = []
    status        = "SAFE"

    for job in jobs:
        confidence = (
            job.get("match_result",{}).get("confidence_score",100) or
            job.get("confidence_score",100) or 100
        )
        result = check_safe_to_apply(job, confidence, dry_run)

        if result.allowed:
            approved_jobs.append(job)
            # Update domain access time after approval
            if job.get("apply_link"):
                update_domain_access(job["apply_link"])
        else:
            all_blocked.extend(result.actions_blocked)
            all_warnings.extend(result.warnings)

        if result.safety_status == "HALTED":
            status = "HALTED"
            break
        elif result.safety_status == "BLOCKED" and status not in ("HALTED",):
            status = "BLOCKED"
        elif result.safety_status == "WARNING" and status == "SAFE":
            status = "WARNING"

    daily_stats = _build_daily_stats(_get_daily_count())
    state       = _load_state()

    return {
        "safety_status":    status,
        "warnings":         list(dict.fromkeys(all_warnings)),   # deduplicate
        "actions_blocked":  all_blocked,
        "approved_jobs":    approved_jobs,
        "daily_stats":      daily_stats,
        "circuit_breaker": {
            "halted":             state.get("halted", False),
            "consecutive_errors": state.get("consecutive_errors", 0),
            "system_mode":        state.get("system_mode","auto"),
            "halt_reason":        state.get("halt_reason",""),
        },
        "summary": {
            "total":    len(jobs),
            "approved": len(approved_jobs),
            "blocked":  len(all_blocked),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
