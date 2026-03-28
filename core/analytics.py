"""
Data Analytics AI
Analyzes job application performance, detects skill gaps,
and generates actionable learning + targeting recommendations.

Output schema:
{
  "insights":            [],
  "skill_gap_analysis":  [],
  "recommended_learning":[],
  "strategy_update":     ""
}
"""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import Counter, defaultdict

from core.db_adapter import _get_conn
from core.memory import init_db, save_strategy
from utils.helpers import safe_json_call, logger, timer

# ── Market demand reference (2025 India tech market) ─────────────────────────
# Used to enrich skill gap analysis when DB data is sparse

MARKET_DEMAND = {
    # ML / AI
    "python":         {"demand": "Very High", "avg_salary_boost": 25, "learning_weeks": 4},
    "tensorflow":     {"demand": "High",      "avg_salary_boost": 30, "learning_weeks": 6},
    "pytorch":        {"demand": "Very High", "avg_salary_boost": 32, "learning_weeks": 6},
    "scikit-learn":   {"demand": "High",      "avg_salary_boost": 20, "learning_weeks": 3},
    "huggingface":    {"demand": "Very High", "avg_salary_boost": 35, "learning_weeks": 4},
    "mlflow":         {"demand": "High",      "avg_salary_boost": 28, "learning_weeks": 2},
    "langchain":      {"demand": "Very High", "avg_salary_boost": 38, "learning_weeks": 3},
    "openai api":     {"demand": "Very High", "avg_salary_boost": 35, "learning_weeks": 2},
    "transformers":   {"demand": "Very High", "avg_salary_boost": 35, "learning_weeks": 5},
    "kubeflow":       {"demand": "Medium",    "avg_salary_boost": 30, "learning_weeks": 5},
    # Data
    "sql":            {"demand": "Very High", "avg_salary_boost": 15, "learning_weeks": 3},
    "pandas":         {"demand": "High",      "avg_salary_boost": 18, "learning_weeks": 2},
    "spark":          {"demand": "High",      "avg_salary_boost": 30, "learning_weeks": 6},
    "tableau":        {"demand": "High",      "avg_salary_boost": 22, "learning_weeks": 2},
    "power bi":       {"demand": "High",      "avg_salary_boost": 20, "learning_weeks": 2},
    # Cloud / DevOps
    "aws":            {"demand": "Very High", "avg_salary_boost": 35, "learning_weeks": 8},
    "docker":         {"demand": "Very High", "avg_salary_boost": 28, "learning_weeks": 3},
    "kubernetes":     {"demand": "High",      "avg_salary_boost": 35, "learning_weeks": 6},
    "terraform":      {"demand": "High",      "avg_salary_boost": 32, "learning_weeks": 4},
    # Web / Backend
    "fastapi":        {"demand": "High",      "avg_salary_boost": 22, "learning_weeks": 2},
    "django":         {"demand": "Medium",    "avg_salary_boost": 18, "learning_weeks": 3},
    "react":          {"demand": "Very High", "avg_salary_boost": 25, "learning_weeks": 5},
    "node.js":        {"demand": "High",      "avg_salary_boost": 22, "learning_weeks": 4},
    # Databases
    "postgresql":     {"demand": "High",      "avg_salary_boost": 20, "learning_weeks": 3},
    "mongodb":        {"demand": "High",      "avg_salary_boost": 20, "learning_weeks": 2},
    "redis":          {"demand": "Medium",    "avg_salary_boost": 18, "learning_weeks": 2},
}

