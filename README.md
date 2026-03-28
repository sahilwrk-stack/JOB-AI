# Omniscient AI — Advanced Job Application Agent

> A fully autonomous, self-learning AI agent that reads your resume, finds matching jobs, applies automatically, tracks outcomes, learns from results, and notifies you in real-time.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OMNISCIENT AI PIPELINE                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│   📄 RESUME INPUT                                                     │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────┐    core/resume_parser.py                            │
│  │  AI PARSER  │  → Extracts: skills, projects, experience, roles    │
│  └──────┬──────┘                                                      │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────────┐    core/candidate_intel.py                      │
│  │ CANDIDATE INTEL │  → Tier (Elite/Premium/Standard), salary range  │
│  └────────┬────────┘    market demand, confidence score              │
│           │                                                           │
│           ▼                                                           │
│  ┌──────────────────────────────────────────────────────────┐        │
│  │                     JOB FETCHER                           │        │
│  │  core/job_aggregator.py + core/scrapers/                 │        │
│  │  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐  │        │
│  │  │ JSearch  │ │ Adzuna │ │ RemoteOK │ │  Himalayas   │  │        │
│  │  │(RapidAPI)│ │(India) │ │(Remote)  │ │(Remote Tech) │  │        │
│  │  └──────────┘ └────────┘ └──────────┘ └──────────────┘  │        │
│  │  ┌─────────────────────────────────────────────────────┐ │        │
│  │  │      Playwright Fallback Scraper (Internshala,      │ │        │
│  │  │      LinkedIn, Indeed)                              │ │        │
│  │  └─────────────────────────────────────────────────────┘ │        │
│  │  → Normalize → Deduplicate → Filter → LLM Skill Enrich   │        │
│  └──────────────────────┬───────────────────────────────────┘        │
│                         │                                             │
│                         ▼                                             │
│  ┌──────────────────────────────┐    core/job_matcher.py             │
│  │     SEMANTIC JOB MATCHER     │  Scoring (0–100):                  │
│  │   ┌──────────────────────┐   │  • Core Skills Match    → 50%      │
│  │   │  5-Factor AI Scoring │   │  • Semantic Match       → 20%      │
│  │   └──────────────────────┘   │  • Experience Fit       → 15%      │
│  └──────────────┬───────────────┘  • Project Relevance    → 10%      │
│                 │                   • Bonus Skills         →  5%      │
│                 ▼                                                      │
│  ┌──────────────────────────────┐    core/orchestrator.py            │
│  │     DECISION ENGINE          │                                     │
│  │   score > 85 + conf > 80 → AUTO APPLY                             │
│  │   score > 70             → REVIEW                                  │
│  │   score ≤ 70             → SKIP                                    │
│  └──────────────┬───────────────┘                                     │
│                 │                                                      │
│                 ▼                                                      │
│  ┌──────────────────────────────┐    core/auto_apply.py              │
│  │       AUTO-APPLY AGENT       │  • Playwright browser automation    │
│  │  • Detects form fields       │  • Adaptive form filling            │
│  │  • Risk-aware submission     │  • Confidence-based execution       │
│  │  • Human-in-loop for review  │  • Abort on risky patterns          │
│  └──────────────┬───────────────┘                                     │
│                 │                                                      │
│                 ▼                                                      │
│  ┌──────────────────────────────┐    core/memory.py                  │
│  │        MEMORY SYSTEM         │    core/db_adapter.py              │
│  │   SQLite (dev) / PostgreSQL  │  • Applications log                 │
│  │      (production)            │  • Skill performance tracking       │
│  │                              │  • Activity log                     │
│  │                              │  • Strategy snapshots               │
│  │                              │  • Analytics cache                  │
│  │                              │  • Notification log                 │
│  └──────────────┬───────────────┘                                     │
│                 │                    Parallel processes ↓             │
│        ┌────────┴────────┬──────────────────────┐                    │
│        ▼                 ▼                        ▼                   │
│  ┌──────────┐  ┌──────────────────┐  ┌───────────────────────┐       │
│  │SELF-LEARN│  │    ANALYTICS     │  │    NOTIFICATIONS      │       │
│  │          │  │                  │  │                        │       │
│  │self_impro│  │  analytics.py    │  │  notifier.py           │       │
│  │ve.py     │  │  • Callback pat- │  │  • Email (SMTP/SG)     │       │
│  │• Callback│  │    terns         │  │  • WhatsApp (Twilio)   │       │
│  │  pattern │  │  • Skill gaps    │  │  • Priority routing    │       │
│  │  analysis│  │  • Learning road-│  │  • Quiet hours         │       │
│  │• Strategy│  │    map           │  │  • Auto-triggers       │       │
│  │  updates │  │  • Role fit      │  │  • Delivery log        │       │
│  └──────────┘  └──────────────────┘  └───────────────────────┘       │
│        │               │                         │                    │
│        └───────────────┴─────────────────────────┘                   │
│                                │                                      │
│                                ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐        │
│  │                    DASHBOARD                              │        │
│  │   dashboard/index.html  ←→  backend/api.py (FastAPI)     │        │
│  │                                                           │        │
│  │  📊 Overview    👜 Jobs       📈 Analytics                │        │
│  │  🕐 History     📋 Tracker   🧠 Intelligence              │        │
│  │  🔔 Notifications                                         │        │
│  └──────────────────────────────────────────────────────────┘        │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Complete Component Map

