"""
RUNTIME AI DEBUGGING ENGINE
Traces live execution of every pipeline stage and logs exact failure points.
"""
import sys, os, json, time, sqlite3, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

# ── Helpers ───────────────────────────────────────────────────────────────────
LINE = "=" * 65
DIV  = "-" * 65
PASS = "[ PASS ]"
FAIL = "[ FAIL ]"
WARN = "[ WARN ]"
INFO = "[ INFO ]"

results = {}  # step -> "PASS" | "FAIL" | "WARN"

def header(step, title):
    print(f"\n{LINE}")
    print(f"  STEP {step}: {title}")
    print(DIV)

def log(tag, msg):
    print(f"  {tag}  {msg}")

def show_json(label, obj, max_chars=400):
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    if len(s) > max_chars:
        s = s[:max_chars] + "\n  ... (truncated)"
    print(f"  {label}:\n{s}")

def record(step, status):
    results[step] = status

# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{LINE}")
print("  OMNISCIENT AI -- LIVE RUNTIME DEBUG ENGINE")
print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(LINE)

# ── ENV SNAPSHOT ─────────────────────────────────────────────────────────────
rapid_key  = os.getenv("RAPIDAPI_KEY", "")
ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
llm_model  = os.getenv("LLM_MODEL", "llama3.2")
try:
    from utils.helpers import ollama_server_reachable
    ollama_ok = ollama_server_reachable()
except Exception:
    ollama_ok = False
has_llm    = bool(ollama_ok)

