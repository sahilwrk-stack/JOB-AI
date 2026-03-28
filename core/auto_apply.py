"""
Component 4 — Auto-Apply Agent
Confidence-based web automation with risk-aware human-in-loop fallback.
"""

import os
import json
import time
from typing import Optional

from utils.helpers import safe_json_call, logger, timer

AUTO_APPLY_ENABLED = os.getenv("AUTO_APPLY_ENABLED", "false").lower() == "true"

# ── Elite Gate threshold ──────────────────────────────────────────────────────
# Playwright is ONLY launched when the AI match score is >= this value.
# Jobs below this threshold are marked REVIEW or SKIP — no browser opened.
# Adjust via ELITE_APPLY_THRESHOLD in .env (default: 80).
ELITE_APPLY_THRESHOLD = int(os.getenv("ELITE_APPLY_THRESHOLD", "80"))


def _get_thresholds() -> tuple[int, int]:
    """Read thresholds live from env so adaptive changes take effect."""
    apply_t  = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD",  "85"))
    review_t = int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "60"))
    return apply_t, review_t


def is_elite_match(match_result: dict) -> bool:
    """
    Step 3 — Elite Gate.

    Returns True only when the AI match score qualifies the job for
    Playwright-based auto-application. Below this threshold, no browser
    is ever launched, saving compute and avoiding spam.

    Args:
        match_result: Output dict from job_matcher.match_job() or rank_jobs().
                      Must contain 'match_score' key (int 0–100).

    Returns:
        True  → job qualifies for Playwright form-fill
        False → mark as REVIEW / SKIP; do not open browser
    """
    score     = int(match_result.get("match_score", 0))
    threshold = int(os.getenv("ELITE_APPLY_THRESHOLD", str(ELITE_APPLY_THRESHOLD)))
    qualifies = score >= threshold
    if qualifies:
        logger.success(
            f"⚡ [ELITE GATE] ✅ Score {score} >= {threshold} — "
            "Playwright AUTHORIZED for this job."
        )
    else:
        logger.info(
            f"⚡ [ELITE GATE] 🚫 Score {score} < {threshold} — "
            "Playwright BLOCKED. Marking as REVIEW/SKIP."
        )
    return qualifies

# Convenience aliases — these are read at import for logging/prompts but
# decision logic calls _get_thresholds() dynamically.
APPLY_THRESHOLD  = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD",  "85"))
REVIEW_THRESHOLD = int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "60"))

# ── Candidate Profile ─────────────────────────────────────────────────────────

def get_candidate_profile() -> dict:
    """Load candidate info from environment variables."""
    return {
        "name":     os.getenv("CANDIDATE_NAME", ""),
        "email":    os.getenv("CANDIDATE_EMAIL", ""),
        "phone":    os.getenv("CANDIDATE_PHONE", ""),
        "location": os.getenv("CANDIDATE_LOCATION", ""),
        "github":   os.getenv("CANDIDATE_GITHUB", ""),
        "linkedin": os.getenv("CANDIDATE_LINKEDIN", ""),
    }


# ── Form Mapper Prompt ────────────────────────────────────────────────────────

FORM_MAPPER_SYSTEM = """
You are a Smart Web Form Mapping AI.

Given a job application form's fields and the candidate profile, produce a mapping
of { field_label -> value_to_fill } and identify any fields you cannot fill.

Rules:
- Return STRICT valid JSON only
- risk_level: "Low" if all critical fields covered, "Medium" if some gaps, "High" if name/email missing

Return this exact schema:
{
  "execution_status": "AUTO_APPLY | REVIEW | SKIP | ABORT",
  "confidence_score": 0,
  "risk_level": "Low | Medium | High",
  "abort_reason": null,
  "form_mapping": {},
  "missing_fields": [],
  "submit_button_selector": ""
}
"""


def _form_mapper_prompt(form_fields: list[str], candidate_profile: dict, match_score: int) -> str:
    return f"""
Map the candidate profile to the following application form fields.

CANDIDATE PROFILE:
{json.dumps(candidate_profile, indent=2)}

FORM FIELDS DETECTED:
{json.dumps(form_fields, indent=2)}

MATCH SCORE: {match_score}/100

Decision thresholds:
- confidence > {APPLY_THRESHOLD} -> AUTO_APPLY
- confidence {REVIEW_THRESHOLD}-{APPLY_THRESHOLD} -> REVIEW
- confidence < {REVIEW_THRESHOLD} -> SKIP
- critical data missing (name/email) -> ABORT

Return only valid JSON.
"""


# ── Playwright Automation ────────────────────────────────────────────────────

def _detect_form_fields(page) -> list[str]:
    """Scrape visible label/placeholder text from form inputs on the page."""
    try:
        fields = page.evaluate("""
            () => {
                const inputs = document.querySelectorAll('input, textarea, select');
                return Array.from(inputs).map(el => {
                    const id = el.id || el.name || '';
                    const label = document.querySelector(`label[for="${id}"]`);
                    return label ? label.innerText.trim() : (el.placeholder || el.name || id);
                }).filter(Boolean);
            }
        """)
        return fields
    except Exception as exc:
        logger.warning(f"[AutoApply] Field detection error: {exc}")
        return []


