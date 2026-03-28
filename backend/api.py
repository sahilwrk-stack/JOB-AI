"""
Omniscient AI — Production FastAPI Backend
Replaces dashboard/server.py for deployed environments.

Endpoints mirror the Flask server exactly so the frontend HTML
needs zero changes whether running locally (Flask) or in production (FastAPI).

Start locally:
    uvicorn backend.api:app --reload --port 5050

Start in production:
    gunicorn backend.api:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
"""

import sys
import os
import json
from pathlib import Path
from typing import Optional

# Ensure the project root is on the Python path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.memory import (
    get_all_applications,
    get_skill_performance,
    init_db,
)
from core.resume_parser import guess_candidate_name, guess_name_from_filename
from core.candidate_intel import merge_intel_for_dashboard
from core.smart_apply import get_apply_link, ui_action_label
from utils.helpers import get_llm_diagnostics, get_agent_diagnostics
from core.tracker import (
    generate_report,
    get_recent_activity,
    get_top_skills,
    update_outcome  as tracker_update_outcome,
    update_status   as tracker_update_status,
    list_applications,
    get_application,
    seed_demo_data,
)
from core.analytics import run_analysis, get_latest_analytics
from core.master_orchestrator import run_master_orchestrator, get_master_status
from core.safety_guard import (
    get_safety_stats,
    get_safety_log,
    reset_halt,
    set_review_mode,
    set_auto_mode,
    run_safety_check_batch,
)
from core.notifier import (
    send_test_notification,
    notify_daily_summary,
    get_notification_log,
    get_notification_stats,
    get_notification_config,
    send_notification,
)

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Omniscient AI — Job Agent API",
    description = "Backend API for the Omniscient Resume AI job application agent.",
    version     = "2.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS — allow frontend domain (Netlify/Vercel) + localhost
