"""
Component 2 — Candidate Intelligence Engine
Market-aware talent evaluation: tier, salary range, company fit, risks.
"""

import os

from utils.helpers import safe_json_call, logger, timer

# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a Market Intelligence AI + Talent Evaluator.

Intelligence Layers:
- Projects-based tier boosting (strong GitHub/projects -> upgrade tier)
- Skill rarity detection (niche skills -> higher value)
- Market demand awareness (2024–2025 Indian tech market context)

Tier Definitions:
- Elite Premium: Top 5% candidates, strong projects + rare skills + 3+ yrs OR exceptional fresher
- Premium: Strong candidate, good projects, relevant skills
- Standard Plus: Average-to-good candidate, some projects
- Standard: Basic candidate, few differentiators
- Needs Work: Significant gaps, low market value

Salary Benchmarks (INR, Indian Market 2025):
- Fresher Premium: 6–12 LPA
- 0-1yr Standard: 3–6 LPA
- 1-3yr Standard: 6–14 LPA
- 3+yr Senior: 14–35 LPA
- Elite at any level: +50% premium

Rules:
- Return STRICT valid JSON only
- No hallucination — base evaluation on provided data
- If fresher + strong GitHub/projects -> upgrade to Premium or Elite Premium

Return this exact JSON schema:
{
  "candidate_tier": "Elite Premium | Premium | Standard Plus | Standard | Needs Work",
  "tier_justification": "",
  "confidence_score": 0,
  "market_demand_score": 0,
  "target_locations": [],
  "target_minimum_salary_inr": 0,
  "company_type_focus": [],
  "risk_flags": []
}
"""


def _build_user_prompt(parsed_resume: dict) -> str:
    import json
    return f"""
Evaluate this candidate based on their parsed resume data.

PARSED RESUME DATA:
{json.dumps(parsed_resume, indent=2)}

Consider:
1. Skill quality and rarity
2. Project complexity and domain relevance
3. Experience level vs. actual skill depth
4. Market demand for their skill set in 2025

