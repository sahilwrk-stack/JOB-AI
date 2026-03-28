"""
Job Data Aggregation AI
Multi-source real-time job fetcher with intelligent filtering,
deduplication, normalization, and LLM-based skill extraction.

Pipeline:
  FETCH (APIs + Scrapers) -> NORMALIZE -> FILTER -> DEDUPLICATE -> ENRICH -> OUTPUT
"""

import os
import re
import json
import hashlib
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.helpers import safe_json_call, logger, timer

# ── Source configuration ──────────────────────────────────────────────────────
#
# ARCHITECTURE NOTE — Hybrid API + Browser
# ─────────────────────────────────────────
# "rapidapi" is now the PRIMARY source for job DISCOVERY.
# It calls JSearch (RapidAPI) which aggregates LinkedIn + Indeed + Glassdoor.
# Playwright is ONLY used in auto_apply.py for form-filling (score >= 80).
#
# Old Playwright scrapers (internshala / linkedin / indeed) are kept as
# optional fallbacks under use_scrapers=True but are NOT in any default set.

API_SOURCES = {
    # ── Primary (RapidAPI — fast, reliable, full descriptions) ──────────────
    "rapidapi":  "core.scrapers.rapidapi_search",   # JSearch via RapidAPI ← PRIMARY
    # ── Secondary (free public APIs, no key required) ────────────────────────
    "remoteok":  "core.scrapers.remoteok",
    "himalayas": "core.scrapers.himalayas",
    # ── Legacy (kept for fallback; require separate API keys) ─────────────────
    "jsearch":   "core.scrapers.jsearch",            # old jsearch (same API, kept for compat)
    "adzuna":    "core.scrapers.adzuna",
}

SCRAPER_SOURCES = {
    # Playwright scrapers — only used when use_scrapers=True (SEARCH phase only)
    "internshala": "internshala",
    "linkedin":    "linkedin",
    "indeed":      "indeed",
}

# Sources that are best for India freshers
FRESHER_PRIORITY_SOURCES = ["rapidapi", "remoteok", "himalayas"]
# Sources that are best for remote jobs
REMOTE_PRIORITY_SOURCES  = ["rapidapi", "remoteok", "himalayas"]
# Default balanced set — rapidapi first (richest descriptions for AI scoring)
DEFAULT_SOURCES = ["rapidapi", "remoteok", "himalayas"]

MAX_JOBS_DEFAULT = int(os.getenv("MAX_JOBS_PER_RUN", "50"))

# ── Blacklist keywords ────────────────────────────────────────────────────────

SPAM_TITLE_KEYWORDS = [
    "apply now", "urgent hiring", "walk-in", "work from home scam",
    "earn money", "home based", "part time earn",
]

EXPERIENCE_MISMATCH = {
    "Fresher": ["8+ years", "10+ years", "15+ years", "vp of", "director of", "cto", "cso"],
    "0-1":     ["8+ years", "10+ years", "vp of", "director of"],
    "1-3":     ["10+ years", "15+ years", "vp of"],
    "3+":      [],
}

# ── LLM Skill Extractor ───────────────────────────────────────────────────────

_SKILL_EXTRACT_SYSTEM = """
You are a skill extraction AI. Given a job title and description, extract a clean
list of technical skills and tools required for the role.

Rules:
- Return ONLY a JSON array of strings (skill names)
- Be specific: "Python" not "programming language"
- Max 15 skills
- No duplicates
- No experience requirements (no "3+ years", etc.)

Example: ["Python", "TensorFlow", "SQL", "Docker", "REST APIs"]
"""


