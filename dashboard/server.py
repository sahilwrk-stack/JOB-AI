"""
Dashboard API Server
Serves the HTML dashboard and exposes /api/dashboard endpoint
that feeds real agent data (last pipeline result or memory DB).
"""

import sys
import os
import json
from pathlib import Path

# Make parent importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv()

from utils.helpers import get_llm_diagnostics, get_agent_diagnostics

from core.memory import (
    get_all_applications,
    get_skill_performance,
    init_db,
    save_application,
)
from core.tracker import (
    generate_report,
    get_recent_activity,
    get_top_skills,
    log_application,
    update_outcome,
    update_status,
    list_applications,
    get_application,
    seed_demo_data,
    Outcome,
    Status,
)
from core.analytics import (
    run_analysis,
    get_latest_analytics,
)
from core.resume_parser import parse_resume, guess_candidate_name, guess_name_from_filename
from core.candidate_intel import evaluate_candidate, merge_intel_for_dashboard
from core.smart_apply import (
    get_apply_link,
    map_execution_to_decision_label,
    open_application,
    process_job_application,
    smart_action_to_execution_status,
    ui_action_label,
    ui_decision_label,
)
from core.job_matcher import rank_jobs
from core.scrapers.rapidapi_search import fetch_jobs_from_rapidapi
from core.master_orchestrator import run_master_orchestrator, get_master_status
from core.safety_guard import (
    get_safety_stats,
    get_safety_log,
    reset_halt,
    set_review_mode,
    set_auto_mode,
)
from core.notifier import (
    send_test_notification,
    notify_daily_summary,
    get_notification_log,
    get_notification_stats,
    get_notification_config,
    send_notification,
)

from werkzeug.utils import secure_filename

# ── Logging ──────────────────────────────────────────────────────────────────
try:
    from loguru import logger as _loguru_logger
    logger = _loguru_logger
except ImportError:
    import logging as _logging
    _logging.basicConfig(level=_logging.DEBUG, format="%(levelname)s | %(name)s | %(message)s")
    logger = _logging.getLogger("dashboard.server")
    logger.success = logger.info   # loguru compat shim

app = Flask(__name__, static_folder=".")
CORS(app)

# ── Global JSON error handler — never return Flask's HTML error pages ─────────
@app.errorhandler(Exception)
def _handle_any_exception(exc):
    import traceback
    tb = traceback.format_exc()
    logger.error(f"[Flask] Unhandled exception:\n{tb}")
    return jsonify({"error": str(exc), "traceback": tb[-800:]}), 500