print(f"\n  ENV SNAPSHOT:")
print(f"    LLM_MODEL       : {llm_model}")
print(f"    OLLAMA_BASE_URL : {ollama_url}")
print(f"    Ollama reachable: {'YES' if ollama_ok else 'NO'}")
print(f"    RAPIDAPI_KEY    : {'SET' if len(rapid_key) > 8 else 'EMPTY (optional jobs API)'}")
print(f"    LLM AVAILABLE   : {'YES' if has_llm else 'NO -- start Ollama (ollama serve)'}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: FILE LOAD CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(1, "FILE LOAD CHECK")

resume_path = None

# Check uploads folder
upload_dir = "uploads"
if os.path.exists(upload_dir):
    files = [f for f in os.listdir(upload_dir)
             if os.path.isfile(os.path.join(upload_dir, f))
             and not f.startswith('.')]
    if files:
        resume_path = os.path.join(upload_dir, files[0])

# Fallback: check db/resume_state.json
if not resume_path:
    state_file = "db/resume_state.json"
    if os.path.exists(state_file):
        try:
            st = json.loads(open(state_file).read())
            candidate_path = st.get("path", "")
            if candidate_path and os.path.exists(candidate_path):
                resume_path = candidate_path
        except Exception:
            pass

log(INFO, f"Uploads folder : {os.path.abspath(upload_dir)}")
if resume_path:
    size_kb = round(os.path.getsize(resume_path) / 1024, 1)
    log(PASS, f"Resume found   : {resume_path}  ({size_kb} KB)")
    record("STEP_1", "PASS")
else:
    log(FAIL, "No resume file found in uploads/ folder!")
    log(INFO, "  --> Go to http://localhost:5050 and click 'Upload Resume'")
    log(INFO, "  --> Then re-run this debug script")
    record("STEP_1", "FAIL")
    # Try sample data to continue testing remaining steps
    sample_path = "data/jobs/sample_jobs.json"
    log(WARN, "Continuing debug with sample data for remaining steps...")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: TEXT EXTRACTION CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(2, "TEXT EXTRACTION CHECK")

extracted_text = ""
if resume_path:
    try:
        from core.resume_parser import extract_resume_text
        t0 = time.time()
        extracted_text = extract_resume_text(resume_path)
        elapsed = round(time.time() - t0, 2)
        log(INFO, f"Extraction time : {elapsed}s")
        log(INFO, f"Characters extracted : {len(extracted_text)}")
        log(INFO, f"First 500 chars:\n{'    ' + '-'*50}")
        print("    " + extracted_text[:500].replace("\n", "\n    "))
        print(f"    {'-'*50}")

        if len(extracted_text) < 50:
            log(FAIL, "Extracted text too short (<50 chars). PDF may be image-based/scanned.")
            log(INFO, "  --> Try converting the PDF to text-searchable using Adobe or Smallpdf")
            record("STEP_2", "FAIL")
        else:
            log(PASS, f"Text extraction successful ({len(extracted_text)} chars)")
            record("STEP_2", "PASS")
    except Exception as e:
        log(FAIL, f"Text extraction threw exception: {e}")
        traceback.print_exc()
        record("STEP_2", "FAIL")
        extracted_text = ""
else:
    log(WARN, "Skipped -- no resume file uploaded")
    record("STEP_2", "WARN")
    # Use dummy text for remaining steps
    extracted_text = """
    Sahil Kumar - Data Analyst
    Email: sahil@example.com | Phone: +91-9876543210
    Skills: Python, SQL, Pandas, NumPy, Tableau, Power BI, Machine Learning, Excel
    Experience: 1 year - Data Analyst Intern at TechCorp (2024)
    Projects: Sales Dashboard using Python and Tableau, Customer Churn ML model (scikit-learn)
    Education: B.Tech Computer Science, 2024
    Certifications: Google Data Analytics, Python for Data Science (Coursera)
    """
    log(WARN, "Using built-in test resume text for debug continuity")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: PARSER OUTPUT CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(3, "RESUME PARSER OUTPUT CHECK (LLM)")

parsed = {}
if extracted_text:
    try:
        if has_llm:
            from core.resume_parser import parse_resume_from_text
            t0 = time.time()
            parsed = parse_resume_from_text(extracted_text)
            elapsed = round(time.time() - t0, 2)
            log(INFO, f"LLM parse time: {elapsed}s")
        else:
            log(WARN, "No LLM key -- using regex fallback parser")
            # Regex-based fallback
            import re
            lines  = extracted_text.lower()
            skills_raw = re.findall(r'\b(python|sql|pandas|numpy|tableau|power bi|excel|'
                                    r'machine learning|tensorflow|pytorch|flask|fastapi|'
                                    r'docker|kubernetes|aws|git|javascript|react|node\.?js|'
                                    r'scikit.learn|nlp|data analysis|data science)\b', lines)
            skills = list(dict.fromkeys(s.title() for s in skills_raw))  # dedup, preserve order
            exp_match = re.search(r'(\d+)\s*year', lines)
            exp_years = int(exp_match.group(1)) if exp_match else 0
            parsed = {
                "primary_skills":        skills[:8],
                "secondary_skills":      skills[8:],
                "tools_and_technologies": [],
                "experience_level":      "Fresher" if exp_years == 0 else "0-1" if exp_years <= 1 else "1-3",
                "years_of_experience":   exp_years,
                "target_job_roles":      ["Data Analyst", "Python Developer", "ML Engineer"],
                "projects":              [],
                "education":             [],
                "certifications":        [],
                "preferred_locations":   ["Remote", "Bangalore"],
                "_parsed_by":            "regex_fallback (no LLM key)",
            }

        skills_count = len(parsed.get("primary_skills", []) + parsed.get("secondary_skills", []))
        log(INFO, f"Fields returned     : {list(parsed.keys())}")
        log(INFO, f"Primary skills      : {parsed.get('primary_skills', [])}")
        log(INFO, f"Secondary skills    : {parsed.get('secondary_skills', [])}")
        log(INFO, f"Experience level    : {parsed.get('experience_level', 'N/A')}")
        log(INFO, f"Years of experience : {parsed.get('years_of_experience', 'N/A')}")
        log(INFO, f"Target roles        : {parsed.get('target_job_roles', [])}")

        if skills_count == 0:
            log(FAIL, "No skills extracted! Parser returned empty skill lists.")
            log(INFO, "  --> If using LLM: ensure Ollama is running (ollama serve) and model is pulled")
            log(INFO, "  --> If resume is image-based PDF: convert to text PDF")
            record("STEP_3", "FAIL")
        else:
            log(PASS, f"Parser returned {skills_count} skills across primary + secondary")
            record("STEP_3", "PASS")

    except Exception as e:
        log(FAIL, f"Parser threw exception: {e}")
        traceback.print_exc()
        parsed = {"primary_skills": ["Python", "SQL"], "target_job_roles": ["Data Analyst"],
                  "experience_level": "Fresher", "years_of_experience": 0}
        record("STEP_3", "FAIL")
else:
    log(WARN, "Skipped -- no text to parse")
    record("STEP_3", "WARN")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: CANDIDATE PROFILE CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(4, "CANDIDATE INTELLIGENCE CHECK")

candidate_profile = {}
if parsed:
    try:
        from core.candidate_intel import evaluate_candidate
        t0 = time.time()
        candidate_profile = evaluate_candidate(parsed)
        elapsed = round(time.time() - t0, 2)
        log(INFO, f"Evaluation time : {elapsed}s")

        log(INFO, f"Candidate Tier       : {candidate_profile.get('candidate_tier', 'MISSING')}")
        log(INFO, f"Confidence Score     : {candidate_profile.get('confidence_score', 'MISSING')}")
        log(INFO, f"Market Demand Score  : {candidate_profile.get('market_demand_score', 'N/A')}")
        log(INFO, f"Salary Range         : {candidate_profile.get('salary_range', 'N/A')}")
        log(INFO, f"Strengths            : {candidate_profile.get('strengths', [])[:3]}")
        log(INFO, f"Risk Flags           : {candidate_profile.get('risk_flags', [])}")

        if not candidate_profile.get("candidate_tier"):
            log(FAIL, "candidate_tier is missing from output!")
            record("STEP_4", "FAIL")
        elif not candidate_profile.get("confidence_score"):
            log(FAIL, "confidence_score is missing from output!")
            record("STEP_4", "FAIL")
        else:
            log(PASS, f"Candidate profile built: Tier={candidate_profile['candidate_tier']}, "
                      f"Confidence={candidate_profile['confidence_score']}")
            record("STEP_4", "PASS")

    except Exception as e:
        log(FAIL, f"candidate_intel threw exception: {e}")
        traceback.print_exc()
        candidate_profile = {
            "candidate_tier": "Standard",
            "confidence_score": 60,
            "market_demand_score": 6,
        }
        record("STEP_4", "FAIL")
else:
    log(WARN, "Skipped -- no parsed resume data")
    record("STEP_4", "WARN")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: JOB FETCH CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(5, "JOB FETCH CHECK")

jobs = []
try:
    profile = {
        "target_job_roles": parsed.get("target_job_roles", ["Data Analyst"]) if parsed else ["Data Analyst"],
        "primary_skills":   parsed.get("primary_skills", []) if parsed else [],
        "experience_level": parsed.get("experience_level", "Fresher") if parsed else "Fresher",
    }

    # Try RemoteOK first (no key needed)
    log(INFO, "Trying RemoteOK (no API key required)...")
    try:
        from core.scrapers.remoteok import fetch
        rjobs = fetch(profile=profile, max_results=10)
        log(INFO, f"  RemoteOK returned: {len(rjobs)} jobs")
        jobs.extend(rjobs)
    except Exception as e:
        log(WARN, f"  RemoteOK failed: {str(e)[:80]}")

    # Try Himalayas (no key needed)
    log(INFO, "Trying Himalayas (no API key required)...")
    try:
        from core.scrapers.himalayas import fetch
        hjobs = fetch(profile=profile, max_results=10)
        log(INFO, f"  Himalayas returned: {len(hjobs)} jobs")
        jobs.extend(hjobs)
    except Exception as e:
        log(WARN, f"  Himalayas failed: {str(e)[:80]}")

    # Try JSearch if key present
    if rapid_key and len(rapid_key) > 8:
        log(INFO, "Trying JSearch (RapidAPI key found)...")
        try:
            from core.scrapers.jsearch import fetch_jsearch_jobs
            jjobs = fetch_jsearch_jobs(
                query=(parsed.get("target_job_roles", [""])[0] if parsed else "") + " remote",
                max_results=10,
            )
            log(INFO, f"  JSearch returned: {len(jjobs)} jobs")
            jobs.extend(jjobs)
        except Exception as e:
            log(WARN, f"  JSearch failed: {str(e)[:80]}")
    else:
        log(WARN, "  JSearch skipped (no RAPIDAPI_KEY in .env)")

    # Fallback: sample jobs
    if not jobs:
        log(WARN, "All live sources returned 0 jobs. Loading local sample jobs...")
        sample_file = "data/jobs/sample_jobs.json"
        if os.path.exists(sample_file):
            with open(sample_file) as f:
                jobs = json.load(f)
            log(INFO, f"  Loaded {len(jobs)} sample jobs from {sample_file}")
        else:
            log(WARN, "  sample_jobs.json not found either. Creating minimal test jobs...")
            jobs = [
                {"job_id": "test_001", "title": "Data Analyst", "company": "TechCorp",
                 "location": "Remote", "skills": ["Python", "SQL", "Tableau"],
                 "description": "Looking for a Data Analyst with Python and SQL skills.",
                 "salary_min": 600000, "salary_max": 900000, "source": "test", "url": "https://example.com/job1"},
                {"job_id": "test_002", "title": "Python Developer", "company": "DataFirm",
                 "location": "Bangalore", "skills": ["Python", "FastAPI", "PostgreSQL"],
                 "description": "Python developer for building data pipelines.",
                 "salary_min": 700000, "salary_max": 1200000, "source": "test", "url": "https://example.com/job2"},
                {"job_id": "test_003", "title": "Marketing Manager", "company": "BrandCo",
                 "location": "Mumbai", "skills": ["Marketing", "SEO", "Google Ads"],
                 "description": "Senior marketing manager with 5 years experience.",
                 "salary_min": 800000, "salary_max": 1500000, "source": "test", "url": "https://example.com/job3"},
            ]

    log(INFO, f"Total jobs collected: {len(jobs)}")
    if jobs:
        sample = jobs[0]
        log(INFO, f"Sample job preview:")
        log(INFO, f"  Title    : {sample.get('title')}")
        log(INFO, f"  Company  : {sample.get('company')}")
        log(INFO, f"  Location : {sample.get('location')}")
        log(INFO, f"  Skills   : {sample.get('skills', [])[:5]}")
        log(INFO, f"  Source   : {sample.get('source')}")

    if len(jobs) == 0:
        log(FAIL, "0 jobs fetched from all sources!")
        record("STEP_5", "FAIL")
    elif len(jobs) < 3:
        log(WARN, f"Only {len(jobs)} jobs fetched. API keys will give more results.")
        record("STEP_5", "WARN")
    else:
        log(PASS, f"{len(jobs)} jobs fetched successfully")
        record("STEP_5", "PASS")

except Exception as e:
    log(FAIL, f"Job aggregator threw exception: {e}")
    traceback.print_exc()
    record("STEP_5", "FAIL")
    jobs = []

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6: MATCHING CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(6, "JOB MATCHING CHECK")

ranked_jobs = []
if jobs and parsed:
    try:
        from core.job_matcher import rank_jobs, match_job
        t0 = time.time()
        # Use min_score=0 for debug so we can see all computed scores
        ranked_all = rank_jobs(parsed, jobs[:11], min_score=0)
        elapsed = round(time.time() - t0, 2)
        ranked_jobs = [j for j in ranked_all if j.get("match_result", {}).get("match_score", 0) >= 50]

        log(INFO, f"Matching time: {elapsed}s for {min(11, len(jobs))} jobs")

        all_scores = [j.get("match_result", {}).get("match_score", 0) for j in ranked_all]
        pass_scores = [s for s in all_scores if s >= 50]
        log(INFO, f"All scores (0+ threshold): {sorted(all_scores, reverse=True)}")
        log(INFO, f"Jobs above threshold (50): {len(pass_scores)}")
        log(INFO, f"Max score  : {max(all_scores) if all_scores else 'N/A'}")
        log(INFO, f"Min score  : {min(all_scores) if all_scores else 'N/A'}")
        log(INFO, f"Avg score  : {round(sum(all_scores)/len(all_scores), 1) if all_scores else 'N/A'}")

        log(INFO, "\n  Top 3 matches:")
        for i, job in enumerate(ranked_all[:3]):
            mr = job.get("match_result", {})
            matched_by = mr.get("_matched_by", "llm")
            print(f"    {i+1}. {job.get('title','?')[:40]} @ {job.get('company','?')}")
            print(f"       Score: {mr.get('match_score', 0)}  |  Engine: {matched_by}  |  Matched: {mr.get('matched_skills',[])[:4]}")

        if not all_scores or all(s == 0 for s in all_scores):
            log(FAIL, "ALL match scores are 0! Keyword fallback also failed. Skills lists are empty.")
            record("STEP_6", "FAIL")
        elif max(all_scores) < 15:
            log(WARN, "Very low match scores even with fallback. Resume skills may be empty.")
            record("STEP_6", "WARN")
        else:
            log(PASS, f"Matching engine working. Highest score: {max(all_scores)} "
                      f"({'LLM' if not has_llm is False else 'keyword fallback'}) "
                      f"| {len(pass_scores)}/{len(all_scores)} jobs above 50 threshold")
            record("STEP_6", "PASS")

    except Exception as e:
        log(FAIL, f"job_matcher threw exception: {e}")
        traceback.print_exc()
        record("STEP_6", "FAIL")
else:
    log(WARN, "Skipped -- no jobs or no parsed profile")
    record("STEP_6", "WARN")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7: DECISION CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(7, "DECISION ENGINE CHECK")

decisions = {"apply": 0, "review": 0, "skip": 0}
apply_t  = int(os.getenv("APPLY_CONFIDENCE_THRESHOLD",  "85"))
review_t = int(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "60"))