LEARNING_RESOURCES = {
    "python":       ["Python.org Official Docs", "CS50P (Harvard, free)", "Kaggle Learn Python"],
    "tensorflow":   ["TF Official Tutorials", "DeepLearning.AI TF Specialization (Coursera)", "Kaggle Intro to DL"],
    "pytorch":      ["PyTorch Official Tutorials", "fast.ai Practical DL (free)", "Udacity Deep Learning ND"],
    "huggingface":  ["HuggingFace NLP Course (free)", "HF Transformers docs", "Kaggle NLP notebooks"],
    "mlflow":       ["MLflow Official Docs", "Databricks MLflow Tutorial (free)", "YouTube: MLflow in 30min"],
    "langchain":    ["LangChain Docs", "DeepLearning.AI LangChain Short Course (free)", "GitHub: langchain-examples"],
    "sql":          ["Mode SQL Tutorial (free)", "W3Schools SQL", "LeetCode SQL 50 (free)"],
    "spark":        ["Spark Official Docs", "Databricks Academy (free tier)", "Coursera IBM Data Engineering"],
    "aws":          ["AWS Free Tier + Labs", "AWS Certified ML Specialty", "A Cloud Guru (free trial)"],
    "docker":       ["Docker Official Getting Started", "YouTube: TechWorld with Nana Docker", "Play with Docker (free)"],
    "kubernetes":   ["Kubernetes Official Docs", "KodeKloud CKA Course", "k8s The Hard Way (GitHub)"],
    "tableau":      ["Tableau Public (free)", "Tableau eLearning", "Udemy Tableau 2024 Bestseller"],
    "fastapi":      ["FastAPI Official Docs", "TestDriven.io FastAPI TDD (free)", "YouTube: FastAPI Crash Course"],
    "react":        ["React Official Docs", "Full Stack Open (free)", "Scrimba React Course (free)"],
}

ROLE_SKILL_MAP = {
    "Machine Learning Engineer":    ["python", "pytorch", "scikit-learn", "mlflow", "docker", "sql"],
    "Data Scientist":               ["python", "pandas", "sql", "tableau", "statistics", "scikit-learn"],
    "NLP Engineer":                 ["python", "huggingface", "transformers", "pytorch", "nlp"],
    "MLOps Engineer":               ["python", "mlflow", "kubeflow", "docker", "kubernetes", "aws"],
    "AI Research Engineer":         ["python", "pytorch", "transformers", "huggingface", "langchain"],
    "Data Engineer":                ["python", "spark", "sql", "aws", "airflow", "docker"],
    "Backend Developer":            ["python", "fastapi", "postgresql", "docker", "redis"],
    "Full Stack Developer":         ["react", "node.js", "postgresql", "docker", "typescript"],
    "Data Analyst":                 ["sql", "python", "tableau", "power bi", "excel"],
    "Cloud Engineer":               ["aws", "terraform", "docker", "kubernetes", "linux"],
}

# ── Statistical Data Gatherer ─────────────────────────────────────────────────