def _extract_skills_llm(title: str, description: str) -> list[str]:
    """Use LLM to extract skills from a job description when none are provided."""
    if not description or len(description) < 30:
        return []
    snippet = description[:800]   # keep token cost low
    prompt  = f"Job Title: {title}\n\nDescription:\n{snippet}"
    result  = safe_json_call(_SKILL_EXTRACT_SYSTEM, prompt)
    if isinstance(result, list):
        return [str(s) for s in result if s]
    return []


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize_experience(raw: str | int | None) -> str:
    """Normalize experience field to human-readable string."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, int):
        if raw == 0:
            return "Fresher"
        years = raw / 12
        if years < 1:
            return "0-1 year"
        elif years < 3:
            return "1-3 years"
        elif years < 6:
            return "3-6 years"
        else:
            return "6+ years"
    raw_str = str(raw).strip()
    if re.search(r"\d", raw_str):
        return raw_str
    return raw_str


def _normalize_location(raw: str) -> str:
    if not raw:
        return "Not specified"
    raw = raw.strip()
    if not raw or raw.lower() in ("", "n/a", "null", "none"):
        return "Remote"
    return raw


def _normalize_salary(raw: str) -> str:
    if not raw:
        return "Not disclosed"
    raw = raw.strip()
    if not raw:
        return "Not disclosed"
    return raw


def _normalize_employment_type(raw: str) -> str:
    if not raw:
        return ""
    raw_lower = raw.lower()
    if "full" in raw_lower:
        return "Full-Time"
    if "part" in raw_lower:
        return "Part-Time"
    if "contract" in raw_lower or "freelance" in raw_lower:
        return "Contract"
    if "intern" in raw_lower:
        return "Internship"
    if "remote" in raw_lower:
        return "Remote Full-Time"
    return raw.title()


def normalize_job(raw: dict) -> dict:
    """
    Normalize a raw job dict (from any source) to the standard output schema.
    """
    return {
        "job_id":              raw.get("job_id", ""),
        "title":               (raw.get("title") or "").strip(),
        "company":             (raw.get("company") or "").strip(),
        "location":            _normalize_location(raw.get("location", "")),
        "skills_required":     raw.get("skills_required") or [],
        "experience_required": _normalize_experience(raw.get("experience_required")),
        "apply_link":          (raw.get("apply_link") or "").strip(),
        "salary":              _normalize_salary(raw.get("salary", "")),
        "description":         (raw.get("description") or "").strip(),
        "employment_type":     _normalize_employment_type(raw.get("employment_type", "")),
        "is_remote":           bool(raw.get("is_remote", False)),
        "posted_at":           raw.get("posted_at", ""),
        "_source":             raw.get("_source", "unknown"),
    }


# ── Deduplication ─────────────────────────────────────────────────────────────

def _url_fingerprint(url: str) -> str:
    """Normalize a URL to a stable fingerprint for dedup."""
    url = re.sub(r"[?&]utm_[^&]+", "", url)   # strip UTM params
    url = url.rstrip("/").lower()
    url = re.sub(r"https?://", "", url)
    return hashlib.md5(url.encode()).hexdigest()


def _title_company_fingerprint(title: str, company: str) -> str:
    """Secondary dedup key: normalized title + company."""
    title_clean   = re.sub(r"[^a-z0-9\s]", "", title.lower()).strip()
    company_clean = re.sub(r"[^a-z0-9\s]", "", company.lower()).strip()
    title_tokens  = " ".join(sorted(title_clean.split()))
    return hashlib.md5(f"{title_tokens}|{company_clean}".encode()).hexdigest()


def deduplicate(jobs: list[dict]) -> list[dict]:
    """
    Remove duplicate jobs using two-pass deduplication:
    1. URL fingerprint (exact URL match)
    2. Title + Company fingerprint (same job, different source)
    """
    seen_urls:    set[str] = set()
    seen_titles:  set[str] = set()
    unique:       list[dict] = []

    for job in jobs:
        url   = job.get("apply_link", "") or job.get("job_id", "")
        title = job.get("title", "")
        co    = job.get("company", "")

        url_fp   = _url_fingerprint(url) if url else ""
        title_fp = _title_company_fingerprint(title, co)

        if url_fp and url_fp in seen_urls:
            continue
        if title_fp in seen_titles:
            continue

        if url_fp:
            seen_urls.add(url_fp)
        seen_titles.add(title_fp)
        unique.append(job)

    removed = len(jobs) - len(unique)
    if removed:
        logger.info(f"[Aggregator] Deduplication: removed {removed} duplicates -> {len(unique)} unique.")
    return unique


# ── Profile-Aware Filtering ───────────────────────────────────────────────────

def _skill_overlap(job_skills: list[str], candidate_skills: list[str]) -> int:
    """Count how many candidate skills appear in job skills (case-insensitive)."""
    job_lower = {s.lower() for s in job_skills}
    return sum(1 for s in candidate_skills if s.lower() in job_lower)


def _is_spam(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in SPAM_TITLE_KEYWORDS)


def _experience_match(job: dict, profile: dict) -> bool:
    """Check if job experience requirements align with candidate's level."""
    exp = profile.get("experience_level", "Fresher")
    blacklist = EXPERIENCE_MISMATCH.get(exp, [])
    title_lower = job.get("title", "").lower()
    desc_lower  = (job.get("description", "") or "")[:300].lower()
    combined    = title_lower + " " + desc_lower
    return not any(kw in combined for kw in blacklist)