if ranked_jobs:
    try:
        from core.orchestrator import _should_apply
        log(INFO, f"Apply threshold  : {apply_t}")
        log(INFO, f"Review threshold : {review_t}")

        for job in ranked_jobs[:10]:
            mr          = job.get("match_result", {})
            score       = mr.get("total_score", 0)
            confidence  = mr.get("confidence",  score)
            decision    = _should_apply(score, confidence)
            decisions[decision] = decisions.get(decision, 0) + 1
            print(f"    {job.get('title','?')[:35]:<35} score={score:>3}  -> {decision.upper()}")

        log(INFO, f"\n  Decision Summary:")
        log(INFO, f"    AUTO-APPLY : {decisions['apply']} jobs")
        log(INFO, f"    REVIEW     : {decisions['review']} jobs")
        log(INFO, f"    SKIP       : {decisions['skip']} jobs")

        if sum(decisions.values()) == 0:
            log(FAIL, "No decisions made at all!")
            record("STEP_7", "FAIL")
        elif decisions["apply"] == 0 and decisions["review"] == 0:
            log(WARN, f"All {decisions['skip']} jobs were SKIPPED. "
                       f"Thresholds may be too high for current match scores.")
            log(INFO, f"  --> Match scores are below {review_t}. "
                       f"Either skills overlap is low OR LLM key needed for semantic matching.")
            record("STEP_7", "WARN")
        else:
            log(PASS, f"Decision engine working: {decisions['apply']} apply, "
                       f"{decisions['review']} review, {decisions['skip']} skip")
            record("STEP_7", "PASS")

    except Exception as e:
        log(FAIL, f"Decision engine threw exception: {e}")
        traceback.print_exc()
        record("STEP_7", "FAIL")
