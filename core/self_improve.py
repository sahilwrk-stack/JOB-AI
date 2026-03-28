"""
Component 7 — Self-Improvement Loop
Analyzes past application data to extract patterns and update job strategy.
"""

import json

from core.memory import (
    get_all_applications,
    get_skill_performance,
    save_strategy,
    get_latest_strategy,
)
from utils.helpers import safe_json_call, logger, timer

# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a Self-Learning Optimization AI for job application strategy.

Goal: Improve job selection and application quality over time by analyzing
historical application data, callback rates, and skill performance.

Analysis Dimensions:
1. Which job types / companies yield callbacks
2. Which match score ranges are actually effective
3. Which skills correlate with callbacks
4. Which application strategies underperform

Output actionable, specific insights (not generic advice).

Rules:
- Return STRICT valid JSON only
- Insights should be data-driven and specific
- Strategy update should be 1-2 actionable sentences

Return this exact JSON schema:
{
  "insights": [],
  "recommended_skill_focus": [],
  "job_strategy_update": "",
  "optimal_match_threshold": 0,
  "avoid_patterns": []
}
"""


def _build_analysis_prompt(applications: list[dict], skill_stats: list[dict]) -> str:
    total = len(applications)
    callbacks = sum(1 for a in applications if a.get("callback_received"))
    applied = sum(1 for a in applications if a.get("execution_status") == "AUTO_APPLY")
    avg_score = (
        sum(a.get("match_score", 0) for a in applications) / max(total, 1)
    )

    top_skills = skill_stats[:10] if skill_stats else []

    recent = applications[:20]
    for app in recent:
        app.pop("form_mapping", None)
        app.pop("notes", None)

    return f"""
Analyze the following job application history and generate strategic insights.

SUMMARY STATISTICS:
- Total applications tracked: {total}
- Auto-applied: {applied}
- Callbacks received: {callbacks}
- Callback rate: {round(callbacks / max(total, 1) * 100, 1)}%
- Average match score: {round(avg_score, 1)}/100

TOP PERFORMING SKILLS (by callback rate):
{json.dumps(top_skills, indent=2)}

RECENT APPLICATION SAMPLE (last 20):
{json.dumps(recent, indent=2)}

Generate specific, data-driven insights to improve the strategy.
Return only valid JSON.
"""


# ── Public API ────────────────────────────────────────────────────────────────

@timer
def run_self_improvement() -> dict:
    """
    Analyze application history and update the agent's job strategy.

    Returns:
        Self-improvement result dict with insights + updated strategy.
    """
    logger.info("[SelfImprove] Starting self-improvement analysis...")

    applications = get_all_applications(limit=200)
    skill_stats  = get_skill_performance()

    if not applications:
        logger.warning("[SelfImprove] No application history found. Skipping analysis.")
        return {
            "insights": ["No application history yet. Apply to more jobs first."],
            "recommended_skill_focus": [],
            "job_strategy_update": "Collect more data before optimizing.",
            "optimal_match_threshold": 70,
            "avoid_patterns": [],
        }

    result = safe_json_call(SYSTEM_PROMPT, _build_analysis_prompt(applications, skill_stats))

    if not result:
        logger.warning("[SelfImprove] LLM returned empty analysis.")
        return {}

    insights: list = result.get("insights", [])
    strategy: str  = result.get("job_strategy_update", "")

    save_strategy(insights, strategy)

    logger.success(f"[SelfImprove] Generated {len(insights)} insights.")
    for i, insight in enumerate(insights, 1):
        logger.info(f"  [{i}] {insight}")

    return result


def get_current_strategy() -> str:
    """Return the latest strategy string from memory (or default)."""
    entry = get_latest_strategy()
    if entry:
        return entry.get("strategy", "")
    return "Apply to jobs with match_score >= 70 and confidence >= 60."