def _remote_filter(job: dict, profile: dict) -> bool:
    """If candidate prefers remote, skip non-remote office jobs."""
    pref_locs = profile.get("preferred_locations", [])
    wants_remote = any("remote" in loc.lower() for loc in pref_locs)
    if not wants_remote:
        return True    # No remote filter required
    return job.get("is_remote", False) or "remote" in job.get("location", "").lower()


def filter_jobs(jobs: list[dict], profile: dict, min_skill_overlap: int = 1) -> list[dict]:
    """
    Apply profile-aware filters:
    - Remove spam
    - Remove experience mismatches
    - Enforce remote preference
    - Require at least `min_skill_overlap` skill matches (if skills provided)

    Args:
        jobs:              Normalized job dicts.
        profile:           Parsed resume dict.
        min_skill_overlap: Minimum number of skills that must match (0 = no filter).

    Returns:
        Filtered list.
    """
    candidate_skills = (
        profile.get("primary_skills", []) + profile.get("secondary_skills", [])
    )
    total   = len(jobs)
    passed  = []

    for job in jobs:
        title = job.get("title", "")

        if _is_spam(title):
            continue

        if not _experience_match(job, profile):
            continue

        if not _remote_filter(job, profile):
            continue

        if min_skill_overlap > 0 and candidate_skills and job.get("skills_required"):
            overlap = _skill_overlap(job["skills_required"], candidate_skills)
            if overlap < min_skill_overlap:
                continue

        passed.append(job)

    logger.info(f"[Aggregator] Filtering: {total} -> {len(passed)} jobs passed.")
    return passed


# ── Skill Enrichment ──────────────────────────────────────────────────────────

def enrich_missing_skills(jobs: list[dict], max_enrich: int = 10) -> list[dict]:
    """
    For jobs with no `skills_required`, use LLM to extract skills from description.
    Limits LLM calls to `max_enrich` jobs for cost control.
    """
    enriched_count = 0
    for job in jobs:
        if job.get("skills_required"):
            continue
        if not job.get("description"):
            continue
        if enriched_count >= max_enrich:
            break

        skills = _extract_skills_llm(job["title"], job["description"])
        if skills:
            job["skills_required"] = skills
            enriched_count += 1

    if enriched_count:
        logger.info(f"[Aggregator] Enriched skills for {enriched_count} jobs via LLM.")
    return jobs


# ── Source Loader ─────────────────────────────────────────────────────────────

def _load_api_source(name: str, profile: dict, max_per: int) -> list[dict]:
    module_path = API_SOURCES.get(name)
    if not module_path:
        return []
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return mod.fetch(profile, max_results=max_per)
    except Exception as exc:
        logger.warning(f"[Aggregator] Source '{name}' failed: {exc}")
        return []


def _load_scraper_source(name: str, profile: dict, max_per: int) -> list[dict]:
    logger.warning(
        f"[Aggregator] Scraper source '{name}' is disabled in Career Copilot mode. "
        "Use RapidAPI search instead."
    )
    return []


# ── Scanned-job DB logger ─────────────────────────────────────────────────────