else:
    log(WARN, "Skipped -- no ranked jobs")
    record("STEP_7", "WARN")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8: DATABASE CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(8, "DATABASE SAVE CHECK")

try:
    from core.memory import init_db, save_application, get_all_applications

    # Count before
    conn = sqlite3.connect("db/agent_memory.db")
    before = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    conn.close()
    log(INFO, f"Rows before test write: {before}")

    # Write one test record using correct field names (job_title / job_url)
    test_job = {
        "title":      "Debug Test Job",
        "company":    "Debug Corp",
        "location":   "Remote",
        "url":        "https://debug.test/job/1",
        "apply_link": "https://debug.test/job/1",
        "source":     "debug",
        "skills":     ["Python", "SQL"],
    }
    save_application(
        job            = test_job,
        match_result   = {"match_score": 75, "confidence_score": 80},
        apply_decision = {"execution_status": "REVIEW", "reason": "debug test"},
    )

    # Count after — use actual column names: job_title, company
    conn = sqlite3.connect("db/agent_memory.db")
    after = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    row   = conn.execute(
        "SELECT * FROM applications WHERE job_title='Debug Test Job' LIMIT 1"
    ).fetchone()
    conn.close()

    log(INFO, f"Rows after test write : {after}")
    if row:
        log(PASS, f"DB write successful. id={row[0]}, title='{row[1]}', status='{row[9]}'")
        record("STEP_8", "PASS")
    else:
        log(FAIL, "Row not found after save_application() call!")
        record("STEP_8", "FAIL")