DASHBOARD_DIR  = Path(__file__).parent
RESULT_CACHE   = DASHBOARD_DIR.parent / "db" / "last_result.json"
UPLOAD_DIR     = DASHBOARD_DIR.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_last_result() -> dict:
    """Load the most recent pipeline run result from cache file."""
    if RESULT_CACHE.exists():
        try:
            return json.loads(RESULT_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _job_dashboard_key(job: dict) -> str:
    for k in ("url", "apply_url", "apply_link", "job_id"):
        v = (job.get(k) or "").strip()
        if v:
            return v.lower()
    return ""


def _build_dashboard_payload(result: dict) -> dict:
    """Transform pipeline result into dashboard-friendly JSON."""
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

    # History from DB
    history = get_all_applications(limit=50)

    return {
        "candidate_profile": parsed,
        "candidate_intel":   intel,
        "jobs":              jobs,
        "history":           history,
        "summary":           summary,
        "resume_ai_analysis": result.get("resume_ai_analysis") or {},
    }


# ── Resume state (persisted between requests) ─────────────────────────────────
_RESUME_STATE_FILE = DASHBOARD_DIR.parent / "db" / "resume_state.json"

def _save_resume_state(path: str, filename: str, preview: dict | None = None):
    """
    Persist the active resume path + its instant-preview profile.

    Storing the preview here means /api/resume-status can return the full
    profile on the second (and any subsequent) modal open, so the dashboard
    never shows stale data from a previous resume.
    """
    _RESUME_STATE_FILE.write_text(
        json.dumps({"path": path, "filename": filename, "preview": preview or {}}),
        encoding="utf-8",
    )

def _clear_pipeline_cache():
    """
    Erase the last pipeline result so the dashboard API returns a clean slate
    after a new resume is uploaded.  Without this, /api/dashboard would keep
    serving Resume #1's full AI analysis even after Resume #2 is uploaded.
    """
    if RESULT_CACHE.exists():
        try:
            RESULT_CACHE.write_text("{}", encoding="utf-8")
            logger.info("💾 [DB UPDATE] 🧹 Cleared last_result.json — dashboard reset for new resume.")
        except Exception as exc:
            logger.warning(f"💾 [DB UPDATE] ⚠️  Could not clear result cache: {exc}")

def _load_resume_state() -> dict:
    import json
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
        from core.resume_parser import extract_resume_text, guess_candidate_name

        txt = extract_resume_text(path)
        g = guess_candidate_name(txt) or guess_name_from_filename(Path(path).name)
        if not g:
            g = guess_name_from_filename((state.get("filename") or "").strip())
        if g:
            profile["name"] = g
            profile["candidate_name"] = g
            logger.info(f"📇 [PROFILE] Name enriched from resume file: {g!r}")
    except Exception as exc:
        logger.debug(f"[dashboard] name enrich from disk skipped: {exc}")
    return profile


def _pipeline_result_meaningful(result: dict) -> bool:
    if not result:
        return False
    return bool(
        result.get("parsed_resume")
        or result.get("ranked_jobs")
        or result.get("jobs")
    )


# ── Market Salary Demand Engine ───────────────────────────────────────────────
# Deterministic India-market estimator using:
#   skills + certifications + internship + projects + experience
_MARKET_SKILL_BANDS = {
    "python":       (5.0, 9.0),
    "sql":          (5.0, 8.0),
    "pandas":       (6.0, 10.0),
    "numpy":        (6.0, 10.0),
    "tableau":      (6.0, 11.0),
    "power bi":     (6.0, 11.0),
    "excel":        (4.0, 7.0),
    "machine learning": (8.0, 14.0),
    "deep learning":    (10.0, 18.0),
    "nlp":          (10.0, 18.0),
    "llm":          (12.0, 22.0),
    "generative ai": (12.0, 22.0),
    "tensorflow":   (9.0, 16.0),
    "pytorch":      (9.0, 16.0),
    "scikit-learn": (7.0, 12.0),
    "huggingface":  (11.0, 20.0),
    "langchain":    (11.0, 20.0),
    "aws":          (8.0, 16.0),
    "azure":        (8.0, 15.0),
    "gcp":          (8.0, 15.0),
    "docker":       (8.0, 14.0),
    "kubernetes":   (10.0, 18.0),
    "react":        (7.0, 13.0),
    "node":         (7.0, 13.0),
    "fastapi":      (7.0, 13.0),
    "django":       (7.0, 12.0),
    "postgresql":   (6.0, 10.0),
    "mongodb":      (6.0, 10.0),
    "spark":        (10.0, 18.0),
    "airflow":      (9.0, 16.0),
}


def _compute_market_salary_demand(profile: dict, raw_text: str = "") -> dict:
    """Estimate market salary demand (LPA) from candidate profile evidence."""
    exp_level = (profile.get("experience_level") or "Fresher").strip()
    years = int(profile.get("years_of_experience") or 0)

    # Base by experience (India tech market)
    if exp_level == "Fresher" or years == 0:
        base_min, base_max = 2.8, 4.5
    elif exp_level == "0-1":
        base_min, base_max = 4.0, 6.5
    elif exp_level == "1-3":
        base_min, base_max = 6.5, 11.0
    else:
        base_min, base_max = 10.0, 18.0

    raw_skills = (
        (profile.get("primary_skills") or []) +
        (profile.get("secondary_skills") or []) +
        (profile.get("tools_and_technologies") or [])
    )
    skill_blob = " | ".join(str(s).lower() for s in raw_skills if s)
    matched_market_skills = [
        k for k in _MARKET_SKILL_BANDS.keys()
        if k in skill_blob
    ]

    # Top-demand skill contribution
    if matched_market_skills:
        mins = [_MARKET_SKILL_BANDS[s][0] for s in matched_market_skills[:8]]
        maxs = [_MARKET_SKILL_BANDS[s][1] for s in matched_market_skills[:8]]
        skill_min = sum(mins) / len(mins)
        skill_max = sum(maxs) / len(maxs)
        est_min = max(base_min, round(skill_min * 0.85, 1))
        est_max = max(base_max, round(skill_max * 0.95, 1))
    else:
        est_min, est_max = base_min, base_max

    txt = (raw_text or "").lower()
    cert_count = len(profile.get("certifications") or [])
    has_cert = cert_count > 0 or any(w in txt for w in ["certified", "certification", "certificate", "coursera", "aws", "gcp", "azure"])
    has_internship = any(w in txt for w in ["internship", "intern", "trainee"])
    project_count = len(profile.get("projects") or [])

    # Evidence boosts
    if has_cert:
        est_min += 0.6
        est_max += 1.2
    if has_internship:
        est_min += 0.5
        est_max += 1.0
    if project_count >= 2:
        est_min += 0.4
        est_max += 0.8

    # Experience scaling for 3+ years (bounded)
    if years >= 3:
        est_min += min(4.0, years * 0.5)
        est_max += min(8.0, years * 0.9)

    est_min = round(max(2.0, est_min), 1)
    est_max = round(min(40.0, max(est_min + 0.8, est_max)), 1)

    demand_score = min(
        98,
        35
        + len(matched_market_skills[:10]) * 5
        + (10 if has_cert else 0)
        + (10 if has_internship else 0)
        + (6 if project_count >= 2 else 0)
        + min(20, years * 4),
    )

    reason_parts = []
    if matched_market_skills:
        reason_parts.append(f"Market-demand skills matched: {', '.join(matched_market_skills[:6])}")
    else:
        reason_parts.append("No high-demand stack evidence found; using baseline market band.")
    if has_cert:
        reason_parts.append("Certification signal added.")
    if has_internship:
        reason_parts.append("Internship signal added.")
    if project_count >= 2:
        reason_parts.append("Strong project portfolio signal added.")
    reason_parts.append(f"Experience considered: {exp_level} ({years} year(s)).")

    return {
        "market_salary_min_lpa": est_min,
        "market_salary_max_lpa": est_max,
        "market_salary_demand_label": "High" if demand_score >= 75 else "Medium" if demand_score >= 55 else "Low",
        "market_salary_demand_score": int(demand_score),
        "market_salary_reasoning": " ".join(reason_parts),
        "market_skills_matched": matched_market_skills[:10],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    return send_from_directory(str(DASHBOARD_DIR), "landing.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory(str(DASHBOARD_DIR), "index.html")


@app.route("/api/dashboard")
def api_dashboard():
    """Main dashboard data endpoint."""
    init_db()
    result = _load_last_result()
    state = _load_resume_state()
    raw_preview = state.get("preview") or {}
    preview_profile = {} if raw_preview.get("_parse_error") else raw_preview
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

    return jsonify(payload)


@app.route("/api/upload-resume", methods=["POST"])
def api_upload_resume():
    """
    Upload a resume file (PDF / DOCX / TXT).
    Returns the saved path and an instant keyword-extracted preview.

    Full tripwire logging at every stage so the exact failure point is visible.
    """
    import re as _re

    # ── TRIPWIRE 1: File received? ────────────────────────────────────────────
    logger.info("📥 [UPLOAD] Incoming upload request received.")

    if "resume" not in request.files:
        logger.error("📥 [UPLOAD] ❌ No 'resume' field in request.files. Check form field name.")
        return jsonify({"error": "No file part in request. Use field name 'resume'."}), 400

    f = request.files["resume"]
    if not f.filename:
        logger.error("📥 [UPLOAD] ❌ File received but filename is empty.")
        return jsonify({"error": "Empty filename."}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.error(f"📥 [UPLOAD] ❌ Rejected format '{ext}'. Allowed: {ALLOWED_EXTENSIONS}")
        return jsonify({"error": f"Unsupported format '{ext}'. Allowed: pdf, docx, doc, txt"}), 400

    # ── TRIPWIRE 2: Save to disk ──────────────────────────────────────────────
    safe_name = secure_filename(f.filename)
    save_path = UPLOAD_DIR / safe_name
    try:
        f.save(str(save_path))
        size_kb = round(save_path.stat().st_size / 1024, 1)
        logger.success(f"📥 [UPLOAD] ✅ File saved: '{save_path}' ({size_kb} KB)")
    except Exception as exc:
        logger.error(f"📥 [UPLOAD] ❌ Failed to save file to disk: {exc}")
        return jsonify({"error": f"Disk write failed: {exc}"}), 500

    # ── STALENESS FIX 3a: save state (no preview yet) + erase old pipeline cache ─
    # Clearing last_result.json ensures /api/dashboard immediately returns a
    # clean slate — it won't serve Resume #1's AI analysis to Resume #2's session.
    _save_resume_state(str(save_path), safe_name)
    _clear_pipeline_cache()

    # ── TRIPWIRE 3: Extract text (PDF / DOCX / TXT) ───────────────────────────
    text = ""
    preview = {}
    try:
        from core.resume_parser import extract_resume_text
        logger.info(f"📄 [PDF PARSE] Attempting text extraction from '{save_path}'...")
        text = extract_resume_text(str(save_path))

        if not text or len(text) < 30:
            logger.error(
                f"📄 [PDF PARSE] ❌ Extracted text is empty or too short "
                f"({len(text) if text else 0} chars). "
                "PDF may be image-based/scanned — use a text-based PDF."
            )
            preview = {"_parse_error": "PDF returned empty text. Use a text-searchable PDF, not a scanned image."}
        else:
            logger.success(
                f"📄 [PDF PARSE] ✅ Text extracted: {len(text)} chars. "
                f"First 120: {text[:120].strip()!r}"
            )

            # ── TRIPWIRE 4: Keyword skill extraction (instant, no LLM) ───────
            logger.info("🤖 [AI REQUEST] Running instant keyword extraction (no LLM needed)...")
            tl = text.lower()

            SKILL_PATTERNS = [
                "python","sql","pandas","numpy","tensorflow","pytorch","scikit.learn",
                "sklearn","excel","tableau","power bi","machine learning","deep learning",
                "nlp","natural language processing","react","node","docker","kubernetes",
                "aws","azure","gcp","flask","fastapi","django","mongodb","postgresql",
                "mysql","git","javascript","typescript","spark","hadoop","airflow",
                "data analysis","data science","matplotlib","seaborn","llm","generative ai",
                "c++","java","go","rust","ruby","php","swift","kotlin","r programming",
                "scikit-learn","huggingface","transformers","langchain","openai","groq",
            ]
            found = list(dict.fromkeys(
                s.title().replace(".","") for s in SKILL_PATTERNS if s in tl
            ))

            ROLE_MAP = {
                "data analyst":       "Data Analyst",
                "data scientist":     "Data Scientist",
                "machine learning":   "ML Engineer",
                "deep learning":      "Deep Learning Engineer",
                "python":             "Python Developer",
                "nlp":                "NLP Engineer",
                "react":              "Frontend Developer",
                "devops":             "DevOps Engineer",
                "backend":            "Backend Developer",
                "full stack":         "Full Stack Developer",
                "generative ai":      "Generative AI Engineer",
                "llm":                "LLM Engineer",
                "data science":       "Data Scientist",
                "business analyst":   "Business Analyst",
            }
            target_roles = list(dict.fromkeys(
                v for k, v in ROLE_MAP.items() if k in tl
            ))

            exp_match = _re.search(r'(\d+)\s*(?:\+\s*)?year', tl)
            exp_years = int(exp_match.group(1)) if exp_match else 0
            exp_level = (
                "Fresher" if exp_years == 0 else
                "0-1"     if exp_years <= 1 else
                "1-3"     if exp_years <= 3 else "3+"
            )

            # Quick rule-based salary estimate (mirrors AI Salary Grading Matrix Rule 1/2)
            # This is a preview only — the full AI run will override with precise numbers.
            has_internship = any(w in tl for w in ["intern", "internship", "trainee"])
            has_cert       = any(w in tl for w in ["certified", "certification", "certificate", "aws", "gcp", "azure", "coursera"])
            num_projects   = tl.count("project")
            has_strong_profile = has_internship or has_cert or num_projects >= 2

            if exp_years == 0:
                if has_strong_profile:
                    sal_min, sal_max = 3.5, 6.0       # Fresher High-Tier (Rule 2)
                    sal_reasoning = "Fresher / Rule 2 (preview): evidence of internship, certification, or 2+ projects detected."
                else:
                    sal_min, sal_max = 2.0, 3.0       # Fresher Low-Tier (Rule 1)
                    sal_reasoning = "Fresher / Rule 1 (preview): no internship, certification, or significant projects found."
            elif exp_years <= 1:
                sal_min, sal_max = 4.0, 6.0
                sal_reasoning = f"Experienced / Rule 3 (preview): ~{exp_years} yr(s) detected."
            elif exp_years <= 3:
                sal_min, sal_max = 5.0, 9.0
                sal_reasoning = f"Experienced / Rule 3 (preview): ~{exp_years} yr(s) detected."
            else:
                sal_min = round(exp_years * 3.5, 1)
                sal_max = round(exp_years * 6.0, 1)
                sal_reasoning = f"Experienced / Rule 3 (preview): {exp_years} yr(s) × base rates."

            cand_loc = os.getenv("CANDIDATE_LOCATION", "").strip()
            guessed_name = (guess_candidate_name(text) or guess_name_from_filename(safe_name) or "").strip()
            preview = {
                "primary_skills":         found[:8],
                "secondary_skills":       found[8:16],
                "tools_and_technologies": [],
                "target_job_roles":       target_roles or ["Software Professional"],
                "preferred_locations":    [cand_loc] if cand_loc else ["India", "Remote"],
                "experience_level":       exp_level,
                "years_of_experience":    exp_years,
                "resume_score":           0,
                "suggested_salary_min":   sal_min,
                "suggested_salary_max":   sal_max,
                "salary_reasoning":       sal_reasoning + " (Full AI analysis pending — click Analyze Resume.)",
                "_parsed_by":             "instant_keyword_extract",
                "_text_chars":            len(text),
            }
            if guessed_name:
                preview["name"] = guessed_name
                preview["candidate_name"] = guessed_name
            preview.update(_compute_market_salary_demand(preview, tl))

            logger.success(
                f"🧩 [JSON PARSE] ✅ Instant preview built — "
                f"name={guessed_name or '(none)'} | "
                f"{len(found)} skills | "
                f"Roles: {target_roles[:3]} | "
                f"Level: {exp_level}"
            )

            # ── TRIPWIRE 5: Save profile to DB for dashboard counter ──────────
            try:
                init_db()
                from core.memory import _get_conn
                with _get_conn() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO resume_profiles "
                        "(filename, text_chars, primary_skills, target_roles, exp_level, uploaded_at) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (
                            safe_name,
                            len(text),
                            ",".join(found[:8]),
                            ",".join(target_roles[:3]),
                            exp_level,
                        )
                    )
                    conn.commit()
                logger.success(f"💾 [DB UPDATE] ✅ Resume profile saved to database: '{safe_name}'")
            except Exception as db_exc:
                # Non-fatal — DB table may not exist yet (created lazily)
                logger.warning(f"💾 [DB UPDATE] ⚠️  Could not save resume profile to DB: {db_exc}")

    except Exception as exc:
        logger.error(f"📄 [PDF PARSE] ❌ Exception during text extraction: {exc}")
        preview = {"_parse_error": str(exc)[:200]}

    logger.info(
        f"📥 [UPLOAD] Response ready — "
        f"ok=True | file='{safe_name}' | "
        f"skills={len(preview.get('primary_skills', []))} | "
        f"error={preview.get('_parse_error', 'none')}"
    )

    # ── STALENESS FIX 3b: persist the preview so /api/resume-status can serve it ─
    # Without this, re-opening the upload modal calls checkResumeStatus() which
    # can't restore the new resume's profile, leaving the panel stale.
    _save_resume_state(str(save_path), safe_name, preview if preview else None)

    return jsonify({
        "ok":       True,
        "filename": safe_name,
        "path":     str(save_path),
        "size_kb":  size_kb,
        "preview":  preview,
    })


@app.route("/api/resume-status")
def api_resume_status():
    """Return currently uploaded resume info + last-parsed preview profile."""
    state = _load_resume_state()
    if not state:
        return jsonify({"uploaded": False})
    p = Path(state.get("path", ""))
    return jsonify({
        "uploaded": p.exists(),
        "filename": state.get("filename", ""),
        "path":     state.get("path", ""),
        "size_kb":  round(p.stat().st_size / 1024, 1) if p.exists() else 0,
        # ── STALENESS FIX 4: return saved preview so modal/dashboard can restore state ──
        "preview":  state.get("preview", {}),
    })


@app.route("/api/resume-analysis", methods=["POST"])
def api_resume_analysis():
    """LLM resume intelligence: strengths, weaknesses, missing skills, improvements."""
    from core.resume_ai import analyze_resume, analyze_resume_from_parsed
    from core.resume_parser import parse_resume

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or data.get("resume_text") or "").strip()
    if text:
        return jsonify(analyze_resume(text))
    state = _load_resume_state()
    path = (state.get("path") or "").strip() if state else ""
    if path and Path(path).is_file():
        try:
            parsed = parse_resume(path)
            return jsonify(analyze_resume_from_parsed(parsed))
        except Exception as exc:
            logger.exception("resume-analysis parse failed")
            return jsonify(
                {
                    "error": str(exc),
                    "strengths": [],
                    "weaknesses": [],
                    "missing_skills": [],
                    "improvements": [],
                }
            ), 500
    return jsonify({"error": "Provide resume text or upload a resume first."}), 400


@app.route("/api/run-pipeline", methods=["POST"])
def api_run_pipeline():
    """
    Career Copilot Analyzer pipeline (NO autonomous browser apply).

    Flow:
      1) Parse resume with AI analyzer
      2) Evaluate profile
      3) Fetch matching jobs from RapidAPI (JSearch)
      4) Score jobs with existing matcher
      5) Return direct apply links for manual application
    """
    data = request.get_json(force=True) or {}
    max_jobs = int(data.get("max_jobs", 20))
    resume_path = data.get("resume_path") or _load_resume_state().get("path", "")

    logger.info(f"🧠 [COPILOT] Analyzer run requested | max_jobs={max_jobs}")

    if not resume_path:
        return jsonify({"error": "No resume uploaded. Please upload a resume first."}), 400
    if not Path(resume_path).exists():
        return jsonify({"error": f"Resume file missing on disk: {resume_path}"}), 400

    try:
        # 1) Parse + 2) Evaluate
        # Fast path (default): use upload preview to avoid long LLM timeout.
        # Optional full parse: pass {"force_llm_parse": true} in request body.
        state = _load_resume_state()
        parsed = state.get("preview") or {}
        force_llm_parse = bool(data.get("force_llm_parse", False))
        if force_llm_parse or not parsed:
            parsed = parse_resume(resume_path)
        if not parsed:
            return jsonify({"error": "Resume parse failed and no preview fallback found"}), 400
        # Always attach deterministic market-demand salary estimator.
        parsed.update(_compute_market_salary_demand(parsed))
        intel = evaluate_candidate(parsed)

        resume_ai_analysis: dict = {}
        try:
            from core.resume_ai import analyze_resume_from_parsed

            resume_ai_analysis = analyze_resume_from_parsed(parsed)
        except Exception as _rai:
            logger.warning(f"[COPILOT] resume AI analysis skipped: {_rai}")

        # 3) Fetch jobs via RapidAPI using matching titles (fallback to target roles)
        preferred_titles = parsed.get("matching_job_titles") or parsed.get("target_job_roles") or ["Software Developer"]
        location = (parsed.get("preferred_locations") or ["India"])[0]
        raw_jobs = []
        seen = set()
        # Keep API latency tight: query only the strongest title per run.
        per_title = max_jobs

        logger.info(f"🌐 [RAPIDAPI] Career Copilot search title: {preferred_titles[:1]} | location={location}")

        for title in preferred_titles[:1]:
            jobs = fetch_jobs_from_rapidapi(
                query=title,
                location=location,
                num_pages=1,
                max_results=per_title,
            )
            for j in jobs:
                key = j.get("apply_url") or j.get("apply_link") or j.get("job_id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                raw_jobs.append(j)

        raw_jobs = raw_jobs[:max_jobs]

        # 4) Fast local scoring (no per-job LLM calls, keeps API responsive)
        cand_skills = [s.lower() for s in (
            (parsed.get("primary_skills") or []) + (parsed.get("secondary_skills") or [])
        )]
        ranked = []
        for job in raw_jobs:
            text = f"{job.get('title','')} {job.get('description','')}".lower()
            overlap = [s for s in cand_skills if s and s in text]
            score = min(95, 35 + len(set(overlap)) * 10)
            ranked.append({
                **job,
                "match_result": {
                    "match_score": score,
                    "confidence_score": 70 if overlap else 45,
                    "matched_skills": sorted(set(overlap))[:8],
                    "missing_critical_skills": [],
                    "is_elite": score >= 80,
                    "justification": "Keyword overlap based quick score (Career Copilot mode).",
                },
            })
        ranked.sort(key=lambda x: x.get("match_result", {}).get("match_score", 0), reverse=True)

        # 5) Smart apply + optional safe auto-open (browser tab only)
        init_db()
        tier = intel.get("candidate_tier", "Standard")
        max_auto = int(os.getenv("SMART_APPLY_MAX_AUTO_OPEN", "5"))
        auto_opened = 0
        ranked_jobs = []
        application_results = []

        for job in ranked:
            apply_url = job.get("apply_url") or job.get("apply_link") or job.get("url") or ""
            job = {
                **job,
                "url": apply_url,
                "apply_link": apply_url,
                "apply_url": apply_url,
            }
            mr = dict(job.get("match_result") or {})
            conf = int(mr.get("confidence_score") or 0)
            try:
                from core.decision_engine import (
                    evaluate_job_fit,
                    pipeline_decision_from_fit,
                    resume_blob_for_llm,
                )

                jd_txt = str(job.get("description") or "")
                ai_fit = evaluate_job_fit(resume_blob_for_llm(parsed), jd_txt)
                pds = pipeline_decision_from_fit(ai_fit)
            except Exception as _fit_e:
                logger.debug(f"[COPILOT] AI fit fallback: {_fit_e}")
                ai_fit = {
                    "decision": "apply",
                    "confidence": conf,
                    "reason": "Keyword copilot score only (AI fit skipped).",
                }
                from core.decision_engine import pipeline_decision_from_fit

                pds = pipeline_decision_from_fit(ai_fit)
            mr["ai_fit"] = ai_fit
            mr["pipeline_decision_status"] = pds

            label = (
                map_execution_to_decision_label("SKIP")
                if pds == "rejected"
                else map_execution_to_decision_label("RECOMMENDED")
            )
            try:
                fc = int((ai_fit or {}).get("confidence"))
            except (TypeError, ValueError):
                fc = None
            smart_conf = fc if fc is not None else conf
            smart = process_job_application(job, label, smart_conf)
            mr["smart_apply"] = smart
            apply_decision = {
                "execution_status": smart_action_to_execution_status(smart["action"]),
                "risk_level": "Low",
                "smart_action": smart["action"],
                "ai_decision_label": ui_decision_label(smart["action"]),
                "form_mapping": {},
            }

            if smart["action"] == "auto_open" and smart.get("link") and auto_opened < max_auto:
                if open_application(smart["link"]):
                    auto_opened += 1
                    apply_decision["browser_opened"] = True

            job = {**job, "match_result": mr, "apply_decision": apply_decision}
            row_id = None
            try:
                row_id = save_application(job, mr, apply_decision, tier)
            except Exception as _save_exc:
                logger.warning(f"[COPILOT] save_application skipped: {_save_exc}")
            if row_id:
                try:
                    from core.learning import record_learning_event

                    record_learning_event(
                        row_id,
                        str(ai_fit.get("decision") or ""),
                        pds,
                        int(ai_fit.get("confidence") or 0),
                        str(ai_fit.get("reason") or ""),
                        str(job.get("title") or ""),
                        str(job.get("company") or ""),
                    )
                except Exception:
                    pass

            ranked_jobs.append(job)
            application_results.append({
                "job": job,
                "match_result": mr,
                "apply_decision": apply_decision,
            })

        result = {
            "parsed_resume": parsed,
            "candidate_intel": intel,
            "resume_ai_analysis": resume_ai_analysis,
            "ranked_jobs": ranked_jobs,
            "application_results": application_results,
            "summary": {
                "total_jobs": len(raw_jobs),
                "ranked_jobs": len(ranked_jobs),
                "manual_apply_mode": True,
                "smart_auto_tabs_opened": auto_opened,
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            },
        }
        try:
            from core.learning import update_strategy

            result["self_improvement"] = {"agent_learning_strategy": update_strategy()}
        except Exception:
            pass
        RESULT_CACHE.write_text(json.dumps(result, indent=2), encoding="utf-8")

        logger.success(
            f"🧠 [COPILOT] ✅ Analysis done | titles={len(preferred_titles)} | "
            f"fetched={len(raw_jobs)} | ranked={len(ranked_jobs)}"
        )

        return jsonify({
            "ok": True,
            "summary": result["summary"],
            "tier": intel.get("candidate_tier", ""),
            "skills": parsed.get("primary_skills", [])[:8],
            "jobs_ranked": len(ranked_jobs),
            "apps_made": len(ranked_jobs),
            "smart_auto_tabs_opened": result["summary"].get("smart_auto_tabs_opened", 0),
        })
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"🧠 [COPILOT] ❌ Pipeline error: {exc}\n{tb}")
        return jsonify({"error": str(exc)[:400], "traceback": tb[-800:]}), 500


@app.route("/api/jobs-scanned")
def api_jobs_scanned():
    """Return total count of jobs the scraper has ever seen (for dashboard counter)."""
    try:
        import sqlite3 as _sqlite3
        project_root = Path(__file__).parent.parent
        db_path = project_root / "db" / "agent_memory.db"
        if not db_path.exists():
            return jsonify({"count": 0, "recent": []})
        conn = _sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM jobs_scanned"
        ).fetchone()[0] if _table_exists(conn, "jobs_scanned") else 0
        recent = []
        if _table_exists(conn, "jobs_scanned"):
            rows = conn.execute(
                "SELECT job_title, company, source, scanned_at FROM jobs_scanned "
                "ORDER BY scanned_at DESC LIMIT 10"
            ).fetchall()
            recent = [{"title": r[0], "company": r[1], "source": r[2], "at": r[3]} for r in rows]
        conn.close()
        return jsonify({"count": count, "recent": recent})
    except Exception as exc:
        return jsonify({"count": 0, "error": str(exc)})


