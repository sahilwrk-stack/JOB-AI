"""Audit edge-case tests - run by the systems auditor."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv; load_dotenv()
os.makedirs("db", exist_ok=True); os.makedirs("logs", exist_ok=True)

results = []

def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((label, status, detail))
    print(f"  [{status}] {label}" + (f" | {detail}" if detail else ""))

print("\n=== AUDIT EDGE CASE TESTS ===\n")

# ── 1. _should_apply ignores env thresholds (hardcoded bug) ──────────────────
os.environ["APPLY_CONFIDENCE_THRESHOLD"] = "90"
os.environ["REVIEW_CONFIDENCE_THRESHOLD"] = "75"
from core.orchestrator import _should_apply
result = _should_apply(match_score=86, confidence=82)
check("_should_apply respects env threshold (86 < 90 = review)",
      result == "review",
      f"got='{result}' expected='review'")

# ── 2. auto_apply module-level threshold stale ────────────────────────────────
from core.auto_apply import APPLY_THRESHOLD
check("auto_apply.APPLY_THRESHOLD is dynamic (not stale module constant)",
      APPLY_THRESHOLD != 85,
      f"APPLY_THRESHOLD={APPLY_THRESHOLD} (stale=85 at import time, env now says 90)")

# ── 3. Orchestrator decision_key override — verify fix in source ──────────────
import inspect, core.orchestrator as _orch
src = inspect.getsource(_orch.step_decide_and_apply)
fix_present = "Enforced REVIEW over AUTO_APPLY" in src or "decision_key == \"review\" and llm_status" in src
check("Orchestrator safety downgrade enforced (fix applied in source)",
      fix_present,
      f"fix_present={fix_present}")

# ── 4. SafetyResult WARNING+allowed=False inconsistency ─────────────────────
from core.safety_guard import check_safe_to_apply, set_review_mode, set_auto_mode
set_review_mode("test")
job = {"title": "SWE", "company": "G", "apply_link": "https://greenhouse.io/j/1",
       "job_id": "jt", "location": "SF"}
r = check_safe_to_apply(job, confidence=88, dry_run=False)
check("SafetyResult consistency: if allowed=False then status=BLOCKED (not WARNING)",
      not (r.safety_status == "WARNING" and not r.allowed),
      f"status={r.safety_status} allowed={r.allowed} – contradictory")
set_auto_mode("restore")

# ── 5. already_applied(None) crash test ───────────────────────────────────────
from core.memory import already_applied, init_db
init_db()
try:
    already_applied(None)
    check("already_applied(None) handles null gracefully", True)
except Exception as e:
    check("already_applied(None) handles null gracefully", False, str(e)[:60])

# ── 6. already_applied empty string ───────────────────────────────────────────
try:
    r2 = already_applied("")
    check("already_applied('') handles empty string", True, f"returned {r2}")
except Exception as e:
    check("already_applied('') handles empty string", False, str(e)[:60])

# ── 7. evaluate_candidate with empty dict ─────────────────────────────────────
from core.candidate_intel import evaluate_candidate
result7 = evaluate_candidate({})
check("evaluate_candidate({}) returns empty dict without crash",
      result7 == {}, f"got={result7}")

# ── 8. rank_jobs with empty list ──────────────────────────────────────────────
from core.job_matcher import rank_jobs
ranked = rank_jobs({}, [], None, 50)
check("rank_jobs(empty list) returns []", ranked == [], f"got={ranked}")

# ── 9. Quality gate spam filtering ───────────────────────────────────────────
from core.master_orchestrator import quality_gate, SystemState
state = SystemState()
spam_jobs = [
    {"title": "Urgent Hiring Commission Only", "company": "X",
     "apply_link": "https://linkedin.com/j/999", "match_score": 30},
    {"title": "Data Analyst", "company": "Infosys",
     "apply_link": "https://linkedin.com/j/111", "match_score": 82},
]
approved, rejected = quality_gate(spam_jobs, state, min_quality=40)
check("quality_gate correctly rejects spam jobs",
      len(rejected) >= 1 and len(approved) >= 1,
      f"approved={len(approved)} rejected={len(rejected)}")

# ── 10. run_self_improvement with no data ────────────────────────────────────
from core.self_improve import run_self_improvement
from unittest.mock import patch
with patch("core.self_improve.get_all_applications", return_value=[]):
    res = run_self_improvement()
check("run_self_improvement handles zero history gracefully",
      isinstance(res, dict) and "insights" in res,
      f"returned keys={list(res.keys())[:3]}")

# ── 11. Circuit breaker: record 3 errors -> halt ──────────────────────────────
from core.safety_guard import record_error, reset_halt, get_safety_stats
os.environ["SAFETY_ERROR_HALT_THRESHOLD"] = "3"
os.environ["SAFETY_HALT_ON_ERROR"] = "true"
reset_halt("pre-test reset")
record_error("err1"); record_error("err2"); halted = record_error("err3")
check("Circuit breaker halts after 3 consecutive errors", halted, f"halted={halted}")
reset_halt("post-test reset")

# ── 12. DB fingerprint migration columns exist ────────────────────────────────
from core.db_adapter import _get_conn
with _get_conn() as conn:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
check("applications table has job_fingerprint column", "job_fingerprint" in cols,
      f"cols_found={'job_fingerprint' in cols}")
check("applications table has url_fingerprint column", "url_fingerprint" in cols,
      f"cols_found={'url_fingerprint' in cols}")

# ── Summary ───────────────────────────────────────────────────────────────────
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s == "FAIL")
print(f"\n{'='*50}")
print(f"RESULTS: {passed} PASSED | {failed} FAILED | {len(results)} TOTAL")
print(f"{'='*50}\n")
sys.exit(1 if failed > 0 else 0)