except Exception as e:
    log(FAIL, f"Database check threw exception: {e}")
    traceback.print_exc()
    record("STEP_8", "FAIL")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9: DASHBOARD API CHECK
# ─────────────────────────────────────────────────────────────────────────────
header(9, "DASHBOARD API CHECK")

try:
    import urllib.request, urllib.error
    endpoints = [
        ("http://localhost:5050/api/dashboard",        "Main dashboard data"),
        ("http://localhost:5050/api/tracker/stats",    "Tracker stats"),
        ("http://localhost:5050/api/safety/status",    "Safety status"),
        ("http://localhost:5050/api/orchestrator/status", "Orchestrator status"),
        ("http://localhost:5050/api/resume-status",    "Resume status"),
    ]

    api_pass = 0
    api_fail = 0
    for url, label in endpoints:
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode()
                data = json.loads(body)
                keys = list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]"
                log(PASS, f"{label:<30} HTTP 200 | keys: {keys}")
                api_pass += 1
        except urllib.error.URLError as e:
            if "Connection refused" in str(e) or "actively refused" in str(e):
                log(FAIL, f"{label:<30} SERVER NOT RUNNING (connection refused)")
            else:
                log(FAIL, f"{label:<30} {e}")
            api_fail += 1
        except Exception as e:
            log(FAIL, f"{label:<30} {str(e)[:60]}")
            api_fail += 1

    if api_fail == len(endpoints):
        log(FAIL, "ALL API endpoints failed -- dashboard server is NOT running!")
        log(INFO, "  --> Run: python -m dashboard.server")
        record("STEP_9", "FAIL")
    elif api_fail > 0:
        log(WARN, f"{api_fail}/{len(endpoints)} endpoints failed")
        record("STEP_9", "WARN")
    else:
        log(PASS, f"All {api_pass} API endpoints responded correctly")
        record("STEP_9", "PASS")

