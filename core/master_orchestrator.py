"""
Master Orchestrator AI
The intelligent brain that coordinates all subsystems with adaptive decision-making.

Responsibilities:
  1. Read full system state (DB, analytics, strategy, notifications)
  2. Run LLM-powered adaptive execution planning
  3. Apply multi-layer quality gating before any application
  4. Execute optimized pipeline with dynamic thresholds
  5. Generate structured learning updates from outcomes
  6. Persist strategy for next run

Output schema:
{
  "system_status":    "healthy | degraded | needs_attention",
  "actions_taken":    [],
  "learning_updates": [],
  "next_strategy":    ""
}
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter, defaultdict

from core.db_adapter    import _get_conn
from core.memory        import (
    init_db, get_all_applications, get_skill_performance,
    get_latest_strategy, save_strategy,
)
from core.analytics     import get_latest_analytics
from core.notifier      import get_notification_stats, notify_daily_summary
from utils.helpers      import safe_json_call, logger, timer

# ── Constants ─────────────────────────────────────────────────────────────────

MASTER_STATE_PATH = os.path.join("db", "master_state.json")

# Spam / low-quality job signals — immediately skip
SPAM_SIGNALS = {
    "urgent hiring", "immediate joiner", "walk-in interview", "commission only",
    "no salary", "unpaid internship", "mlm", "direct selling", "business development executive",
    "earn daily", "work from home part-time", "part time online", "bpo process",
    "data entry", "telecaller", "copy paste", "form fill", "survey",
    "no experience required", "freshers only (0-0)", "notice period: immediate",
}

# High-quality signals — boost scoring
QUALITY_SIGNALS = {
    "equity", "esops", "stock options", "annual bonus", "learning budget",
    "remote-first", "flexible hours", "health insurance", "4-day workweek",
    "open source", "ai startup", "series", "funded",
}

# Output schema reference
EMPTY_OUTPUT = {
    "system_status":    "unknown",
    "actions_taken":    [],
    "learning_updates": [],
    "next_strategy":    "",
    "execution_plan":   {},
    "performance":      {},
    "timestamp":        "",
}


# ── System State ──────────────────────────────────────────────────────────────

@dataclass
class SystemState:
    """Cross-subsystem snapshot read before each orchestration cycle."""
    # Application performance
    total_applications: int       = 0
    applied_count:      int       = 0
    callback_count:     int       = 0
    interview_count:    int       = 0
    offer_count:        int       = 0
    callback_rate:      float     = 0.0
    interview_rate:     float     = 0.0
    offer_rate:         float     = 0.0

    # Score performance
    avg_score_at_callback: float  = 0.0
    avg_score_overall:     float  = 0.0
    optimal_threshold:     int    = 70

    # Skill intelligence
    top_skills:          list     = field(default_factory=list)
    weak_skills:         list     = field(default_factory=list)
    top_skill_gaps:      list     = field(default_factory=list)

    # Company / source patterns
    best_company_types:  list     = field(default_factory=list)
    best_sources:        list     = field(default_factory=list)
    worst_sources:       list     = field(default_factory=list)

    # Strategy
    current_strategy:    str      = ""
    last_insights:       list     = field(default_factory=list)

    # System health
    last_run_timestamp:  str      = ""
    notifications_sent:  int      = 0
    db_healthy:          bool     = True
    llm_available:       bool     = True

    # Candidate
    candidate_tier:      str      = "Standard"
    candidate_skills:    list     = field(default_factory=list)


# ── State Reader ──────────────────────────────────────────────────────────────

def _read_system_state() -> SystemState:
    """Pull all subsystem data into a unified SystemState in one pass."""
    state = SystemState()

    try:
        init_db()
        with _get_conn() as conn:

            # Application performance (last 30 days)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            apps = conn.execute(
                "SELECT outcome, match_score, execution_status, company FROM applications WHERE applied_at >= ?",
                (cutoff,)
            ).fetchall()

            total = len(apps)
            state.total_applications = total
            if total:
                applied   = sum(1 for a in apps if a["execution_status"] == "AUTO_APPLY")
                callbacks = sum(1 for a in apps if a["outcome"] in ("interview", "offer"))
                interviews= sum(1 for a in apps if a["outcome"] == "interview")
                offers    = sum(1 for a in apps if a["outcome"] == "offer")
                scores    = [a["match_score"] for a in apps if (a["match_score"] or 0) > 0]
                cb_scores = [a["match_score"] for a in apps
                             if a["outcome"] in ("interview","offer") and (a["match_score"] or 0) > 0]

                state.applied_count   = applied
                state.callback_count  = callbacks
                state.interview_count = interviews
                state.offer_count     = offers
                state.callback_rate   = round(callbacks / max(applied, 1) * 100, 1)
                state.interview_rate  = round(interviews / max(applied, 1) * 100, 1)
                state.offer_rate      = round(offers / max(applied, 1) * 100, 1)
                state.avg_score_overall     = round(sum(scores) / max(len(scores), 1), 1)
                state.avg_score_at_callback = round(sum(cb_scores) / max(len(cb_scores), 1), 1)

            # Optimal threshold — score bucket with highest callback rate
            buckets = _compute_score_buckets(apps)
            state.optimal_threshold = _compute_optimal_threshold(buckets)

            # Skill performance
            skill_rows = conn.execute(
                """SELECT skill, appearances, callbacks, interviews
                   FROM skill_performance WHERE appearances > 0
                   ORDER BY interviews DESC, callbacks DESC LIMIT 20"""
            ).fetchall()
            state.top_skills  = [r["skill"] for r in skill_rows if r["interviews"] > 0][:8]
            state.weak_skills = [r["skill"] for r in skill_rows
                                 if r["appearances"] >= 3 and r["callbacks"] == 0][:6]

            # Source performance
            src_rows = conn.execute(
                """SELECT source,
                          COUNT(*) as total,
                          SUM(CASE WHEN outcome IN('interview','offer') THEN 1 ELSE 0 END) as cbs,
                          ROUND(CAST(SUM(CASE WHEN outcome IN('interview','offer') THEN 1 ELSE 0 END)
                                AS FLOAT)/MAX(COUNT(*),1)*100, 1) as rate
                   FROM applications WHERE source IS NOT NULL AND source != ''
                   GROUP BY source HAVING total >= 2
                   ORDER BY rate DESC"""
            ).fetchall()
            state.best_sources  = [r["source"] for r in src_rows if r["rate"] > 10][:3]
            state.worst_sources = [r["source"] for r in src_rows if r["rate"] == 0][:3]

        # Strategy log
        strategy_entry = get_latest_strategy()
        if strategy_entry:
            state.current_strategy = strategy_entry.get("strategy", "")
            try:
                state.last_insights = json.loads(strategy_entry.get("insights", "[]") or "[]")[:5]
            except Exception:
                pass

        # Analytics cache (skill gaps)
        analytics = get_latest_analytics()
        if analytics:
            gaps = analytics.get("skill_gap_analysis", [])
            state.top_skill_gaps = [g.get("skill","") for g in gaps[:5] if g.get("gap_severity") in ("High","Medium")]

        # Notification stats
        notif_stats = get_notification_stats()
        state.notifications_sent = notif_stats.get("total", 0)

        # Last master state (for resume data)
        master_state_cache = _load_master_state()
        if master_state_cache:
            state.last_run_timestamp = master_state_cache.get("timestamp", "")
            state.candidate_tier     = master_state_cache.get("candidate_tier", "Standard")
            state.candidate_skills   = master_state_cache.get("candidate_skills", [])

    except Exception as exc:
        logger.warning(f"[MasterOrch] State read error (partial data): {exc}")
        state.db_healthy = False

    return state


def _compute_score_buckets(apps: list) -> dict:
    buckets = {"90-100":{"apps":0,"cbs":0},"80-89":{"apps":0,"cbs":0},
               "70-79":{"apps":0,"cbs":0},"60-69":{"apps":0,"cbs":0},"<60":{"apps":0,"cbs":0}}
    for a in apps:
        sc = a["match_score"] or 0
        cb = a["outcome"] in ("interview","offer")
        k = "90-100" if sc>=90 else "80-89" if sc>=80 else "70-79" if sc>=70 else "60-69" if sc>=60 else "<60"
        buckets[k]["apps"] += 1
        if cb: buckets[k]["cbs"] += 1
    for b in buckets.values():
        b["rate"] = round(b["cbs"] / max(b["apps"],1) * 100, 1)
    return buckets


def _compute_optimal_threshold(buckets: dict) -> int:
    for k in ["90-100","80-89","70-79","60-69","<60"]:
        b = buckets[k]
        if b["rate"] >= 30 and b["apps"] >= 2:
            try:
                return int(k.split("-")[0].replace("<",""))
            except Exception:
                pass
    return 70


# ── Quality Gate ──────────────────────────────────────────────────────────────

def _quality_score(job: dict, state: SystemState) -> tuple[int, list[str]]:
    """
    Multi-layer quality score (0-100) + list of detected issues.
    Higher score = higher quality job worth applying to.
    """
    score    = 50  # neutral baseline
    issues   = []
    signals  = []

    title   = (job.get("title",   "") or "").lower()
    company = (job.get("company", "") or "").lower()
    desc    = (job.get("description", "") or "").lower()
    salary  = (job.get("salary",  "") or "").lower()
    text    = f"{title} {company} {desc} {salary}"

    # ── Layer 1: Spam detection ─────────────────────────────────
    for signal in SPAM_SIGNALS:
        if signal in text:
            score -= 30
            issues.append(f"spam_signal: '{signal}'")
            break

    # ── Layer 2: Quality signals ─────────────────────────────────
    quality_hits = sum(1 for s in QUALITY_SIGNALS if s in text)
    score += quality_hits * 8
    if quality_hits:
        signals.append(f"quality_signals: {quality_hits}")

    # ── Layer 3: Match score ─────────────────────────────────────
    match_score = job.get("match_score") or job.get("match_result",{}).get("match_score",0) or 0
    if match_score >= 90: score += 25
    elif match_score >= 80: score += 15
    elif match_score >= 70: score += 5
    elif match_score < 50: score -= 20; issues.append("low_match_score")

    # ── Layer 4: Skills alignment ────────────────────────────────
    matched  = job.get("match_result",{}).get("matched_skills",[]) or []
    missing  = job.get("match_result",{}).get("missing_critical_skills",[]) or []
    if len(missing) > 4: score -= 10; issues.append("too_many_missing_skills")
    if len(matched) >= 5: score += 10

    # ── Layer 5: Source reputation ───────────────────────────────
    src = job.get("_source", "") or ""
    if src in state.best_sources:  score += 10; signals.append(f"trusted_source: {src}")
    if src in state.worst_sources: score -= 15; issues.append(f"poor_source: {src}")

    # ── Layer 6: Company name completeness ───────────────────────
    if not company or company in ("unknown", "n/a", "confidential"):
        score -= 10; issues.append("anonymous_company")

    # ── Layer 7: Salary alignment ────────────────────────────────
    # Very basic — if salary info is present it signals a legit posting
    if salary and any(c.isdigit() for c in salary): score += 8

    return max(0, min(100, score)), issues


def quality_gate(jobs: list[dict], state: SystemState, min_quality: int = 40) -> tuple[list[dict], list[dict]]:
    """
    Filter jobs through the quality gate.

    Returns:
        (approved_jobs, rejected_jobs)
    """
    approved, rejected = [], []
    for job in jobs:
        q_score, issues = _quality_score(job, state)
        job["_quality_score"] = q_score
        job["_quality_issues"] = issues
        if q_score >= min_quality:
            approved.append(job)
        else:
            rejected.append(job)
            logger.debug(
                f"[QualityGate] REJECTED {job.get('title','')} @ {job.get('company','')} "
                f"| quality={q_score} | {issues}"
            )

    approved.sort(key=lambda j: j["_quality_score"], reverse=True)
    logger.info(f"[QualityGate] {len(approved)} approved / {len(rejected)} rejected from {len(jobs)} jobs")
    return approved, rejected


# ── Adaptive Thresholds ───────────────────────────────────────────────────────

def _compute_adaptive_thresholds(state: SystemState) -> dict:
    """
    Compute optimal apply/review thresholds from historical performance.
    Implements a simple feedback loop:
      - Low callback rate  → raise threshold (be more selective)
      - High callback rate → lower threshold slightly (cast wider net)
    """
    base_apply  = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD", "85"))
    base_review = int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "70"))

    # Not enough data yet
    if state.applied_count < 5:
        return {"apply_threshold": base_apply, "review_threshold": base_review, "adjusted": False}

    adjusted_apply  = base_apply
    adjusted_review = base_review
    reason          = "baseline"

    if state.callback_rate == 0 and state.applied_count >= 10:
        # No callbacks at all — raise threshold significantly
        adjusted_apply  = min(base_apply  + 10, 92)
        adjusted_review = min(base_review + 5,  82)
        reason = "zero_callback_rate — threshold raised"

    elif state.callback_rate < 5 and state.applied_count >= 10:
        # Very low callback rate — raise threshold
        adjusted_apply  = min(base_apply  + 5, 90)
        adjusted_review = min(base_review + 3, 80)
        reason = f"low_callback_rate ({state.callback_rate}%) — threshold raised"

    elif state.callback_rate > 20:
        # Good callback rate — can be slightly less restrictive
        adjusted_apply  = max(base_apply  - 3, 78)
        adjusted_review = max(base_review - 3, 65)
        reason = f"high_callback_rate ({state.callback_rate}%) — threshold lowered"

    # Also factor in score-at-callback
    if state.avg_score_at_callback > 0:
        # Use the score that actually leads to callbacks as the apply threshold
        data_driven = int(state.avg_score_at_callback) - 5
        if abs(data_driven - adjusted_apply) <= 10:
            adjusted_apply = data_driven
            reason += f" | data_driven_threshold={data_driven}"

    return {
        "apply_threshold":  adjusted_apply,
        "review_threshold": adjusted_review,
        "adjusted":         adjusted_apply != base_apply or adjusted_review != base_review,
        "reason":           reason,
    }


# ── LLM Master Brain ──────────────────────────────────────────────────────────

MASTER_BRAIN_PROMPT = """
You are the Master Orchestrator AI — the central intelligence coordinating an autonomous job application system.

