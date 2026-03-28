"""
RapidAPI Job Search — Primary Discovery Engine
===============================================
Replaces Playwright for the SEARCH phase entirely.
Playwright is now ONLY used for the form-filling (application) phase.

Data flow:
  fetch_jobs_from_rapidapi()          ← this module (Step 1)
        ↓
  job_matcher.rank_jobs()             ← AI scores each description  (Step 2)
        ↓
  [score >= ELITE_APPLY_THRESHOLD]    ← Elite Gate                  (Step 3)
        ↓
  auto_apply.apply_with_browser()     ← Playwright fills the form

API:  JSearch on RapidAPI
      https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
      Aggregates: LinkedIn · Indeed · Glassdoor · ZipRecruiter
      Free tier: 200 req / month

Key is read from the RAPIDAPI_KEY env-var (set in .env).
"""

from __future__ import annotations

import os
import time
import hashlib
import requests
from typing import Optional

from utils.helpers import logger

# ── Config ────────────────────────────────────────────────────────────────────

RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "")
JSEARCH_HOST  = "jsearch.p.rapidapi.com"
BASE_URL      = f"https://{JSEARCH_HOST}/search"
DETAILS_URL   = f"https://{JSEARCH_HOST}/job-details"

# Score threshold above which Playwright will be triggered for auto-apply.
# Anything below this → REVIEW or SKIP; Playwright never launches.
ELITE_APPLY_THRESHOLD = int(os.getenv("ELITE_APPLY_THRESHOLD", "80"))

# How many seconds to wait between paginated API calls (rate-limit safety)
_PAGE_DELAY_S = 0.4


def _headers() -> dict:
    """Always reads the key fresh from env so hot-reloads work."""
    return {
        "X-RapidAPI-Key":  os.getenv("RAPIDAPI_KEY", RAPIDAPI_KEY),
        "X-RapidAPI-Host": JSEARCH_HOST,
    }


# ── Query Builder ─────────────────────────────────────────────────────────────

def _build_queries(profile: dict) -> list[str]:
    """
    Generate up to 3 distinct search queries from the candidate profile so
    we spread net wide across different JSearch result sets.

    Examples:
      "Python Data Analyst entry level India"
      "Machine Learning Engineer junior"
      "fresher data science"
    """
    roles  = profile.get("target_job_roles", [])
    skills = profile.get("primary_skills", [])
    exp    = profile.get("experience_level", "Fresher")

    primary_role  = roles[0]  if roles  else (skills[0] if skills else "Software Developer")
    fallback_role = roles[1]  if len(roles) > 1 else primary_role
    skill_combo   = " ".join(skills[:2]) if len(skills) >= 2 else skills[0] if skills else "Python"

    level_prefix = {
        "Fresher": "fresher entry level",
        "0-1":     "junior entry level",
        "1-3":     "junior mid-level",
        "3+":      "senior",
    }.get(exp, "")

    queries = []
    if level_prefix:
        queries.append(f"{level_prefix} {primary_role}")
    else:
        queries.append(primary_role)

    if fallback_role != primary_role:
        queries.append(f"{fallback_role} {level_prefix}".strip())

    queries.append(f"{skill_combo} developer")

    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def _resolve_location(profile: dict) -> str:
    locs = profile.get("preferred_locations", [])
    if locs:
        return locs[0]
    return os.getenv("CANDIDATE_LOCATION", "India")


# ── Raw → Standard Schema ─────────────────────────────────────────────────────