def _log_scanned_jobs_to_db(raw_jobs: list[dict]) -> None:
    """
    Write every job seen by the scraper to the DB with status='SCANNED'.
    This makes the "Jobs Scanned" counter on the dashboard go up immediately,
    even if none pass the match threshold.

    Non-fatal — any DB error is logged and suppressed.
    """
    if not raw_jobs:
        return
    try:
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path
        _project_root = _Path(__file__).parent.parent
        _db_path = _project_root / "db" / "agent_memory.db"
        conn = _sqlite3.connect(str(_db_path))
        cur  = conn.cursor()

        # Ensure the jobs_scanned table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs_scanned (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_title   TEXT,
                company     TEXT,
                location    TEXT,
                source      TEXT,
                url         TEXT,
                scanned_at  DATETIME DEFAULT (datetime('now')),
                UNIQUE(job_title, company, url)
            )
        """)

        inserted = 0
        for job in raw_jobs:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO jobs_scanned "
                    "(job_title, company, location, source, url) VALUES (?,?,?,?,?)",
                    (
                        (job.get("title") or "")[:200],
                        (job.get("company") or "")[:200],
                        (job.get("location") or "")[:200],
                        (job.get("_source") or job.get("source") or "")[:100],
                        (job.get("apply_link") or job.get("url") or "")[:500],
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except _sqlite3.Error:
                pass

        conn.commit()
        conn.close()
        logger.success(
            f"💾 [DB UPDATE] ✅ Logged {inserted} new scanned jobs to DB "
            f"(total attempted: {len(raw_jobs)})"
        )
    except Exception as exc:
        logger.warning(f"💾 [DB UPDATE] ⚠️  Could not log scanned jobs to DB: {exc}")


# ── Main Aggregator ───────────────────────────────────────────────────────────

@timer
def aggregate_jobs(
    profile: dict,
    sources: Optional[list[str]] = None,
    max_total: int = MAX_JOBS_DEFAULT,
    max_per_source: int = 20,
    use_scrapers: bool = False,
    min_skill_overlap: int = 0,
    enrich_skills: bool = True,
) -> dict:
    """
    Fetch, filter, deduplicate and normalize jobs from multiple sources.

    Args:
        profile:           Parsed resume dict from resume_parser.
        sources:           Explicit list of source names. Auto-selected if None.
        max_total:         Max jobs in final output.
        max_per_source:    Max jobs fetched per source.
        use_scrapers:      If True, enable Playwright fallback scrapers.
        min_skill_overlap: Minimum skill overlap for filtering (0 = disabled).
        enrich_skills:     Use LLM to fill in missing skills.

    Returns:
        Dict with "jobs" list and "meta" stats.
    """
    # ── Auto-select sources based on profile ─────────────────────────────────
    if sources is None:
        exp    = profile.get("experience_level", "Fresher")
        locs   = profile.get("preferred_locations", [])
        is_remote_preferred = any("remote" in l.lower() for l in locs)

        if exp in ("Fresher", "0-1"):
            sources = FRESHER_PRIORITY_SOURCES
            if use_scrapers:
                sources = list(dict.fromkeys(
                    ["jsearch", "adzuna", "internshala", "indeed", "remoteok"]
                ))
        elif is_remote_preferred:
            sources = REMOTE_PRIORITY_SOURCES
        else:
            sources = DEFAULT_SOURCES

    logger.info(
        f"🚀 [SCRAPER] Starting job fetch — "
        f"Sources: {sources} | "
        f"Profile: {profile.get('experience_level','?')} | "
        f"Roles: {profile.get('target_job_roles', [])[:3]}"
    )

    # ── Parallel fetch ────────────────────────────────────────────────────────
    all_raw: list[dict] = []

    api_names     = [s for s in sources if s in API_SOURCES]
    scraper_names = [s for s in sources if s in SCRAPER_SOURCES] if use_scrapers else []

    logger.info(f"🚀 [SCRAPER] API sources: {api_names} | Scraper sources: {scraper_names}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_load_api_source, name, profile, max_per_source): name
            for name in api_names
        }
        if use_scrapers:
            for name in scraper_names:
                futures[pool.submit(_load_scraper_source, name, profile, max_per_source)] = name

        for future in as_completed(futures):
            source_name = futures[future]
            try:
                results = future.result()
                all_raw.extend(results)
                if results:
                    logger.success(f"🚀 [SCRAPER] ✅ {source_name}: {len(results)} raw jobs fetched")
                else:
                    logger.warning(f"🚀 [SCRAPER] ⚠️  {source_name}: 0 jobs returned (empty or rate-limited)")
            except Exception as exc:
                logger.error(f"🚀 [SCRAPER] ❌ {source_name} raised exception: {exc}")

    logger.info(f"🚀 [SCRAPER] Total raw jobs collected across all sources: {len(all_raw)}")
    if not all_raw:
        logger.warning(
            "🚀 [SCRAPER] ⚠️  ALL sources returned 0 jobs. "
            "Check internet connection, API keys, and that sources are not rate-limited."
        )

    # ── Normalize ─────────────────────────────────────────────────────────────
    normalized = [normalize_job(j) for j in all_raw]

    # ── Deduplicate ───────────────────────────────────────────────────────────
    unique = deduplicate(normalized)
    logger.info(f"🚀 [SCRAPER] After dedup: {len(unique)}/{len(normalized)} unique jobs")

    # ── Filter ────────────────────────────────────────────────────────────────
    filtered = filter_jobs(unique, profile, min_skill_overlap)
    logger.info(f"🚀 [SCRAPER] After filter: {len(filtered)}/{len(unique)} jobs passed relevance filter")

    # ── Enrich missing skills via LLM ─────────────────────────────────────────
    if enrich_skills:
        filtered = enrich_missing_skills(filtered, max_enrich=10)

    # ── Trim to max_total ─────────────────────────────────────────────────────
    final = filtered[:max_total]

    # ── Log every scanned job to DB so "Jobs Scanned" counter goes up ─────────
    _log_scanned_jobs_to_db(all_raw)

    logger.success(
        f"🚀 [SCRAPER] ✅ Done — {len(final)} jobs ready for matching "
        f"[raw={len(all_raw)} → dedup={len(unique)} → filter={len(filtered)} → final={len(final)}]"
    )

    return {
        "jobs": final,
        "meta": {
            "sources_used":     sources,
            "raw_fetched":      len(all_raw),
            "after_dedup":      len(unique),
            "after_filter":     len(filtered),
            "final_count":      len(final),
            "experience_level": profile.get("experience_level", ""),
            "target_roles":     profile.get("target_job_roles", []),
        },
    }


# ── Convenience wrapper for orchestrator ─────────────────────────────────────

def fetch_jobs_for_candidate(
    parsed_resume: dict,
    candidate_intel: dict,
    use_scrapers: bool = False,
    max_total: int = MAX_JOBS_DEFAULT,
) -> list[dict]:
    """
    High-level wrapper used by the orchestrator.
    Automatically tunes source selection and filtering based on candidate tier.

    Args:
        parsed_resume:   Output from resume_parser.
        candidate_intel: Output from candidate_intel engine.
        use_scrapers:    Enable Playwright fallback scrapers.
        max_total:       Cap on total jobs returned.

    Returns:
        List of normalized job dicts (ready for job_matcher.rank_jobs).
    """
    tier = candidate_intel.get("candidate_tier", "Standard")

    # Elite/Premium: RapidAPI first (full descriptions needed for precise AI scoring),
    # then supplementary free sources. Requires at least 1 skill overlap.
    if tier in ("Elite Premium", "Premium"):
        sources    = ["rapidapi", "himalayas", "remoteok"]
        skill_min  = 1
    else:
        sources    = None   # auto-select based on exp level (defaults to rapidapi-first)
        skill_min  = 0

    result = aggregate_jobs(
        profile=parsed_resume,
        sources=sources,
        max_total=max_total,
        max_per_source=20,
        use_scrapers=use_scrapers,
        min_skill_overlap=skill_min,
        enrich_skills=True,
    )

    # Map aggregator schema -> orchestrator schema (add url / title aliases)
    jobs = []
    for j in result["jobs"]:
        jobs.append({
            **j,
            "url":  j.get("apply_link", ""),
            "title": j.get("title", ""),
        })

    return jobs
