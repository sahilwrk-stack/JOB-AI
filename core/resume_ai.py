"""
Resume intelligence — strengths, gaps, and improvement suggestions.

Uses local **Ollama** only via ``utils.helpers.safe_json_call`` (no cloud LLM APIs).
"""

from __future__ import annotations

import json
from typing import Any

from utils.helpers import logger, safe_json_call

# Re-export legacy shims used elsewhere
from core.candidate_intel import evaluate_candidate
from core.resume_parser import parse_resume, parse_resume_from_text

SYSTEM_ANALYZE = """You are an expert resume coach for tech and data roles.

Analyze the resume text and return STRICT JSON only (no markdown):
{
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "missing_skills": ["...", "..."],
  "improvements": ["...", "..."]
}

Rules:
- strengths: 3-6 concrete positives backed by evidence in the resume
- weaknesses: real gaps or weak phrasing (be constructive)
- missing_skills: skills commonly expected for the candidate's stated targets but absent or thin
- improvements: specific actionable edits (metrics, keywords, structure)
"""


def analyze_resume(resume_text: str) -> dict[str, Any]:
    """
    Return structured resume feedback.

    {
        "strengths": [...],
        "weaknesses": [...],
        "missing_skills": [...],
        "improvements": [...],
    }
    """
    text = (resume_text or "").strip()
    if not text:
        return {
            "strengths": [],
            "weaknesses": [],
            "missing_skills": [],
            "improvements": ["Add resume content to analyze."],
        }

    max_chars = 18000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"

    user = f"""RESUME TEXT:
\"\"\"
{text}
\"\"\"

Return only the JSON object."""

    raw = safe_json_call(SYSTEM_ANALYZE, user, temperature=0.2)
    return _normalize_analysis(raw if isinstance(raw, dict) else {})


def analyze_resume_from_parsed(parsed_resume: dict) -> dict[str, Any]:
    """Run analysis on a parsed resume dict (e.g. from resume_parser)."""
    try:
        blob = json.dumps(parsed_resume, ensure_ascii=False, indent=2, default=str)
    except Exception:
        blob = str(parsed_resume)
    return analyze_resume(blob)


def _normalize_analysis(data: dict) -> dict[str, Any]:
    def _list(key: str) -> list[str]:
        v = data.get(key) or []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if not isinstance(v, list):
            return []
        out = []
        for x in v[:12]:
            s = str(x).strip()
            if s:
                out.append(s[:500])
        return out

    return {
        "strengths": _list("strengths"),
        "weaknesses": _list("weaknesses"),
        "missing_skills": _list("missing_skills"),
        "improvements": _list("improvements"),
    }


__all__ = [
    "analyze_resume",
    "analyze_resume_from_parsed",
    "evaluate_candidate",
    "parse_resume",
    "parse_resume_from_text",
]