| # | Component | File(s) | Status | Description |
|---|-----------|---------|--------|-------------|
| 1 | **Resume Parser** | `core/resume_parser.py` | ✅ Done | LLM extracts skills, projects, experience, roles from PDF/DOCX/TXT |
| 2 | **Candidate Intel** | `core/candidate_intel.py` | ✅ Done | Assigns tier (Elite/Premium/Standard), market demand score, salary range |
| 3 | **Job Fetcher** | `core/job_aggregator.py` + `core/scrapers/` | ✅ Done | Parallel fetch from 4 APIs + Playwright scraper fallback |
| 4 | **Semantic Matcher** | `core/job_matcher.py` | ✅ Done | 5-factor AI scoring with partial credit for related skills |
| 5 | **Decision Engine** | `core/orchestrator.py` | ✅ Done | apply/review/skip logic based on match + confidence thresholds |
| 6 | **Auto-Apply Agent** | `core/auto_apply.py` | ✅ Done | Playwright browser automation, form detection, risk-aware submission |
| 7 | **Memory System** | `core/memory.py` + `core/db_adapter.py` | ✅ Done | SQLite (dev) / PostgreSQL (prod), full CRUD for all agent data |
| 8 | **Application Tracker** | `core/tracker.py` | ✅ Done | Full lifecycle: Applied→Interview→Offer, stats, activity log |
| 9 | **Self-Improvement Loop** | `core/self_improve.py` | ✅ Done | Analyzes callback patterns, updates strategy for next run |
| 10 | **Data Analytics AI** | `core/analytics.py` | ✅ Done | Skill gap detection, learning roadmap, role fit, callback patterns |
| 11 | **Dynamic Prompts** | `core/prompt_engine.py` | ✅ Done | Tier-aware context-sensitive prompts for all LLM calls |
| 12 | **Notification AI** | `core/notifier.py` | ✅ Done | Email + WhatsApp alerts for jobs/applications/callbacks |
| 13 | **Orchestrator** | `core/orchestrator.py` | ✅ Done | Master pipeline coordinator (SCAN→RANK→DECIDE→APPLY→LEARN) |
| 14 | **Dashboard** | `dashboard/index.html` | ✅ Done | 7-section glassmorphism UI: Jobs, Tracker, Analytics, Notifications |
| 15 | **API Server (Flask)** | `dashboard/server.py` | ✅ Done | Local dev Flask server with all endpoints |
| 16 | **API Server (FastAPI)** | `backend/api.py` | ✅ Done | Production FastAPI server with OpenAPI docs |
| 17 | **CLI Interface** | `main.py` | ✅ Done | Full argparse CLI with 10+ commands |
| 18 | **Deployment** | `Dockerfile`, `docker-compose.yml`, `render.yaml`, `railway.toml` | ✅ Done | Docker + Render + Railway + Netlify/Vercel |

