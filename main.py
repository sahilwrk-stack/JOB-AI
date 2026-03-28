"""
Omniscient Resume AI — Main Entry Point
CLI for running the full agent pipeline or individual components.
"""

import sys
import os
import json
import argparse
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel   import Panel
from rich.table   import Table
from rich.text    import Text
from rich import box

console = Console(highlight=False, legacy_windows=False)

BANNER = """
[bold cyan]
  OMNISCIENT  RESUME  AI
[/bold cyan]
[bold yellow]  Advanced ATS + Context-Aware Resume Intelligence Agent[/bold yellow]
[dim]  SCAN -> FILTER -> ANALYZE -> RANK -> DECIDE -> APPLY -> LEARN -> UPDATE[/dim]
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def print_banner():
    console.print(BANNER)


def print_summary(result: dict):
    summary = result.get("summary", {})
    intel   = result.get("candidate_intel", {})
    improve = result.get("self_improvement", {})

    # Candidate card
    console.print(Panel(
        f"[bold green]Tier:[/bold green] {intel.get('candidate_tier', 'Unknown')}\n"
        f"[bold]Confidence:[/bold] {intel.get('confidence_score', 0)}%  |  "
        f"[bold]Market Demand:[/bold] {intel.get('market_demand_score', 0)}%\n"
        f"[bold]Min Salary:[/bold] ₹{intel.get('target_minimum_salary_inr', 0):,}/yr\n"
        f"[italic]{intel.get('tier_justification', '')}[/italic]",
        title="[bold cyan]🎯 Candidate Intelligence[/bold cyan]",
        border_style="cyan",
    ))

    # Applications table
    app_results = result.get("application_results", [])
    if app_results:
        table = Table(
            title="Job Application Results",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Job Title",     style="cyan",  no_wrap=True)
        table.add_column("Company",       style="white")
        table.add_column("Score",         style="bold yellow", justify="center")
        table.add_column("Confidence",    justify="center")
        table.add_column("Status",        justify="center")
        table.add_column("Risk",          justify="center")

        status_colors = {
            "AUTO_APPLY": "bold green",
            "REVIEW":     "bold yellow",
            "SKIP":       "dim",
            "ABORT":      "bold red",
        }

        for r in app_results:
            job    = r.get("job", {})
            match  = r.get("match_result", {})
            dec    = r.get("apply_decision", {})
            status = dec.get("execution_status", "?")
            risk   = dec.get("risk_level", "?")
            color  = status_colors.get(status, "white")

            table.add_row(
                job.get("title", ""),
                job.get("company", ""),
                str(match.get("match_score", 0)),
                f"{match.get('confidence_score', 0)}%",
                f"[{color}]{status}[/{color}]",
                f"{'🟢' if risk == 'Low' else '🟡' if risk == 'Medium' else '🔴'} {risk}",
            )
        console.print(table)

    # Summary stats
    console.print(Panel(
        f"Total Jobs Scanned : [bold]{summary.get('total_jobs', 0)}[/bold]\n"
        f"Passed Threshold   : [bold green]{summary.get('ranked_jobs', 0)}[/bold green]\n"
        f"Auto Applied       : [bold green]{summary.get('auto_applied', 0)}[/bold green]\n"
        f"Needs Review       : [bold yellow]{summary.get('reviewed', 0)}[/bold yellow]\n"
        f"Skipped            : [dim]{summary.get('skipped', 0)}[/dim]\n"
        f"Dry Run Mode       : {'[yellow]ON[/yellow]' if summary.get('dry_run') else '[red]OFF[/red]'}",
        title="[bold]📊 Pipeline Summary[/bold]",
        border_style="green",
    ))

    # Self-improvement insights
    if improve and improve.get("insights"):
        insights_text = "\n".join(
            f"  [{i+1}] {ins}" for i, ins in enumerate(improve["insights"])
        )
        console.print(Panel(
            f"{insights_text}\n\n"
            f"[bold]Strategy Update:[/bold] {improve.get('job_strategy_update', '')}",
            title="[bold magenta]🧠 Self-Improvement Insights[/bold magenta]",
            border_style="magenta",
        ))


def load_jobs_from_file(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def demo_jobs() -> list[dict]:
    """Built-in demo job descriptions for testing without a real job feed."""
    return [
        {
            "title":       "Machine Learning Engineer",
            "company":     "DataSpark AI",
            "location":    "Bangalore, India (Hybrid)",
            "url":         "https://example.com/jobs/ml-engineer-dataspark",
            "description": """
We are looking for a Machine Learning Engineer to join our AI team.

Requirements:
- 2+ years of experience with Python and ML frameworks
- Strong knowledge of scikit-learn, TensorFlow or PyTorch
- Experience with data preprocessing, feature engineering
- Familiarity with MLOps tools (MLflow, Kubeflow)
- SQL and data pipeline experience
- NLP or Computer Vision exposure is a plus

Responsibilities:
- Build and deploy ML models to production
- Design data pipelines for model training
- Work with data scientists to productionize research models
- Optimize model performance and inference speed
""",
        },
        {
            "title":       "Full Stack Developer",
            "company":     "TechVentures Inc",
            "location":    "Remote",
            "url":         "https://example.com/jobs/fullstack-techventures",
            "description": """
Join our product team as a Full Stack Developer.

Requirements:
- 1-3 years experience with React.js and Node.js
- RESTful API development
- PostgreSQL or MongoDB
- Git, Docker basics
- TypeScript preferred

Nice to have:
- Next.js experience
- AWS deployment knowledge
- Redis / caching strategies
""",
        },
        {
            "title":       "Data Analyst",
            "company":     "FinInsights Corp",
            "location":    "Mumbai, India",
            "url":         "https://example.com/jobs/data-analyst-fininsights",
            "description": """