def _table_exists(conn, table: str) -> bool:
    """Check if a SQLite table exists."""
    import sqlite3 as _sqlite3
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


@app.route("/api/history")
def api_history():
    """Application history endpoint."""
    init_db()
    limit = request.args.get("limit", 50, type=int)
    return jsonify(get_all_applications(limit=limit))


@app.route("/api/skills")
def api_skills():
    """Skill performance stats endpoint."""
    init_db()
    return jsonify(get_skill_performance())


@app.route("/api/status")
def api_status():
    """Agent status ping."""
    result = _load_last_result()
    summary = result.get("summary", {})
    llm = get_llm_diagnostics()
    return jsonify({
        "status":     "ready",
        "last_run":   summary.get("timestamp", None),
        "total_jobs": summary.get("total_jobs", 0),
        "tier":       result.get("candidate_intel", {}).get("candidate_tier", "—"),
        "ai_brain_online": llm.get("llm_ready", False),
        "llm_model":       llm.get("llm_model", "llama3.2"),
    })


@app.route("/api/agent-diagnostics")
def api_agent_diagnostics():
    """LLM brain health — same `status` as Ollama reachability (brain_online / brain_offline)."""
    return jsonify(get_agent_diagnostics())


# ── Tracker Routes ────────────────────────────────────────────────────────────