def _gather_raw_stats() -> dict:
    """Pull all analytical data from the DB in one pass."""
    init_db()
    with _get_conn() as conn:

        # All applications (clean subset of fields)
        apps = [dict(r) for r in conn.execute(
            """SELECT id, job_title, company, match_score, confidence_score,
                      execution_status, outcome, callback_received,
                      skills_matched, skills_missing, source, location,
                      candidate_tier, applied_at, interview_round
               FROM applications ORDER BY applied_at DESC"""
        ).fetchall()]

        # Skill performance
        skills = [dict(r) for r in conn.execute(
            """SELECT skill, appearances, callbacks, interviews, offers,
                      ROUND(CAST(interviews AS FLOAT)/MAX(appearances,1)*100,1) AS interview_rate,
                      ROUND(CAST(offers     AS FLOAT)/MAX(appearances,1)*100,1) AS offer_rate
               FROM skill_performance WHERE appearances > 0
               ORDER BY interviews DESC, appearances DESC"""
        ).fetchall()]

        # Missing skills frequency — skills that appeared in jobs we MISSED
        missing_raw = conn.execute(
            "SELECT skills_missing FROM applications WHERE skills_missing IS NOT NULL AND skills_missing != '[]'"
        ).fetchall()

        # Outcome counts
        outcomes = dict(conn.execute(
            "SELECT outcome, COUNT(*) FROM applications GROUP BY outcome"
        ).fetchall())

        # Score distribution per outcome
        score_by_outcome = defaultdict(list)
        for r in conn.execute("SELECT outcome, match_score FROM applications WHERE match_score > 0"):
            score_by_outcome[r[0]].append(r[1])

        # Company patterns
        companies = [dict(r) for r in conn.execute(
            """SELECT company,
                      COUNT(*) as total,
                      SUM(CASE WHEN outcome IN('interview','offer') THEN 1 ELSE 0 END) as callbacks,
                      AVG(match_score) as avg_score
               FROM applications WHERE company != ''
               GROUP BY company HAVING total > 0
               ORDER BY callbacks DESC, avg_score DESC LIMIT 10"""
        ).fetchall()]

        # Source platform patterns
        sources = [dict(r) for r in conn.execute(
            """SELECT source,
                      COUNT(*) as total,
                      SUM(CASE WHEN outcome IN('interview','offer') THEN 1 ELSE 0 END) as callbacks,
                      ROUND(CAST(SUM(CASE WHEN outcome IN('interview','offer') THEN 1 ELSE 0 END)
                            AS FLOAT)/MAX(COUNT(*),1)*100,1) as callback_rate
               FROM applications WHERE source IS NOT NULL AND source != ''
               GROUP BY source ORDER BY callback_rate DESC"""
        ).fetchall()]

        # Latest analytics log entry
        last_analytics = conn.execute(
            "SELECT * FROM analytics_log ORDER BY logged_at DESC LIMIT 1"
        ).fetchone()

    # Aggregate missing skills
    missing_counter: Counter = Counter()
    for row in missing_raw:
        try:
            for sk in json.loads(row[0] or "[]"):
                missing_counter[sk.lower()] += 1
        except Exception:
            pass

    # Avg score per outcome
    avg_score_by_outcome = {
        k: round(sum(v) / len(v), 1) for k, v in score_by_outcome.items() if v
    }

    return {
        "applications":         apps,
        "skills":               skills,
        "missing_skills":       missing_counter.most_common(20),
        "outcomes":             outcomes,
        "avg_score_by_outcome": avg_score_by_outcome,
        "companies":            companies,
        "sources":              sources,
        "last_analytics":       dict(last_analytics) if last_analytics else None,
    }


# ── Pattern Detector ──────────────────────────────────────────────────────────