Your mission: Analyze the complete system state and generate a precise execution plan that MAXIMIZES callback probability while MINIMIZING wasted applications.

CORE PRINCIPLES:
1. Quality over quantity — 3 high-quality applications beat 30 spray-and-pray
2. Learn from every outcome — rejections and offers are equally valuable signals
3. Adapt continuously — yesterday's strategy may not work today
4. Protect the candidate's time — never apply to low-quality or irrelevant roles
5. Maximize salary outcomes — tier Premium/Elite candidates deserve Premium/Elite roles

DECISION POLICY:
- AUTO APPLY: match_score > adaptive_threshold AND confidence > 80 AND quality_score > 60
- REVIEW:     match_score > 70 OR confidence 60-80 OR quality_score 40-60
- SKIP:       anything else

OUTPUT this EXACT JSON schema:
{
  "system_status": "healthy | degraded | needs_attention",
  "status_reason": "one sentence explaining the health assessment",
  "execution_plan": {
    "should_run_pipeline": true,
    "fetch_live_jobs": true,
    "use_scrapers": false,
    "max_jobs": 40,
    "apply_score_threshold": 80,
    "review_score_threshold": 70,
    "min_quality_gate_score": 45,
    "prioritize_sources": [],
    "deprioritize_sources": [],
    "focus_roles": [],
    "avoid_company_types": [],
    "max_applications_this_run": 5
  },
  "learning_updates": [
    {
      "update_type": "threshold_change | skill_focus | source_focus | salary_adjustment | company_targeting",
      "old_value": "",
      "new_value": "",
      "reason": "",
      "confidence": "High | Medium | Low"
    }
  ],
  "actions_taken": [],
  "next_strategy": "One clear, actionable strategy sentence for the next run"
}