@app.route("/api/tracker/stats")
def api_tracker_stats():
    """Full tracker statistics — the main report endpoint."""
    init_db()
    return jsonify(generate_report())


@app.route("/api/tracker/activity")
def api_tracker_activity():
    """Recent activity feed."""
    init_db()
    limit = request.args.get("limit", 20, type=int)
    return jsonify(get_recent_activity(limit))


@app.route("/api/tracker/skills")
def api_tracker_skills():
    """Top performing skills."""
    init_db()
    limit = request.args.get("limit", 15, type=int)
    return jsonify(get_top_skills(limit))


@app.route("/api/tracker/applications")
def api_tracker_applications():
    """List applications with optional filters."""
    init_db()
    status  = request.args.get("status")
    outcome = request.args.get("outcome")
    limit   = request.args.get("limit", 50, type=int)
    offset  = request.args.get("offset", 0, type=int)
    return jsonify(list_applications(status, outcome, limit, offset))


@app.route("/api/tracker/mark-applied", methods=["POST"])
def api_tracker_mark_applied():
    """
    Log a manual application click from the dashboard "Apply Now" button.
    Body:
      {
        "job_id": "...",
        "title": "...",
        "company": "...",
        "url": "...",
        "match_score": 0-100,
        "location": "...",
        "salary": "...",
        "source": "...",
        "skills": ["..."]
      }
    """
    init_db()
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    company = (data.get("company") or "").strip()
    url = (data.get("url") or data.get("apply_url") or data.get("apply_link") or "").strip()
    if not title or not company or not url:
        return jsonify({"error": "title, company and url are required"}), 400

    status = (data.get("status") or "MANUAL_APPLIED").strip().upper()
    if status not in ("MANUAL_APPLIED", "PENDING_REVIEW", "SKIP", "AUTO_APPLY", "REVIEW"):
        status = "MANUAL_APPLIED"

    row_id = log_application(
        job_id_str=str(data.get("job_id") or ""),
        title=title,
        company=company,
        url=url,
        status=status,
        match_score=int(data.get("match_score") or 0),
        skills=data.get("skills") or [],
        location=str(data.get("location") or ""),
        salary=str(data.get("salary") or ""),
        source=str(data.get("source") or "rapidapi_jsearch"),
    )
    return jsonify({"success": True, "id": row_id})