def _detect_patterns(raw: dict) -> dict:
    """
    Pure algorithmic pattern detection — no LLM needed.
    Returns structured pattern data for the LLM prompt and direct reporting.
    """
    apps      = raw["applications"]
    outcomes  = raw["outcomes"]
    skills    = raw["skills"]
    missing   = dict(raw["missing_skills"])

    total    = len(apps)
    applied  = sum(1 for a in apps if a.get("execution_status") == "AUTO_APPLY")
    callbacks = sum(1 for a in apps if a.get("outcome") in ("interview", "offer"))

    # Score threshold analysis
    score_buckets = {
        "90-100": {"apps": 0, "callbacks": 0},
        "80-89":  {"apps": 0, "callbacks": 0},
        "70-79":  {"apps": 0, "callbacks": 0},
        "60-69":  {"apps": 0, "callbacks": 0},
        "<60":    {"apps": 0, "callbacks": 0},
    }
    for a in apps:
        sc = a.get("match_score", 0) or 0
        got_cb = a.get("outcome") in ("interview", "offer")
        if sc >= 90:    bucket = "90-100"
        elif sc >= 80:  bucket = "80-89"
        elif sc >= 70:  bucket = "70-79"
        elif sc >= 60:  bucket = "60-69"
        else:           bucket = "<60"
        score_buckets[bucket]["apps"] += 1
        if got_cb: score_buckets[bucket]["callbacks"] += 1

    for k, v in score_buckets.items():
        v["callback_rate"] = round(v["callbacks"] / max(v["apps"], 1) * 100, 1)

    # Optimal score threshold — highest bucket with ≥ 50% callback rate
    optimal_threshold = 60
    for bucket_name in ["90-100", "80-89", "70-79", "60-69", "<60"]:
        b = score_buckets[bucket_name]
        if b["callback_rate"] >= 50 and b["apps"] >= 1:
            optimal_threshold = int(bucket_name.split("-")[0].replace("<", ""))
            break

    # Skill gap severity — skills frequently missing + high market demand
    skill_gaps = []
    for skill, miss_count in raw["missing_skills"]:
        market = MARKET_DEMAND.get(skill, {})
        demand_score = {"Very High": 4, "High": 3, "Medium": 2, "Low": 1}.get(
            market.get("demand", "Low"), 1
        )
        severity_score = miss_count * demand_score
        skill_gaps.append({
            "skill":                skill,
            "appears_in_missed_jobs": miss_count,
            "market_demand":        market.get("demand", "Unknown"),
            "salary_boost_pct":     market.get("avg_salary_boost", 0),
            "learning_weeks":       market.get("learning_weeks", 4),
            "gap_severity":         "High" if severity_score >= 8
                                    else "Medium" if severity_score >= 4
                                    else "Low",
            "severity_score":       severity_score,
        })
    skill_gaps.sort(key=lambda x: x["severity_score"], reverse=True)

    # Top performing skills (from skill_performance table)
    top_skills = [s for s in skills if s.get("interviews", 0) > 0]

    # Underperforming skills (high appearances, zero callbacks)
    weak_skills = [
        s["skill"] for s in skills
        if s.get("appearances", 0) >= 3 and s.get("interviews", 0) == 0
    ]

    # Callback patterns — which job attributes correlate with callbacks
    callback_jobs = [a for a in apps if a.get("outcome") in ("interview", "offer")]
    callback_titles = Counter(
        a.get("job_title", "")[:40] for a in callback_jobs
    ).most_common(5)
    callback_score_avg = (
        round(sum(a.get("match_score", 0) for a in callback_jobs) / max(len(callback_jobs), 1), 1)
        if callback_jobs else 0
    )

    return {
        "total":             total,
        "applied":           applied,
        "callbacks":         callbacks,
        "callback_rate":     round(callbacks / max(applied, 1) * 100, 1),
        "score_buckets":     score_buckets,
        "optimal_threshold": optimal_threshold,
        "skill_gaps":        skill_gaps[:15],
        "top_skills":        top_skills[:8],
        "weak_skills":       weak_skills[:8],
        "callback_titles":   [t[0] for t in callback_titles],
        "callback_score_avg":callback_score_avg,
        "companies":         raw["companies"],
        "sources":           raw["sources"],
        "avg_score_by_outcome": raw["avg_score_by_outcome"],
    }


# ── Learning Recommendation Builder ──────────────────────────────────────────

def _build_learning_recs(skill_gaps: list[dict]) -> list[dict]:
    """
    Build structured learning recommendations for the top-priority skill gaps.
    """
    recs = []
    for i, gap in enumerate(skill_gaps[:8], 1):
        skill = gap["skill"]
        resources = LEARNING_RESOURCES.get(skill, [
            f"Search: '{skill} tutorial site:github.com'",
            f"YouTube: '{skill} crash course'",
            f"Official docs: {skill}.io or docs.{skill}.com",
        ])
        recs.append({
            "priority":            i,
            "skill":               skill,
            "gap_severity":        gap["gap_severity"],
            "market_demand":       gap["market_demand"],
            "estimated_weeks":     gap["learning_weeks"],
            "salary_boost_pct":    gap["salary_boost_pct"],
            "appears_in_n_jobs":   gap["appears_in_missed_jobs"],
            "resources":           resources[:3],
            "expected_score_boost": min(gap["appears_in_missed_jobs"] * 2, 15),
        })
    return recs


# ── Role Targeting ────────────────────────────────────────────────────────────