Return only valid JSON. No explanation.
"""


# ── Public API ───────────────────────────────────────────────────────────────

def _deterministic_fallback(parsed_resume: dict) -> dict:
    """
    Rule-based candidate evaluation when LLM is unavailable.
    Guarantees all required fields are always present.
    """
    skills = (
        parsed_resume.get("primary_skills", []) +
        parsed_resume.get("secondary_skills", []) +
        parsed_resume.get("tools_and_technologies", [])
    )
    skills_lower = [s.lower() for s in skills]
    years  = parsed_resume.get("years_of_experience", 0) or 0
    level  = parsed_resume.get("experience_level", "Fresher")

    # Tier scoring
    HIGH_VALUE = {"python", "ml", "machine learning", "deep learning", "llm",
                  "aws", "kubernetes", "docker", "react", "node.js", "sql",
                  "tensorflow", "pytorch", "fastapi", "data science"}
    hv_count = sum(1 for s in skills_lower if any(h in s for h in HIGH_VALUE))
    projects = parsed_resume.get("projects", [])

    if years >= 3 or (years >= 1 and hv_count >= 5):
        tier = "Premium"
        conf = 80
        sal_min, sal_max = 1000000, 2000000
    elif years >= 1 or hv_count >= 4 or len(projects) >= 2:
        tier = "Standard Plus"
        conf = 65
        sal_min, sal_max = 600000, 1000000
    elif hv_count >= 2:
        tier = "Standard"
        conf = 55
        sal_min, sal_max = 400000, 700000
    else:
        tier = "Needs Work"
        conf = 40
        sal_min, sal_max = 300000, 500000

    demand = min(10, 4 + hv_count)

    risk_flags = []
    if not skills:
        risk_flags.append("No skills detected — check resume format")
    if years == 0 and not projects:
        risk_flags.append("No work experience or projects listed")
    if len(skills) < 3:
        risk_flags.append("Very few skills listed")

    locs = parsed_resume.get("preferred_locations")
    if not locs or not isinstance(locs, list) or not any(str(x).strip() for x in locs):
        el = os.getenv("CANDIDATE_LOCATION", "").strip()
        locs = [el] if el else ["India", "Remote", "Hybrid"]

    return {
        "candidate_tier":              tier,
        "tier_justification":          f"Rule-based: {hv_count} high-value skills, {years} yrs exp, {len(projects)} projects",
        "confidence_score":            conf,
        "market_demand_score":         demand,
        "target_locations":            locs,
        "target_minimum_salary_inr":   sal_min,
        "target_maximum_salary_inr":   sal_max,
        "company_type_focus":          ["Startup", "Tech Company", "MNC"],
        "strengths":                   [s.title() for s in skills_lower[:5]],
        "risk_flags":                  risk_flags,
        "salary_range":                f"₹{sal_min//100000}L – ₹{sal_max//100000}L",
        "_evaluated_by":               "deterministic_fallback (no LLM key)",
    }


def _ensure_intel_lists(result: dict, parsed_resume: dict) -> dict:
    _memo: dict = {"fb": None}

    def _fb() -> dict:
        if _memo["fb"] is None:
            _memo["fb"] = _deterministic_fallback(parsed_resume)
        return _memo["fb"]

    def _nonempty_list(val) -> bool:
        return isinstance(val, list) and any(str(x).strip() for x in val if x is not None)

    if not _nonempty_list(result.get("target_locations")):
        pref = parsed_resume.get("preferred_locations")
        if _nonempty_list(pref):
            result["target_locations"] = [str(x).strip() for x in pref if str(x).strip()][:8]
        else:
            el = os.getenv("CANDIDATE_LOCATION", "").strip()
            result["target_locations"] = [el] if el else ["India", "Remote", "Hybrid"]

    if not _nonempty_list(result.get("company_type_focus")):
        result["company_type_focus"] = list(
            _fb().get("company_type_focus") or ["Startup", "Product tech", "MNC"]
        )

    if not _nonempty_list(result.get("risk_flags")):
        rf = _fb().get("risk_flags")
        result["risk_flags"] = (
            list(rf) if _nonempty_list(rf) else ["No major flags — profile looks balanced"]
        )

    if not (result.get("candidate_tier") or "").strip():
        result["candidate_tier"] = _fb().get("candidate_tier", "Standard")

    return result


def preview_intel(parsed_resume: dict) -> dict:
    if not parsed_resume or parsed_resume.get("_parse_error"):
        return {}
    base = _deterministic_fallback(parsed_resume)
    return _ensure_intel_lists(base, parsed_resume)


def _coerce_int_score(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def merge_intel_for_dashboard(parsed_resume: dict, intel: dict | None) -> dict:
    intel = dict(intel or {})
    fill = preview_intel(parsed_resume) if parsed_resume else {}
    if not fill:
        return intel

    out = {**fill, **intel}

    if _coerce_int_score(intel.get("confidence_score")) <= 0:
        out["confidence_score"] = fill["confidence_score"]
    if _coerce_int_score(intel.get("market_demand_score")) <= 0:
        out["market_demand_score"] = fill["market_demand_score"]

    def _nonempty_list(val) -> bool:
        return isinstance(val, list) and any(str(x).strip() for x in val if x is not None)

    if not _nonempty_list(intel.get("target_locations")):
        out["target_locations"] = fill["target_locations"]
    if not _nonempty_list(intel.get("company_type_focus")):
        out["company_type_focus"] = fill["company_type_focus"]

    rf = intel.get("risk_flags")
    if not _nonempty_list(rf):
        out["risk_flags"] = fill["risk_flags"]
    elif isinstance(rf, list) and len(rf) == 1 and str(rf[0]).strip().lower() in ("none", "null", ""):
        out["risk_flags"] = fill["risk_flags"]

    if not (intel.get("candidate_tier") or "").strip():
        out["candidate_tier"] = fill["candidate_tier"]

    return out


@timer
def evaluate_candidate(parsed_resume: dict) -> dict:
    """
    Evaluate candidate tier, salary range, target companies and risk flags.
    Falls back to deterministic rule-based evaluation if LLM is unavailable.

    Args:
        parsed_resume: Output from resume_parser.parse_resume()

    Returns:
        Candidate intelligence dict.
    """
    if not parsed_resume:
        logger.warning("[CandidateIntel] Empty resume data provided.")
        return {}

    logger.info("[CandidateIntel] Evaluating candidate profile...")
    result = safe_json_call(SYSTEM_PROMPT, _build_user_prompt(parsed_resume))

    if not result:
        logger.warning("[CandidateIntel] LLM unavailable — using deterministic fallback.")
        result = _deterministic_fallback(parsed_resume)
    else:
        try:
            cs = int(float(result.get("confidence_score", 0)))
        except (TypeError, ValueError):
            cs = 0
        try:
            md = int(float(result.get("market_demand_score", 0)))
        except (TypeError, ValueError):
            md = 0
        if cs <= 0 or md <= 0:
            fb = _deterministic_fallback(parsed_resume)
            if cs <= 0:
                result["confidence_score"] = fb.get("confidence_score", 55)
            if md <= 0:
                result["market_demand_score"] = fb.get("market_demand_score", 5)

    result = _ensure_intel_lists(result, parsed_resume)

    logger.success(
        f"[CandidateIntel] Tier: {result.get('candidate_tier')} | "
        f"Confidence: {result.get('confidence_score')}% | "
        f"Market Demand: {result.get('market_demand_score')}"
    )
    return result