def _parse_item(item: dict, source_query: str = "") -> dict:
    """
    Map one JSearch API result object to our standard job schema.

    Standard output fields (used by job_matcher and job_aggregator):
        job_id      — stable unique identifier
        title       — job title
        company     — employer name
        location    — city/state/country string
        description — FULL job description text  ← fed directly to AI scorer
        apply_url   — direct application URL     ← handed to Playwright if elite
        apply_link  — alias of apply_url (for aggregator compatibility)
        salary      — formatted salary string
        skills_required — list of required skills (if API provides them)
        employment_type — Full-Time / Part-Time / Contract / Internship
        is_remote   — bool
        posted_at   — ISO datetime string
        _source     — "rapidapi_jsearch"
    """
    mn  = item.get("job_min_salary")
    mx  = item.get("job_max_salary")
    cur = item.get("job_salary_currency", "")
    per = item.get("job_salary_period", "")
    if mn and mx:
        salary_str = f"{cur} {mn:,.0f} – {mx:,.0f} / {per}".strip()
    elif mn:
        salary_str = f"{cur} {mn:,.0f}+ / {per}".strip()
    else:
        salary_str = ""

    loc_parts = [
        item.get("job_city", ""),
        item.get("job_state", ""),
        item.get("job_country", ""),
    ]
    location = ", ".join(p for p in loc_parts if p) or "Remote"

    apply_url = (
        (item.get("job_apply_link") or "").strip()
        or (item.get("apply_link") or "").strip()
        or (item.get("apply_url") or "").strip()
        or (item.get("applyUrl") or "").strip()
    )
    if not apply_url:
        apply_url = (item.get("job_google_link") or item.get("url") or item.get("job_link") or "").strip()

    # Dedupe key: stable fingerprint from job_id, falling back to title+company
    raw_id = item.get("job_id") or f"{item.get('job_title','')}|{item.get('employer_name','')}"
    job_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]

    exp_months = ""
    exp_data = item.get("job_required_experience") or {}
    if isinstance(exp_data, dict):
        exp_months = exp_data.get("required_experience_in_months", "")

    return {
        "job_id":               job_id,
        "title":                item.get("job_title", "").strip(),
        "company":              item.get("employer_name", "").strip(),
        "location":             location,
        "description":          item.get("job_description", "").strip(),   # ← full text for AI
        "apply_url":            apply_url,
        "apply_link":           apply_url,                                  # aggregator alias
        "url":                  apply_url or (item.get("job_google_link") or item.get("url") or "").strip(),
        "salary":               salary_str,
        "skills_required":      item.get("job_required_skills") or [],
        "experience_required":  str(exp_months),
        "employment_type":      item.get("job_employment_type", ""),
        "is_remote":            bool(item.get("job_is_remote", False)),
        "posted_at":            item.get("job_posted_at_datetime_utc", ""),
        "_source":              "rapidapi_jsearch",
        "_query":               source_query,
    }


# ── Core Fetch Function ───────────────────────────────────────────────────────