def _recommend_target_roles(patterns: dict, candidate_skills: list[str]) -> list[dict]:
    """
    Suggest target roles based on which role skill requirements best match
    the candidate's current skills + callback patterns.
    """
    cs = {s.lower() for s in candidate_skills}
    roles = []

    for role, required in ROLE_SKILL_MAP.items():
        matched   = [s for s in required if s in cs]
        missing   = [s for s in required if s not in cs]
        fit_pct   = round(len(matched) / max(len(required), 1) * 100)
        # Boost roles that appear in callback_titles
        title_boost = sum(
            10 for t in patterns.get("callback_titles", [])
            if role.lower() in t.lower() or t.lower() in role.lower()
        )
        score = fit_pct + title_boost
        roles.append({
            "role":          role,
            "fit_score":     fit_pct,
            "boost_score":   score,
            "matched_skills":matched,
            "missing_skills":missing[:3],
            "readiness":     "Ready" if fit_pct >= 70 else
                             "Almost Ready" if fit_pct >= 50 else
                             "Needs Work",
        })

    roles.sort(key=lambda x: x["boost_score"], reverse=True)
    return roles[:6]


# ── LLM Analysis ─────────────────────────────────────────────────────────────

ANALYTICS_SYSTEM_PROMPT = """
You are a Data Analytics AI specializing in job application performance analysis.

Your job is to analyze real application data and produce precise, actionable intelligence.

ANALYSIS GOALS:
1. Identify exactly which job types, companies, and match score ranges yield callbacks
2. Detect specific skill gaps blocking interview success
3. Recommend concrete learning paths (specific courses/tools)
4. Suggest realistic target roles based on current skill profile
5. Provide one clear strategy update sentence

RULES:
- Be DATA-DRIVEN: reference specific numbers from the input
- Be SPECIFIC: name actual skills, companies, score thresholds
- Be ACTIONABLE: every insight must lead to a concrete next step
- No generic advice ("work hard", "practice more")
- Return STRICT valid JSON only

Return this EXACT schema:
{
  "insights": [
    {
      "type": "callback_pattern | skill_gap | score_insight | platform_insight | role_insight",
      "title": "",
      "finding": "",
      "action": "",
      "priority": "High | Medium | Low",
      "evidence": ""
    }
  ],
  "skill_gap_analysis": [
    {
      "skill": "",
      "gap_severity": "High | Medium | Low",
      "appears_in_missed_jobs": 0,
      "market_demand": "",
      "learning_difficulty": "Beginner | Intermediate | Advanced",
      "why_critical": ""
    }
  ],
  "recommended_learning": [
    {
      "skill": "",
      "priority": 0,
      "why_now": "",
      "estimated_time": "",
      "free_resource": "",
      "expected_impact": ""
    }
  ],
  "target_roles": [],
  "strategy_update": "",
  "optimal_score_threshold": 0,
  "weak_areas": []
}
"""


def _build_llm_prompt(patterns: dict, raw: dict) -> str:
    apps       = raw["applications"]
    applied    = patterns["applied"]
    callbacks  = patterns["callbacks"]
    skill_gaps = patterns["skill_gaps"][:10]
    top_skills = patterns["top_skills"][:8]
    weak_skills= patterns["weak_skills"][:6]

    # Sample of callback jobs
    cb_sample = [
        {k: a.get(k) for k in ("job_title", "company", "match_score", "skills_matched", "outcome")}
        for a in apps if a.get("outcome") in ("interview", "offer")
    ][:8]

    # Sample of rejected jobs
    rj_sample = [
        {k: a.get(k) for k in ("job_title", "match_score", "skills_missing", "outcome")}
        for a in apps if a.get("outcome") == "rejected"
    ][:6]

    return f"""
Analyze this job application performance data and generate insights.

=== SUMMARY ===
Total applications: {patterns['total']}
Auto-applied: {applied} | Callbacks: {callbacks} | Callback rate: {patterns['callback_rate']}%
Avg match score at callback: {patterns['callback_score_avg']}/100

Score effectiveness:
{json.dumps(patterns['score_buckets'], indent=2)}

=== SKILL DATA ===
Top skills (led to interviews): {json.dumps(top_skills, indent=2)}
Weak skills (0 callbacks despite appearing): {weak_skills}
Most frequent missing skills in missed jobs: {json.dumps(skill_gaps[:8], indent=2)}

=== CALLBACK JOBS (got interview/offer) ===
{json.dumps(cb_sample, indent=2)}

=== REJECTED JOBS ===
{json.dumps(rj_sample, indent=2)}

=== SOURCE PERFORMANCE ===
{json.dumps(patterns['sources'], indent=2)}

=== COMPANY PATTERNS ===
{json.dumps(patterns['companies'][:6], indent=2)}

Generate 5-8 specific, data-driven insights.
For each skill gap, explain specifically WHY it is critical.
For each recommended learning item, name a specific free resource.
Return only valid JSON.
"""