@app.route("/api/tracker/update-outcome", methods=["POST"])
def api_update_outcome():
    """
    Update the outcome of an application.
    Body: { "id": int|str, "outcome": str, "interview_round": int, "rejection_reason": str, "notes": str }
    """
    init_db()
    data = request.get_json(force=True) or {}
    identifier  = data.get("id")
    outcome     = data.get("outcome", "")
    irnd        = data.get("interview_round", 0)
    reason      = data.get("rejection_reason", "")
    notes       = data.get("notes", "")

    if not identifier or not outcome:
        return jsonify({"error": "id and outcome are required"}), 400

    # Accept numeric string IDs
    if isinstance(identifier, str) and identifier.isdigit():
        identifier = int(identifier)

    ok = update_outcome(identifier, outcome, irnd, reason, notes)
    return jsonify({"success": ok, "outcome": outcome})


@app.route("/api/tracker/update-status", methods=["POST"])
def api_update_status():
    """
    Update the execution_status of an application.
    Body: { "id": int|str, "status": str, "notes": str }
    """
    init_db()
    data   = request.get_json(force=True) or {}
    idf    = data.get("id")
    status = data.get("status", "")
    notes  = data.get("notes", "")

    if not idf or not status:
        return jsonify({"error": "id and status are required"}), 400

    if isinstance(idf, str) and idf.isdigit():
        idf = int(idf)

    ok = update_status(idf, status, notes)
    return jsonify({"success": ok})


