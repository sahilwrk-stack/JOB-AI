"""
Component 1 — ATS Resume Parser
Extracts structured, semantically-enriched data from a PDF/DOCX resume.
"""

import os
import json
from pathlib import Path
from typing import Union

from utils.helpers import safe_json_call, logger, timer

# Minimum characters required to consider extracted text valid
MIN_TEXT_LENGTH = 50


# ── Text Extraction ──────────────────────────────────────────────────────────

def extract_text_from_pdf(path: str) -> str:
    """Extract text from a PDF using pdfplumber. Warns if pages return empty text (image-based PDF)."""
    import pdfplumber
    text       = ""
    empty_pages = 0
    try:
        with pdfplumber.open(path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    empty_pages += 1
                    logger.warning(f"📄 [PDF PARSE] ⚠️  Page {i}/{total} returned empty text.")
                text += page_text + "\n"
    except Exception as e:
        raise ValueError(f"📄 [PDF PARSE] ❌ pdfplumber failed to open '{path}': {e}") from e

    text = text.strip()
    if empty_pages > 0 and not text:
        raise ValueError(
            f"📄 [PDF PARSE] ❌ ALL {empty_pages} page(s) returned empty text. "
            "This PDF is likely image-based/scanned. Convert it to a text-based PDF and retry."
        )
    return text


def extract_text_from_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join(para.text for para in doc.paragraphs).strip()


def extract_resume_text(path: str) -> str:
    """Auto-detect file type and extract plain text from a resume."""
    ext = Path(path).suffix.lower()
    logger.info(f"📄 [PDF PARSE] Detected file type: '{ext}' | Path: {path}")
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext in (".docx", ".doc"):
        return extract_text_from_docx(path)
    elif ext == ".txt":
        return Path(path).read_text(encoding="utf-8")
    else:
        raise ValueError(f"📄 [PDF PARSE] ❌ Unsupported resume format: '{ext}'. Use PDF, DOCX, or TXT.")


def guess_candidate_name(text: str) -> str:
    import re

    if not text or len(text.strip()) < 3:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    head = t[:8000]

    for pat in (
        r"(?im)^(?:full\s*name|name|candidate\s*name)\s*[:\-]\s*(.+?)(?:\n|$)",
        r"(?im)^(?:i\s+am|i'm|i am)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'\-.]{1,60})(?:\.|,|\n|$)",
    ):
        m = re.search(pat, head)
        if m:
            cand = m.group(1).strip().strip(",.|•")
            cand = re.sub(r"\s+", " ", cand)
            if 2 <= len(cand) <= 80 and not re.search(r"@|http|linkedin\.com|github\.com", cand, re.I):
                return cand[:80]

    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    sectionish = {
        "objective", "summary", "profile", "experience", "education", "skills",
        "projects", "certifications", "contact", "technical", "employment", "work",
        "internship", "internships", "achievements", "publications", "references", "hobbies",
        "resume", "curriculum", "vitae", "personal", "details",
    }
    section_upper = {
        "SKILLS", "EXPERIENCE", "EDUCATION", "PROJECTS", "SUMMARY", "OBJECTIVE",
        "CONTACT", "CERTIFICATIONS", "INTERNSHIP", "INTERNSHIPS", "WORK",
        "TECHNICAL", "ACHIEVEMENTS", "HOBBIES", "LANGUAGES",
    }

    def _title_name(s: str) -> str:
        return " ".join(w.capitalize() for w in s.split())

    for i, line in enumerate(lines[:12]):
        if len(line) > 70:
            continue
        if not re.match(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\.'\-]{1,68}$", line):
            continue
        toks = line.split()
        if not (2 <= len(toks) <= 5):
            continue
        if not all(re.match(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\.'\-]*$", w) for w in toks):
            continue
        low = line.lower()
        if low.split()[0] in sectionish:
            continue
        if line.isupper() and line.strip() in section_upper:
            continue
        if line.isupper() and 2 <= len(toks) <= 4:
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                nxt_up = nxt.upper()
                if nxt_up in section_upper or (
                    nxt.split() and nxt.lower().split()[0].rstrip(":") in sectionish
                ):
                    continue
                if "|" in nxt or "@" in nxt or re.search(
                    r"(analyst|developer|engineer|scientist|intern|fresher|student|consultant|designer|specialist|lead|manager|architect)",
                    nxt,
                    re.I,
                ):
                    return _title_name(line)
                if i == 0 and len(nxt) < 100:
                    return _title_name(line)
            else:
                return _title_name(line)

    for line in lines[:40]:
        if len(line) > 90:
            continue
        low = line.lower()
        if "@" in line or "http" in low or "www." in low or "linkedin" in low:
            continue
        first_tok = low.split()[0].rstrip(":") if low.split() else ""
        if first_tok in sectionish:
            continue
        if line.isupper() and line.strip() in section_upper:
            continue
        seg = line.split("|")[0].strip()
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-.]*", seg)
        if 2 <= len(words) <= 5:
            joined = " ".join(words)
            if joined.upper() == joined and len(words) <= 4:
                return _title_name(joined)
            return joined
    return ""


def guess_name_from_filename(filename: str) -> str:
    import re

    if not filename or not str(filename).strip():
        return ""
    stem = Path(str(filename)).name
    stem = re.sub(r"(?i)\.(pdf|docx?|txt)$", "", stem)
    stem = re.sub(
        r"(?i)(^|[\s_\-])(resume|cv|curriculum|vitae|final|draft|updated|copy|new)(\s|$|[_\-.])",
        " ",
        stem,
    )
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\d{4,}", " ", stem)
    stem = " ".join(stem.split())
    parts = [w for w in stem.split() if w.isalpha() and len(w) >= 2]
    if len(parts) >= 2:
        return " ".join(p[:1].upper() + p[1:].lower() for p in parts[:5])
    if len(parts) == 1:
        p = parts[0]
        return p[:1].upper() + p[1:].lower()
    return ""


# ── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are an Advanced ATS + Context-Aware Resume Intelligence AI calibrated
specifically for the INDIAN TECH JOB MARKET (2024-2025 CTC benchmarks).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORE EXTRACTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Return STRICT valid JSON only — absolutely no prose, no markdown fences.
- Zero hallucination — extract ONLY what is explicitly or semantically present.
- Use semantic grouping for skills (e.g. Pandas → Data Analysis).
- If a field has no evidence in the resume, use its empty default ([], 0, "").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SALARY GRADING MATRIX  (Indian market, CTC in Lakhs Per Annum)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — FRESHER / LOW TIER  (resume_score: 20–35)
  Conditions (ALL must be true):
    • years_of_experience = 0
    • No internships mentioned anywhere in the resume
    • No significant independent projects OR no meaningful certifications
    • Skills list is sparse (< 5 distinct skills) OR skills are generic/basic only
  Action:
    → resume_score    : 20–35
    → suggested_salary_min : 2.0   (floor — do NOT go below this)
    → suggested_salary_max : 3.0   (ceiling — do NOT exceed this)

RULE 2 — FRESHER / HIGH TIER  (resume_score: 45–70)
  Conditions (at least ONE must be true, zero work-experience still applies):
    • Has 1+ internship(s) with a named company OR open-source contributions (GitHub links, PRs)
    • Has 2+ end-to-end projects with real tech stacks, deployed or with measurable outcomes
    • Holds a recognised certification (AWS, GCP, Azure, TensorFlow, Coursera specialisations, etc.)
    • Won a hackathon, coding competition, or has kaggle/leetcode rankings
  Action:
    → resume_score    : 45–70  (higher end if multiple signals are strong)
    → suggested_salary_min : 3.5
    → suggested_salary_max : 6.0   (cap at 7.0 ONLY for exceptional IIT/NIT + FAANG internship)

RULE 3 — EXPERIENCED PROFESSIONALS  (1+ years)
  Base rates by tech stack (India, 2024-2025 median CTC):
    ┌──────────────────────────────┬────────────┬──────────────────────────────┐
    │ Stack / Role                 │ per year   │ Notes                        │
    ├──────────────────────────────┼────────────┼──────────────────────────────┤
    │ General Software / CRUD      │ 3.5 – 5 L  │ PHP, basic Java, basic Python │
    │ Python / Data Analyst        │ 4 – 6 L    │ SQL + pandas + visualisation  │
    │ ML / Data Science            │ 6 – 10 L   │ needs model deployment proof  │
    │ Backend (Node/Django/Spring) │ 5 – 8 L    │ APIs, DB design, auth         │
    │ Full Stack (React + BE)      │ 6 – 10 L   │ both layers demonstrated      │
    │ DevOps / Cloud (AWS/GCP)     │ 7 – 12 L   │ CI/CD, K8s, Terraform         │
    │ GenAI / LLM Engineering      │ 10 – 20 L  │ RAG, fine-tuning, prod deploy  │
    │ Android / iOS                │ 5 – 8 L    │ published apps are a plus     │
    └──────────────────────────────┴────────────┴──────────────────────────────┘
  Formula:
    → suggested_salary_min = base_min × years_of_experience (capped at 15 years)
    → suggested_salary_max = base_max × years_of_experience (capped at 15 years)
    → Reduce by 20 % if role is in a Tier-2/3 city or company is not a product company.
    → Increase by 15 % if candidate has worked at a FAANG / unicorn or has a top-tier degree (IIT/NIT/BITS).

RULE 4 — HONESTY CLAUSE  (non-negotiable)
  • Do NOT inflate resume_score or salary to make the candidate feel good.
  • Base every number on TANGIBLE EVIDENCE found in the resume text.
  • If a skill is listed but no project, job, or certification proves it was used,
    treat it as "exposure only" and do NOT use it to justify a higher salary band.
  • If there is conflicting evidence (claimed 5 years experience, graduated 2023),
    trust the education timeline over the claim and lower the score accordingly.
  • salary_reasoning MUST name the specific rule applied (Rule 1 / 2 / 3) and
    cite the exact evidence (or lack of evidence) that drove the numbers.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RETURN SCHEMA  (all fields required; use defaults when no evidence)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{
  "name":                   "",
  "candidate_name":         "",
  "target_job_roles":       [],
  "primary_skills":         [],
  "secondary_skills":       [],
  "tools_and_technologies": [],
  "projects": [
    {
      "name":       "",
      "tech_stack": [],
      "domain":     "",
      "complexity": "Beginner | Intermediate | Advanced"
    }
  ],
  "experience_level":   "Fresher | 0-1 | 1-3 | 3+",
  "years_of_experience": 0,
  "education":          [],
  "certifications":     [],
  "tournaments_and_awards": [],
  "preferred_locations":    [],
  "resume_score":           0,
  "suggested_salary_min":   0.0,
  "suggested_salary_max":   0.0,
  "salary_reasoning":       "",
  "current_estimated_salary": "0-0 LPA",
  "matching_job_titles":      [],
  "skill_gap_analysis":       [],
  "upskill_recommendations":  []
}
"""


def _build_user_prompt(resume_text: str) -> str:
    return f"""
Parse the following resume and return the exact JSON schema described in your system instructions.

CRITICAL REMINDERS:
0. Extract the candidate's full name from the resume header into "name" and duplicate it in "candidate_name" (empty string only if truly absent).

CRITICAL REMINDERS before you compute resume_score and salary fields:
1. Apply the Salary Grading Matrix rules STRICTLY (Rule 1 / 2 / 3).
2. Fresher with NO internship, NO deployed project, NO certification → salary MAX 3.0 L.
3. Fresher WITH strong projects / internships / certifications → salary 3.5–6.0 L.
4. Every rupee above the floor must be justified by explicit evidence in the resume text below.
5. salary_reasoning must name the rule used and cite specific evidence.
6. current_estimated_salary must be a realistic India CTC band string (e.g., "4.0-6.0 LPA").
7. matching_job_titles should include 5-8 roles the candidate can apply to RIGHT NOW.
8. skill_gap_analysis must list concrete missing skills blocking higher-paying roles.
9. upskill_recommendations must be actionable and salary-linked:
   Example: "Learn AWS + Docker to unlock 10-14 LPA backend roles."

RESUME TEXT:
\"\"\"
{resume_text}
\"\"\"

Return ONLY valid JSON. No markdown. No explanation. No prefix text.
"""


# ── Public API ───────────────────────────────────────────────────────────────

@timer
def parse_resume(source: Union[str, Path]) -> dict:
    """
    Parse a resume file (PDF / DOCX / TXT) and return structured JSON.

    Full tripwire logging at every stage so failures are immediately visible.
    """
    path = str(source)
    file_size_kb = round(Path(path).stat().st_size / 1024, 1) if Path(path).exists() else "?"

    logger.info(f"📥 [UPLOAD] File received: '{path}' ({file_size_kb} KB)")

    # ── STAGE 1: Extract text ────────────────────────────────────────────────
    try:
        text = extract_resume_text(path)
    except Exception as exc:
        logger.error(f"📄 [PDF PARSE] ❌ Text extraction raised an exception: {exc}")
        raise

    # ── STAGE 2: Validate extracted text ────────────────────────────────────
    if not text or len(text) < MIN_TEXT_LENGTH:
        msg = (
            f"📄 [PDF PARSE] ❌ Extracted text is too short ({len(text) if text else 0} chars, "
            f"minimum {MIN_TEXT_LENGTH}). "
            "Possible causes: image-based PDF, empty file, or corrupted document."
        )
        logger.error(msg)
        raise ValueError(msg)

    logger.success(
        f"📄 [PDF PARSE] ✅ Text extracted successfully. "
        f"{len(text)} characters | First 120 chars: {text[:120].strip()!r}"
    )

    # ── STAGE 3: Send to AI ──────────────────────────────────────────────────
    result = safe_json_call(SYSTEM_PROMPT, _build_user_prompt(text))

    # ── STAGE 4: Validate AI output ─────────────────────────────────────────
    if not result:
        logger.warning(
            "🤖 [AI REQUEST] ⚠️  LLM returned an empty result for the resume. "
            "Check the AI response logs above for the raw output."
        )
        return {}

    skills_count = len(result.get("primary_skills", []) + result.get("secondary_skills", []))
    logger.success(
        f"✅ [AI RESPONSE] Resume parsed successfully. "
        f"Skills: {skills_count} | "
        f"Score: {result.get('resume_score', '?')}/100 | "
        f"Experience: {result.get('experience_level', '?')} | "
        f"Salary: {result.get('suggested_salary_min', '?')}–{result.get('suggested_salary_max', '?')} LPA | "
        f"Roles: {result.get('target_job_roles', [])}"
    )
    return result


@timer
def parse_resume_from_text(resume_text: str) -> dict:
    """
    Parse resume from a raw text string (no file needed).
    """
    if not resume_text or len(resume_text) < MIN_TEXT_LENGTH:
        logger.error(
            f"📄 [PDF PARSE] ❌ Raw text too short ({len(resume_text) if resume_text else 0} chars). "
            f"Minimum required: {MIN_TEXT_LENGTH}."
        )
        return {}

    logger.info(
        f"📄 [PDF PARSE] ✅ Parsing from raw text — "
        f"{len(resume_text)} chars | First 80: {resume_text[:80].strip()!r}"
    )
    result = safe_json_call(SYSTEM_PROMPT, _build_user_prompt(resume_text))

    if not result:
        logger.warning("🤖 [AI REQUEST] ⚠️  LLM returned empty result for raw text input.")
        return {}

    logger.success(
        f"✅ [AI RESPONSE] Done. "
        f"Skills: {result.get('primary_skills', [])[:5]} | "
        f"Score: {result.get('resume_score', '?')}/100 | "
        f"Salary: {result.get('suggested_salary_min', '?')}–{result.get('suggested_salary_max', '?')} LPA | "
        f"Level: {result.get('experience_level', '?')}"
    )
    return result