Rules:
- Be DATA-DRIVEN — reference actual numbers from the system state
- Be SPECIFIC — name actual skills, sources, companies, score thresholds
- max_applications_this_run should NEVER exceed 8 (quality over quantity)
- If callback_rate < 5% and applied_count > 15: system_status = "needs_attention"
- Return STRICT valid JSON only
"""


def _run_master_brain(state: SystemState, thresholds: dict) -> dict:
    """Call the LLM with full system context to generate the master execution plan."""

    state_prompt = f"""
SYSTEM STATE — {datetime.now().strftime('%Y-%m-%d %H:%M')}

=== PERFORMANCE METRICS (Last 30 Days) ===
Total tracked    : {state.total_applications}
Auto-applied     : {state.applied_count}
Callbacks        : {state.callback_count}  ({state.callback_rate}%)
Interviews       : {state.interview_count} ({state.interview_rate}%)
Offers           : {state.offer_count}     ({state.offer_rate}%)
Avg score overall: {state.avg_score_overall}/100
Avg score @ callback: {state.avg_score_at_callback}/100
Computed optimal threshold: {state.optimal_threshold}

=== ADAPTIVE THRESHOLDS ===
Recommended apply threshold  : {thresholds['apply_threshold']} (base: {os.getenv('APPLY_CONFIDENCE_THRESHOLD','85')})
Recommended review threshold : {thresholds['review_threshold']}
Threshold adjustment reason  : {thresholds.get('reason','none')}

