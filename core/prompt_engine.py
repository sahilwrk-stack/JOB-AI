"""
Component 8 — Dynamic Prompt Engine
Generates context-aware, adaptive prompts based on candidate tier and strategy.
"""

import json
from typing import Optional

from utils.helpers import logger


# ── Tier-specific configurations ─────────────────────────────────────────────

TIER_CONFIG = {
    "Elite Premium": {
        "salary_priority": True,
        "company_size":    "FAANG / Unicorn / Series B+",
        "role_level":      "Senior / Lead / Staff",
        "min_match_score": 75,
        "job_keywords":    ["staff engineer", "lead", "principal", "architect", "senior"],
        "avoid":           ["junior", "intern", "entry-level", "trainee"],
    },
    "Premium": {
        "salary_priority": True,
        "company_size":    "Mid-size to Large / Funded Startup",
        "role_level":      "Mid / Senior",
        "min_match_score": 70,
        "job_keywords":    ["senior", "mid-level", "engineer", "developer", "data scientist"],
        "avoid":           ["intern", "trainee"],
    },
    "Standard Plus": {
        "salary_priority": False,
        "company_size":    "Any / Service Companies / Startups",
        "role_level":      "Junior / Mid",
        "min_match_score": 60,
        "job_keywords":    ["developer", "engineer", "analyst", "associate"],
        "avoid":           ["5+ years required", "10+ years"],
    },
    "Standard": {
        "salary_priority": False,
        "company_size":    "Service Companies / Small Startups",
        "role_level":      "Junior / Associate",
        "min_match_score": 55,
        "job_keywords":    ["junior", "associate", "analyst", "trainee", "fresher"],
        "avoid":           ["senior", "lead", "manager", "5+ years"],
    },
    "Needs Work": {
        "salary_priority": False,
        "company_size":    "Any",
        "role_level":      "Entry Level / Internship",
        "min_match_score": 50,
        "job_keywords":    ["fresher", "trainee", "intern", "entry level", "0-1 year"],
        "avoid":           ["3+ years", "senior", "lead"],
    },
}


# ── Dynamic Prompt Builders ───────────────────────────────────────────────────

def build_job_search_prompt(
    parsed_resume: dict,
    candidate_intel: dict,
    current_strategy: str = "",
    location_override: Optional[str] = None,
) -> str:
    """
    Build a dynamic job search instruction prompt tailored to candidate tier.
    """
    tier = candidate_intel.get("candidate_tier", "Standard")
    cfg  = TIER_CONFIG.get(tier, TIER_CONFIG["Standard"])

    skills = parsed_resume.get("primary_skills", [])[:8]
    roles  = parsed_resume.get("target_job_roles", [])[:4]
    exp    = parsed_resume.get("experience_level", "Fresher")
    locs   = location_override or ", ".join(
        candidate_intel.get("target_locations", [parsed_resume.get("preferred_locations", ["Remote"])[0]])
    )
    salary = candidate_intel.get("target_minimum_salary_inr", 0)
    demand = candidate_intel.get("market_demand_score", 0)

    strategy_block = f"\nCurrent Strategy: {current_strategy}" if current_strategy else ""

    return f"""
=== DYNAMIC JOB SEARCH PROMPT ===

Candidate Level  : {tier}
Experience       : {exp}
Core Skills      : {', '.join(skills)}
Target Roles     : {', '.join(roles)}
Target Locations : {locs}
Min Salary (INR) : {salary:,}
Market Demand    : {demand}%
{strategy_block}

--- ADAPTIVE RULES ---
{"-> Prioritize HIGH-SALARY roles. Target top-tier companies." if cfg['salary_priority'] else "-> Prioritize ROLE FIT over salary. Build experience first."}
-> Target company size  : {cfg['company_size']}
-> Target role level    : {cfg['role_level']}
-> Min match score      : {cfg['min_match_score']}/100
-> Preferred keywords   : {', '.join(cfg['job_keywords'])}
-> Avoid roles with     : {', '.join(cfg['avoid'])}

--- SEARCH INSTRUCTION ---
Find the best matching job postings for this candidate.
Filter by: match_score >= {cfg['min_match_score']}.
Rank by: match_score DESC, salary DESC.
Return structured job list.
"""


def build_cover_letter_prompt(
    job: dict,
    parsed_resume: dict,
    match_result: dict,
    candidate_intel: dict,
) -> str:
    """
    Build a dynamic cover letter generation prompt.
    """
    tier = candidate_intel.get("candidate_tier", "Standard")
    matched_skills = match_result.get("matched_skills", [])
    projects = parsed_resume.get("projects", [])[:2]

    tone = (
        "confident and value-driven"
        if tier in ("Elite Premium", "Premium")
        else "enthusiastic and growth-oriented"
    )

    return f"""
Write a compelling, ATS-optimized cover letter for this job application.

TONE: {tone}
CANDIDATE TIER: {tier}

JOB: {job.get('title', '')} at {job.get('company', '')}
LOCATION: {job.get('location', '')}

MATCHED SKILLS TO HIGHLIGHT: {', '.join(matched_skills[:6])}
RELEVANT PROJECTS: {', '.join(p['name'] for p in projects if p.get('name'))}

MATCH SCORE: {match_result.get('match_score', 0)}/100

Write 3 paragraphs:
1. Opening: Why this specific company + role
2. Body: Match skills + project evidence -> business value
3. Closing: Confident call to action

Max 250 words. ATS-friendly (avoid tables/images). Professional tone.
"""


def build_skill_gap_prompt(
    parsed_resume: dict,
    job: dict,
    match_result: dict,
) -> str:
    """
    Build a prompt for generating a targeted learning roadmap.
    """
    missing = match_result.get("missing_critical_skills", [])
    score   = match_result.get("match_score", 0)

    return f"""
Create a targeted 30-day skill improvement roadmap.

TARGET ROLE: {job.get('title', '')}
CURRENT MATCH SCORE: {score}/100
MISSING CRITICAL SKILLS: {', '.join(missing)}

CANDIDATE EXISTING SKILLS: {', '.join(parsed_resume.get('primary_skills', []))}

Generate:
1. Top 3 skills to learn immediately (with free resources)
2. Daily learning plan (30 days)
3. Estimated score improvement after completing plan

Be specific — name actual courses, GitHub projects, or tools to practice with.
"""


def get_tier_min_score(tier: str) -> int:
    """Return the minimum match score threshold for a given candidate tier."""
    cfg = TIER_CONFIG.get(tier, TIER_CONFIG["Standard"])
    return cfg["min_match_score"]


def log_dynamic_prompt(prompt_type: str, tier: str):
    logger.debug(f"[PromptEngine] Built '{prompt_type}' prompt for tier: {tier}")