We need a Data Analyst to support our BI and reporting team.

Requirements:
- SQL (intermediate to advanced)
- Excel / Google Sheets
- Python or R for data analysis
- Tableau or Power BI
- Strong communication skills

Responsibilities:
- Create dashboards and reports
- Conduct ad-hoc analysis for stakeholders
- Data cleaning and transformation
- Present insights to non-technical teams
""",
        },
        {
            "title":       "DevOps Engineer",
            "company":     "CloudBase Systems",
            "location":    "Hyderabad, India",
            "url":         "https://example.com/jobs/devops-cloudbase",
            "description": """
Senior DevOps Engineer needed for our infrastructure team.

Requirements:
- 4+ years of DevOps experience
- AWS / GCP / Azure (at least one cloud)
- Kubernetes and Docker (production experience)
- CI/CD pipelines (Jenkins, GitHub Actions)
- Terraform or Ansible
- Strong Linux administration
- Monitoring (Prometheus, Grafana, ELK)
""",
        },
    ]


# ── CLI Commands ──────────────────────────────────────────────────────────────

def cmd_run(args):
    from core.orchestrator import run_pipeline

    resume  = args.resume
    is_text = not os.path.exists(resume) if resume else False

    jobs        = []
    auto_fetch  = getattr(args, "fetch", False)

    if args.jobs:
        jobs = load_jobs_from_file(args.jobs)
        console.print(f"\n[cyan]Loaded {len(jobs)} jobs from file.[/cyan]")
    elif auto_fetch:
        console.print("\n[cyan]Auto-fetch mode: fetching live jobs from APIs...[/cyan]")
    else:
        console.print("[yellow]No jobs file provided — using built-in demo jobs.[/yellow]")
        jobs = demo_jobs()

    console.print(
        f"[cyan]Pipeline:[/cyan] Resume={resume} | "
        f"AutoFetch={auto_fetch} | DryRun={not args.apply}\n"
    )

    result = run_pipeline(
        resume_source=resume,
        jobs=jobs if jobs else None,
        is_text=is_text,
        dry_run=not args.apply,
        run_learning=not args.no_learn,
        auto_fetch=auto_fetch,
        use_scrapers=getattr(args, "scrapers", False),
        max_jobs=getattr(args, "max_jobs", 50),
    )

    print_summary(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        console.print(f"\n[green]Full results saved to:[/green] {args.output}")


def cmd_fetch(args):
    """Stand-alone live job fetch command."""
    from core.resume_parser   import parse_resume, parse_resume_from_text
    from core.candidate_intel import evaluate_candidate
    from core.job_aggregator  import aggregate_jobs

    resume  = args.resume
    is_text = not os.path.exists(resume)

    console.print("\n[cyan]Parsing resume...[/cyan]")
    parsed = parse_resume(resume) if not is_text else parse_resume_from_text(resume)

    console.print("[cyan]Evaluating candidate...[/cyan]")
    intel = evaluate_candidate(parsed)

    sources = args.sources.split(",") if args.sources else None
    console.print(f"[cyan]Fetching jobs (sources={sources or 'auto'})...[/cyan]\n")

    result = aggregate_jobs(
        profile=parsed,
        sources=sources,
        max_total=args.limit,
        use_scrapers=args.scrapers,
        enrich_skills=not args.no_enrich,
    )

    jobs = result["jobs"]
    meta = result["meta"]

    # Print table
    from rich.table import Table
    from rich import box as rbox
    table = Table(
        title=f"Live Job Results ({meta['final_count']} jobs)",
        box=rbox.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Title",    style="cyan",  no_wrap=True, max_width=35)
    table.add_column("Company",  style="white", max_width=20)
    table.add_column("Location", max_width=20)
    table.add_column("Skills",   max_width=30)
    table.add_column("Salary",   max_width=18)
    table.add_column("Source",   style="dim",   max_width=12)

    for j in jobs:
        skills_str = ", ".join(j.get("skills_required", [])[:4])
        table.add_row(
            j.get("title", ""),
            j.get("company", ""),
            j.get("location", ""),
            skills_str,
            j.get("salary", "Not disclosed"),
            j.get("_source", ""),
        )
    console.print(table)

    console.print(
        f"\n[bold]Meta:[/bold] raw={meta['raw_fetched']} | "
        f"deduped={meta['after_dedup']} | filtered={meta['after_filter']} | "
        f"final={meta['final_count']}"
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"jobs": jobs, "meta": meta}, f, indent=2, default=str)
        console.print(f"[green]Saved to:[/green] {args.output}")


def cmd_parse(args):
    from core.resume_parser import parse_resume, parse_resume_from_text
    resume = args.resume
    if os.path.exists(resume):
        result = parse_resume(resume)
    else:
        result = parse_resume_from_text(resume)
    console.print_json(json.dumps(result, indent=2))


def cmd_match(args):
    from core.resume_parser import parse_resume
    from core.job_matcher   import match_job

    parsed = parse_resume(args.resume)
    jd     = open(args.jd, encoding="utf-8").read()
    result = match_job(parsed, jd, args.title or "Target Role")
    console.print_json(json.dumps(result, indent=2))


def cmd_cover(args):
    from core.orchestrator import generate_cover_letter
    job = {
        "title":       args.title or "Target Role",
        "company":     args.company or "Target Company",
        "description": open(args.jd, encoding="utf-8").read() if args.jd else "",
    }
    letter = generate_cover_letter(args.resume, job)
    console.print(Panel(letter, title="[bold cyan]Generated Cover Letter[/bold cyan]"))


def cmd_roadmap(args):
    from core.orchestrator import generate_learning_roadmap
    job = {
        "title":       args.title or "Target Role",
        "description": open(args.jd, encoding="utf-8").read() if args.jd else "",
    }
    roadmap = generate_learning_roadmap(args.resume, job)
    console.print(Panel(roadmap, title="[bold green]30-Day Learning Roadmap[/bold green]"))


def cmd_track(args):
    """Application Tracker CLI."""
    from core.tracker import (
        log_application, update_outcome, update_status,
        generate_report, list_applications, seed_demo_data,
        Outcome, Status,
    )
    from core.memory import init_db
    init_db()

    sub = args.track_sub

    # ── track report ────────────────────────────────────────────────────────
    if sub == "report":
        stats = generate_report()
        c, r, sc = stats["counts"], stats["rates"], stats["score_stats"]

        console.print(Panel(
            f"[bold green]Total Applied:[/bold green]    {c.get('applied',0)}\n"
            f"[bold]Total Tracked:[/bold]    {c.get('total',0)}\n\n"
            f"[bold cyan]Interviews:[/bold cyan]       {c.get('interviews',0)}\n"
            f"[bold yellow]Offers:[/bold yellow]           {c.get('offers',0)}\n"
            f"[bold red]Rejections:[/bold red]        {c.get('rejections',0)}\n"
            f"[dim]Pending:[/dim]           {c.get('pending',0)}\n\n"
            f"[bold green]Success Rate:[/bold green]     {r.get('success_rate',0)}%\n"
            f"[bold cyan]Interview Rate:[/bold cyan]   {r.get('interview_rate',0)}%\n"
            f"[bold]Response Rate:[/bold]    {r.get('response_rate',0)}%\n\n"
            f"[dim]Avg Match Score:  {sc.get('average',0)} | Best: {sc.get('maximum',0)} | Lowest: {sc.get('minimum',0)}[/dim]",
            title="[bold]Application Tracker Report[/bold]",
            border_style="green",
        ))

        skills = stats.get("top_skills", [])[:8]
        if skills:
            table = Table(title="Top Performing Skills", box=box.SIMPLE)
            table.add_column("Skill")
            table.add_column("Seen", justify="right")
            table.add_column("Interviews", justify="right")
            table.add_column("Offers", justify="right")
            table.add_column("Rate", justify="right")
            for s in skills:
                table.add_row(
                    s["skill"],
                    str(s.get("appearances", 0)),
                    str(s.get("interviews", 0)),
                    str(s.get("offers", 0)),
                    f"{s.get('interview_rate', 0)}%",
                )
            console.print(table)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, default=str)
            console.print(f"[green]Full report saved to:[/green] {args.output}")

    # ── track update ────────────────────────────────────────────────────────
    elif sub == "update":
        identifier = int(args.id) if args.id.isdigit() else args.id
        ok = update_outcome(
            identifier,
            args.outcome,
            interview_round=args.round or 0,
            rejection_reason=args.reason or "",
            notes=args.notes or "",
        )
        if ok:
            console.print(f"[green]Updated[/green] id={args.id} -> outcome=[bold]{args.outcome}[/bold]")
        else:
            console.print(f"[red]Not found:[/red] {args.id}")

    # ── track list ──────────────────────────────────────────────────────────
    elif sub == "list":
        apps = list_applications(
            status=args.status or None,
            outcome=args.outcome or None,
            limit=args.limit,
        )
        if not apps:
            console.print("[yellow]No applications found.[/yellow]")
            return
        table = Table(title=f"{len(apps)} Applications", box=box.ROUNDED)
        table.add_column("ID", style="dim", width=5)
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Score", justify="right")
        table.add_column("Status")
        table.add_column("Outcome")
        table.add_column("Applied")
        for a in apps:
            sc = a.get("match_score", 0)
            table.add_row(
                str(a["id"]),
                a.get("company", ""),
                (a.get("job_title") or "")[:30],
                str(sc),
                a.get("execution_status", ""),
                a.get("outcome", "pending"),
                (a.get("applied_at") or "")[:10],
            )
        console.print(table)

    # ── track seed ──────────────────────────────────────────────────────────
    elif sub == "seed":
        seed_demo_data()
        console.print("[green]Demo data seeded.[/green] Run [bold]python main.py track report[/bold] to see stats.")

    else:
        console.print("[yellow]Usage:[/yellow] python main.py track {report|update|list|seed}")


def cmd_analyze(args):
    from core.analytics import run_analysis, get_latest_analytics
    from core.memory    import init_db

    init_db()

    skills  = getattr(args, "skills",  None)
    cached  = getattr(args, "cached",  False)
    output  = getattr(args, "output",  None)
    no_llm  = getattr(args, "no_llm",  False)

    if cached:
        result = get_latest_analytics()
        if not result:
            console.print("[yellow]No cached analytics found. Running fresh analysis...[/yellow]")
            result = run_analysis(candidate_skills=skills, force_llm=not no_llm)
    else:
        result = run_analysis(candidate_skills=skills, force_llm=not no_llm)

    if not result:
        console.print("[red]Analysis failed. Check API keys and DB.[/red]")
        return

    # ── Summary Panel ────────────────────────────────────────────────────────
    stats = result.get("summary_stats", {})
    console.print(Panel(
        f"[bold green]Data Analytics AI — Results[/bold green]\n\n"
        f"  Total Applications : [bold]{stats.get('total', 0)}[/bold]\n"
        f"  Auto-Applied       : [bold]{stats.get('applied', 0)}[/bold]\n"
        f"  Callbacks          : [bold]{stats.get('callbacks', 0)}[/bold]\n"
        f"  Callback Rate      : [bold cyan]{stats.get('callback_rate', 0)}%[/bold cyan]\n"
        f"  Optimal Score      : [bold cyan]{result.get('optimal_score_threshold', 70)}+[/bold cyan]",
        title="[bold]Analytics Summary[/bold]",
        border_style="green",
    ))

    # ── Insights ─────────────────────────────────────────────────────────────
    insights = result.get("insights", [])
    if insights:
        ins_table = Table(title="AI Insights", box=box.SIMPLE, show_header=True)
        ins_table.add_column("Priority", style="bold", width=8)
        ins_table.add_column("Title",    style="bold cyan", width=30)
        ins_table.add_column("Finding",  width=50)
        ins_table.add_column("Action",   style="green dim", width=40)
        for ins in insights[:8]:
            if isinstance(ins, str):
                ins_table.add_row("—", ins[:30], ins[:50], "")
            else:
                prio_color = {"High": "red", "Medium": "yellow", "Low": "green"}.get(ins.get("priority",""), "white")
                ins_table.add_row(
                    f"[{prio_color}]{ins.get('priority','—')}[/{prio_color}]",
                    (ins.get("title","") or "")[:30],
                    (ins.get("finding","") or "")[:50],
                    (ins.get("action","") or "")[:40],
                )
        console.print(ins_table)

    # ── Skill Gaps ───────────────────────────────────────────────────────────
    gaps = result.get("skill_gap_analysis", [])
    if gaps:
        gap_table = Table(title="Skill Gap Analysis", box=box.SIMPLE, show_header=True)
        gap_table.add_column("Skill",        style="bold", width=18)
        gap_table.add_column("Severity",     width=10)
        gap_table.add_column("Missed Jobs",  width=12)
        gap_table.add_column("Demand",       width=12)
        gap_table.add_column("Why Critical", width=50)
        for g in gaps[:10]:
            sev = g.get("gap_severity", "Low")
            sev_col = {"High": "red", "Medium": "yellow", "Low": "green"}.get(sev, "white")
            gap_table.add_row(
                g.get("skill",""),
                f"[{sev_col}]{sev}[/{sev_col}]",
                str(g.get("appears_in_missed_jobs","—")),
                g.get("market_demand","—"),
                (g.get("why_critical","") or "")[:50],
            )
        console.print(gap_table)

    # ── Learning Roadmap ─────────────────────────────────────────────────────
    learning = result.get("recommended_learning", [])
    if learning:
        learn_table = Table(title="Learning Roadmap", box=box.SIMPLE, show_header=True)
        learn_table.add_column("#",       width=4)
        learn_table.add_column("Skill",   style="bold cyan", width=18)
        learn_table.add_column("Time",    width=12)
        learn_table.add_column("Why Now", width=40)
        learn_table.add_column("Resource", width=40)
        for item in learning[:8]:
            learn_table.add_row(
                str(item.get("priority","")),
                item.get("skill",""),
                item.get("estimated_time", str(item.get("estimated_weeks","")) + "w"),
                (item.get("why_now","") or "")[:40],
                (item.get("free_resource","") or (item.get("resources",[""])[0] if item.get("resources") else ""))[:40],
            )
        console.print(learn_table)

    # ── Target Roles ─────────────────────────────────────────────────────────
    role_fit = result.get("role_fit_analysis", [])
    if role_fit:
        role_table = Table(title="Role Fit Analysis", box=box.SIMPLE, show_header=True)
        role_table.add_column("Role",      style="bold", width=28)
        role_table.add_column("Fit",       width=8)
        role_table.add_column("Readiness", width=14)
        role_table.add_column("Missing",   width=35)
        for r in role_fit[:6]:
            rdy = r.get("readiness","")
            rdy_col = {"Ready": "green", "Almost Ready": "yellow", "Needs Work": "red"}.get(rdy, "white")
            role_table.add_row(
                r.get("role",""),
                f"[cyan]{r.get('fit_score',0)}%[/cyan]",
                f"[{rdy_col}]{rdy}[/{rdy_col}]",
                ", ".join(r.get("missing_skills",[])[:3]),
            )
        console.print(role_table)

    # ── Strategy ─────────────────────────────────────────────────────────────
    strategy = result.get("strategy_update","")
    if strategy:
        console.print(Panel(
            f"[bold yellow]{strategy}[/bold yellow]",
            title="[bold]Strategy Update[/bold]",
            border_style="yellow",
        ))

    # ── Save ─────────────────────────────────────────────────────────────────
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        console.print(f"[green]Analytics saved to[/green] {output}")


def cmd_notify(args):
    from core.notifier import (
        send_test_notification, notify_daily_summary,
        get_notification_log, get_notification_stats, get_notification_config,
        send_notification,
    )
    from core.memory import init_db
    init_db()

    sub = getattr(args, "notify_sub", None)

    if sub == "test":
        console.print("[cyan]Sending test notification...[/cyan]")
        result = send_test_notification()
        status = result.get("status", {})
        console.print(Panel(
            f"[bold]Notification Type:[/bold] {result.get('notification_type','')}\n"
            f"[bold]Message:[/bold]           {result.get('message','')}\n"
            f"[bold]Priority:[/bold]          {result.get('priority','')}\n"
            f"[bold]Channel Status:[/bold]    {json.dumps(status, indent=2) if isinstance(status, dict) else status}",
            title="[bold green]Test Notification[/bold green]",
            border_style="green",
        ))

    elif sub == "send":
        ntype   = getattr(args, "type",     "test")
        message = getattr(args, "message",  "Manual notification from CLI")
        force   = getattr(args, "force",    False)
        result  = send_notification(ntype, message, force=force, async_send=False)
        console.print_json(json.dumps(result, default=str))

    elif sub == "daily":
        from core.tracker import generate_report
        report = generate_report()
        s      = report.get("stats", {})
        result = notify_daily_summary({
            "applied":    s.get("by_status", {}).get("AUTO_APPLY", 0),
            "interviews": s.get("by_outcome", {}).get("interview",  0),
            "offers":     s.get("by_outcome", {}).get("offer",      0),
            "new_jobs":   s.get("total", 0),
        })
        console.print_json(json.dumps(result, default=str))

    elif sub == "log":
        limit = getattr(args, "limit", 20)
        logs  = get_notification_log(limit)
        if not logs:
            console.print("[yellow]No notifications logged yet.[/yellow]")
            return
        t = Table(title="Notification Log", box=box.SIMPLE, show_header=True)
        t.add_column("Type",     style="bold cyan", width=22)
        t.add_column("Priority", width=8)
        t.add_column("Channel",  width=10)
        t.add_column("Status",   width=8)
        t.add_column("Message",  width=50)
        t.add_column("Sent At",  width=18)
        for n in logs:
            prio_col = {"High":"red","Medium":"yellow","Low":"green"}.get(n.get("priority",""), "white")
            t.add_row(
                n.get("notification_type",""),
                f"[{prio_col}]{n.get('priority','—')}[/{prio_col}]",
                n.get("channel","—"),
                n.get("status","—"),
                (n.get("message","") or "")[:50],
                (n.get("sent_at","") or "")[:16],
            )
        console.print(t)

    elif sub == "config":
        cfg = get_notification_config()
        console.print(Panel(
            "\n".join(f"  [dim]{k:26s}[/dim] [cyan]{v}[/cyan]" for k, v in cfg.items()),
            title="[bold]Notification Configuration[/bold]",
            border_style="cyan",
        ))

    elif sub == "stats":
        stats = get_notification_stats()
        console.print_json(json.dumps(stats, default=str))

    else:
        console.print(
            "[bold yellow]notify sub-commands:[/bold yellow]\n"
            "  python main.py notify test                         (send test)\n"
            "  python main.py notify send --type new_job_found --message 'Test'\n"
            "  python main.py notify daily                        (daily summary)\n"
            "  python main.py notify log --limit 30              (view log)\n"
            "  python main.py notify config                       (show config)\n"
            "  python main.py notify stats                        (stats by type)\n"
        )


def cmd_safety(args):
    """Safe Automation AI — manage safety rules, limits, and circuit breaker."""
    from core.safety_guard import (
        get_safety_stats, get_safety_log,
        reset_halt, set_review_mode, set_auto_mode,
    )

    sub = getattr(args, "safety_sub", None)

    if sub == "status" or not sub:
        stats = get_safety_stats()
        halted   = stats.get("system_halted", False)
        sys_mode = stats.get("system_mode", "auto")

        status_color = "red" if halted else ("yellow" if sys_mode == "review" else "green")
        status_text  = "HALTED" if halted else ("REVIEW MODE" if sys_mode == "review" else "SAFE")

        console.print(Panel(
            f"[bold {status_color}]Status: {status_text}[/bold {status_color}]\n"
            + (f"[red]Halt reason: {stats.get('halt_reason','')}[/red]\n" if halted else "")
            + (f"[yellow]Mode reason: {stats.get('mode_reason','')}[/yellow]\n" if sys_mode == "review" else "")
            + f"\n[bold]Daily Applications:[/bold]  {stats.get('applied_today',0)} / {stats.get('daily_limit',10)}"
            f"  (hard cap: {stats.get('hard_cap','?')})\n"
            f"[bold]Remaining slots:[/bold]    {stats.get('remaining_today',0)}\n"
            f"[bold]Total blocked:[/bold]      {stats.get('total_blocked',0)}\n"
            f"[bold]Consecutive errors:[/bold] {stats.get('consecutive_errors',0)}\n"
            f"[bold]Avg confidence:[/bold]     {stats.get('last_confidence_avg') or '—'}%",
            title="[bold cyan]Safety Guard Status[/bold cyan]",
            border_style=status_color,
        ))

        cats = stats.get("blocks_by_category",{})
        if cats:
            t = Table(title="Blocks by Category", box=box.SIMPLE)
            t.add_column("Category"); t.add_column("Count")
            for cat, cnt in cats.items():
                t.add_row(cat.replace("_"," "), str(cnt))
            console.print(t)

    elif sub == "log":
        limit = getattr(args, "limit", 20)
        events = get_safety_log(limit=limit)
        if not events:
            console.print("[yellow]No safety events recorded yet.[/yellow]")
            return
        t = Table(title="Safety Event Log", box=box.ROUNDED)
        t.add_column("Event", style="bold")
        t.add_column("Category")
        t.add_column("Job / Detail")
        t.add_column("Time", style="dim")
        for e in events:
            color = {"BLOCK":"red","WARNING":"yellow","HALT":"red","RESET":"green","MODE_CHANGE":"blue"}.get(e.get("event_type",""),"white")
            t.add_row(
                f"[{color}]{e.get('event_type','')}[/{color}]",
                (e.get("category","") or "").replace("_"," "),
                e.get("job_title","") or e.get("detail","")[:60] or "—",
                (e.get("logged_at","") or "")[:16],
            )
        console.print(t)

    elif sub == "reset-halt":
        result = reset_halt("Manual reset via CLI")
        console.print(f"[bold green]{result.get('message','')}[/bold green]")

    elif sub == "review-mode":
        result = set_review_mode("Manual override via CLI")
        console.print(f"[bold yellow]System switched to REVIEW mode.[/bold yellow]")

    elif sub == "auto-mode":
        result = set_auto_mode("Manual override via CLI")
        console.print(f"[bold green]System restored to AUTO mode.[/bold green]")

    else:
        console.print("[red]Unknown safety sub-command.[/red] Use: status | log | reset-halt | review-mode | auto-mode")


def cmd_orchestrate(args):
    """Master Orchestrator AI — coordinate all subsystems intelligently."""
    from core.master_orchestrator import run_master_orchestrator, get_master_status

    if getattr(args, "status", False):
        # Just show current status, no execution
        result = get_master_status()
        status = result.get("system_status", "unknown").upper()
        color  = {"HEALTHY": "green", "DEGRADED": "red", "NEEDS_ATTENTION": "yellow"}.get(status, "white")
        console.print(Panel(
            f"[bold {color}]Status: {status}[/bold {color}]\n"
            f"[dim]{result.get('status_reason','')}[/dim]\n\n"
            f"[bold]Callback Rate:[/bold] {result.get('performance',{}).get('callback_rate',0)}%  |  "
            f"[bold]Interviews:[/bold] {result.get('performance',{}).get('interview_rate',0)}%  |  "
            f"[bold]Offers:[/bold] {result.get('performance',{}).get('offer_rate',0)}%\n\n"
            f"[bold]Strategy:[/bold] [italic]{result.get('next_strategy','—')[:120]}[/italic]\n\n"
            f"[dim]Last run: {result.get('timestamp','Never')}[/dim]",
            title="[bold cyan]Master Orchestrator Status[/bold cyan]",
            border_style=color,
        ))
        return

    if getattr(args, "plan", False):
        # Show adaptive plan without executing
        from core.master_orchestrator import (
            _read_system_state, _compute_adaptive_thresholds, _assess_system_health
        )
        state  = _read_system_state()
        thresh = _compute_adaptive_thresholds(state)
        health, reason = _assess_system_health(state, thresh)
        console.print(Panel(
            f"[bold green]Health:[/bold green] {health.upper()} — {reason}\n\n"
            f"[bold]Adaptive Thresholds:[/bold]\n"
            f"  Apply  ≥ [cyan]{thresh['apply_threshold']}[/cyan]  |  "
            f"  Review ≥ [cyan]{thresh['review_threshold']}[/cyan]\n"
            f"  Adjustment: {thresh.get('reason','none')}\n\n"
            f"[bold]Performance (30d):[/bold]\n"
            f"  Applied: {state.applied_count}  |  Callbacks: {state.callback_rate}%  |  "
            f"Interviews: {state.interview_rate}%  |  Offers: {state.offer_rate}%\n\n"
            f"[bold]Top Skills (high callback):[/bold] {', '.join(state.top_skills[:5]) or '—'}\n"
            f"[bold]Skill Gaps (urgent):[/bold]       {', '.join(state.top_skill_gaps[:4]) or '—'}\n"
            f"[bold]Best Sources:[/bold]               {', '.join(state.best_sources) or '—'}\n"
            f"[bold]Avoid Sources:[/bold]              {', '.join(state.worst_sources) or '—'}",
            title="[bold cyan]Adaptive Execution Plan[/bold cyan]",
            border_style="cyan",
        ))
        return

    # Full orchestration run
    resume = getattr(args, "resume", None)
    result = run_master_orchestrator(
        resume_source     = resume,
        dry_run           = not getattr(args, "apply", False),
        force_pipeline    = getattr(args, "force", False),
        max_applications  = getattr(args, "max", 5),
        override_strategy = getattr(args, "strategy", None),
    )

    status = result.get("system_status", "unknown").upper()
    color  = {"HEALTHY": "green", "DEGRADED": "red", "NEEDS_ATTENTION": "yellow"}.get(status, "white")

    # Status panel
    console.print(Panel(
        f"[bold {color}]{status}[/bold {color}]  —  {result.get('status_reason','')}\n\n"
        f"Callback: {result.get('performance',{}).get('callback_rate',0)}%  "
        f"Interview: {result.get('performance',{}).get('interview_rate',0)}%  "
        f"Offer: {result.get('performance',{}).get('offer_rate',0)}%",
        title="[bold cyan]Master Orchestrator[/bold cyan]",
        border_style=color,
    ))

    # Actions taken
    actions = result.get("actions_taken", [])
    if actions:
        tbl = Table(title="Actions Taken", box=box.SIMPLE)
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("Action")
        for i, a in enumerate(actions, 1):
            tbl.add_row(str(i), a)
        console.print(tbl)

    # Learning updates
    updates = result.get("learning_updates", [])
    if updates:
        tbl2 = Table(title="Learning Updates", box=box.SIMPLE)
        tbl2.add_column("Type", style="cyan", width=22)
        tbl2.add_column("Change")
        tbl2.add_column("Conf", width=6)
        for u in updates:
            tbl2.add_row(
                u.get("update_type",""),
                f"{u.get('old_value','')} -> {u.get('new_value','')}",
                u.get("confidence",""),
            )
        console.print(tbl2)

    # Strategy
    console.print(Panel(
        f"[italic]{result.get('next_strategy','—')}[/italic]",
        title="[bold yellow]Next Strategy[/bold yellow]",
        border_style="yellow",
    ))

    if getattr(args, "output", None):
        out_path = args.output
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        console.print(f"[dim]Full result saved to {out_path}[/dim]")


def cmd_dashboard(args):
    import webbrowser
    import threading
    from dashboard.server import app as dash_app

    port = getattr(args, "port", 5050)
    url  = f"http://localhost:{port}"

    console.print(Panel(
        f"[bold green]Dashboard server starting...[/bold green]\n\n"
        f"  URL     : [bold cyan]{url}[/bold cyan]\n"
        f"  API     : [dim]{url}/api/dashboard[/dim]\n"
        f"  History : [dim]{url}/api/history[/dim]\n\n"
        f"[dim]Press Ctrl+C to stop.[/dim]",
        title="[bold]Omniscient AI Dashboard[/bold]",
        border_style="cyan",
    ))

    if not getattr(args, "no_open", False):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    dash_app.run(host="0.0.0.0", port=port, debug=False)


def cmd_stats(args):
    from core.memory       import get_all_applications, get_skill_performance
    from core.self_improve import run_self_improvement

    apps   = get_all_applications(50)
    skills = get_skill_performance()

    if apps:
        table = Table(title="Recent Applications", box=box.SIMPLE)
        for col in ("Job Title", "Company", "Score", "Status", "Outcome", "Applied At"):
            table.add_column(col)
        for a in apps[:15]:
            table.add_row(
                a.get("job_title", ""),
                a.get("company", ""),
                str(a.get("match_score", 0)),
                a.get("execution_status", ""),
                a.get("outcome", "pending"),
                (a.get("applied_at") or "")[:16],
            )
        console.print(table)
    else:
        console.print("[yellow]No applications recorded yet.[/yellow]")

    if args.improve:
        result = run_self_improvement()
        console.print_json(json.dumps(result, indent=2))


# ── Argument Parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Omniscient Resume AI — Advanced ATS + Job Matching Agent"
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run the full agent pipeline")
    p_run.add_argument("resume",              help="Path to resume (PDF/DOCX/TXT)")
    p_run.add_argument("--jobs",    "-j",     help="Path to JSON file with job listings")
    p_run.add_argument("--fetch",   "-f",     action="store_true",
                       help="Fetch live jobs from APIs (overrides --jobs)")
    p_run.add_argument("--scrapers",          action="store_true",
                       help="Enable Playwright scrapers as fallback (requires playwright install)")
    p_run.add_argument("--max-jobs",          type=int, default=50,
                       help="Max jobs to fetch/process (default: 50)")
    p_run.add_argument("--apply",             action="store_true",
                       help="Enable real browser form submission")
    p_run.add_argument("--no-learn",          action="store_true",
                       help="Skip self-improvement step")
    p_run.add_argument("--output",  "-o",     help="Save full results to JSON file")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch live job listings for a resume")
    p_fetch.add_argument("resume",              help="Path to resume (PDF/DOCX/TXT)")
    p_fetch.add_argument("--sources", "-s",     help="Comma-separated sources: jsearch,adzuna,remoteok,himalayas,internshala,linkedin,indeed")
    p_fetch.add_argument("--limit",   "-n",     type=int, default=30,
                         help="Max total jobs to return (default: 30)")
    p_fetch.add_argument("--scrapers",          action="store_true",
                         help="Enable Playwright scrapers")
    p_fetch.add_argument("--no-enrich",         action="store_true",
                         help="Skip LLM skill enrichment")
    p_fetch.add_argument("--output",  "-o",     help="Save results to JSON file")

    # parse
    p_parse = sub.add_parser("parse", help="Parse a resume and print structured JSON")
    p_parse.add_argument("resume", help="Path to resume")

    # match
    p_match = sub.add_parser("match", help="Match a resume against a single job description")
    p_match.add_argument("resume",         help="Path to resume")
    p_match.add_argument("jd",             help="Path to job description text file")
    p_match.add_argument("--title", "-t",  help="Job title (optional)")

    # cover
    p_cover = sub.add_parser("cover", help="Generate a cover letter")
    p_cover.add_argument("resume",            help="Path to resume")
    p_cover.add_argument("--jd",              help="Path to job description")
    p_cover.add_argument("--title",  "-t",    help="Job title")
    p_cover.add_argument("--company", "-c",   help="Company name")

    # roadmap
    p_road = sub.add_parser("roadmap", help="Generate a 30-day learning roadmap")
    p_road.add_argument("resume",          help="Path to resume")
    p_road.add_argument("--jd",            help="Path to job description")
    p_road.add_argument("--title", "-t",   help="Job title")

    # stats
    p_stats = sub.add_parser("stats", help="Show application history and insights")
    p_stats.add_argument("--improve", "-i", action="store_true",
                         help="Run self-improvement analysis")

    # track
    p_track = sub.add_parser("track", help="Application tracker: report, update outcome, list")
    track_sub = p_track.add_subparsers(dest="track_sub")

    # track report
    p_tr = track_sub.add_parser("report", help="Generate tracker stats report")
    p_tr.add_argument("--output", "-o", help="Save JSON report to file")

    # track update
    p_tu = track_sub.add_parser("update", help="Update outcome for an application")
    p_tu.add_argument("id",                  help="Application DB ID or URL")
    p_tu.add_argument("outcome",             help="pending | interview | offer | rejected | withdrawn | ghosted")
    p_tu.add_argument("--round",   "-r",     type=int, default=0, help="Interview round number")
    p_tu.add_argument("--reason",            help="Rejection reason")
    p_tu.add_argument("--notes",             help="Extra notes")

    # track list
    p_tl = track_sub.add_parser("list", help="List tracked applications")
    p_tl.add_argument("--status",            help="Filter by status (AUTO_APPLY/REVIEW/SKIP)")
    p_tl.add_argument("--outcome",           help="Filter by outcome (pending/interview/...)")
    p_tl.add_argument("--limit",  "-n",      type=int, default=30)

    # track seed
    track_sub.add_parser("seed", help="Insert demo data for testing")

    # notify
    p_notify = sub.add_parser("notify", help="Notification AI: send alerts, view log, configure channels")
    notify_sub = p_notify.add_subparsers(dest="notify_sub")

    # notify test
    notify_sub.add_parser("test", help="Send test notification to verify all channels")

    # notify send
    p_ns = notify_sub.add_parser("send", help="Send a custom notification")
    p_ns.add_argument("--type",    "-t", default="test",
                      help="Notification type (new_job_found | application_submitted | callback_received | ...)")
    p_ns.add_argument("--message", "-m", default="Manual notification from CLI",
                      help="Notification message text")
    p_ns.add_argument("--force",         action="store_true",
                      help="Bypass quiet hours and score threshold")

    # notify daily
    notify_sub.add_parser("daily", help="Send daily summary notification")

    # notify log
    p_nl = notify_sub.add_parser("log", help="View notification delivery log")
    p_nl.add_argument("--limit", "-n", type=int, default=20)

    # notify config
    notify_sub.add_parser("config", help="Show current notification configuration")

    # notify stats
    notify_sub.add_parser("stats", help="Show notification stats by type")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Run Data Analytics AI: insights, gaps, roadmap")
    p_analyze.add_argument("--skills",  "-s", nargs="*",
                           help="Override candidate skills for role matching (space-separated)")
    p_analyze.add_argument("--cached",  action="store_true",
                           help="Return cached result without running LLM again")
    p_analyze.add_argument("--output",  "-o", help="Save JSON result to file")
    p_analyze.add_argument("--no-llm",  action="store_true",
                           help="Skip LLM; use statistical analysis only")

    # safety
    p_safety = sub.add_parser("safety", help="Safety Automation AI — limits, duplicates, circuit breaker")
    safety_sub = p_safety.add_subparsers(dest="safety_sub")
    safety_sub.add_parser("status",       help="Show safety system status and daily stats")
    p_sl = safety_sub.add_parser("log",   help="View recent safety events")
    p_sl.add_argument("--limit", "-n",    type=int, default=20)
    safety_sub.add_parser("reset-halt",   help="Clear system halt (circuit breaker reset)")
    safety_sub.add_parser("review-mode",  help="Force system into review mode")
    safety_sub.add_parser("auto-mode",    help="Restore auto mode")

    # orchestrate
    p_orch = sub.add_parser("orchestrate", help="Master Orchestrator AI — adaptive pipeline coordination")
    p_orch.add_argument("resume",         nargs="?",      help="Path to resume (optional for status/plan modes)")
    p_orch.add_argument("--status",       action="store_true", help="Show current system status (no execution)")
    p_orch.add_argument("--plan",         action="store_true", help="Show adaptive execution plan (no execution)")
    p_orch.add_argument("--apply",        action="store_true", help="Actually apply (disables dry_run)")
    p_orch.add_argument("--force",        action="store_true", help="Force pipeline even without resume changes")
    p_orch.add_argument("--max",          type=int, default=5, help="Max applications per run (default: 5)")
    p_orch.add_argument("--strategy","-s",help="Override strategy text (bypasses LLM planning)")
    p_orch.add_argument("--output", "-o", help="Save full JSON output to file")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Launch the web dashboard (http://localhost:5050)")
    p_dash.add_argument("--port", "-p", type=int, default=5050, help="Port to listen on (default: 5050)")
    p_dash.add_argument("--no-open",    action="store_true",    help="Do not auto-open browser")

    return parser


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    print_banner()
    parser = build_parser()
    args   = parser.parse_args()

    if not args.command:
        parser.print_help()
        console.print(
            "\n[bold yellow]Quick Start:[/bold yellow]\n"
            "  python main.py run my_resume.pdf                          (demo jobs)\n"
            "  python main.py run my_resume.pdf --fetch                  (live API jobs)\n"
            "  python main.py run my_resume.pdf --fetch --scrapers       (APIs + web scraping)\n"
            "  python main.py fetch my_resume.pdf --limit 30 --output jobs.json\n"
            "  python main.py fetch my_resume.pdf --sources jsearch,adzuna\n"
            "  python main.py parse my_resume.pdf\n"
            "  python main.py match my_resume.pdf jd.txt\n"
            "  python main.py cover my_resume.pdf --jd jd.txt --title 'ML Engineer'\n"
            "  python main.py roadmap my_resume.pdf --jd jd.txt\n"
            "  python main.py stats --improve\n"
            "  python main.py track seed                                (load demo data)\n"
            "  python main.py track report                            (stats + top skills)\n"
            "  python main.py track update 3 interview --round 1     (update outcome)\n"
            "  python main.py track list --outcome interview          (filter applications)\n"
            "  python main.py notify test                             (test all channels)\n"
            "  python main.py notify send --type new_job_found --message 'ML role found!'\n"
            "  python main.py notify daily                            (send daily summary)\n"
            "  python main.py notify log                              (view delivery log)\n"
            "  python main.py notify config                           (show channel config)\n"
            "  python main.py analyze                                 (full analytics AI)\n"
            "  python main.py analyze --skills python pytorch --output report.json\n"
            "  python main.py analyze --cached                        (use cached result)\n"
            "  python main.py safety status                           (safety system status)\n"
            "  python main.py safety log                             (view safety event log)\n"
            "  python main.py safety reset-halt                      (clear circuit breaker)\n"
            "  python main.py safety review-mode                     (force human review)\n"
            "  python main.py safety auto-mode                       (restore automation)\n"
            "  python main.py orchestrate --status                    (system health check)\n"
            "  python main.py orchestrate --plan                      (view adaptive plan)\n"
            "  python main.py orchestrate my_resume.pdf               (full AI coordination, dry run)\n"
            "  python main.py orchestrate my_resume.pdf --apply --max 3  (coordinate + apply)\n"
            "  python main.py dashboard                               (open web UI)\n"
        )
        sys.exit(0)

    dispatch = {
        "run":         cmd_run,
        "fetch":       cmd_fetch,
        "parse":       cmd_parse,
        "match":       cmd_match,
        "cover":       cmd_cover,
        "roadmap":     cmd_roadmap,
        "stats":       cmd_stats,
        "track":       cmd_track,
        "analyze":     cmd_analyze,
        "notify":      cmd_notify,
        "safety":      cmd_safety,
        "orchestrate": cmd_orchestrate,
        "dashboard":   cmd_dashboard,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
