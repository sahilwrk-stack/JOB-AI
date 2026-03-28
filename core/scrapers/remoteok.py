"""
Source: RemoteOK API
Covers: Remote-only global jobs.
Docs: https://remoteok.com/api — completely free, no auth required.
Rate limit: Be respectful (1 req/2s).
"""

import time
import httpx
from utils.helpers import logger

BASE_URL = "https://remoteok.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ResumeAI/1.0",
}


def fetch(profile: dict, max_results: int = 20) -> list[dict]:
    """
    Fetch remote jobs from RemoteOK (free, no key required).

    Args:
        profile:     Parsed resume dict.
        max_results: Max jobs to return.

    Returns:
        List of normalized job dicts.
    """
    tags = _build_tags(profile)
    logger.info(f"[RemoteOK] Fetching jobs for tags: {tags}")

    all_jobs: list[dict] = []

    for tag in tags[:3]:   # Query top 3 tags to avoid hammering
        url = f"{BASE_URL}?tag={tag.replace(' ', '%20').lower()}"
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            items = resp.json()
            if isinstance(items, list) and items:
                items = items[1:]   # First item is metadata
            all_jobs.extend(items or [])
        except Exception as exc:
            logger.warning(f"[RemoteOK] Failed for tag '{tag}': {exc}")
        time.sleep(1.2)   # Polite rate limiting

    jobs = []
    seen: set[str] = set()

    for item in all_jobs:
        if not isinstance(item, dict):
            continue

        job_id = str(item.get("id", ""))
        if job_id in seen:
            continue
        seen.add(job_id)

        jobs.append({
            "job_id":               job_id,
            "title":                item.get("position", ""),
            "company":              item.get("company", ""),
            "location":             "Remote",
            "skills_required":      _parse_tags(item.get("tags", [])),
            "experience_required":  "",
            "apply_link":           item.get("apply_url") or f"https://remoteok.com/remote-jobs/{job_id}",
            "salary":               _fmt_salary(item),
            "description":          item.get("description", ""),
            "employment_type":      "Remote Full-Time",
            "is_remote":            True,
            "posted_at":            item.get("date", ""),
            "_source":              "remoteok",
        })

        if len(jobs) >= max_results:
            break

    logger.success(f"[RemoteOK] Fetched {len(jobs)} jobs.")
    return jobs


def _build_tags(profile: dict) -> list[str]:
    """Pick the best RemoteOK tags from the candidate profile."""
    roles  = profile.get("target_job_roles", [])
    skills = profile.get("primary_skills", [])

    # Map common role names to RemoteOK tag conventions
    tag_map = {
        "machine learning":     "machine-learning",
        "data science":         "data-science",
        "data scientist":       "data-science",
        "ml engineer":          "machine-learning",
        "full stack":           "fullstack",
        "full stack developer": "fullstack",
        "frontend":             "frontend",
        "backend":              "backend",
        "devops":               "devops",
        "python developer":     "python",
        "data analyst":         "data",
        "nlp":                  "nlp",
        "react":                "react",
        "node.js":              "node",
    }

    tags = []
    for r in roles + skills:
        key = r.lower()
        tags.append(tag_map.get(key, key.replace(" ", "-")))

    return list(dict.fromkeys(tags)) or ["developer"]


def _parse_tags(raw: list | str) -> list[str]:
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _fmt_salary(item: dict) -> str:
    mn = item.get("salary_min") or item.get("salary")
    mx = item.get("salary_max")
    if mn and mx:
        return f"USD {mn:,} - {mx:,} / yr"
    if mn:
        return f"USD {mn:,}+ / yr"
    return ""