---

## Project Structure

```
omniscient-ai/
│
├── 📁 core/                        # All AI components
│   ├── resume_parser.py            # Component 1 — PDF/DOCX → JSON
│   ├── candidate_intel.py          # Component 2 — Market intelligence
│   ├── job_matcher.py              # Component 3 — 5-factor semantic scoring
│   ├── auto_apply.py               # Component 4 — Browser automation
│   ├── memory.py                   # Component 5 — Persistent storage
│   ├── self_improve.py             # Component 7 — Self-learning loop
│   ├── prompt_engine.py            # Component 8 — Dynamic prompts
│   ├── orchestrator.py             # Component 6 — Master pipeline
│   ├── job_aggregator.py           # Job Fetcher AI — multi-source aggregator
│   ├── tracker.py                  # Application Tracking AI
│   ├── analytics.py                # Data Analytics AI
│   ├── notifier.py                 # Notification AI
│   ├── db_adapter.py               # SQLite ↔ PostgreSQL adapter
│   └── scrapers/
│       ├── jsearch.py              # JSearch / RapidAPI (LinkedIn, Indeed)
│       ├── adzuna.py               # Adzuna (India-focused)
│       ├── remoteok.py             # RemoteOK (remote-only, free)
│       ├── himalayas.py            # Himalayas (remote tech, free)
│       └── playwright_scraper.py   # Fallback: Internshala, LinkedIn, Indeed
│
├── 📁 backend/                     # Production FastAPI server
│   └── api.py                      # All API routes (replaces Flask in prod)
│
├── 📁 dashboard/                   # Frontend UI
│   ├── index.html                  # Full dashboard (7 sections, ~3500 lines)
│   ├── server.py                   # Local Flask dev server
│   └── _redirects                  # Netlify API proxy rules
│
├── 📁 db/                          # Database storage
│   └── agent_memory.db             # SQLite (auto-created, gitignored)
│
├── 📁 logs/                        # Log files (gitignored)
│
├── 📁 utils/
│   └── helpers.py                  # LLM client, logging, retry, timing
│
├── 📁 .github/workflows/
│   └── deploy.yml                  # CI/CD: test → Docker build → Render/Netlify
│
├── main.py                         # CLI entry point (10+ commands)
├── requirements.txt                # All Python dependencies
├── .env.example                    # Environment variable template
├── Dockerfile                      # Multi-stage production Docker image
├── docker-compose.yml              # Local dev stack (API + PostgreSQL)
├── render.yaml                     # Render 1-click deployment blueprint
├── railway.toml                    # Railway deployment config
├── netlify.toml                    # Netlify frontend deployment
├── vercel.json                     # Vercel frontend deployment
├── Procfile                        # Heroku/Render process declaration
└── gunicorn.conf.py                # Production ASGI server config
```

---

## Quick Start

### 1. Setup

```bash
git clone https://github.com/yourusername/omniscient-ai
cd omniscient-ai
pip install -r requirements.txt
cp .env.example .env
# Edit .env — Ollama (local LLM) and optional API keys; never commit .env
```

### 2. Minimum .env (Ollama — no cloud LLM key required)

```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434/v1
```

For Docker Compose with PostgreSQL, add the fields described in [docs/DOCKER_ENV.md](docs/DOCKER_ENV.md) to your private `.env` only.

### 3. Run the full pipeline

```bash
# With sample jobs (fastest way to test)
python main.py run my_resume.pdf

# With live job fetching from APIs
python main.py run my_resume.pdf --fetch

# With web scraping fallback
python main.py run my_resume.pdf --fetch --scrapers --max-jobs 30
```

### 4. Open the dashboard

```bash
python main.py dashboard
# Opens: http://localhost:5050
```

---

## All CLI Commands