_origins_raw  = os.getenv("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _origins_raw.split(",") if o.strip()]
    if _origins_raw
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins     = _cors_origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
RESULT_CACHE  = Path(__file__).parent.parent / "db" / "last_result.json"
_RESUME_STATE_FILE = Path(__file__).parent.parent / "db" / "resume_state.json"

# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()


# ── Request / Response models ─────────────────────────────────────────────────

class OutcomeUpdateRequest(BaseModel):
    id:               str | int
    outcome:          str
    interview_round:  int = 0
    rejection_reason: str = ""
    notes:            str = ""

class StatusUpdateRequest(BaseModel):
    id:     str | int
    status: str
    notes:  str = ""

class AnalyticsRunRequest(BaseModel):
    candidate_skills: list[str] = []

class NotificationSendRequest(BaseModel):
    notification_type: str = "test"
    message:           str = "Test notification from Omniscient AI"
    priority:          str = "Low"
    force:             bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_last_result() -> dict:
    if RESULT_CACHE.exists():
        try:
            return json.loads(RESULT_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_resume_state() -> dict:
    try:
        if _RESUME_STATE_FILE.exists():
            return json.loads(_RESUME_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _enrich_profile_name_from_disk(state: dict, profile: dict | None) -> dict:
    profile = dict(profile or {})
    if (profile.get("name") or profile.get("candidate_name") or "").strip():
        return profile
    path = (state.get("path") or "").strip()
    if not path or not Path(path).is_file():
        return profile
    try:
        from core.resume_parser import extract_resume_text

        txt = extract_resume_text(path)
        g = guess_candidate_name(txt) or guess_name_from_filename(Path(path).name)
        if not g:
            g = guess_name_from_filename((state.get("filename") or "").strip())
        if g:
            profile["name"] = g
            profile["candidate_name"] = g
    except Exception:
        pass
    return profile


def _pipeline_result_meaningful(result: dict) -> bool:
    if not result:
        return False
    return bool(
        result.get("parsed_resume")
        or result.get("ranked_jobs")
        or result.get("jobs")
    )


def _job_dashboard_key(job: dict) -> str:
    for k in ("url", "apply_url", "apply_link", "job_id"):
        v = (job.get(k) or "").strip()
        if v:
            return v.lower()
    return ""


def _build_dashboard_payload(result: dict) -> dict:
    parsed  = result.get("parsed_resume", {})
    intel   = merge_intel_for_dashboard(parsed, result.get("candidate_intel"))
    ranked  = result.get("ranked_jobs", [])
    app_res = result.get("application_results", [])
    summary = result.get("summary", {})

    jobs = []
    for job in ranked[:30]:
        mr = job.get("match_result", {}) or {}
        sa = mr.get("smart_apply") or {}
        ad0 = job.get("apply_decision") or {}
        jobs.append({
            **job,
            "match_result":   mr,
            "apply_decision": dict(ad0),
            "decision":       ad0.get("ai_decision_label")
                or ("Reject" if sa.get("action") == "ignore" else "Apply"),
            "confidence":     mr.get("confidence_score"),
            "action":         sa.get("action") or ad0.get("smart_action"),
        })

    decision_map = {}
    for r in app_res:
        j = r.get("job") or {}
        dk = _job_dashboard_key(j)
        if dk:
            decision_map[dk] = r.get("apply_decision") or {}

    for job in jobs:
        key = _job_dashboard_key(job)
        if key in decision_map:
            job["apply_decision"] = decision_map[key]
        mr = job.get("match_result") or {}
        sa = mr.get("smart_apply") or {}
        ad = job.get("apply_decision") or {}
        job["decision"] = ad.get("ai_decision_label") or (
            "Reject" if sa.get("action") == "ignore" else "Apply"
        )
        af = mr.get("ai_fit") or {}
        job["matcher_confidence"] = mr.get("confidence_score")
        job["confidence"] = af.get("confidence") if af.get("confidence") is not None else mr.get("confidence_score")
        job["action"] = sa.get("action") or ad.get("smart_action")
        job["action_label"] = ui_action_label(job["action"] or "")
        job["apply_link"] = get_apply_link(job)
        job["ai_confidence"] = af.get("confidence")
        job["ai_reason"] = af.get("reason")
        job["recruiter_decision"] = str(af.get("decision") or "").title()
        job["pipeline_decision_status"] = mr.get("pipeline_decision_status") or ""

    return {
        "candidate_profile": parsed,
        "candidate_intel":   intel,
        "jobs":              jobs,
        "history":           get_all_applications(limit=50),
        "summary":           summary,
        "resume_ai_analysis": result.get("resume_ai_analysis") or {},
    }


def _int_id(identifier: str | int) -> str | int:
    if isinstance(identifier, str) and identifier.isdigit():
        return int(identifier)
    return identifier


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health():
    """Health-check endpoint for Render / Railway uptime monitoring."""
    return {"status": "ok", "service": "omniscient-ai-backend"}


@app.get("/api/status", tags=["System"])
async def api_status():
    result  = _load_last_result()
    summary = result.get("summary", {})
    llm = get_llm_diagnostics()
    return {
        "status":     "ready",
        "last_run":   summary.get("timestamp"),
        "total_jobs": summary.get("total_jobs", 0),
        "tier":       result.get("candidate_intel", {}).get("candidate_tier", "—"),
        "ai_brain_online": llm.get("llm_ready", False),
        "llm_model":       llm.get("llm_model", "llama3.2"),
    }


@app.get("/api/agent-diagnostics", tags=["System"])
async def api_agent_diagnostics():
    return get_agent_diagnostics()


@app.post("/api/resume-analysis", tags=["Dashboard"])
async def api_resume_analysis(body: dict = Body(default={})):
    from core.resume_ai import analyze_resume, analyze_resume_from_parsed
    from core.resume_parser import parse_resume

    text = (body.get("text") or body.get("resume_text") or "").strip()
    if text:
        return analyze_resume(text)
    state = _load_resume_state()
    path = (state.get("path") or "").strip() if state else ""
    if path and Path(path).is_file():
        try:
            parsed = parse_resume(path)
            return analyze_resume_from_parsed(parsed)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Provide resume text or upload a resume first.")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/api/dashboard", tags=["Dashboard"])
async def api_dashboard():
    init_db()
    result = _load_last_result()
    state = _load_resume_state()
    raw_preview = state.get("preview") or {}
    preview_profile = {} if raw_preview.get("_parse_error") else dict(raw_preview)
    preview_profile = _enrich_profile_name_from_disk(state, preview_profile)

    if _pipeline_result_meaningful(result):
        payload = _build_dashboard_payload(result)
        payload["candidate_profile"] = _enrich_profile_name_from_disk(
            state, payload.get("candidate_profile") or {}
        )
    else:
        intel = merge_intel_for_dashboard(preview_profile, {})
        payload = {
            "candidate_profile": preview_profile,
            "candidate_intel": intel,
            "jobs": [],
            "history": get_all_applications(limit=50),
            "summary": {},
            "resume_ai_analysis": {},
        }
    return JSONResponse(content=payload)


@app.get("/api/history", tags=["Dashboard"])
async def api_history(limit: int = Query(50, ge=1, le=500)):
    return get_all_applications(limit=limit)


@app.get("/api/skills", tags=["Dashboard"])
async def api_skills():
    return get_skill_performance()


# ── Tracker ───────────────────────────────────────────────────────────────────

@app.get("/api/tracker/stats", tags=["Tracker"])
async def api_tracker_stats():
    return generate_report()


@app.get("/api/tracker/activity", tags=["Tracker"])
async def api_tracker_activity(limit: int = Query(20, ge=1, le=100)):
    return get_recent_activity(limit)


@app.get("/api/tracker/skills", tags=["Tracker"])
async def api_tracker_skills(limit: int = Query(15, ge=1, le=50)):
    return get_top_skills(limit)


@app.get("/api/tracker/applications", tags=["Tracker"])
async def api_tracker_applications(
    status:  Optional[str] = None,
    outcome: Optional[str] = None,
    limit:   int = Query(50, ge=1, le=500),
    offset:  int = Query(0, ge=0),
):
    return list_applications(status, outcome, limit, offset)


@app.post("/api/tracker/update-outcome", tags=["Tracker"])
async def api_update_outcome(body: OutcomeUpdateRequest):
    ok = tracker_update_outcome(
        _int_id(body.id),
        body.outcome,
        body.interview_round,
        body.rejection_reason,
        body.notes,
    )
    return {"success": ok, "outcome": body.outcome}


@app.post("/api/tracker/update-status", tags=["Tracker"])
async def api_update_status(body: StatusUpdateRequest):
    ok = tracker_update_status(_int_id(body.id), body.status, body.notes)
    return {"success": ok}


@app.get("/api/tracker/application/{app_id}", tags=["Tracker"])
async def api_get_application(app_id: int):
    record = get_application(app_id)
    if not record:
        raise HTTPException(status_code=404, detail="Application not found")
    return record


@app.post("/api/tracker/seed", tags=["Tracker"])
async def api_seed_demo():
    seed_demo_data()
    return {"success": True, "message": "Demo data seeded."}


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics", tags=["Analytics"])
async def api_analytics(refresh: bool = False):
    if refresh:
        return run_analysis(force_llm=True)
    result = get_latest_analytics()
    if not result:
        result = run_analysis(force_llm=False)
    return result


@app.post("/api/analytics/run", tags=["Analytics"])
async def api_analytics_run(body: AnalyticsRunRequest):
    return run_analysis(
        candidate_skills=body.candidate_skills or None,
        force_llm=True,
    )


@app.get("/api/analytics/cached", tags=["Analytics"])
async def api_analytics_cached():
    result = get_latest_analytics()
    if not result:
        raise HTTPException(status_code=404, detail="No analytics data yet. Call /api/analytics first.")
    return result


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/api/notifications", tags=["Notifications"])
async def api_notifications(limit: int = Query(50, ge=1, le=500)):
    """Fetch the notification history log."""
    return get_notification_log(limit)


@app.get("/api/notifications/stats", tags=["Notifications"])
async def api_notification_stats():
    """Aggregated notification stats by type and status."""
    return get_notification_stats()


@app.get("/api/notifications/config", tags=["Notifications"])
async def api_notification_config():
    """Current notification configuration (no secrets exposed)."""
    return get_notification_config()


@app.post("/api/notifications/test", tags=["Notifications"])
async def api_notification_test():
    """Send a test notification to verify channel configuration."""
    result = send_test_notification()
    return result


@app.post("/api/notifications/send", tags=["Notifications"])
async def api_notification_send(body: NotificationSendRequest):
    """Send a custom notification manually."""
    result = send_notification(
        notification_type = body.notification_type,
        message           = body.message,
        force             = body.force,
        async_send        = True,
    )
    return result


@app.post("/api/notifications/daily-summary", tags=["Notifications"])
async def api_notification_daily():
    """Trigger the daily summary notification manually."""
    from core.tracker import generate_report
    report = generate_report()
    stats  = report.get("stats", {})
    result = notify_daily_summary({
        "applied":    stats.get("by_status", {}).get("AUTO_APPLY", 0),
        "interviews": stats.get("by_outcome", {}).get("interview", 0),
        "offers":     stats.get("by_outcome", {}).get("offer", 0),
        "new_jobs":   stats.get("total", 0),
    })
    return result


# ── Safety Guard endpoints ────────────────────────────────────────────────────

@app.get("/api/safety/status")
async def safety_status():
    """Current safety system status and daily stats."""
    return get_safety_stats()


@app.get("/api/safety/log")
async def safety_log_endpoint(limit: int = Query(50, le=200)):
    """Recent safety events (blocks, halts, mode changes)."""
    return get_safety_log(limit=limit)


@app.post("/api/safety/reset-halt")
async def safety_reset_halt():
    """Operator-triggered halt reset (clears circuit breaker)."""
    return reset_halt("Manual reset via API")


@app.post("/api/safety/set-review-mode")
async def safety_set_review():
    """Force system into review mode (manual override)."""
    return set_review_mode("Manual override via API")


@app.post("/api/safety/set-auto-mode")
async def safety_set_auto():
    """Restore automatic mode."""
    return set_auto_mode("Restored via API")


# ── Master Orchestrator endpoints ────────────────────────────────────────────

class OrchestratorRunRequest(BaseModel):
    resume_path:       Optional[str]  = None
    dry_run:           bool           = True
    force_pipeline:    bool           = False
    max_applications:  int            = 5
    override_strategy: Optional[str]  = None


@app.get("/api/orchestrator/status")
async def orchestrator_status():
    """Current master orchestrator status (no execution)."""
    return get_master_status()


@app.post("/api/orchestrator/run")
async def orchestrator_run(req: OrchestratorRunRequest):
    """Run one full Master Orchestrator cycle."""
    result = run_master_orchestrator(
        resume_source     = req.resume_path,
        dry_run           = req.dry_run,
        force_pipeline    = req.force_pipeline,
        max_applications  = req.max_applications,
        override_strategy = req.override_strategy,
    )
    return result


@app.get("/api/orchestrator/plan")
async def orchestrator_plan():
    """
    Return adaptive execution plan based on current system state
    (LLM planning only — no pipeline execution).
    """
    from core.master_orchestrator import _read_system_state, _compute_adaptive_thresholds, _assess_system_health
    state      = _read_system_state()
    thresholds = _compute_adaptive_thresholds(state)
    health, reason = _assess_system_health(state, thresholds)
    return {
        "system_status":  health,
        "status_reason":  reason,
        "thresholds":     thresholds,
        "performance": {
            "callback_rate":  state.callback_rate,
            "interview_rate": state.interview_rate,
            "offer_rate":     state.offer_rate,
            "applied_30d":    state.applied_count,
            "optimal_threshold": state.optimal_threshold,
        },
        "top_skills":     state.top_skills,
        "top_skill_gaps": state.top_skill_gaps,
        "best_sources":   state.best_sources,
        "worst_sources":  state.worst_sources,
        "current_strategy": state.current_strategy,
    }


# ── Static files (dashboard HTML) — only when not behind Netlify ──────────────

if DASHBOARD_DIR.exists():
    # Serve the dashboard HTML at /
    @app.get("/", include_in_schema=False)
    async def serve_dashboard():
        index = DASHBOARD_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"message": "Dashboard not found. Deploy frontend separately."})

    # Serve other static assets (CSS, JS, images)
    try:
        app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")
    except Exception:
        pass