except Exception as e:
    log(FAIL, f"Dashboard API check threw exception: {e}")
    traceback.print_exc()
    record("STEP_9", "FAIL")

# ─────────────────────────────────────────────────────────────────────────────
# FINAL DIAGNOSIS
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{LINE}")
print("  FINAL DIAGNOSIS")
print(LINE)

fails   = [k for k, v in results.items() if v == "FAIL"]
warns   = [k for k, v in results.items() if v == "WARN"]
passes  = [k for k, v in results.items() if v == "PASS"]

print(f"\n  STEP RESULTS:")
step_labels = {
    "STEP_1": "File Load",        "STEP_2": "Text Extraction",
    "STEP_3": "Resume Parser",    "STEP_4": "Candidate Intel",
    "STEP_5": "Job Fetch",        "STEP_6": "Job Matching",
    "STEP_7": "Decision Engine",  "STEP_8": "Database",
    "STEP_9": "Dashboard API",
}
for key, label in step_labels.items():
    status = results.get(key, "SKIP")
    icon   = "[PASS]" if status == "PASS" else "[FAIL]" if status == "FAIL" else "[WARN]" if status == "WARN" else "[SKIP]"
    print(f"    {icon}  {label}")

# Determine overall status
if len(fails) == 0 and len(warns) <= 2:
    system_status = "WORKING"