```bash
# ── Pipeline ──────────────────────────────────────────────────────────
python main.py run my_resume.pdf                          # Full pipeline (dry run)
python main.py run my_resume.pdf --fetch                  # Live job fetching
python main.py run my_resume.pdf --fetch --scrapers       # APIs + web scraping

# ── Individual Components ─────────────────────────────────────────────
python main.py parse   my_resume.pdf                      # Resume parsing only
python main.py match   my_resume.pdf job_description.txt  # Match score check
python main.py cover   my_resume.pdf --jd jd.txt          # Generate cover letter
python main.py roadmap my_resume.pdf --jd jd.txt          # Skill gap + learning path
python main.py stats   --improve                          # Stats + self-improvement

# ── Job Fetcher ───────────────────────────────────────────────────────
python main.py fetch my_resume.pdf --limit 30 --output jobs.json
python main.py fetch my_resume.pdf --sources jsearch,adzuna

# ── Application Tracker ───────────────────────────────────────────────
python main.py track seed                                 # Load demo data
python main.py track report                               # Full tracker report
python main.py track list --outcome interview             # Filter by outcome
python main.py track update 5 offer --round 2            # Update outcome

# ── Analytics ─────────────────────────────────────────────────────────
python main.py analyze                                    # Full LLM analysis
python main.py analyze --skills python pytorch            # Custom skills
python main.py analyze --cached                           # Use cached result
python main.py analyze --output report.json               # Save to file

# ── Notifications ─────────────────────────────────────────────────────
python main.py notify test                                # Test all channels
python main.py notify send --type new_job_found --message "ML role found!"
python main.py notify daily                               # Send daily summary
python main.py notify log                                 # View delivery log
python main.py notify config                              # Channel config

# ── Dashboard & Server ────────────────────────────────────────────────
python main.py dashboard                                  # Open web UI
python main.py dashboard --port 8080 --no-open
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check (uptime monitoring) |
| GET | `/api/dashboard` | Full dashboard data payload |
| GET | `/api/history` | Application history |
| GET | `/api/skills` | Skill performance stats |
| GET | `/api/tracker/stats` | Tracker KPIs and stats |
| GET | `/api/tracker/applications` | List applications (filterable) |
| POST | `/api/tracker/update-outcome` | Update job outcome |
| GET | `/api/tracker/activity` | Recent activity feed |
| GET | `/api/analytics` | Latest analytics result |
| POST | `/api/analytics/run` | Trigger fresh LLM analysis |
| GET | `/api/notifications` | Notification delivery log |
| GET | `/api/notifications/config` | Channel configuration |
| POST | `/api/notifications/test` | Send test notification |
| POST | `/api/notifications/send` | Send custom notification |
| GET | `/docs` | Swagger UI (FastAPI only) |

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` **or** `OPENAI_API_KEY` | LLM provider key |
| `LLM_PROVIDER` | `groq` or `openai` |

### Job Boards (optional — needed for live fetching)

| Variable | Source | Free Tier |
|----------|--------|-----------|
| `RAPIDAPI_KEY` | jsearch.io (RapidAPI) | 200 req/month |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | adzuna.com | 250 req/month |
| *(none)* | RemoteOK + Himalayas | Unlimited |

### Notifications (optional)

| Variable | Purpose |
|----------|---------|
| `EMAIL_ENABLED=true` + SMTP fields in `.env` | Email (set host/user in `.env` per `core/notifier.py`) |
| `SENDGRID_ENABLED=true` + `SENDGRID_API_KEY` | SendGrid (100/day free) |
| `WHATSAPP_ENABLED=true` + `TWILIO_*` | WhatsApp via Twilio |

### Database

| Variable | Default | Notes |
|----------|---------|-------|
| `DB_PATH` | `db/agent_memory.db` | SQLite path (local dev) |
| `DATABASE_URL` | *(unset)* | PostgreSQL URL — auto-injected by Render/Railway |

---

## Deployment

### Option A — Render (Recommended, Free)

```bash
# 1. Push to GitHub
git push origin main

# 2. render.com → New → Blueprint → connect repo → render.yaml is auto-detected
# 3. Set env vars in Render Dashboard (GROQ_API_KEY etc.)
# DATABASE_URL auto-injected from PostgreSQL add-on
```

### Option B — Railway