@app.route("/api/tracker/application/<int:app_id>")
def api_get_application(app_id: int):
    """Fetch a single application by DB ID."""
    init_db()
    record = get_application(app_id)
    if not record:
        return jsonify({"error": "Not found"}), 404
    return jsonify(record)


@app.route("/api/tracker/seed", methods=["POST"])
def api_seed_demo():
    """Seed demo data for testing the tracker dashboard."""
    init_db()
    seed_demo_data()
    return jsonify({"success": True, "message": "Demo data seeded."})


# ── Analytics Routes ──────────────────────────────────────────────────────────

@app.route("/api/analytics")
def api_analytics():
    """
    Return the latest analytics result.
    Pass ?refresh=1 to force a fresh LLM analysis run.
    """
    init_db()
    force = request.args.get("refresh", "0") == "1"
    if force:
        result = run_analysis(force_llm=True)
    else:
        result = get_latest_analytics()
        if not result:
            result = run_analysis(force_llm=False)
    return jsonify(result)


@app.route("/api/analytics/run", methods=["POST"])
def api_analytics_run():
    """Trigger a fresh analytics run (with LLM)."""
    init_db()
    data   = request.get_json(force=True) or {}
    skills = data.get("candidate_skills", [])
    result = run_analysis(candidate_skills=skills or None, force_llm=True)
    return jsonify(result)