elif len(fails) <= 2 or (len(fails) == 1 and "STEP_1" in fails):
    system_status = "PARTIAL"
else:
    system_status = "BROKEN"

# First failing step
failed_step    = fails[0] if fails else (warns[0] if warns else "NONE")
step_name      = step_labels.get(failed_step, failed_step)

# Build specific diagnosis
diagnostics = {
    "STEP_1": ("No resume uploaded",       "Upload a PDF/DOCX resume via the dashboard at http://localhost:5050", "HIGH"),
    "STEP_2": ("PDF text extraction fail", "Try a text-based PDF. Avoid scanned/image PDFs.", "HIGH"),
    "STEP_3": ("LLM not available",        "Start Ollama (ollama serve) and pull a model, e.g. ollama pull llama3.2", "HIGH"),
    "STEP_4": ("Candidate eval error",     "Check core/candidate_intel.py for import errors", "MEDIUM"),
    "STEP_5": ("0 jobs fetched",           "Add RAPIDAPI_KEY to .env for JSearch, or check internet connection", "HIGH"),
    "STEP_6": ("Match scores all zero",    "Skills are empty or not overlapping. Ensure resume skills match job keywords", "MEDIUM"),
    "STEP_7": ("No apply/review decisions","Lower APPLY_CONFIDENCE_THRESHOLD in .env or improve resume match", "LOW"),
    "STEP_8": ("DB save failed",           "Check db/ folder exists and is writable", "HIGH"),
    "STEP_9": ("Dashboard not running",    "Run: python -m dashboard.server", "HIGH"),
}

exact_issue, fix_action, severity = diagnostics.get(
    failed_step,
    ("All steps passed", "No action needed", "LOW")
)

print(f"""
  DIAGNOSIS JSON:
  {{
    "failed_step"   : "{step_name}",
    "exact_issue"   : "{exact_issue}",
    "why_it_failed" : "{'Ollama not reachable -- start ollama serve and ensure a model is pulled' if not has_llm and failed_step == 'STEP_3' else exact_issue}",
    "fix_action"    : "{fix_action}",
    "severity"      : "{severity}",
    "system_status" : "{system_status}"
  }}
""")

print(f"  PASSES : {len(passes)}/9 steps")
print(f"  WARNS  : {len(warns)}/9 steps")
print(f"  FAILS  : {len(fails)}/9 steps")
print(f"  STATUS : {system_status}")

if not has_llm:
    print(f"""
  MOST CRITICAL ACTION:
  ─────────────────────
  1. Install/start Ollama: https://ollama.com
  2. Terminal:  ollama serve   (or use the desktop app)
  3. Pull a model:  ollama pull llama3.2
  4. Restart dashboard server and re-run: python scripts/live_debug.py
""")

if results.get("STEP_1") == "FAIL":
    print(f"""
  ALSO NEEDED:
  ─────────────
  Upload your real resume at: http://localhost:5050
  Click "Upload Resume" button in the topbar.
""")

print(LINE)
