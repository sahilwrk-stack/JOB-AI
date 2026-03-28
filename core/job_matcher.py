"""
Component 3 — Semantic Job Matching Engine
Scores job postings against a candidate profile with nuanced semantic understanding.
Falls back to keyword-overlap scoring when LLM is unavailable.
"""

import json
import re
from typing import Union

from utils.helpers import safe_json_call, logger, timer

# Synonym groups for semantic fallback matching
_SYNONYM_GROUPS = [
    {"python", "py"},
    {"sql", "mysql", "postgresql", "sqlite", "database"},
    {"machine learning", "ml", "ai", "artificial intelligence", "deep learning"},
    {"data science", "data analysis", "data analytics", "data analyst"},
    {"pandas", "numpy", "scipy", "data manipulation"},
    {"tableau", "power bi", "powerbi", "looker", "data visualization", "matplotlib", "seaborn"},
    {"tensorflow", "pytorch", "keras", "neural network"},
    {"nlp", "natural language processing", "huggingface", "transformers", "bert"},
    {"aws", "azure", "gcp", "cloud"},
    {"docker", "kubernetes", "k8s", "container"},
    {"react", "reactjs", "vue", "angular", "frontend"},
    {"node", "nodejs", "express", "fastapi", "flask", "backend"},
    {"git", "github", "version control"},
    {"excel", "spreadsheet"},
    {"scikit-learn", "sklearn", "scikit learn"},
]


def _keyword_match(parsed_resume: dict, job_description: str, job_title: str = "") -> dict:
    """
    Keyword + synonym overlap scoring used when LLM is unavailable.
    Returns a result dict compatible with the LLM schema.
    """
    resume_skills = set(
        s.lower() for s in (
            parsed_resume.get("primary_skills", []) +
            parsed_resume.get("secondary_skills", []) +
            parsed_resume.get("tools_and_technologies", [])
        )
    )
    jd_lower = (job_description + " " + job_title).lower()

    matched_skills   = []
    semantic_matches = []
    jd_keywords      = set(re.findall(r'\b\w[\w\+\#\.]*\b', jd_lower))

    # Direct keyword hits
    for skill in resume_skills:
        if skill in jd_lower:
            matched_skills.append(skill.title())

    # Synonym group hits
    for group in _SYNONYM_GROUPS:
        resume_hit = any(s in resume_skills for s in group)
        jd_hit     = any(s in jd_lower for s in group)
        if resume_hit and jd_hit:
            hit_skill = next((s for s in group if s in resume_skills), None)
            jd_skill  = next((s for s in group if s in jd_lower), None)
            if hit_skill and jd_skill and hit_skill != jd_skill:
                semantic_matches.append({
                    "candidate_skill": hit_skill.title(),
                    "job_requirement": jd_skill.title(),
                    "similarity_score": 75,
                    "reasoning":       "synonym group match",
                })
                if hit_skill.title() not in matched_skills:
                    matched_skills.append(hit_skill.title())

    total_resume_skills = max(len(resume_skills), 1)
    skill_score   = min(50, round(len(matched_skills) / total_resume_skills * 50))
    semantic_score = min(20, len(semantic_matches) * 5)

    years = parsed_resume.get("years_of_experience", 0) or 0
    exp_score = 10 if years >= 1 else 7

    project_score = min(10, len(parsed_resume.get("projects", [])) * 3)
    bonus = 5 if len(matched_skills) >= 5 else 0

    total = skill_score + semantic_score + exp_score + project_score + bonus
    confidence = min(70, 40 + len(matched_skills) * 5)

    return {
        "match_score":             total,
        "is_elite":                total >= 85,
        "confidence_score":        confidence,
        "matched_skills":          matched_skills,
        "missing_critical_skills": [],
        "semantic_matches":        semantic_matches,
        "justification":           f"Keyword fallback: {len(matched_skills)} direct matches, {len(semantic_matches)} semantic matches",
        "_matched_by":             "keyword_fallback",
    }

# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an Elite AI Job Matcher with deep semantic understanding.

SCORING SYSTEM (must sum to 100):
- Core Skills Match       -> 50 pts  (exact + close synonyms)
- Semantic Skill Match    -> 20 pts  (related domain skills, transferable)
- Experience Fit          -> 15 pts  (years + seniority alignment)
- Project Relevance       -> 10 pts  (domain overlap, complexity match)
- Bonus Skills            -> 5 pts   (extra value-adds, certs, rare skills)