=== SKILL INTELLIGENCE ===
Top performing skills (lead to callbacks): {state.top_skills}
Underperforming skills (0 callbacks)     : {state.weak_skills}
Critical skill gaps                       : {state.top_skill_gaps}

=== SOURCE PERFORMANCE ===
Best sources  (high callback rate): {state.best_sources}
Worst sources (0 callback rate)   : {state.worst_sources}

=== CURRENT STRATEGY ===
{state.current_strategy or "No strategy set yet"}

=== LAST INSIGHTS ===
{json.dumps(state.last_insights[:3], indent=2)}

=== SYSTEM HEALTH ===
Database: {"OK" if state.db_healthy else "ERROR"}
Notifications sent: {state.notifications_sent}
Candidate tier: {state.candidate_tier}
Candidate skills: {state.candidate_skills[:10]}
Last run: {state.last_run_timestamp or "Never"}

Generate a precise execution plan and learning updates.
Return only valid JSON.
"""

    result = safe_json_call(MASTER_BRAIN_PROMPT, state_prompt, temperature=0.1)
    return result or {}


# ── Learning Update Generator ─────────────────────────────────────────────────

def _generate_learning_updates(
    state: SystemState,
    thresholds: dict,
    llm_updates: list,
) -> list[dict]:
    """
    Merge LLM-generated learning updates with deterministic algorithmic ones.
    Returns a clean, deduplicated list of learning updates.
    """
    updates = list(llm_updates) if llm_updates else []

    # ── Deterministic updates ──────────────────────────────────────

    # 1. Threshold change
    if thresholds.get("adjusted"):
        base = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD","85"))
        new  = thresholds["apply_threshold"]
        if base != new:
            updates.append({
                "update_type": "threshold_change",
                "old_value":   f"apply_threshold={base}",
                "new_value":   f"apply_threshold={new}",
                "reason":      thresholds.get("reason",""),
                "confidence":  "High",
            })

    # 2. Skill focus — promote skills that appear in callbacks
    if state.top_skills:
        updates.append({
            "update_type": "skill_focus",
            "old_value":   "General skill matching",
            "new_value":   f"Prioritize: {', '.join(state.top_skills[:4])}",
            "reason":      "These skills consistently appear in callback applications",
            "confidence":  "High" if len(state.top_skills) >= 3 else "Medium",
        })

    # 3. Source deprioritization
    if state.worst_sources:
        updates.append({
            "update_type": "source_focus",
            "old_value":   f"All sources equal",
            "new_value":   f"Deprioritize: {', '.join(state.worst_sources)}",
            "reason":      "0% callback rate from these sources in last 30 days",
            "confidence":  "Medium",
        })

    # 4. Skill gap urgency
    if state.top_skill_gaps:
        updates.append({
            "update_type": "skill_focus",
            "old_value":   "Current skill set",
            "new_value":   f"Learn urgently: {', '.join(state.top_skill_gaps[:3])}",
            "reason":      "These skills block entry to high-callback job categories",
            "confidence":  "High",
        })

    # Deduplicate by update_type + new_value
    seen, deduped = set(), []
    for u in updates:
        key = f"{u.get('update_type')}:{u.get('new_value','')[:40]}"
        if key not in seen:
            seen.add(key)
            deduped.append(u)

    return deduped[:8]  # cap at 8 updates


# ── Execution Summary Builder ─────────────────────────────────────────────────

def _build_actions_taken(
    pipeline_result: Optional[dict],
    quality_result: Optional[dict],
    state: SystemState,
    plan: dict,
) -> list[str]:
    """Build human-readable list of actions taken this cycle."""
    actions = []

    if quality_result:
        actions.append(
            f"Quality gate processed {quality_result.get('total',0)} jobs — "
            f"{quality_result.get('approved',0)} approved, "
            f"{quality_result.get('rejected',0)} rejected"
        )

    if pipeline_result and not pipeline_result.get("error"):
        summary = pipeline_result.get("summary", {})
        actions += [
            f"Pipeline: scanned {summary.get('total_jobs',0)} jobs → ranked {summary.get('ranked_jobs',0)}",
            f"Auto-applied: {summary.get('auto_applied',0)} | Review: {summary.get('reviewed',0)} | Skipped: {summary.get('skipped',0)}",
            f"Candidate tier: {summary.get('tier','—')} | Dry run: {summary.get('dry_run',True)}",
        ]
        if summary.get("auto_fetched"):
            actions.append("Live job fetching: enabled (APIs + optional scrapers)")

    if plan.get("apply_score_threshold"):
        actions.append(
            f"Adaptive thresholds applied — "
            f"apply ≥ {plan.get('apply_score_threshold',85)} | "
            f"review ≥ {plan.get('review_score_threshold',70)}"
        )

    actions.append(f"System state snapshot taken — {state.total_applications} applications in 30-day window")

    return actions


# ── System Health Assessor ────────────────────────────────────────────────────

def _assess_system_health(state: SystemState, plan: dict) -> tuple[str, str]:
    """Compute system health status and reason."""
    if not state.db_healthy:
        return "degraded", "Database error — check DB_PATH or DATABASE_URL"

    if state.applied_count == 0:
        return "healthy", "No applications yet — system ready for first run"

    if state.callback_rate == 0 and state.applied_count >= 15:
        return "needs_attention", f"Zero callbacks after {state.applied_count} applications — thresholds or targeting need adjustment"

    if state.callback_rate < 5 and state.applied_count >= 20:
        return "needs_attention", f"Low callback rate ({state.callback_rate}%) — strategy adjustment required"

    if state.offer_rate > 0:
        return "healthy", f"System performing well — {state.offer_rate}% offer rate, {state.callback_rate}% callback rate"

    if state.callback_rate >= 10:
        return "healthy", f"Good callback rate ({state.callback_rate}%) — strategy working"

    if state.callback_rate >= 5:
        return "healthy", f"Acceptable performance — {state.callback_rate}% callbacks, continue optimizing"

    return "healthy", "Insufficient data for full health assessment — continue collecting outcomes"


# ── State Persistence ─────────────────────────────────────────────────────────

def _load_master_state() -> dict:
    try:
        if os.path.exists(MASTER_STATE_PATH):
            with open(MASTER_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_master_state(output: dict, state: SystemState, plan: dict):
    os.makedirs("db", exist_ok=True)
    cache = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "system_status":  output.get("system_status",""),
        "next_strategy":  output.get("next_strategy",""),
        "last_thresholds": {
            "apply":  plan.get("apply_score_threshold",  85),
            "review": plan.get("review_score_threshold", 70),
        },
        "performance": {
            "callback_rate":  state.callback_rate,
            "interview_rate": state.interview_rate,
            "offer_rate":     state.offer_rate,
            "applied_count":  state.applied_count,
        },
        "candidate_tier":   state.candidate_tier,
        "candidate_skills": state.candidate_skills,
        "learning_updates": output.get("learning_updates", []),
        "actions_taken":    output.get("actions_taken", []),
    }
    with open(MASTER_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, default=str)
    logger.debug(f"[MasterOrch] State saved to {MASTER_STATE_PATH}")


# ── Master Orchestrator ───────────────────────────────────────────────────────

@timer
def run_master_orchestrator(
    resume_source: Optional[str] = None,
    dry_run: bool = True,
    force_pipeline: bool = False,
    max_applications: int = 5,
    override_strategy: Optional[str] = None,
) -> dict:
    """
    Execute one full Master Orchestrator cycle.

    Cycle:
      READ STATE → PLAN → QUALITY GATE → EXECUTE → LEARN → REPORT

    Args:
        resume_source     : Path to resume (required for pipeline execution).
        dry_run           : If True, never submits browser forms.
        force_pipeline    : Run pipeline even if system says not needed.
        max_applications  : Hard cap on number of applications per run.
        override_strategy : Manual strategy text (bypasses LLM planning).

    Returns:
        Output schema dict: system_status, actions_taken, learning_updates, next_strategy
    """
    t_start = time.perf_counter()
    init_db()
    actions = []

    logger.info("══════════════════════════════════════════════════════════════")
    logger.info("  MASTER ORCHESTRATOR AI — Starting coordination cycle")
    logger.info("══════════════════════════════════════════════════════════════")

    # ── 1. READ SYSTEM STATE ─────────────────────────────────────────────────
    logger.info("[MasterOrch] STEP 1: Reading full system state...")
    state   = _read_system_state()
    actions.append(f"System state read: {state.total_applications} apps, {state.callback_rate}% callback rate")
    logger.info(
        f"[MasterOrch] State: applied={state.applied_count} callbacks={state.callback_count} "
        f"rate={state.callback_rate}% tier={state.candidate_tier}"
    )

    # ── 2. COMPUTE ADAPTIVE THRESHOLDS ──────────────────────────────────────
    logger.info("[MasterOrch] STEP 2: Computing adaptive thresholds...")
    thresholds = _compute_adaptive_thresholds(state)
    if thresholds["adjusted"]:
        logger.info(f"[MasterOrch] Thresholds adjusted: apply={thresholds['apply_threshold']} review={thresholds['review_threshold']} — {thresholds['reason']}")
        actions.append(f"Thresholds adjusted: apply≥{thresholds['apply_threshold']} ({thresholds.get('reason','')})")

    # ── 3. LLM MASTER BRAIN ──────────────────────────────────────────────────
    logger.info("[MasterOrch] STEP 3: Running LLM Master Brain planning...")
    llm_output = {}
    if not override_strategy:
        llm_output = _run_master_brain(state, thresholds)
        if llm_output:
            logger.success("[MasterOrch] LLM plan received")
            actions.append("LLM Master Brain executed: execution plan generated")
        else:
            logger.warning("[MasterOrch] LLM unavailable — using rule-based plan")
            state.llm_available = False
            actions.append("LLM unavailable — rule-based fallback plan applied")

    # ── Build execution plan (merge LLM + adaptive) ──────────────────────────
    llm_plan = llm_output.get("execution_plan", {})
    plan = {
        "should_run_pipeline":    force_pipeline or (llm_plan.get("should_run_pipeline", True) if llm_plan else bool(resume_source)),
        "fetch_live_jobs":        llm_plan.get("fetch_live_jobs",        True),
        "use_scrapers":           llm_plan.get("use_scrapers",           False),
        "max_jobs":               llm_plan.get("max_jobs",               50),
        "apply_score_threshold":  llm_plan.get("apply_score_threshold",  thresholds["apply_threshold"]),
        "review_score_threshold": llm_plan.get("review_score_threshold", thresholds["review_threshold"]),
        "min_quality_gate_score": llm_plan.get("min_quality_gate_score", 40),
        "prioritize_sources":     llm_plan.get("prioritize_sources",     state.best_sources),
        "deprioritize_sources":   llm_plan.get("deprioritize_sources",   state.worst_sources),
        "focus_roles":            llm_plan.get("focus_roles",            []),
        "avoid_company_types":    llm_plan.get("avoid_company_types",    []),
        "max_applications_this_run": min(
            llm_plan.get("max_applications_this_run", max_applications),
            max_applications,  # hard cap from CLI
        ),
    }

    # ── 4. EXECUTE PIPELINE ──────────────────────────────────────────────────
    pipeline_result  = None
    quality_result   = None

    if plan["should_run_pipeline"] and resume_source:
        logger.info(f"[MasterOrch] STEP 4: Executing pipeline (apply≥{plan['apply_score_threshold']} review≥{plan['review_score_threshold']})...")

        # Inject adaptive thresholds into environment for orchestrator
        os.environ["APPLY_CONFIDENCE_THRESHOLD"]  = str(plan["apply_score_threshold"])
        os.environ["REVIEW_CONFIDENCE_THRESHOLD"] = str(plan["review_score_threshold"])

        try:
            from core.orchestrator import run_pipeline

            pipeline_result = run_pipeline(
                resume_source = resume_source,
                dry_run       = dry_run,
                run_learning  = True,
                auto_fetch    = plan["fetch_live_jobs"],
                use_scrapers  = plan["use_scrapers"],
                max_jobs      = plan["max_jobs"],
            )

            # Post-pipeline: update state with candidate data
            parsed = pipeline_result.get("parsed_resume", {})
            intel  = pipeline_result.get("candidate_intel", {})
            if parsed:
                state.candidate_skills = (
                    parsed.get("primary_skills", []) +
                    parsed.get("secondary_skills", [])
                )[:20]
            if intel:
                state.candidate_tier = intel.get("candidate_tier", state.candidate_tier)

            # Quality gate on ranked jobs
            ranked = pipeline_result.get("ranked_jobs", [])
            if ranked:
                approved, rejected = quality_gate(ranked, state, plan["min_quality_gate_score"])
                quality_result = {
                    "total":    len(ranked),
                    "approved": len(approved),
                    "rejected": len(rejected),
                    "rejection_reasons": Counter(
                        issue
                        for j in rejected
                        for issue in j.get("_quality_issues", [])
                    ).most_common(5),
                }
                actions.append(f"Quality gate: {len(approved)}/{len(ranked)} jobs passed filtering")
                logger.info(f"[MasterOrch] Quality gate: {quality_result}")

            if pipeline_result.get("error"):
                actions.append(f"Pipeline error: {pipeline_result['error']}")
            else:
                s = pipeline_result.get("summary", {})
                actions.append(
                    f"Pipeline complete: {s.get('auto_applied',0)} applied, "
                    f"{s.get('reviewed',0)} review, {s.get('skipped',0)} skipped"
                )

        except Exception as exc:
            logger.error(f"[MasterOrch] Pipeline execution error: {exc}")
            actions.append(f"Pipeline error (non-fatal): {str(exc)[:100]}")
            pipeline_result = {"error": str(exc)}
    else:
        reason = "No resume provided" if not resume_source else "Plan decided pipeline not needed"
        actions.append(f"Pipeline skipped: {reason}")
        logger.info(f"[MasterOrch] Pipeline skipped — {reason}")

    # ── 5. GENERATE LEARNING UPDATES ────────────────────────────────────────
    logger.info("[MasterOrch] STEP 5: Generating learning updates...")
    llm_learning = llm_output.get("learning_updates", [])
    learning_updates = _generate_learning_updates(state, thresholds, llm_learning)
    logger.info(f"[MasterOrch] {len(learning_updates)} learning updates generated")

    # ── 6. COMPUTE FINAL STRATEGY ────────────────────────────────────────────
    next_strategy = (
        override_strategy or
        llm_output.get("next_strategy") or
        state.current_strategy or
        f"Target jobs with match_score ≥ {plan['apply_score_threshold']} "
        f"from {', '.join(state.best_sources[:2]) if state.best_sources else 'all sources'}. "
        f"Prioritize skills: {', '.join(state.top_skills[:3]) if state.top_skills else 'core ML/AI stack'}."
    )

    # Persist updated strategy
    if learning_updates:
        insight_texts = [u.get("reason","") for u in learning_updates[:3] if u.get("reason")]
        save_strategy(insight_texts, next_strategy)

    # ── 7. ASSESS HEALTH ─────────────────────────────────────────────────────
    final_health, health_reason = _assess_system_health(state, plan)
    # Override with LLM assessment if available
    if llm_output.get("system_status") in ("healthy","degraded","needs_attention"):
        final_health = llm_output["system_status"]
        health_reason = llm_output.get("status_reason", health_reason)

    # ── 8. BUILD FINAL ACTIONS LIST ──────────────────────────────────────────
    all_actions = _build_actions_taken(pipeline_result, quality_result, state, plan) + actions

    # ── 9. ASSEMBLE OUTPUT ───────────────────────────────────────────────────
    duration_ms = int((time.perf_counter() - t_start) * 1000)

    output = {
        "system_status":   final_health,
        "status_reason":   health_reason,
        "actions_taken":   all_actions,
        "learning_updates": learning_updates,
        "next_strategy":   next_strategy,
        "execution_plan":  plan,
        "performance": {
            "callback_rate":  state.callback_rate,
            "interview_rate": state.interview_rate,
            "offer_rate":     state.offer_rate,
            "applied_30d":    state.applied_count,
            "callbacks_30d":  state.callback_count,
            "optimal_threshold": state.optimal_threshold,
        },
        "quality_gate":  quality_result or {},
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "duration_ms":   duration_ms,
    }

    # ── 10. PERSIST & NOTIFY ──────────────────────────────────────────────────
    _save_master_state(output, state, plan)

    # Send daily summary if offer/interview this cycle
    if pipeline_result and not pipeline_result.get("error"):
        s = pipeline_result.get("summary", {})
        if s.get("auto_applied", 0) > 0:
            try:
                notify_daily_summary({
                    "applied":    s.get("auto_applied", 0),
                    "interviews": state.interview_count,
                    "offers":     state.offer_count,
                    "new_jobs":   s.get("total_jobs", 0),
                })
            except Exception as _ne:
                logger.debug(f"[MasterOrch] Notification error (non-fatal): {_ne}")

    logger.success(
        f"[MasterOrch] COMPLETE in {duration_ms}ms | "
        f"Status: {final_health.upper()} | "
        f"{len(learning_updates)} updates | "
        f"Strategy: {next_strategy[:60]}..."
    )
    for i, action in enumerate(all_actions[:5], 1):
        logger.info(f"  [{i}] {action}")

    return output


# ── Lightweight Status Reader (no pipeline execution) ─────────────────────────

def get_master_status() -> dict:
    """
    Return current master orchestrator status without running any pipeline.
    Used by dashboard and API status endpoints.
    """
    cached = _load_master_state()
    if cached:
        return cached

    init_db()
    state = _read_system_state()
    health, reason = _assess_system_health(state, {})

    return {
        "system_status":  health,
        "status_reason":  reason,
        "performance": {
            "callback_rate":  state.callback_rate,
            "interview_rate": state.interview_rate,
            "offer_rate":     state.offer_rate,
            "applied_30d":    state.applied_count,
        },
        "next_strategy":   state.current_strategy or "No strategy set yet",
        "learning_updates": [],
        "actions_taken":   [],
        "timestamp":       state.last_run_timestamp,
        "candidate_tier":  state.candidate_tier,
    }
