"""
Component 6 — Orchestrator (The AI Brain)
Full pipeline: SCAN -> FILTER -> ANALYZE -> RANK -> DECIDE -> APPLY -> LEARN -> UPDATE
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from core.resume_parser   import parse_resume, parse_resume_from_text
from core.candidate_intel import evaluate_candidate
from core.job_matcher     import rank_jobs, match_job
from core.decision_engine import (
    evaluate_job_fit,
    pipeline_decision_from_fit,
    resume_blob_for_llm,
)
from core.learning import get_learning_min_score_delta, record_learning_event
from core.auto_apply      import decide_application, apply_with_browser
from core.auto_apply_engine import apply_job
from core.smart_apply import (
    get_apply_link,
    open_application,
    process_job_application,
    smart_action_to_execution_status,
    ui_decision_label,
)
from core.job_aggregator  import fetch_jobs_for_candidate
from core.memory          import (
    init_db, save_application, already_applied,
    record_skill_appearance, get_all_applications,
)
from core.self_improve    import run_self_improvement, get_current_strategy
from core.prompt_engine   import (
    build_job_search_prompt, build_cover_letter_prompt,
    build_skill_gap_prompt, get_tier_min_score, log_dynamic_prompt,
)
from core.notifier import (
    notify_application_submitted,
    notify_review_required,
    notify_new_jobs_batch,
)
from core.safety_guard import (
    check_safe_to_apply,
    record_error,
    record_success,
    get_system_mode,
    update_domain_access,
)
from utils.helpers        import logger, timer, call_llm


def _is_major_job_board_url(link: str) -> bool:
    """LinkedIn / Naukri / Indeed — browser open only, no Selenium autofill."""
    low = (link or "").lower()
    return any(x in low for x in ("linkedin", "naukri", "indeed"))


# ── Decision Engine ──────────────────────────────────────────────────────────

def _should_apply(match_score: int, confidence: int) -> str:
    """
    Core decision logic — reads live env vars so adaptive thresholds take effect.
    Returns: 'apply' | 'review' | 'skip'
    """
    apply_t  = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD", "85"))
    review_t = int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "60"))

    if match_score >= apply_t and confidence >= apply_t:
        return "apply"
    elif match_score >= review_t or confidence >= review_t:
        return "review"
    else:
        return "skip"


# ── Pipeline Steps ────────────────────────────────────────────────────────────

def step_parse(resume_source: str, is_text: bool = False) -> dict:
    logger.info("━━━ STEP 1: SCAN (Resume Parsing) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if is_text:
        return parse_resume_from_text(resume_source)
    return parse_resume(resume_source)


def step_evaluate(parsed_resume: dict) -> dict:
    logger.info("━━━ STEP 2: FILTER (Candidate Intelligence) ━━━━━━━━━━━━━━━━━━━━")
    return evaluate_candidate(parsed_resume)


def step_fetch_jobs(
    parsed_resume: dict,
    candidate_intel: dict,
    use_scrapers: bool = False,
    max_jobs: int = 50,
) -> list[dict]:
    logger.info("━━━ STEP 2b: FETCH (Job Aggregation) ━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return fetch_jobs_for_candidate(
        parsed_resume, candidate_intel, use_scrapers=use_scrapers, max_total=max_jobs
    )


def step_rank(parsed_resume: dict, jobs: list[dict], candidate_intel: dict, tier: str) -> list[dict]:
    logger.info("━━━ STEP 3: ANALYZE + RANK (Job Matching) ━━━━━━━━━━━━━━━━━━━━━━")
    min_score = int(get_tier_min_score(tier)) + int(get_learning_min_score_delta())
    min_score = max(0, min(100, min_score))
    return rank_jobs(parsed_resume, jobs, candidate_intel, min_score=min_score)


def step_decide_and_apply(
    ranked_jobs: list[dict],
    parsed_resume: dict,
    candidate_intel: dict,
    dry_run: bool = True,
) -> list[dict]:
    logger.info("━━━ STEP 4: DECIDE + APPLY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    results = []
    tier = candidate_intel.get("candidate_tier", "Standard")
    max_auto_tabs = int(os.getenv("SMART_APPLY_MAX_AUTO_OPEN", "2"))
    use_playwright = os.getenv("SMART_APPLY_USE_PLAYWRIGHT", "false").lower() == "true"
    use_selenium_autofill = os.getenv("SMART_APPLY_SELENIUM_AUTOFILL", "true").lower() == "true"
    auto_opened = 0

    sys_mode = get_system_mode()

    for job in ranked_jobs:
        url = get_apply_link(job) or job.get("url", "") or job.get("apply_link", "")
        if url:
            job["apply_link"] = job.get("apply_link") or url
            if not job.get("url"):
                job["url"] = url
        if already_applied(url):
            logger.info(f"[Orchestrator] Already applied: {job.get('title')} — skipping.")
            continue

        match_result = dict(job.get("match_result") or {})
        jd = (
            job.get("description")
            or job.get("job_description")
            or job.get("job_highlights")
            or ""
        )
        if isinstance(jd, list):
            jd = " ".join(str(x) for x in jd)
        jd = str(jd)

        try:
            ai_fit = evaluate_job_fit(resume_blob_for_llm(parsed_resume), jd)
        except Exception as de_exc:
            logger.warning(f"[Orchestrator] AI fit fallback: {de_exc}")
            ai_fit = {
                "decision": "apply",
                "confidence": int(match_result.get("confidence_score") or 55),
                "reason": "Recruiter AI unavailable — using matcher confidence.",
            }
        pipeline_status = pipeline_decision_from_fit(ai_fit)
        match_result["ai_fit"] = ai_fit
        match_result["pipeline_decision_status"] = pipeline_status
        job["match_result"] = match_result

        match_score = match_result.get("match_score", 0)
        confidence  = match_result.get("confidence_score", 0)
        recruiter_reject = pipeline_status == "rejected"

        # ── Safety Guard — check before every application ─────────────────
        safety = check_safe_to_apply(job, confidence=confidence, dry_run=dry_run)
        if safety.safety_status == "HALTED":
            logger.error("[Orchestrator] Safety HALT — stopping pipeline.")
            for w in safety.warnings:
                logger.warning(f"  [Safety] {w}")
            break  # stop the entire loop

        if not safety.allowed:
            for w in safety.warnings:
                logger.info(f"  [Safety] BLOCKED: {w}")
            results.append({
                "job": job, "match_result": match_result,
                "apply_decision": {"execution_status": "SAFETY_BLOCKED"},
                "safety": safety.__dict__,
                "db_id": None,
            })
            continue

        if safety.warnings:
            for w in safety.warnings:
                logger.info(f"  [Safety] WARNING: {w}")

        # Compute safe decision key (reads live adaptive thresholds)
        if recruiter_reject:
            decision_key = "skip"
        else:
            decision_key = _should_apply(match_score, confidence)
            # Safety WARNING → downgrade apply to review
            if safety.safety_status == "WARNING" and decision_key == "apply":
                decision_key = "review"
                logger.info(f"[Safety] Downgraded {job.get('title')} to REVIEW due to safety warning")
            # System-mode override: force review on all borderline jobs
            if sys_mode == "review" and decision_key == "apply" and match_score < 90:
                decision_key = "review"
            # Recruiter AI strongly positive → nudge toward review/apply
            if pipeline_status == "auto_applied" and decision_key == "skip" and match_score >= 55:
                decision_key = "review"

        if recruiter_reject:
            apply_decision = {
                "execution_status": "SKIP",
                "risk_level": "Low",
                "form_mapping": {},
            }
        else:
            apply_decision = decide_application(job, match_result)

            # Enforce decision_key: override LLM's execution_status if safety/mode demanded a downgrade.
            llm_status = apply_decision.get("execution_status", "SKIP")
            if llm_status == "ABORT":
                pass
            elif decision_key == "skip":
                apply_decision["execution_status"] = "SKIP"
            elif decision_key == "review" and llm_status == "AUTO_APPLY":
                apply_decision["execution_status"] = "REVIEW"
                logger.info(f"[Orchestrator] Enforced REVIEW over AUTO_APPLY for {job.get('title')} (safety/mode override)")
            elif decision_key == "apply" and llm_status not in ("AUTO_APPLY",):
                apply_decision["execution_status"] = "AUTO_APPLY"

        # ── Smart apply — Ollama fit decision + confidence (no cloud LLM) ───
        if recruiter_reject:
            smart_decision = "reject"
        else:
            smart_decision = str(ai_fit.get("decision") or "apply").lower().strip()
            if smart_decision not in ("apply", "reject"):
                smart_decision = "apply"
        try:
            smart_conf = int(float(ai_fit.get("confidence")))
        except (TypeError, ValueError):
            smart_conf = int(confidence) if confidence is not None else 0
        smart = process_job_application(job, smart_decision, smart_conf)
        job["decision"] = smart_decision
        job["confidence"] = smart_conf
        job["action"] = smart["action"]
        apply_decision["smart_action"] = smart["action"]
        apply_decision["ai_decision_label"] = ui_decision_label(smart["action"])
        apply_decision["execution_status"] = smart_action_to_execution_status(smart["action"])

        match_result = dict(match_result)
        match_result["smart_apply"] = smart
        job["match_result"] = match_result

        row_id = save_application(job, match_result, apply_decision, tier)
        record_skill_appearance(match_result.get("matched_skills", []))
        try:
            record_learning_event(
                row_id,
                str(ai_fit.get("decision") or ""),
                pipeline_status,
                int(ai_fit.get("confidence") or 0),
                str(ai_fit.get("reason") or ""),
                str(job.get("title") or ""),
                str(job.get("company") or ""),
            )
        except Exception as le_exc:
            logger.debug(f"[Orchestrator] record_learning_event: {le_exc}")

        status = apply_decision.get("execution_status")

        if not dry_run and smart["action"] == "auto_open" and smart.get("link") and auto_opened < max_auto_tabs:
            link_raw = str(smart["link"]).strip()
            opened_ok = False
            if _is_major_job_board_url(link_raw):
                opened_ok = open_application(link_raw)
                apply_decision["selenium_autofill"] = False
            elif use_selenium_autofill:
                cand_name = (os.getenv("CANDIDATE_NAME") or "Sahil").strip()
                cand_email = (os.getenv("CANDIDATE_EMAIL") or "your@email.com").strip()
                resume_rel = (os.getenv("RESUME_PATH") or "resume.pdf").strip()
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                resume_abs = resume_rel if os.path.isabs(resume_rel) else os.path.join(root, resume_rel)
                try:
                    if os.path.isfile(resume_abs):
                        apply_job(link_raw, cand_name, cand_email, resume_abs)
                        opened_ok = True
                        apply_decision["selenium_autofill"] = True
                    else:
                        logger.warning(
                            "[Orchestrator] RESUME_PATH not found (%s) — opening browser only",
                            resume_abs,
                        )
                        opened_ok = open_application(link_raw)
                        apply_decision["selenium_autofill"] = False
                except Exception as se_exc:
                    logger.warning("[Orchestrator] Selenium autofill failed (%s) — opening browser", se_exc)
                    opened_ok = open_application(link_raw)
                    apply_decision["selenium_autofill"] = False
            else:
                opened_ok = open_application(link_raw)
                apply_decision["selenium_autofill"] = False

            if opened_ok:
                auto_opened += 1
                apply_decision["browser_opened"] = True
                record_success()
                if url:
                    update_domain_access(url)
            else:
                apply_decision["browser_opened"] = False
                record_error(f"Smart apply browser/autofill failed: {job.get('title')}")
        elif not dry_run and status == "AUTO_APPLY" and use_playwright and url and not apply_decision.get("browser_opened"):
            try:
                success = apply_with_browser(url, apply_decision, match_result)
                apply_decision["browser_success"] = success
                if success:
                    record_success()
                    update_domain_access(url)
                else:
                    record_error(f"Browser apply failed: {job.get('title')}")
            except Exception as apply_err:
                logger.error(f"[Orchestrator] Apply error: {apply_err}")
                record_error(str(apply_err))
                apply_decision["browser_success"] = False

        # ── Notifications ──────────────────────────────────────────────────
        job_with_score = {**job, "match_score": match_score,
                          "skills_matched": match_result.get("matched_skills", []),
                          "skills_missing": match_result.get("missing_critical_skills", [])}
        try:
            if status == "AUTO_APPLY":
                notify_application_submitted(job_with_score, apply_decision)
            elif status in ("REVIEW", "PENDING_REVIEW"):
                notify_review_required(job_with_score, apply_decision)
        except Exception as _ne:
            logger.debug(f"[Orchestrator] Notification error (non-fatal): {_ne}")

        results.append({
            "job":            job,
            "match_result":   match_result,
            "apply_decision": apply_decision,
            "safety":         {"status": safety.safety_status, "warnings": safety.warnings},
            "db_id":          row_id,
        })

        logger.info(
            f"  [{status:12s}] [{safety.safety_status:7s}] "
            f"{job.get('title','')} @ {job.get('company','')} "
            f"| Score: {match_score} | MatchConf: {confidence}% | "
            f"AI: {ai_fit.get('decision')} {ai_fit.get('confidence')}% [{pipeline_status}]"
        )

    return results


def step_learn():
    logger.info("━━━ STEP 5: LEARN + UPDATE (Self-Improvement) ━━━━━━━━━━━━━━━━━━")
    from core.learning import update_strategy

    out = run_self_improvement()
    if not isinstance(out, dict):
        out = {"self_improve_raw": out}
    try:
        out["agent_learning_strategy"] = update_strategy()
    except Exception as exc:
        logger.warning(f"[Orchestrator] update_strategy failed: {exc}")
        out["agent_learning_strategy"] = {"learning_status": str(exc)}
    return out


# ── Main Orchestrator ─────────────────────────────────────────────────────────

@timer
def run_pipeline(
    resume_source: str,
    jobs: Optional[list[dict]] = None,
    is_text: bool = False,
    dry_run: bool = True,
    run_learning: bool = True,
    auto_fetch: bool = False,
    use_scrapers: bool = False,
    max_jobs: int = 50,
) -> dict:
    """
    Execute the full agent pipeline.

    SCAN -> FILTER -> FETCH (optional) -> ANALYZE -> RANK -> DECIDE -> APPLY -> LEARN -> UPDATE

    Args:
        resume_source: File path to resume OR raw resume text.
        jobs:          Pre-supplied job list. If None and auto_fetch=True, jobs are
                       fetched live from APIs/scrapers.
        is_text:       True if resume_source is raw text (not a file path).
        dry_run:       If True, never actually submits browser forms.
        run_learning:  If True, runs self-improvement analysis at the end.
        auto_fetch:    If True and no jobs supplied, fetch jobs live from aggregator.
        use_scrapers:  If True, enable Playwright fallback scrapers in aggregator.
        max_jobs:      Maximum jobs to process when auto_fetch=True.

    Returns:
        Full pipeline result dict.
    """
    init_db()

    # 1. Parse
    parsed_resume = step_parse(resume_source, is_text)
    if not parsed_resume:
        logger.error("[Orchestrator] Resume parsing failed. Aborting.")
        return {"error": "Resume parsing failed"}

    # 2. Evaluate
    candidate_intel = step_evaluate(parsed_resume)
    tier = candidate_intel.get("candidate_tier", "Standard")

    resume_ai_analysis: dict = {}
    try:
        from core.resume_ai import analyze_resume_from_parsed

        resume_ai_analysis = analyze_resume_from_parsed(parsed_resume)
    except Exception as ra_exc:
        logger.warning(f"[Orchestrator] Resume intelligence pass skipped: {ra_exc}")

    # 3. Dynamic search prompt
    strategy = get_current_strategy()
    search_prompt = build_job_search_prompt(parsed_resume, candidate_intel, strategy)
    log_dynamic_prompt("job_search", tier)
    logger.debug(f"\n{search_prompt}\n")

    # 2b. Live job fetch (if no jobs supplied and auto_fetch enabled)
    if not jobs:
        if auto_fetch:
            jobs = step_fetch_jobs(parsed_resume, candidate_intel, use_scrapers, max_jobs)
            if not jobs:
                logger.warning("[Orchestrator] Job aggregation returned 0 results.")
                jobs = []
        else:
            logger.warning("[Orchestrator] No jobs provided. Pass --fetch or supply a jobs file.")
            jobs = []

    # 4. Rank jobs
    ranked_jobs = step_rank(parsed_resume, jobs, candidate_intel, tier)

    # Notify about newly discovered high-match jobs (async, non-blocking)
    if ranked_jobs:
        try:
            notify_new_jobs_batch([
                {**j, "match_score": j.get("match_result", {}).get("match_score", 0),
                 "skills_matched": j.get("match_result", {}).get("matched_skills", []),
                 "skills_missing": j.get("match_result", {}).get("missing_critical_skills", [])}
                for j in ranked_jobs
            ])
        except Exception as _ne:
            logger.debug(f"[Orchestrator] Job batch notification error (non-fatal): {_ne}")

    # 5. Decide + Apply
    application_results = step_decide_and_apply(
        ranked_jobs, parsed_resume, candidate_intel, dry_run
    )

    # 6. Self-improvement
    improvement = {}
    if run_learning:
        improvement = step_learn()

    # ── Summary ──────────────────────────────────────────────────────────────
    auto_applied = sum(
        1 for r in application_results
        if r["apply_decision"].get("execution_status") == "AUTO_APPLY"
    )
    reviewed = sum(
        1 for r in application_results
        if r["apply_decision"].get("execution_status") == "REVIEW"
    )
    skipped = sum(
        1 for r in application_results
        if r["apply_decision"].get("execution_status") == "SKIP"
    )

    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.success(
        f"[Orchestrator] DONE | Tier: {tier} | "
        f"Jobs: {len(jobs)} -> Ranked: {len(ranked_jobs)} -> "
        f"AutoApply: {auto_applied} | Review: {reviewed} | Skip: {skipped}"
    )

    pipeline_result = {
        "parsed_resume":       parsed_resume,
        "candidate_intel":     candidate_intel,
        "resume_ai_analysis":  resume_ai_analysis,
        "ranked_jobs":         ranked_jobs,
        "application_results": application_results,
        "self_improvement":    improvement,
        "summary": {
            "total_jobs":    len(jobs),
            "ranked_jobs":   len(ranked_jobs),
            "auto_applied":  auto_applied,
            "reviewed":      reviewed,
            "skipped":       skipped,
            "tier":          tier,
            "dry_run":       dry_run,
            "auto_fetched":  auto_fetch,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
        },
    }

    # Persist result for dashboard to consume
    _save_result_cache(pipeline_result)
    return pipeline_result


def _save_result_cache(result: dict):
    """Save the pipeline result to db/last_result.json for the dashboard."""
    try:
        # Use __file__-based absolute path so this works regardless of CWD
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_dir       = os.path.join(project_root, "db")
        os.makedirs(db_dir, exist_ok=True)
        cache_path   = os.path.join(db_dir, "last_result.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        logger.debug(f"[Orchestrator] Pipeline result cached → {cache_path}")
    except Exception as exc:
        logger.warning(f"[Orchestrator] Could not save result cache: {exc}")


# ── Standalone Utilities ──────────────────────────────────────────────────────

@timer
def generate_cover_letter(
    resume_source: str,
    job: dict,
    is_text: bool = False,
) -> str:
    """
    Generate a tailored cover letter for a specific job.

    Args:
        resume_source: File path or raw resume text.
        job:           Job dict with title, company, description.
        is_text:       True if resume_source is raw text.

    Returns:
        Cover letter as a plain string.
    """
    parsed_resume   = step_parse(resume_source, is_text)
    candidate_intel = step_evaluate(parsed_resume)
    match_result    = match_job(parsed_resume, job.get("description", ""), job.get("title", ""))

    prompt = build_cover_letter_prompt(job, parsed_resume, match_result, candidate_intel)
    return call_llm(
        "You are a professional career coach. Write concise, compelling cover letters.",
        prompt,
        temperature=0.4,
    )


@timer
def generate_learning_roadmap(
    resume_source: str,
    job: dict,
    is_text: bool = False,
) -> str:
    """
    Generate a skill-gap learning roadmap for a target role.

    Args:
        resume_source: File path or raw resume text.
        job:           Target job dict.
        is_text:       True if resume_source is raw text.

    Returns:
        Learning roadmap as a plain string.
    """
    parsed_resume = step_parse(resume_source, is_text)
    match_result  = match_job(parsed_resume, job.get("description", ""), job.get("title", ""))

    prompt = build_skill_gap_prompt(parsed_resume, job, match_result)
    return call_llm(
        "You are a senior career coach and tech educator.",
        prompt,
        temperature=0.3,
    )