@app.route("/api/analytics/cached")
def api_analytics_cached():
    """Return last cached analytics without triggering a new run."""
    init_db()
    result = get_latest_analytics()
    if not result:
        return jsonify({"error": "No analytics data yet. Run /api/analytics first."}), 404
    return jsonify(result)


# ── Notification Routes ────────────────────────────────────────────────────────

@app.route("/api/notifications")
def api_notifications():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(get_notification_log(limit))

@app.route("/api/notifications/stats")
def api_notification_stats():
    return jsonify(get_notification_stats())

@app.route("/api/notifications/config")
def api_notification_config():
    return jsonify(get_notification_config())

@app.route("/api/notifications/test", methods=["POST"])
def api_notification_test():
    return jsonify(send_test_notification())

@app.route("/api/notifications/send", methods=["POST"])
def api_notification_send():
    data = request.get_json(force=True) or {}
    result = send_notification(
        notification_type = data.get("notification_type", "test"),
        message           = data.get("message", "Manual notification"),
        force             = data.get("force", False),
        async_send        = True,
    )
    return jsonify(result)

@app.route("/api/notifications/daily-summary", methods=["POST"])
def api_notification_daily():
    from core.tracker import generate_report
    report = generate_report()
    stats  = report.get("stats", {})
    result = notify_daily_summary({
        "applied":    stats.get("by_status", {}).get("AUTO_APPLY", 0),
        "interviews": stats.get("by_outcome", {}).get("interview", 0),
        "offers":     stats.get("by_outcome", {}).get("offer", 0),
        "new_jobs":   stats.get("total", 0),
    })
    return jsonify(result)


