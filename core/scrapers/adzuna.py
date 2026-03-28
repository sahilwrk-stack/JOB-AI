"""
Source: Adzuna Jobs API
Covers: India (in), UK, US, AU and 15+ countries.
Docs: https://developer.adzuna.com/
Free tier: 250 calls/month — register at developer.adzuna.com
"""

import os
import httpx
from utils.helpers import logger

ADZUNA_APP_ID  = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "in")   # "in" for India

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"

EXPERIENCE_KEYWORDS = {
    "Fresher": ["fresher", "entry level", "graduate", "0-1 year"],
    "0-1":     ["fresher", "entry level", "0-1 year", "junior"],
    "1-3":     ["junior", "associate", "1-3 years"],
    "3+":      ["senior", "lead", "3+ years", "experienced"],
}


def _build_what(profile: dict) -> str:
    roles  = profile.get("target_job_roles", [])
    skills = profile.get("primary_skills", [])[:3]
    base   = roles[0] if roles else (skills[0] if skills else "developer")
    return base


def _build_where(profile: dict) -> str:
    locs = profile.get("preferred_locations", [])
    if locs:
        return locs[0]
    return os.getenv("CANDIDATE_LOCATION", "Bangalore")


def fetch(profile: dict, max_results: int = 20) -> list[dict]:
    """
    Fetch jobs from Adzuna API.

    Args:
        profile:     Parsed resume dict.
        max_results: Max jobs to return.

    Returns:
        List of normalized job dicts.
    """
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        logger.warning("[Adzuna] ADZUNA_APP_ID / ADZUNA_APP_KEY not set. Skipping.")
        return []

    exp   = profile.get("experience_level", "Fresher")
    what  = _build_what(profile)
    where = _build_where(profile)

    # Append experience hint to query
    exp_hint = EXPERIENCE_KEYWORDS.get(exp, [""])[0]
    full_query = f"{what} {exp_hint}".strip()

    url = BASE_URL.format(country=ADZUNA_COUNTRY)
    params = {
        "app_id":           ADZUNA_APP_ID,
        "app_key":          ADZUNA_APP_KEY,
        "results_per_page": min(max_results, 50),
        "what":             full_query,
        "where":            where,
        "sort_by":          "date",
        "max_days_old":     30,
        "content-type":     "application/json",
    }

    logger.info(f"[Adzuna] Querying: '{full_query}' in '{where}'")

    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error(f"[Adzuna] Request failed: {exc}")
        return []

    jobs = []
    for item in data.get("results", []):
        salary = _fmt_salary(item)
        jobs.append({
            "job_id":               str(item.get("id", "")),
            "title":                item.get("title", ""),
            "company":              item.get("company", {}).get("display_name", ""),
            "location":             item.get("location", {}).get("display_name", ""),
            "skills_required":      [],   # Adzuna doesn't return skills; extracted later
            "experience_required":  "",
            "apply_link":           item.get("redirect_url", ""),
            "salary":               salary,
            "description":          item.get("description", ""),
            "employment_type":      item.get("contract_type", ""),
            "is_remote":            "remote" in item.get("title", "").lower()
                                    or "remote" in item.get("description", "").lower(),
            "posted_at":            item.get("created", ""),
            "_source":              "adzuna",
        })

    logger.success(f"[Adzuna] Fetched {len(jobs)} jobs.")
    return jobs


def _fmt_salary(item: dict) -> str:
    mn = item.get("salary_min")
    mx = item.get("salary_max")
    if mn and mx:
        return f"INR {mn:,.0f} - {mx:,.0f} / yr"
    if mn:
        return f"INR {mn:,.0f}+ / yr"
    return ""
