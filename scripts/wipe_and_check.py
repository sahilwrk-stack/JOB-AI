"""
Wipe all dummy/seed data and run a full system component check.
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

OK   = "[OK]"
FAIL = "[!!]"
WARN = "[--]"

print("\n" + "="*60)
print("  OMNISCIENT AI -- SYSTEM WIPE & HEALTH CHECK")
print("="*60)

# 1. Wipe database
print("\n[1] Wiping database...")
from core.memory import init_db
init_db()
conn = sqlite3.connect("db/agent_memory.db")
c = conn.cursor()
c.execute("DELETE FROM applications")
try:   c.execute("DELETE FROM safety_log")
except Exception: pass
try:   c.execute("DELETE FROM skill_log")
except Exception: pass
try:   c.execute("DELETE FROM sqlite_sequence")
except Exception: pass
conn.commit()
conn.close()
print(f"   {OK} All records deleted - database is clean")

# 2. Clear cached state files
print("\n[2] Clearing cached state files...")
state_files = [
    "db/last_result.json",
    "db/master_state.json",
    "db/safety_state.json",
    "db/resume_state.json",
]
for f in state_files:
    if os.path.exists(f):
        os.remove(f)
        print(f"   {OK} Deleted: {f}")
    else:
        print(f"   {WARN} Not found (skip): {f}")

# 3. Check LLM + API keys
print("\n[3] LLM & API Key Status:")
try:
    from utils.helpers import ollama_server_reachable
    ollama_ok = ollama_server_reachable()
except Exception:
    ollama_ok = False
rapid_key   = os.getenv("RAPIDAPI_KEY", "")
adzuna_id   = os.getenv("ADZUNA_APP_ID", "")

has_llm      = bool(ollama_ok)
has_jsearch  = rapid_key and len(rapid_key) > 8
has_adzuna   = adzuna_id and len(adzuna_id) > 4

def key_status(v): return OK if v and len(v) > 5 else FAIL

print(f"   {OK if ollama_ok else FAIL}   Ollama (local LLM — all AI; no cloud keys)")
print(f"   {key_status(rapid_key)}   RAPIDAPI_KEY   (optional JSearch)")
print(f"   {key_status(adzuna_id)}   ADZUNA_APP_ID  (Adzuna jobs)")

# 4. Module import checks
print("\n[4] Module Import Check:")
modules = [
    ("core.resume_parser",       "Resume Parser"),
    ("core.candidate_intel",     "Candidate Intelligence"),
    ("core.job_aggregator",      "Job Aggregator"),
    ("core.job_matcher",         "Job Matcher"),
    ("core.auto_apply",          "Auto Apply Agent"),
    ("core.orchestrator",        "Pipeline Orchestrator"),
    ("core.memory",              "Memory / DB"),
    ("core.self_improve",        "Self-Improvement"),
    ("core.analytics",           "Analytics AI"),
    ("core.tracker",             "Application Tracker"),
    ("core.notifier",            "Notifier"),
    ("core.safety_guard",        "Safety Guard"),
    ("core.master_orchestrator", "Master Orchestrator"),
    ("core.prompt_engine",       "Prompt Engine"),
]
failed = []
for mod, name in modules:
    try:
        __import__(mod)
        print(f"   {OK}  {name}")
    except Exception as e:
        print(f"   {FAIL}  {name}: {str(e)[:70]}")
        failed.append(name)

# 5. Free job sources
print("\n[5] Job Sources:")
print(f"   {OK}  RemoteOK (no key required)")
print(f"   {OK}  Himalayas (no key required)")
print(f"   {OK}  Sample jobs (local fallback)")
print(f"   {key_status(rapid_key)}  JSearch via RapidAPI")
print(f"   {key_status(adzuna_id)}  Adzuna")

# 6. Resume status
print("\n[6] Resume Status:")
uploads = []
if os.path.exists("uploads"):
    uploads = [p for p in os.listdir("uploads") if os.path.isfile(f"uploads/{p}")]
if uploads:
    for u in uploads:
        size = round(os.path.getsize(f"uploads/{u}") / 1024, 1)
        print(f"   {OK}  Found: {u} ({size} KB)")
else:
    print(f"   {FAIL}  No resume uploaded yet")

# Summary
print("\n" + "="*60)
print("  RESULT SUMMARY")
print("="*60)
print(f"  DB cleaned:     {OK} Empty and ready")
print(f"  Modules:        {OK + ' All loaded' if not failed else FAIL + ' ' + str(len(failed)) + ' failed'}")
print(f"  LLM (AI brain): {OK + ' Ready' if has_llm else FAIL + ' MISSING -- agent cannot parse or match without this'}")
print(f"  Job APIs:       {OK + ' JSearch ready' if has_jsearch else WARN + ' No paid APIs (will use free sources only)'}")
print(f"  Resume:         {OK + ' ' + uploads[0] if uploads else FAIL + ' Not uploaded'}")
print()

if not has_llm:
    print("  ACTION REQUIRED BEFORE RUNNING:")
    print("  --------------------------------")
    print("  Step 1: Install and start Ollama: https://ollama.com")
    print("  Step 2: Run:  ollama serve   (or start the Ollama app)")
    print("  Step 3: Pull a model, e.g.:  ollama pull llama3.2")
    print("  Optional: set OLLAMA_BASE_URL if not using http://localhost:11434/v1")
    print()
    print("  Step 4 (optional, more job results):")
    print("  Get FREE RapidAPI key for JSearch:")
    print("  https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch")
    print("  Then set:  RAPIDAPI_KEY=your_key_here")
    print()

if not uploads:
    print("  Upload your resume at: http://localhost:5050")
    print("  Click the 'Upload Resume' button in the topbar")
    print()

print("="*60)