```bash
# railway.app → New Project → Deploy from GitHub
# + Add Service → Database → PostgreSQL (DATABASE_URL auto-set)
railway variables set GROQ_API_KEY=<your_key>   # set in Railway dashboard, not in git
```

### Option C — Docker Compose (Local/VPS)

```bash
cp .env.example .env
# Add Postgres + DATABASE_URL + optional pgAdmin fields per docs/DOCKER_ENV.md
docker-compose up -d        # API on :8000 + PostgreSQL
```

### Frontend → Netlify / Vercel

```bash
# Netlify: publish dir = "dashboard", add BACKEND_URL env var
# The _redirects file proxies /api/* to your backend automatically
# No code changes needed in the frontend HTML
```

### Production start commands

```bash
# FastAPI + Gunicorn (production)
gunicorn backend.api:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT

# Or uvicorn directly
uvicorn backend.api:app --host 0.0.0.0 --port 8000

# Local dev (Flask)
python dashboard/server.py
```

---

## Database Schema

```sql
applications        -- Job applications + outcomes + scores
skill_performance   -- Which skills lead to callbacks
activity_log        -- Timeline of all status changes
strategy_log        -- Self-improvement strategy snapshots
analytics_log       -- Cached analytics results
notifications_log   -- All notifications sent (email/WhatsApp)
```

---

## Decision Flow

```
Resume Input
     │
     ▼ parse_resume()
Candidate Profile (JSON)
     │
     ▼ evaluate_candidate()
Tier: Elite / Premium / Standard
     │
     ├── Elite    → fetch jobs from all sources, min_score = 80
     ├── Premium  → fetch from APIs, min_score = 70
     └── Standard → fetch from free APIs, min_score = 60
     │
     ▼ fetch_jobs_for_candidate()
Raw Jobs (normalized, deduplicated)
     │
     ▼ rank_jobs()
Scored + Ranked Jobs
     │
     ▼ For each job:
     │
     ├── score > 85 AND confidence > 80 → AUTO_APPLY
     │       │
     │       └── apply_with_browser()
     │           ├── Success → save(outcome=pending) → notify_submitted()
     │           └── Fail    → save(outcome=error)   → mark_retry()
     │
     ├── score > 70 → REVIEW
     │       └── save(outcome=pending) → notify_review_required()
     │
     └── score ≤ 70 → SKIP
             └── save(outcome=skipped)
     │
     ▼ run_self_improvement()
Updated strategy for next run
     │
     ▼ (async, parallel)
     ├── notify_new_jobs_batch()   → Email/WhatsApp
     └── run_analysis()            → skill gaps, role fit, insights
```

---

## Notification Triggers

| Event | Trigger | Priority |
|-------|---------|----------|
| New high-match job found | After `rank_jobs()` | Medium (High if score ≥ 90) |
| Application submitted | After `apply_with_browser()` succeeds | Medium |
| Review required | After REVIEW decision | Medium |
| Callback / Interview | After `tracker.update_outcome('interview')` | **High** |
| Offer received | After `tracker.update_outcome('offer')` | **High** |
| Daily summary | Manual or scheduled | Low |
| Analytics complete | After `run_analysis()` (if enabled) | Low |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI / LLM | OpenAI GPT-4o or Groq (Llama 3 70B) |
| Resume Parsing | pdfplumber, python-docx |
| Web Automation | Playwright, Selenium |
| HTTP / Scraping | httpx, requests, BeautifulSoup4 |
| Backend API | FastAPI + Uvicorn + Gunicorn |
| Legacy Dev Server | Flask + flask-cors |
| Frontend | HTML5, Bootstrap 5, Chart.js, Bootstrap Icons |
| Database | SQLite (dev) / PostgreSQL (prod) via psycopg2 |
| Notifications | smtplib (SMTP) / SendGrid API / Twilio WhatsApp |
| Validation | Pydantic v2 |
| Data | Pandas |
| CLI | argparse + rich |
| Logging | loguru |
| Retry | tenacity |
| Containerization | Docker + docker-compose |
| CI/CD | GitHub Actions |
| Backend Hosting | Render / Railway |
| Frontend Hosting | Netlify / Vercel |

---

## License

MIT — free to use, modify, and deploy.