# ── Safety Guard endpoints ────────────────────────────────────────────────────

@app.route("/api/safety/status")
def api_safety_status():
    return jsonify(get_safety_stats())

@app.route("/api/safety/log")
def api_safety_log():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_safety_log(limit=limit))

@app.route("/api/safety/reset-halt", methods=["POST"])
def api_safety_reset():
    return jsonify(reset_halt("Manual reset via dashboard"))

@app.route("/api/safety/set-review-mode", methods=["POST"])
def api_safety_review():
    return jsonify(set_review_mode("Manual override via dashboard"))

@app.route("/api/safety/set-auto-mode", methods=["POST"])
def api_safety_auto():
    return jsonify(set_auto_mode("Restored via dashboard"))


# ── Master Orchestrator endpoints ────────────────────────────────────────────

@app.route("/api/orchestrator/status")
def api_orchestrator_status():
    return jsonify(get_master_status())


@app.route("/api/orchestrator/run", methods=["POST"])
def api_orchestrator_run():
    data = request.get_json(force=True) or {}
    result = run_master_orchestrator(
        resume_source     = data.get("resume_path"),
        dry_run           = data.get("dry_run", True),
        force_pipeline    = data.get("force_pipeline", False),
        max_applications  = data.get("max_applications", 5),
        override_strategy = data.get("override_strategy"),
    )
    return jsonify(result)


@app.route("/api/orchestrator/plan")
def api_orchestrator_plan():
    from core.master_orchestrator import (
        _read_system_state, _compute_adaptive_thresholds, _assess_system_health
    )
    state      = _read_system_state()
    thresholds = _compute_adaptive_thresholds(state)
    health, reason = _assess_system_health(state, thresholds)
    return jsonify({
        "system_status":    health,
        "status_reason":    reason,
        "thresholds":       thresholds,
        "performance": {
            "callback_rate":  state.callback_rate,
            "interview_rate": state.interview_rate,
            "offer_rate":     state.offer_rate,
            "applied_30d":    state.applied_count,
            "optimal_threshold": state.optimal_threshold,
        },
        "top_skills":       state.top_skills,
        "top_skill_gaps":   state.top_skill_gaps,
        "best_sources":     state.best_sources,
        "worst_sources":    state.worst_sources,
        "current_strategy": state.current_strategy,
    })


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("DASHBOARD_PORT", "5050"))
    _llm = get_llm_diagnostics()
    print(f"\n  Omniscient AI Dashboard -> http://127.0.0.1:{port}")
    print(f"  LLM brain: {_llm['status']}  |  Ollama: {_llm['ollama_base_url']}  |  model: {_llm['llm_model']}")
    if _llm["status"] != "brain_online":
        print("  [!] If Ollama is running, set NO_PROXY=127.0.0.1,localhost or OLLAMA_PROBE_URL (see .env comments)\n")
    else:
        print()
    app.run(host="0.0.0.0", port=port, debug=False)