def fetch_jobs_from_rapidapi(
    query: str,
    location: str = "India",
    *,
    num_pages: int = 2,
    date_posted: str = "month",
    employment_types: str = "FULLTIME,PARTTIME,INTERN,CONTRACTOR",
    remote_only: bool = False,
    max_results: int = 20,
) -> list[dict]:
    """
    Step 1 — Primary job discovery via RapidAPI / JSearch.

    Replaces ALL Playwright-based search scraping.  Only one HTTP call per
    page (fast, reliable, no browser overhead).

    Args:
        query            Raw search string, e.g. "Python Data Analyst entry level"
        location         City/country string or "Remote"
        num_pages        Pages to fetch (each page ≈ 10 results)
        date_posted      Recency filter: "today" | "3days" | "week" | "month"
        employment_types Comma-separated JSearch type codes
        remote_only      Return only remote roles
        max_results      Hard cap on returned jobs

    Returns:
        List of standard job dicts ready for job_matcher.rank_jobs().
        Every dict has `description` (full text) for Step 2 AI scoring
        and `apply_url` for Step 3 Playwright handoff.
    """
    key = os.getenv("RAPIDAPI_KEY", RAPIDAPI_KEY)
    if not key:
        logger.error(
            "🔑 [RAPIDAPI] ❌ RAPIDAPI_KEY is not set. "
            "Add it to .env: RAPIDAPI_KEY=your_key_here"
        )
        return []

    logger.info(
        f"🌐 [RAPIDAPI] Searching — query='{query}' | location='{location}' | "
        f"pages={num_pages} | remote_only={remote_only}"
    )

    all_items: list[dict] = []

    for page in range(1, num_pages + 1):
        full_query = f"{query} in {location}" if location and location.lower() != "remote" else query

        params: dict = {
            "query":        full_query,
            "page":         str(page),
            "num_pages":    "1",
            "date_posted":  date_posted,
        }
        # employment_types is optional — omit when empty to avoid 403 on some free tiers
        if employment_types:
            params["employment_types"] = employment_types
        if remote_only:
            params["remote_jobs_only"] = "true"

        try:
            logger.info(f"🌐 [RAPIDAPI] Page {page}/{num_pages} → '{full_query}'")
            resp = requests.get(
                BASE_URL,
                headers=_headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

        except requests.exceptions.Timeout:
            logger.error(f"🌐 [RAPIDAPI] ❌ Request timed out on page {page}.")
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            if status == 429:
                logger.warning(
                    "🌐 [RAPIDAPI] ⚠️  Rate-limited (429). "
                    "Waiting 6 s then retrying once. "
                    "Consider upgrading to a paid RapidAPI plan for higher limits."
                )
                time.sleep(6)
                try:
                    resp = requests.get(BASE_URL, headers=_headers(), params=params, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as retry_exc:
                    logger.error(f"🌐 [RAPIDAPI] ❌ Retry also failed: {retry_exc}")
                    break
            elif status == 403:
                logger.error(
                    "🌐 [RAPIDAPI] ❌ 403 Forbidden. Most likely cause: "
                    "JSearch API is not subscribed on your RapidAPI account. "
                    "Fix in 30 seconds: "
                    "1. Go to https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch "
                    "2. Click 'Subscribe to Test' (free tier = 200 req/month) "
                    "3. Re-run the pipeline — jobs will start flowing."
                )
                break
            elif status == 401:
                logger.error(
                    "🌐 [RAPIDAPI] ❌ 401 Unauthorized. "
                    "RAPIDAPI_KEY in .env is invalid or expired. "
                    "Get a fresh key from https://rapidapi.com/developer/apps"
                )
                break
            else:
                logger.error(f"🌐 [RAPIDAPI] ❌ HTTP {status}: {e}")
                break
        except Exception as exc:
            logger.error(f"🌐 [RAPIDAPI] ❌ Unexpected error on page {page}: {exc}")
            break

        items = data.get("data", [])
        logger.success(
            f"🌐 [RAPIDAPI] ✅ Page {page}: {len(items)} results "
            f"(status={data.get('status', '?')})"
        )
        all_items.extend(items)

        if len(all_items) >= max_results:
            break
        if page < num_pages:
            time.sleep(_PAGE_DELAY_S)   # be polite to the rate limiter

    # Parse all raw items into standard schema
    jobs = [_parse_item(item, query) for item in all_items[:max_results]]

    logger.success(
        f"🌐 [RAPIDAPI] ✅ Fetch complete — {len(jobs)} jobs "
        f"| descriptions available: "
        f"{sum(1 for j in jobs if j.get('description'))}/{len(jobs)}"
    )
    return jobs


# ── Profile-Aware Wrapper (used by job_aggregator) ───────────────────────────

def fetch(profile: dict, max_results: int = 20) -> list[dict]:
    """
    Aggregator-compatible entry point.
    Runs up to 3 smart queries derived from the candidate profile, merges the
    results, and deduplicates by job_id before returning.

    Args:
        profile     Parsed resume dict from resume_parser.
        max_results Cap on returned jobs (across all queries).

    Returns:
        Deduplicated list of standard job dicts.
    """
    location = _resolve_location(profile)
    queries  = _build_queries(profile)
    remote_ok = any("remote" in str(loc).lower()
                    for loc in profile.get("preferred_locations", []))

    logger.info(
        f"🌐 [RAPIDAPI] Profile-aware fetch — "
        f"queries={queries} | location='{location}' | remote={remote_ok}"
    )

    seen_ids: set[str] = set()
    combined: list[dict] = []

    per_query = max(max_results // len(queries), 10)

    for q in queries:
        if len(combined) >= max_results:
            break
        results = fetch_jobs_from_rapidapi(
            query        = q,
            location     = location,
            num_pages    = 2,
            date_posted  = "month",
            remote_only  = remote_ok,
            max_results  = per_query,
        )
        for job in results:
            jid = job.get("job_id", "")
            if jid and jid in seen_ids:
                continue
            if jid:
                seen_ids.add(jid)
            combined.append(job)

    logger.success(
        f"🌐 [RAPIDAPI] Profile fetch done — "
        f"{len(combined)} unique jobs from {len(queries)} queries"
    )
    return combined[:max_results]


# ── Elite Gate Helper (used by orchestrator / auto_apply) ────────────────────

def should_use_playwright(match_score: int, elite_threshold: int | None = None) -> bool:
    """
    Step 3 — Elite Gate.

    Returns True ONLY when the AI match score clears the elite threshold,
    meaning Playwright should be invoked to auto-fill the application form.

    Args:
        match_score      AI-computed score (0–100) from job_matcher.rank_jobs().
        elite_threshold  Override; defaults to ELITE_APPLY_THRESHOLD env-var (80).

    Returns:
        True  → hand apply_url to Playwright
        False → mark as REVIEW or SKIP; no browser launched
    """
    threshold = elite_threshold if elite_threshold is not None else ELITE_APPLY_THRESHOLD
    return match_score >= threshold
