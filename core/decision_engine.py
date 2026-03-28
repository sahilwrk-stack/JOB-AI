"""
Recruiter-level AI job fit decisions.

All reasoning goes through ``utils.helpers.safe_json_call`` → ``call_llm_advanced`` → **local Ollama**
(``OLLAMA_BASE_URL``, default http://localhost:11434/v1). No cloud LLM APIs.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from utils.helpers import logger, safe_json_call

SYSTEM_PROMPT = """You are a senior technical recruiter. Compare the candidate resume to the job description.

Evaluate:
- Skills overlap and depth vs stated requirements
- Experience level and seniority fit
- Domain relevance (industry, role type)
- Red flags (major gaps, wrong stack, location/seniority mismatch)

RULES (use them to set confidence 0-100):
- Strong fit (skills + experience align well): confidence > 70 → decision "apply"
- Borderline (some gaps but plausible): confidence 40-70 → decision "apply" with honest reason about gaps; confidence reflects uncertainty
- Poor fit: confidence < 40 → decision "reject"

Output STRICT JSON only, no markdown, no prose outside JSON:
{"decision":"apply"|"reject","confidence":<integer 0-100>,"reason":"<one short sentence>"}
"""


def resume_blob_for_llm(parsed_resume: dict) -> str:
    """Compact resume text for LLM context (truncated)."""
    if not parsed_resume:
        return ""
    try:
        blob = json.dumps(parsed_resume, ensure_ascii=False, default=str)
    except Exception:
        blob = str(parsed_resume)
    max_chars = int(os.getenv("DECISION_RESUME_MAX_CHARS", "14000"))
    if len(blob) > max_chars:
        return blob[:max_chars] + "\n…[truncated]"
    return blob


def evaluate_job_fit(resume_text: str, job_description: str) -> dict[str, Any]:
    """
    Use LLM to compare resume vs job.

    Returns:
        {
            "decision": "apply" | "reject",
            "confidence": 0-100,
            "reason": "short explanation",
        }
    """
    jd = (job_description or "").strip()
    if len(jd) > 16000:
        jd = jd[:16000] + "\n…[truncated]"
    rt = (resume_text or "").strip()
    if not rt:
        return {"decision": "reject", "confidence": 0, "reason": "No resume text available."}
    if not jd:
        return {"decision": "reject", "confidence": 15, "reason": "No job description to evaluate."}

    user = f"""RESUME / PROFILE (JSON or text):
{rt}

JOB DESCRIPTION:
\"\"\"
{jd}
\"\"\"

Return only the JSON object with decision, confidence, reason."""

    raw = safe_json_call(SYSTEM_PROMPT, user, temperature=0.15)
    out = _normalize_fit_dict(raw if isinstance(raw, dict) else {})

    if not out.get("reason"):
        out["reason"] = "AI fit evaluation complete."
    logger.info(
        f"[DecisionEngine] fit → {out.get('decision')} @ {out.get('confidence')}% — {out.get('reason', '')[:80]}"
    )
    return out


def _normalize_fit_dict(data: dict) -> dict[str, Any]:
    d = (data.get("decision") or data.get("verdict") or "").strip().lower()
    if d not in ("apply", "reject"):
        # infer from match_percent / score if model used different keys
        mp = data.get("match_percent")
        if mp is None:
            mp = data.get("match_score")
        try:
            mp = int(mp)
        except (TypeError, ValueError):
            mp = None
        if mp is not None:
            d = "apply" if mp >= 40 else "reject"
        else:
            d = "reject"

    try:
        c = int(float(data.get("confidence", data.get("match_percent", 50))))
    except (TypeError, ValueError):
        c = 50
    c = max(0, min(100, c))

    # Enforce rubric: map match bands if LLM returned mismatched decision/confidence
    if c > 70 and d == "reject":
        d = "apply"
    if c < 40:
        d = "reject"

    reason = str(data.get("reason") or data.get("explanation") or "").strip()
    reason = re.sub(r"\s+", " ", reason)[:400]

    return {"decision": d, "confidence": c, "reason": reason or "Fit assessed by AI."}


def pipeline_decision_from_fit(fit: dict) -> str:
    """
    Map AI fit to pipeline storage labels.

    Returns:
        "auto_applied" | "recommended" | "rejected"
    """
    d = (fit.get("decision") or "").lower()
    try:
        c = int(fit.get("confidence") or 0)
    except (TypeError, ValueError):
        c = 0
    if d == "reject" or c < 40:
        return "rejected"
    if d == "apply" and c >= 75:
        return "auto_applied"
    if d == "apply":
        return "recommended"
    return "rejected"


__all__ = [
    "evaluate_job_fit",
    "resume_blob_for_llm",
    "pipeline_decision_from_fit",
]
