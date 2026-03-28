"""
Source: JSearch API (via RapidAPI)
Covers: LinkedIn, Indeed, Glassdoor, ZipRecruiter aggregated data.
Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
Free tier: 200 requests/month
"""

import os
import httpx
from utils.helpers import logger

RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "")
JSEARCH_HOST  = "jsearch.p.rapidapi.com"
BASE_URL      = f"https://{JSEARCH_HOST}/search"

HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": JSEARCH_HOST,
}


def _build_query(profile: dict) -> str:
    """Construct a smart search query from candidate profile."""
    roles  = profile.get("target_job_roles", [])
    skills = profile.get("primary_skills", [])
    exp    = profile.get("experience_level", "Fresher")

    base = roles[0] if roles else (skills[0] if skills else "software developer")

    if exp in ("Fresher", "0-1"):
        return f"fresher {base} entry level"
    elif exp == "1-3":
        return f"{base} junior mid-level"
    else:
        return f"senior {base}"


def fetch(profile: dict, max_results: int = 20) -> list[dict]:
    """
    Fetch jobs from JSearch (RapidAPI).

    Args:
        profile: Parsed resume dict (from resume_parser).
        max_results: Max jobs to return.

    Returns:
        List of raw normalized job dicts.
    """
    if not RAPIDAPI_KEY:
        logger.warning("[JSearch] RAPIDAPI_KEY not set. Skipping.")
        return []

    location  = _resolve_location(profile)
    query     = _build_query(profile)
    remote_ok = not profile.get("preferred_locations") or "remote" in str(
        profile.get("preferred_locations", [])
    ).lower()

    params = {
        "query":         f"{query} in {location}" if location else query,
        "page":          "1",
        "num_pages":     "2",
        "date_posted":   "month",
        "remote_jobs_only": "true" if remote_ok else "false",
    }

    logger.info(f"[JSearch] Querying: '{params['query']}'")

    try:
        resp = httpx.get(BASE_URL, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[JSearch] Request failed: {exc}")
        return []

    jobs = []
    for item in data.get("data", [])[:max_results]:
        jobs.append({
            "job_id":               item.get("job_id", ""),
            "title":                item.get("job_title", ""),
            "company":              item.get("employer_name", ""),
            "location":             _fmt_location(item),
            "skills_required":      item.get("job_required_skills") or [],
            "experience_required":  item.get("job_required_experience", {}).get(
                                        "required_experience_in_months", ""
                                    ),
            "apply_link":           item.get("job_apply_link", ""),
            "salary":               _fmt_salary(item),
            "description":          item.get("job_description", ""),
            "employment_type":      item.get("job_employment_type", ""),
            "is_remote":            item.get("job_is_remote", False),
            "posted_at":            item.get("job_posted_at_datetime_utc", ""),
            "_source":              "jsearch",
        })

    logger.success(f"[JSearch] Fetched {len(jobs)} jobs.")
    return jobs


def _resolve_location(profile: dict) -> str:
    locs = profile.get("preferred_locations", [])
    candidate_loc = os.getenv("CANDIDATE_LOCATION", "")
    if locs:
        return locs[0]
    return candidate_loc or "India"


def _fmt_location(item: dict) -> str:
    parts = [
        item.get("job_city", ""),
        item.get("job_state", ""),
        item.get("job_country", ""),
    ]
    return ", ".join(p for p in parts if p) or "Remote"


def _fmt_salary(item: dict) -> str:
    mn = item.get("job_min_salary")
    mx = item.get("job_max_salary")
    period = item.get("job_salary_period", "")
    currency = item.get("job_salary_currency", "")
    if mn and mx:
        return f"{currency} {mn:,} - {mx:,} / {period}".strip()
    if mn:
        return f"{currency} {mn:,}+ / {period}".strip()
    return ""