INTELLIGENCE RULES:
- Understand similar skills: ML ≈ AI ≈ Data Science ≈ Deep Learning
- Give partial credit for related experience (React -> Vue.js = 60% credit)
- Penalize ONLY critical required skills mismatches
- Consider Fresher with strong projects as 0-1 years equivalent
- A candidate with 80%+ match should be considered for application

is_elite = match_score >= 85 AND confidence_score >= 80

Rules:
- Return STRICT valid JSON only
- No hallucination

Return this exact JSON schema:
{
  "match_score": 0,
  "is_elite": false,
  "confidence_score": 0,
  "matched_skills": [],
  "missing_critical_skills": [],
  "semantic_matches": [
    {
      "candidate_skill": "",
      "job_requirement": "",
      "similarity_score": 0,
      "reasoning": ""
    }
  ],
  "justification": ""
}
"""


def _build_user_prompt(parsed_resume: dict, job_description: str, candidate_intel: dict = None) -> str:
    context = ""
    if candidate_intel:
        context = f"""
CANDIDATE INTELLIGENCE:
Tier: {candidate_intel.get('candidate_tier', 'Unknown')}
Market Demand Score: {candidate_intel.get('market_demand_score', 0)}%
"""
    return f"""
Score this candidate-job match.

CANDIDATE PROFILE:
{json.dumps(parsed_resume, indent=2)}
{context}

JOB DESCRIPTION:
\"\"\"
{job_description}
\"\"\"

Apply the 5-factor scoring rubric. Return only valid JSON.
"""


# ── Batch Matching ────────────────────────────────────────────────────────────

@timer
def match_job(
    parsed_resume: dict,
    job_description: str,
    job_title: str = "",
    candidate_intel: dict = None,
) -> dict:
    """
    Score a single job posting against a candidate profile.

    Args:
        parsed_resume:   Output from resume_parser.
        job_description: Raw JD text.
        job_title:       Optional job title for logging.
        candidate_intel: Optional output from candidate_intel engine.

    Returns:
        Match result dict.
    """
    label = job_title or "unnamed role"
    logger.info(f"[JobMatcher] Matching: '{label}'")

    result = safe_json_call(
        SYSTEM_PROMPT,
        _build_user_prompt(parsed_resume, job_description, candidate_intel),
    )

    if not result:
        logger.warning(f"[JobMatcher] LLM unavailable for '{label}' — using keyword fallback.")
        result = _keyword_match(parsed_resume, job_description, label)

    score = result.get("match_score", 0)
    is_elite = result.get("is_elite", False)
    logger.success(
        f"[JobMatcher] '{label}' -> Score: {score}/100 | "
        f"Elite: {is_elite} | Confidence: {result.get('confidence_score', 0)}%"
    )
    return result


@timer
def rank_jobs(
    parsed_resume: dict,
    jobs: list[dict],
    candidate_intel: dict = None,
    min_score: int = 50,
) -> list[dict]:
    """
    Score and rank a list of job postings.

    Args:
        parsed_resume:   Parsed resume dict.
        jobs:            List of dicts with keys: title, description, url, company, location.
        candidate_intel: Optional candidate intelligence dict.
        min_score:       Minimum match_score to include in results.

    Returns:
        Sorted list of (job + match_result) dicts, highest score first.
    """
    logger.info(f"[JobMatcher] Ranking {len(jobs)} jobs...")
    ranked = []

    for job in jobs:
        if not isinstance(job, dict):
            logger.warning(f"[JobMatcher] Skipping non-dict job item: {type(job)}")
            continue
        jd = (
            job.get("description")
            or job.get("job_description")
            or job.get("job_highlights")
            or ""
        )
        if isinstance(jd, list):
            jd = " ".join(str(x) for x in jd)
        match = match_job(
            parsed_resume,
            str(jd),
            job.get("title", ""),
            candidate_intel,
        )
        score = match.get("match_score", 0)
        if score >= min_score:
            ranked.append({**job, "match_result": match})

    ranked.sort(key=lambda x: x["match_result"]["match_score"], reverse=True)
    logger.info(f"[JobMatcher] {len(ranked)}/{len(jobs)} jobs passed threshold (>= {min_score}).")
    return ranked