def _fill_and_submit(page, apply_decision: dict) -> bool:
    """
    Fill detected form fields and optionally click submit.
    Returns True on success.
    """
    mapping: dict = apply_decision.get("form_mapping", {})
    submit_selector: str = apply_decision.get("submit_button_selector", "")

    for field_label, value in mapping.items():
        try:
            page.fill(f"[placeholder*='{field_label}' i], [name*='{field_label}' i]", str(value))
            time.sleep(0.2)
        except Exception:
            logger.debug(f"[AutoApply] Could not fill field: {field_label}")

    if AUTO_APPLY_ENABLED and submit_selector:
        try:
            page.click(submit_selector)
            logger.success("[AutoApply] Form submitted.")
            return True
        except Exception as exc:
            logger.error(f"[AutoApply] Submit click failed: {exc}")
            return False
    else:
        logger.info("[AutoApply] AUTO_APPLY_ENABLED=false — form filled but NOT submitted.")
        return True


# ── Public API ────────────────────────────────────────────────────────────────

@timer
def decide_application(
    job: dict,
    match_result: dict,
    candidate_profile: Optional[dict] = None,
) -> dict:
    """
    Determine AUTO_APPLY / REVIEW / SKIP / ABORT for a job without browser.
    Uses form field inference from job description text.

    Args:
        job:               Job dict (title, description, url, company, location).
        match_result:      Output from job_matcher.match_job().
        candidate_profile: Optional override; defaults to env-based profile.

    Returns:
        Apply decision dict.
    """
    profile = candidate_profile or get_candidate_profile()
    match_score = match_result.get("match_score", 0)
    confidence = match_result.get("confidence_score", 0)

    # Infer likely form fields from job description
    common_fields = [
        "Full Name", "Email", "Phone", "LinkedIn URL", "GitHub URL",
        "Current Location", "Years of Experience", "Cover Letter",
        "Expected Salary", "Notice Period", "Resume Upload",
    ]

    decision = safe_json_call(
        FORM_MAPPER_SYSTEM,
        _form_mapper_prompt(common_fields, profile, match_score),
    )

    if not decision:
        decision = {
            "execution_status": "SKIP",
            "confidence_score": 0,
            "risk_level": "High",
            "abort_reason": "Form mapping failed",
            "form_mapping": {},
            "missing_fields": [],
            "submit_button_selector": "",
        }

    # Override status based on live adaptive thresholds
    apply_t, review_t = _get_thresholds()
    if confidence >= apply_t and match_score >= apply_t:
        decision["execution_status"] = "AUTO_APPLY"
    elif confidence >= review_t or match_score >= review_t:
        decision["execution_status"] = "REVIEW"
    else:
        decision["execution_status"] = "SKIP"

    logger.info(
        f"[AutoApply] '{job.get('title','')}' @ {job.get('company','')} -> "
        f"Status: {decision['execution_status']} | Risk: {decision.get('risk_level','?')}"
    )
    return decision


@timer
def apply_with_browser(
    job_url: str,
    apply_decision: dict,
    match_result: Optional[dict] = None,
) -> bool:
    """
    Step 3 — Playwright Form-Fill (ONLY for Elite Matches).

    Playwright is invoked ONLY when:
      1. match_result score >= ELITE_APPLY_THRESHOLD (default 80)
      2. apply_decision execution_status != SKIP / ABORT

    Jobs that don't clear the Elite Gate are never opened in a browser.
    This prevents wasted compute, reduces detection risk, and stops spam.

    Args:
        job_url:        Direct application URL from apply_url (API output).
        apply_decision: Output from decide_application().
        match_result:   AI match result dict (contains match_score).
                        If omitted, the Elite Gate is skipped (legacy behaviour).

    Returns:
        True  if the form was successfully filled (submitted if AUTO_APPLY_ENABLED).
        False otherwise.
    """
    # ── Elite Gate check ─────────────────────────────────────────────────────
    if match_result is not None:
        if not is_elite_match(match_result):
            # Score below threshold — do not open a browser
            apply_decision["execution_status"] = (
                "REVIEW"
                if int(match_result.get("match_score", 0)) >= int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "60"))
                else "SKIP"
            )
            logger.info(
                f"⚡ [ELITE GATE] Playwright BLOCKED for '{job_url}' — "
                f"Status set to {apply_decision['execution_status']}"
            )
            return False

    # ── Standard status checks ────────────────────────────────────────────────
    if apply_decision.get("execution_status") == "SKIP":
        logger.info(f"[AutoApply] Skipping {job_url}")
        return False

    if apply_decision.get("execution_status") == "ABORT":
        logger.warning(f"[AutoApply] Aborting {job_url}: {apply_decision.get('abort_reason')}")
        return False

    # ── Launch browser ────────────────────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[AutoApply] Playwright not installed. Run: pip install playwright && playwright install")
        return False

    logger.info(f"⚡ [PLAYWRIGHT] Opening browser for ELITE job: {job_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(job_url, timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
            form_fields = _detect_form_fields(page)
            if form_fields:
                logger.info(f"[AutoApply] Detected {len(form_fields)} form fields.")
            success = _fill_and_submit(page, apply_decision)
            time.sleep(2)
        except Exception as exc:
            logger.error(f"[AutoApply] Browser error: {exc}")
            success = False
        finally:
            browser.close()

    return success