# ── Result Persistence ────────────────────────────────────────────────────────

def _save_analytics(result: dict, duration_ms: int, total: int):
    """Cache the analytics result in the DB."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO analytics_log
               (logged_at, total_analyzed, insights, skill_gap_analysis,
                recommended_learning, strategy_update, target_roles,
                callback_patterns, weak_areas, run_duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now, total,
                json.dumps(result.get("insights", [])),
                json.dumps(result.get("skill_gap_analysis", [])),
                json.dumps(result.get("recommended_learning", [])),
                result.get("strategy_update", ""),
                json.dumps(result.get("target_roles", [])),
                json.dumps(result.get("callback_patterns", {})),
                json.dumps(result.get("weak_areas", [])),
                duration_ms,
            )
        )
    logger.debug(f"[Analytics] Result cached (duration={duration_ms}ms, total={total})")


def get_latest_analytics() -> Optional[dict]:
    """Return the most recent analytics result from the log."""
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM analytics_log ORDER BY logged_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in ("insights", "skill_gap_analysis", "recommended_learning",
                  "target_roles", "callback_patterns", "weak_areas"):
        try:
            d[field] = json.loads(d.get(field) or "[]")
        except Exception:
            d[field] = []
    return d


# ── Main Public API ───────────────────────────────────────────────────────────

@timer
def run_analysis(
    candidate_skills: Optional[list[str]] = None,
    force_llm: bool = True,
) -> dict:
    """
    Run the full Data Analytics pipeline.

    Pipeline:
        GATHER(DB) -> DETECT PATTERNS -> BUILD GAPS -> LLM INSIGHTS -> ASSEMBLE -> CACHE

    Args:
        candidate_skills: Optional list of candidate's current skills for role matching.
        force_llm:        If False, return cached result if < 24hrs old.

    Returns:
        Analytics result matching the output schema.
    """
    t_start = time.perf_counter()
    init_db()

    # Check cache
    if not force_llm:
        cached = get_latest_analytics()
        if cached:
            age_hours = (
                datetime.now(timezone.utc) -
                datetime.fromisoformat(cached.get("logged_at", "2000-01-01T00:00:00+00:00"))
            ).total_seconds() / 3600
            if age_hours < 24:
                logger.info(f"[Analytics] Using cached result ({age_hours:.1f}h old)")
                return cached

    logger.info("[Analytics] Starting full analysis pipeline...")

    # 1. Gather raw stats from DB
    raw = _gather_raw_stats()
    apps = raw["applications"]

    if not apps:
        logger.warning("[Analytics] No application data found. Returning empty analysis.")
        return _empty_result()

    # 2. Algorithmic pattern detection
    patterns = _detect_patterns(raw)
    logger.info(
        f"[Analytics] Patterns: applied={patterns['applied']} "
        f"callbacks={patterns['callbacks']} "
        f"rate={patterns['callback_rate']}% "
        f"skill_gaps={len(patterns['skill_gaps'])}"
    )

    # 3. Structured learning recommendations (deterministic)
    learning_recs = _build_learning_recs(patterns["skill_gaps"])

    # 4. Role recommendations (deterministic)
    skills_for_roles = candidate_skills or [s["skill"] for s in raw["skills"] if s.get("appearances", 0) > 0]
    target_roles = _recommend_target_roles(patterns, skills_for_roles)

    # 5. LLM deep analysis
    llm_result = {}
    if patterns["total"] > 0:
        logger.info("[Analytics] Running LLM deep analysis...")
        llm_result = safe_json_call(
            ANALYTICS_SYSTEM_PROMPT,
            _build_llm_prompt(patterns, raw),
            temperature=0.15,
        )
        if not llm_result:
            logger.warning("[Analytics] LLM returned empty — using algorithmic results only.")

    # 6. Assemble final result (merge LLM + deterministic)
    insights = llm_result.get("insights", []) or _build_fallback_insights(patterns)
    skill_gaps_final = _merge_skill_gaps(
        llm_result.get("skill_gap_analysis", []),
        patterns["skill_gaps"]
    )
    learning_final = llm_result.get("recommended_learning", []) or [
        {
            "skill":           r["skill"],
            "priority":        r["priority"],
            "why_now":         f"Appears in {r['appears_in_n_jobs']} missed jobs",
            "estimated_time":  f"{r['estimated_weeks']} weeks",
            "free_resource":   r["resources"][0] if r["resources"] else "Search online",
            "expected_impact": f"+{r['expected_score_boost']} pts to match score",
        }
        for r in learning_recs
    ]
    target_roles_final = llm_result.get("target_roles", []) or [r["role"] for r in target_roles if r["fit_score"] >= 50]
    strategy = (
        llm_result.get("strategy_update", "") or
        f"Focus on {patterns['skill_gaps'][0]['skill'] if patterns['skill_gaps'] else 'core skills'} "
        f"and target roles with match_score >= {patterns['optimal_threshold']}."
    )
    weak_areas = llm_result.get("weak_areas", []) or patterns["weak_skills"]

    result = {
        # ── Core output schema ──────────────────────────────────────────────
        "insights":            insights,
        "skill_gap_analysis":  skill_gaps_final,
        "recommended_learning":learning_final,
        "strategy_update":     strategy,

        # ── Extended fields ─────────────────────────────────────────────────
        "target_roles":        target_roles_final,
        "role_fit_analysis":   target_roles,
        "callback_patterns": {
            "titles":         patterns["callback_titles"],
            "avg_score":      patterns["callback_score_avg"],
            "optimal_threshold": patterns["optimal_threshold"],
            "score_buckets":  patterns["score_buckets"],
        },
        "weak_areas":          weak_areas,
        "optimal_score_threshold": llm_result.get("optimal_score_threshold", patterns["optimal_threshold"]),
        "summary_stats": {
            "total":          patterns["total"],
            "applied":        patterns["applied"],
            "callbacks":      patterns["callbacks"],
            "callback_rate":  patterns["callback_rate"],
        },
        "source_performance":  raw["sources"],
        "top_companies":       raw["companies"],
    }

    # 7. Persist & log
    duration_ms = int((time.perf_counter() - t_start) * 1000)
    _save_analytics(result, duration_ms, patterns["total"])
    save_strategy(
        [i.get("finding", i) if isinstance(i, dict) else i for i in insights[:5]],
        strategy,
    )

    logger.success(
        f"[Analytics] Done in {duration_ms}ms | "
        f"{len(insights)} insights | "
        f"{len(skill_gaps_final)} skill gaps | "
        f"{len(learning_final)} learning recs"
    )

    for i, ins in enumerate(insights[:5], 1):
        title = ins.get("title", ins) if isinstance(ins, dict) else str(ins)
        logger.info(f"  [{i}] {title}")

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_result() -> dict:
    return {
        "insights": [{
            "type":     "no_data",
            "title":    "No Application Data Yet",
            "finding":  "The analytics engine needs at least 1 application to analyze.",
            "action":   "Run the agent pipeline or use 'python main.py track seed' to load demo data.",
            "priority": "High",
            "evidence": "0 records in applications table",
        }],
        "skill_gap_analysis":  [],
        "recommended_learning":[],
        "strategy_update":     "Start by running the agent on your resume to collect application data.",
        "target_roles":        [],
        "role_fit_analysis":   [],
        "callback_patterns":   {},
        "weak_areas":          [],
        "optimal_score_threshold": 70,
        "summary_stats":       {"total": 0, "applied": 0, "callbacks": 0, "callback_rate": 0},
        "source_performance":  [],
        "top_companies":       [],
    }


def _build_fallback_insights(patterns: dict) -> list[dict]:
    """Generate rule-based insights when LLM is unavailable."""
    insights = []

    if patterns["callback_rate"] > 0:
        insights.append({
            "type":     "callback_pattern",
            "title":    f"{patterns['callback_rate']}% Callback Rate Detected",
            "finding":  f"You received callbacks on {patterns['callbacks']} of {patterns['applied']} applications.",
            "action":   f"Focus on roles scoring {patterns['optimal_threshold']}+ for best results.",
            "priority": "High",
            "evidence": f"Avg match score at callback: {patterns['callback_score_avg']}",
        })

    for bucket, data in patterns["score_buckets"].items():
        if data["callback_rate"] >= 60 and data["apps"] >= 2:
            insights.append({
                "type":     "score_insight",
                "title":    f"Score Range {bucket} Has {data['callback_rate']}% Callback Rate",
                "finding":  f"{data['apps']} applications in {bucket} range, {data['callbacks']} callbacks.",
                "action":   f"Prioritize jobs where your match score falls in {bucket}.",
                "priority": "High",
                "evidence": f"{data['callbacks']}/{data['apps']} applications converted",
            })
            break

    if patterns["skill_gaps"]:
        top_gap = patterns["skill_gaps"][0]
        insights.append({
            "type":     "skill_gap",
            "title":    f"Critical Gap: {top_gap['skill'].title()}",
            "finding":  f"'{top_gap['skill']}' appeared in {top_gap['appears_in_missed_jobs']} jobs you missed.",
            "action":   f"Learn {top_gap['skill']} ({top_gap['learning_weeks']} weeks) for ~{top_gap['salary_boost_pct']}% salary boost.",
            "priority": "High",
            "evidence": f"Market demand: {top_gap['market_demand']}",
        })

    if patterns["weak_skills"]:
        insights.append({
            "type":     "skill_gap",
            "title":    "Underperforming Skills Detected",
            "finding":  f"Skills {patterns['weak_skills'][:3]} appear often but yield 0 callbacks.",
            "action":   "These may be listed superficially — deepen project experience with each.",
            "priority": "Medium",
            "evidence": "0 interview conversions despite appearances",
        })

    return insights


def _merge_skill_gaps(llm_gaps: list[dict], stat_gaps: list[dict]) -> list[dict]:
    """
    Merge LLM skill gaps with statistically derived gaps.
    LLM gaps take priority; fill missing ones from stat analysis.
    """
    if llm_gaps:
        llm_skills = {g.get("skill", "").lower() for g in llm_gaps}
        for sg in stat_gaps[:5]:
            if sg["skill"] not in llm_skills:
                llm_gaps.append({
                    "skill":                  sg["skill"],
                    "gap_severity":           sg["gap_severity"],
                    "appears_in_missed_jobs": sg["appears_in_missed_jobs"],
                    "market_demand":          sg["market_demand"],
                    "learning_difficulty":    "Intermediate",
                    "why_critical":           f"Appears in {sg['appears_in_missed_jobs']} missed jobs. Market demand: {sg['market_demand']}.",
                })
        return llm_gaps[:12]
    return [
        {
            "skill":                  g["skill"],
            "gap_severity":           g["gap_severity"],
            "appears_in_missed_jobs": g["appears_in_missed_jobs"],
            "market_demand":          g["market_demand"],
            "learning_difficulty":    "Intermediate",
            "why_critical":           f"High frequency in missed jobs + {g['market_demand']} market demand.",
        }
        for g in stat_gaps[:12]
    ]
