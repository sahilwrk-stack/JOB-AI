"""
Source: Himalayas Jobs API
Covers: Remote-first global tech jobs (very clean, structured data).
Docs: https://himalayas.app/api — free, no auth required.
"""

import httpx
from utils.helpers import logger

BASE_URL = "https://himalayas.app/jobs/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 ResumeAI/1.0",
    "Accept":     "application/json",
}


def fetch(profile: dict, max_results: int = 20) -> list[dict]:
    """
    Fetch remote jobs from Himalayas API.

    Args:
        profile:     Parsed resume dict.
        max_results: Max jobs to return.

    Returns:
        List of normalized job dicts.
    """
    queries = _build_queries(profile)
    exp     = profile.get("experience_level", "Fresher")
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for query in queries[:2]:
        params = {
            "q":    query,
            "limit": min(max_results, 50),
        }
        logger.info(f"[Himalayas] Querying: '{query}'")
        try:
            resp = httpx.get(BASE_URL, headers=HEADERS, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("jobs", [])
        except Exception as exc:
            logger.warning(f"[Himalayas] Request failed for '{query}': {exc}")
            continue

        for item in items:
            job_id = str(item.get("id", item.get("slug", "")))
            if job_id in seen:
                continue
            seen.add(job_id)

            # Experience filter: skip "senior" roles for freshers
            if exp in ("Fresher", "0-1"):
                title_lower = item.get("title", "").lower()
                if any(s in title_lower for s in ("senior", "lead", "principal", "staff", "director")):
                    continue

            all_jobs.append(_normalize(item))

        if len(all_jobs) >= max_results:
            break

    logger.success(f"[Himalayas] Fetched {len(all_jobs)} jobs.")
    return all_jobs[:max_results]


def _build_queries(profile: dict) -> list[str]:
    roles  = profile.get("target_job_roles", [])
    skills = profile.get("primary_skills", [])
    queries = []
    for r in roles[:2]:
        queries.append(r)
    if not queries and skills:
        queries.append(skills[0])
    return queries or ["software engineer"]


def _normalize(item: dict) -> dict:
    company = item.get("company", {})
    if isinstance(company, str):
        company_name = company
    else:
        company_name = company.get("name", "") if isinstance(company, dict) else ""

    skills = []
    for tag in item.get("categories", []) + item.get("skills", []):
        if isinstance(tag, str):
            skills.append(tag)
        elif isinstance(tag, dict):
            skills.append(tag.get("name", ""))

    salary = _fmt_salary(item)
    slug   = item.get("slug", item.get("id", ""))

    return {
        "job_id":              str(item.get("id", slug)),
        "title":               item.get("title", ""),
        "company":             company_name,
        "location":            "Remote",
        "skills_required":     [s for s in skills if s],
        "experience_required": item.get("experience", ""),
        "apply_link":          item.get("applicationUrl") or f"https://himalayas.app/jobs/{slug}",
        "salary":              salary,
        "description":         item.get("description", ""),
        "employment_type":     item.get("type", "Remote Full-Time"),
        "is_remote":           True,
        "posted_at":           item.get("publishedAt", ""),
        "_source":             "himalayas",
    }


def _fmt_salary(item: dict) -> str:
    mn = item.get("salaryMin") or item.get("salary_min")
    mx = item.get("salaryMax") or item.get("salary_max")
    currency = item.get("salaryCurrency", "USD")
    if mn and mx:
        return f"{currency} {int(mn):,} - {int(mx):,} / yr"
    if mn:
        return f"{currency} {int(mn):,}+ / yr"
    return ""
