"""
Notification AI
Sends real-time alerts across Email and WhatsApp when key events occur.

Trigger events:
  - new_job_found         : Elite / high-match job discovered
  - application_submitted : Auto-apply executed by browser agent
  - review_required       : Job needs manual human review
  - callback_received     : Interview or offer status recorded
  - offer_received        : Dedicated high-priority offer alert
  - daily_summary         : End-of-day digest of all activity
  - analytics_complete    : Analytics report finished
  - test                  : Test / connectivity check

Output schema (logged to DB + returned by every send* function):
{
  "notification_type": "",
  "message":           "",
  "priority":          "High | Medium | Low"
}
"""

import os
import json
import smtplib
import threading
from datetime import datetime, timezone, time as dtime
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from typing import Optional

from core.db_adapter import _get_conn
from core.memory     import init_db
from utils.helpers   import logger

# ── Configuration from environment ───────────────────────────────────────────

class _Cfg:
    # ── Email (SMTP) ────────────────────────────────────────────
    EMAIL_ENABLED    = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
    EMAIL_HOST       = os.getenv("EMAIL_HOST",    "")
    EMAIL_PORT       = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_USER       = os.getenv("EMAIL_USER",    "")
    EMAIL_PASSWORD   = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_FROM       = os.getenv("EMAIL_FROM",    os.getenv("EMAIL_USER", ""))
    EMAIL_TO         = os.getenv("EMAIL_TO",      os.getenv("EMAIL_USER", ""))
    EMAIL_FROM_NAME  = os.getenv("EMAIL_FROM_NAME", "Omniscient AI")

    # ── SendGrid (alternative to SMTP) ─────────────────────────
    SENDGRID_ENABLED = os.getenv("SENDGRID_ENABLED", "false").lower() == "true"
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
    SENDGRID_FROM    = os.getenv("SENDGRID_FROM_EMAIL", os.getenv("EMAIL_FROM", ""))

    # ── WhatsApp (Twilio) ───────────────────────────────────────
    WHATSAPP_ENABLED    = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
    TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN",  "")
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")  # Twilio sandbox
    TWILIO_WHATSAPP_TO   = os.getenv("TWILIO_WHATSAPP_TO", "")   # e.g. whatsapp:+919999999999

    # ── Notification preferences ────────────────────────────────
    NOTIFY_MIN_SCORE    = int(os.getenv("NOTIFY_MIN_SCORE",    "70"))   # ignore jobs below this
    NOTIFY_MAX_PER_RUN  = int(os.getenv("NOTIFY_MAX_PER_RUN",  "5"))    # batch if more
    QUIET_HOUR_START    = int(os.getenv("NOTIFY_QUIET_START",  "23"))   # 11 PM
    QUIET_HOUR_END      = int(os.getenv("NOTIFY_QUIET_END",    "7"))    # 7 AM
    DASHBOARD_URL       = os.getenv("DASHBOARD_URL", "http://localhost:8000")

    # Feature flags per notification type
    NOTIFY_NEW_JOB      = os.getenv("NOTIFY_NEW_JOB",      "true").lower()  == "true"
    NOTIFY_SUBMITTED    = os.getenv("NOTIFY_SUBMITTED",    "true").lower()  == "true"
    NOTIFY_CALLBACK     = os.getenv("NOTIFY_CALLBACK",     "true").lower()  == "true"
    NOTIFY_REVIEW       = os.getenv("NOTIFY_REVIEW",       "true").lower()  == "true"
    NOTIFY_DAILY        = os.getenv("NOTIFY_DAILY",        "true").lower()  == "true"
    NOTIFY_ANALYTICS    = os.getenv("NOTIFY_ANALYTICS",    "false").lower() == "true"


# ── Priority and type maps ────────────────────────────────────────────────────

PRIORITY_MAP = {
    "offer_received":        "High",
    "callback_received":     "High",
    "new_job_found":         "Medium",
    "application_submitted": "Medium",
    "review_required":       "Medium",
    "daily_summary":         "Low",
    "analytics_complete":    "Low",
    "test":                  "Low",
}

TYPE_EMOJI = {
    "offer_received":        "🎉",
    "callback_received":     "📞",
    "new_job_found":         "🔍",
    "application_submitted": "✅",
    "review_required":       "👁️",
    "daily_summary":         "📊",
    "analytics_complete":    "🧠",
    "test":                  "🔔",
}

TYPE_SUBJECT = {
    "offer_received":        "🎉 Offer Received! — Omniscient AI",
    "callback_received":     "📞 Callback Alert — Omniscient AI",
    "new_job_found":         "🔍 New Job Match Found — Omniscient AI",
    "application_submitted": "✅ Application Submitted — Omniscient AI",
    "review_required":       "👁️ Review Required — Omniscient AI",
    "daily_summary":         "📊 Daily Activity Summary — Omniscient AI",
    "analytics_complete":    "🧠 Analytics Report Ready — Omniscient AI",
    "test":                  "🔔 Test Notification — Omniscient AI",
}


# ── Quiet hours check ─────────────────────────────────────────────────────────

def _is_quiet_hours() -> bool:
    """Return True if current local time is within configured quiet hours."""
    now_h = datetime.now().hour
    s, e  = _Cfg.QUIET_HOUR_START, _Cfg.QUIET_HOUR_END
    if s > e:   # spans midnight (e.g. 23 → 7)
        return now_h >= s or now_h < e
    return s <= now_h < e


# ── HTML email template ───────────────────────────────────────────────────────

def _build_email_html(
    notification_type: str,
    message: str,
    priority: str,
    job: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> str:
    """Render a beautiful dark-theme HTML email (email-client safe, table-based)."""

    emoji     = TYPE_EMOJI.get(notification_type, "🔔")
    prio_col  = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#00ff7f"}.get(priority, "#00ff7f")
    prio_bg   = {"High": "#2d0a0a", "Medium": "#2d1a04", "Low": "#0a2d18"}.get(priority, "#0a2d18")
    dashboard = _Cfg.DASHBOARD_URL

    # Job card block (if provided)
    job_block = ""
    if job:
        score       = job.get("match_score", 0)
        score_col   = "#00ff7f" if score >= 85 else "#f59e0b" if score >= 70 else "#ef4444"
        salary_str  = job.get("salary", "") or "—"
        matched_str = ", ".join(job.get("skills_matched", [])[:5]) or "—"
        missing_str = ", ".join(job.get("skills_missing", [])[:3]) or "None"
        job_block = f"""
        <tr>
          <td style="background:#1a1a2e;border:1px solid #2a2a3e;border-radius:10px;padding:20px;margin:16px 0;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <div style="font-size:18px;font-weight:800;color:#ffffff;margin-bottom:4px;">
                    {job.get("title","Job Title")}
                  </div>
                  <div style="font-size:13px;color:#888;margin-bottom:12px;">
                    {job.get("company","Company")} &nbsp;·&nbsp; {job.get("location","Location")} &nbsp;·&nbsp; {salary_str}
                  </div>
                </td>
                <td align="right" valign="top">
                  <div style="background:{prio_bg};border:1px solid {score_col};border-radius:8px;padding:8px 14px;text-align:center;">
                    <div style="font-size:22px;font-weight:900;color:{score_col};font-family:monospace;">{score}</div>
                    <div style="font-size:9px;color:#888;text-transform:uppercase;letter-spacing:1px;">Match Score</div>
                  </div>
                </td>
              </tr>
              <tr>
                <td colspan="2">
                  <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
                    <tr>
                      <td style="padding:4px 0;">
                        <span style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;">Skills Match</span><br>
                        <span style="font-size:12px;color:#00cc60;">{matched_str}</span>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:4px 0;">
                        <span style="font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;">Skills Gap</span><br>
                        <span style="font-size:12px;color:#ef4444;">{missing_str}</span>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    # Extra info block
    extra_block = ""
    if extra:
        rows = "".join(
            f'<tr><td style="font-size:11px;color:#666;padding:2px 0;text-transform:uppercase;letter-spacing:1px;">{k}</td>'
            f'<td style="font-size:12px;color:#aaa;padding:2px 0 2px 12px;">{v}</td></tr>'
            for k, v in extra.items()
        )
        extra_block = f"""
        <tr>
          <td style="padding:12px 0 0;">
            <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
          </td>
        </tr>"""

    type_label = notification_type.replace("_", " ").title()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{TYPE_SUBJECT.get(notification_type, "Omniscient AI")}</title>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0f;padding:30px 20px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#0d1117 0%,#111827 100%);
                       border:1px solid #1a2a1a;border-bottom:none;
                       border-radius:14px 14px 0 0;padding:28px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <div style="font-size:10px;font-weight:700;color:#00ff7f;
                                letter-spacing:3px;text-transform:uppercase;margin-bottom:6px;">
                      OMNISCIENT AI
                    </div>
                    <div style="font-size:11px;color:#444;letter-spacing:1px;text-transform:uppercase;">
                      Job Intelligence Agent
                    </div>
                  </td>
                  <td align="right">
                    <div style="background:{prio_bg};border:1px solid {prio_col};
                                border-radius:20px;padding:5px 14px;display:inline-block;">
                      <span style="font-size:10px;font-weight:700;color:{prio_col};
                                   text-transform:uppercase;letter-spacing:1px;">
                        {priority} PRIORITY
                      </span>
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Alert banner -->
          <tr>
            <td style="background:linear-gradient(90deg,#00ff7f22,#00ff7f08);
                       border-left:3px solid #00ff7f;border-right:1px solid #1a2a1a;
                       padding:18px 32px;">
              <div style="font-size:11px;color:#00cc60;letter-spacing:2px;
                          text-transform:uppercase;margin-bottom:4px;">
                {emoji} {type_label}
              </div>
              <div style="font-size:20px;font-weight:700;color:#ffffff;line-height:1.4;">
                {message[:200]}
              </div>
            </td>
          </tr>

          <!-- Content -->
          <tr>
            <td style="background:#0f0f18;border:1px solid #1a1a2e;
                       border-top:none;border-bottom:none;padding:24px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                {job_block}
                {extra_block}
              </table>
            </td>
          </tr>

          <!-- CTA button -->
          <tr>
            <td style="background:#0f0f18;border:1px solid #1a1a2e;
                       border-top:none;border-bottom:none;
                       padding:0 32px 28px;text-align:center;">
              <a href="{dashboard}"
                 style="display:inline-block;background:linear-gradient(135deg,#00ff7f,#00cc60);
                        color:#000000;font-weight:800;font-size:13px;text-decoration:none;
                        padding:12px 32px;border-radius:8px;letter-spacing:0.5px;
                        margin-right:10px;">
                View Dashboard &#8594;
              </a>
              <a href="{dashboard}/api/analytics"
                 style="display:inline-block;background:transparent;
                        color:#00ff7f;font-weight:600;font-size:12px;text-decoration:none;
                        padding:12px 20px;border-radius:8px;
                        border:1px solid #00ff7f44;letter-spacing:0.5px;">
                Analytics
              </a>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#070710;border:1px solid #1a1a2e;border-top:none;
                       border-radius:0 0 14px 14px;padding:20px 32px;text-align:center;">
              <div style="font-size:11px;color:#333;margin-bottom:6px;">
                Sent by Omniscient AI &nbsp;·&nbsp;
                <a href="{dashboard}" style="color:#00ff7f44;text-decoration:none;">Dashboard</a>
                &nbsp;·&nbsp;
                <span style="color:#222;">To unsubscribe, set EMAIL_ENABLED=false in .env</span>
              </div>
              <div style="font-size:10px;color:#222;">
                {datetime.now().strftime("%A, %d %B %Y %H:%M")} local time
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ── Plain text fallback ───────────────────────────────────────────────────────

def _build_email_text(notification_type: str, message: str, priority: str, job: Optional[dict] = None) -> str:
    lines = [
        "=" * 52,
        "  OMNISCIENT AI — JOB INTELLIGENCE AGENT",
        "=" * 52,
        f"  Type    : {notification_type.replace('_',' ').upper()}",
        f"  Priority: {priority}",
        f"  Time    : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "-" * 52,
        f"  {message}",
    ]
    if job:
        lines += [
            "",
            f"  Job     : {job.get('title','')}",
            f"  Company : {job.get('company','')}",
            f"  Location: {job.get('location','')}",
            f"  Score   : {job.get('match_score',0)}/100",
            f"  Salary  : {job.get('salary') or '—'}",
        ]
        if job.get("skills_matched"):
            lines.append(f"  Matched : {', '.join(job['skills_matched'][:5])}")
        if job.get("skills_missing"):
            lines.append(f"  Missing : {', '.join(job['skills_missing'][:3])}")
    lines += [
        "-" * 52,
        f"  Dashboard: {_Cfg.DASHBOARD_URL}",
        "=" * 52,
    ]
    return "\n".join(lines)


# ── WhatsApp message formatter ────────────────────────────────────────────────

def _build_whatsapp_message(
    notification_type: str,
    message: str,
    priority: str,
    job: Optional[dict] = None,
) -> str:
    emoji  = TYPE_EMOJI.get(notification_type, "🔔")
    prio_e = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(priority, "⚪")
    type_l = notification_type.replace("_", " ").title()

    lines = [
        f"🤖 *OMNISCIENT AI ALERT*",
        f"{'─' * 30}",
        f"{emoji} *{type_l}*",
        f"{prio_e} Priority: *{priority}*",
        "",
        message,
    ]
    if job:
        score   = job.get("match_score", 0)
        score_e = "✨ ELITE" if score >= 85 else "⭐ GOOD" if score >= 70 else ""
        lines += [
            "",
            f"━━━ JOB DETAILS ━━━",
            f"📋 *{job.get('title','')}*",
            f"🏢 {job.get('company','')}",
            f"📍 {job.get('location','')}",
            f"💰 {job.get('salary') or '—'}",
            f"🎯 Match: *{score}/100* {score_e}",
        ]
        if job.get("skills_matched"):
            lines.append(f"✅ Matched: {', '.join(job['skills_matched'][:4])}")
        if job.get("skills_missing"):
            lines.append(f"❌ Missing: {', '.join(job['skills_missing'][:3])}")
        if job.get("apply_link") or job.get("url"):
            lines.append(f"🔗 Apply: {job.get('apply_link') or job.get('url','')}")

    lines += [
        "",
        f"{'─' * 30}",
        f"🌐 Dashboard: {_Cfg.DASHBOARD_URL}",
        f"⏰ {datetime.now().strftime('%d %b %Y %H:%M')}",
    ]
    return "\n".join(lines)


# ── Delivery channels ─────────────────────────────────────────────────────────

def _send_smtp(subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    """Send via Gmail / any SMTP server."""
    if not all([_Cfg.EMAIL_HOST, _Cfg.EMAIL_USER, _Cfg.EMAIL_PASSWORD, _Cfg.EMAIL_TO]):
        return False, "EMAIL_HOST, EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO not configured"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{_Cfg.EMAIL_FROM_NAME} <{_Cfg.EMAIL_FROM or _Cfg.EMAIL_USER}>"
        msg["To"]      = _Cfg.EMAIL_TO

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(_Cfg.EMAIL_HOST, _Cfg.EMAIL_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(_Cfg.EMAIL_USER, _Cfg.EMAIL_PASSWORD)
            server.sendmail(_Cfg.EMAIL_FROM or _Cfg.EMAIL_USER, _Cfg.EMAIL_TO, msg.as_string())

        return True, ""
    except Exception as exc:
        return False, str(exc)


def _send_sendgrid(subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    """Send via SendGrid REST API (more reliable than SMTP)."""
    if not _Cfg.SENDGRID_API_KEY:
        return False, "SENDGRID_API_KEY not configured"
    try:
        import httpx
        payload = {
            "personalizations": [{"to": [{"email": _Cfg.EMAIL_TO}]}],
            "from":    {"email": _Cfg.SENDGRID_FROM or _Cfg.EMAIL_USER, "name": _Cfg.EMAIL_FROM_NAME},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text_body},
                {"type": "text/html",  "value": html_body},
            ],
        }
        r = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {_Cfg.SENDGRID_API_KEY}"},
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 202):
            return True, ""
        return False, f"SendGrid error {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return False, str(exc)


def _send_whatsapp(message_body: str) -> tuple[bool, str]:
    """Send via Twilio WhatsApp API."""
    if not all([_Cfg.TWILIO_ACCOUNT_SID, _Cfg.TWILIO_AUTH_TOKEN, _Cfg.TWILIO_WHATSAPP_TO]):
        return False, "Twilio credentials (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_TO) not configured"
    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(_Cfg.TWILIO_ACCOUNT_SID, _Cfg.TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            body    = message_body,
            from_   = _Cfg.TWILIO_WHATSAPP_FROM,
            to      = _Cfg.TWILIO_WHATSAPP_TO,
        )
        return True, str(msg.sid)
    except ImportError:
        return False, "twilio package not installed — run: pip install twilio"
    except Exception as exc:
        return False, str(exc)


# ── DB log helper ─────────────────────────────────────────────────────────────

def _log_notification(
    notification_type: str,
    message: str,
    priority: str,
    channel: str,
    status: str,
    error_message: str = "",
    job: Optional[dict] = None,
):
    """Persist notification record to notifications_log table."""
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO notifications_log
                   (notification_type, message, priority, channel, recipient,
                    subject, status, error_message, job_title, company, match_score, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    notification_type,
                    message[:500],
                    priority,
                    channel,
                    _Cfg.EMAIL_TO,
                    TYPE_SUBJECT.get(notification_type, ""),
                    status,
                    error_message[:500] if error_message else "",
                    job.get("title", "")    if job else "",
                    job.get("company", "")  if job else "",
                    job.get("match_score", 0) if job else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as exc:
        logger.warning(f"[Notifier] Could not log notification to DB: {exc}")


# ── Core dispatch ─────────────────────────────────────────────────────────────

def send_notification(
    notification_type: str,
    message: str,
    job: Optional[dict] = None,
    extra: Optional[dict] = None,
    force: bool = False,
    async_send: bool = True,
) -> dict:
    """
    Main notification dispatch.

    Args:
        notification_type : One of the TYPE_MAP keys.
        message           : Short summary message.
        job               : Optional job dict for rich context.
        extra             : Optional dict of extra key-value pairs.
        force             : If True, bypass quiet hours.
        async_send        : Send in a background thread (default True).

    Returns:
        Notification result dict matching the output schema.
    """
    priority  = PRIORITY_MAP.get(notification_type, "Low")

    # Bump priority to High for elite job scores
    if notification_type == "new_job_found" and job:
        if job.get("match_score", 0) >= 90:
            priority = "High"

    result = {
        "notification_type": notification_type,
        "message":           message,
        "priority":          priority,
    }

    # Check quiet hours (skip for High priority unless forcing)
    if not force and priority != "High" and _is_quiet_hours():
        logger.info(f"[Notifier] Quiet hours — suppressing {notification_type} notification.")
        result["status"] = "suppressed_quiet_hours"
        return result

    if async_send:
        t = threading.Thread(target=_dispatch, args=(notification_type, message, priority, job, extra), daemon=True)
        t.start()
        result["status"] = "dispatched_async"
    else:
        statuses = _dispatch(notification_type, message, priority, job, extra)
        result["status"] = statuses

    logger.info(f"[Notifier] {priority:6s} | {notification_type:24s} | {message[:70]}")
    return result


def _dispatch(
    notification_type: str,
    message: str,
    priority: str,
    job: Optional[dict],
    extra: Optional[dict],
) -> dict:
    """Internal: actually send via all enabled channels."""
    statuses = {}
    subject  = TYPE_SUBJECT.get(notification_type, "Omniscient AI Alert")

    # ── Email channel ────────────────────────────────────────────
    if _Cfg.EMAIL_ENABLED or _Cfg.SENDGRID_ENABLED:
        html_body = _build_email_html(notification_type, message, priority, job, extra)
        text_body = _build_email_text(notification_type, message, priority, job)

        if _Cfg.SENDGRID_ENABLED and _Cfg.SENDGRID_API_KEY:
            ok, err = _send_sendgrid(subject, html_body, text_body)
            statuses["sendgrid"] = "sent" if ok else f"failed: {err}"
            _log_notification(notification_type, message, priority, "sendgrid",
                              "sent" if ok else "failed", err, job)
            if ok:
                logger.success(f"[Notifier] Email (SendGrid) -> {_Cfg.EMAIL_TO}")
            else:
                logger.warning(f"[Notifier] SendGrid failed: {err}")

        elif _Cfg.EMAIL_ENABLED:
            ok, err = _send_smtp(subject, html_body, text_body)
            statuses["email"] = "sent" if ok else f"failed: {err}"
            _log_notification(notification_type, message, priority, "smtp",
                              "sent" if ok else "failed", err, job)
            if ok:
                logger.success(f"[Notifier] Email (SMTP) -> {_Cfg.EMAIL_TO}")
            else:
                logger.warning(f"[Notifier] SMTP failed: {err}")
    else:
        statuses["email"] = "disabled"

    # ── WhatsApp channel ─────────────────────────────────────────
    if _Cfg.WHATSAPP_ENABLED:
        wa_body = _build_whatsapp_message(notification_type, message, priority, job)
        ok, err = _send_whatsapp(wa_body)
        statuses["whatsapp"] = "sent" if ok else f"failed: {err}"
        _log_notification(notification_type, message, priority, "whatsapp",
                          "sent" if ok else "failed", err, job)
        if ok:
            logger.success(f"[Notifier] WhatsApp -> {_Cfg.TWILIO_WHATSAPP_TO}")
        else:
            logger.warning(f"[Notifier] WhatsApp failed: {err}")
    else:
        statuses["whatsapp"] = "disabled"

    return statuses


# ── High-level trigger functions ──────────────────────────────────────────────

def notify_new_job(job: dict) -> dict:
    """
    Notify when a new high-match job is discovered.
    Respects NOTIFY_MIN_SCORE threshold.
    """
    if not _Cfg.NOTIFY_NEW_JOB:
        return _suppressed("new_job_found")
    if job.get("match_score", 0) < _Cfg.NOTIFY_MIN_SCORE:
        return _suppressed("new_job_found")

    score  = job.get("match_score", 0)
    elite  = " — ELITE MATCH ✨" if score >= 85 else ""
    msg = (
        f"{job.get('title','Job')} at {job.get('company','Company')}"
        f" | Score: {score}/100{elite}"
        f" | {job.get('location','Remote')}"
    )
    return send_notification("new_job_found", msg, job=job)


def notify_application_submitted(job: dict, decision: dict) -> dict:
    """Notify when the auto-apply agent submits an application."""
    if not _Cfg.NOTIFY_SUBMITTED:
        return _suppressed("application_submitted")
    msg = (
        f"Applied to {job.get('title','Job')} at {job.get('company','Company')}"
        f" | Confidence: {decision.get('confidence_score',0)}%"
        f" | Risk: {decision.get('risk_level','Low')}"
    )
    return send_notification(
        "application_submitted", msg, job=job,
        extra={
            "Execution":  decision.get("execution_status", ""),
            "Risk Level": decision.get("risk_level", ""),
            "Confidence": f"{decision.get('confidence_score',0)}%",
        },
    )


def notify_review_required(job: dict, decision: dict) -> dict:
    """Notify when a job needs manual human review before applying."""
    if not _Cfg.NOTIFY_REVIEW:
        return _suppressed("review_required")
    msg = (
        f"Review needed: {job.get('title','Job')} at {job.get('company','Company')}"
        f" | Score: {job.get('match_score', 0)}/100"
        f" | Confidence: {decision.get('confidence_score',0)}%"
    )
    return send_notification("review_required", msg, job=job)


def notify_callback_received(
    job_title: str,
    company: str,
    outcome: str,
    interview_round: int = 0,
) -> dict:
    """Notify when an interview or offer is received."""
    if not _Cfg.NOTIFY_CALLBACK:
        return _suppressed("callback_received")

    ntype   = "offer_received" if outcome == "offer" else "callback_received"
    outcome_label = outcome.title()
    round_str = f" (Round {interview_round})" if interview_round else ""

    msg = f"{outcome_label}{round_str}: {job_title} at {company}"
    return send_notification(
        ntype, msg,
        extra={
            "Job":     job_title,
            "Company": company,
            "Outcome": outcome_label,
            "Round":   str(interview_round) if interview_round else "—",
        },
        force=True,  # Always send callbacks, even in quiet hours
    )


def notify_new_jobs_batch(jobs: list[dict]) -> dict:
    """
    Batch notification for multiple new jobs found in a pipeline run.
    Sends individual alerts for top jobs up to NOTIFY_MAX_PER_RUN,
    then sends one summary for the rest.
    """
    eligible = [j for j in jobs if j.get("match_score", 0) >= _Cfg.NOTIFY_MIN_SCORE]
    if not eligible:
        return _suppressed("new_job_found")

    eligible.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    # Send individual alerts for top N jobs
    results = []
    for job in eligible[:_Cfg.NOTIFY_MAX_PER_RUN]:
        results.append(notify_new_job(job))

    # Send summary if there are more
    remainder = len(eligible) - _Cfg.NOTIFY_MAX_PER_RUN
    if remainder > 0:
        top = eligible[0]
        msg = (
            f"{len(eligible)} new job matches found this run! "
            f"Top: {top.get('title','')} at {top.get('company','')} ({top.get('match_score',0)}/100). "
            f"+{remainder} more matches available in dashboard."
        )
        results.append(send_notification(
            "new_job_found", msg,
            extra={"Total Matches": str(len(eligible)), "Top Score": str(eligible[0].get("match_score", 0))},
        ))

    return {"batch": results, "total_notified": len(eligible)}


def notify_daily_summary(stats: dict) -> dict:
    """Send end-of-day digest with key stats."""
    if not _Cfg.NOTIFY_DAILY:
        return _suppressed("daily_summary")

    applied    = stats.get("applied",   0)
    interviews = stats.get("interviews", 0)
    offers     = stats.get("offers",     0)
    new_jobs   = stats.get("new_jobs",   0)

    msg = (
        f"Today: {applied} applied · {interviews} interviews · "
        f"{offers} offers · {new_jobs} new jobs found"
    )
    return send_notification(
        "daily_summary", msg,
        extra={
            "Applications": str(applied),
            "Interviews":   str(interviews),
            "Offers":       str(offers),
            "New Jobs":     str(new_jobs),
        },
    )


def notify_analytics_complete(insight_count: int, top_gap: str = "") -> dict:
    """Notify when an analytics run completes."""
    if not _Cfg.NOTIFY_ANALYTICS:
        return _suppressed("analytics_complete")
    msg = f"Analytics complete — {insight_count} insights generated"
    if top_gap:
        msg += f" | Top skill gap: {top_gap}"
    return send_notification("analytics_complete", msg)


def send_test_notification() -> dict:
    """Send a test notification to verify all channels are working."""
    return send_notification(
        "test",
        "Test notification from Omniscient AI. All channels are working!",
        extra={"From": "Omniscient AI Agent", "Status": "Healthy"},
        force=True,
        async_send=False,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _suppressed(notification_type: str) -> dict:
    return {
        "notification_type": notification_type,
        "message":           "Notification suppressed (disabled or below threshold)",
        "priority":          PRIORITY_MAP.get(notification_type, "Low"),
        "status":            "suppressed",
    }


# ── DB query helpers ──────────────────────────────────────────────────────────

def get_notification_log(limit: int = 50) -> list[dict]:
    """Fetch recent notifications from the log."""
    try:
        init_db()
        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM notifications_log
                   ORDER BY sent_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning(f"[Notifier] Could not fetch log: {exc}")
        return []


def get_notification_stats() -> dict:
    """Get aggregated stats about notifications sent."""
    try:
        init_db()
        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT notification_type, status, COUNT(*) as cnt
                   FROM notifications_log
                   GROUP BY notification_type, status"""
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM notifications_log").fetchone()
            unread = conn.execute(
                "SELECT COUNT(*) FROM notifications_log WHERE status='sent'"
            ).fetchone()

        by_type: dict = {}
        for r in rows:
            t = r["notification_type"]
            if t not in by_type:
                by_type[t] = {}
            by_type[t][r["status"]] = r["cnt"]

        return {
            "total":   total[0] if total else 0,
            "sent":    unread[0] if unread else 0,
            "by_type": by_type,
            "channels": {
                "email":     _Cfg.EMAIL_ENABLED or _Cfg.SENDGRID_ENABLED,
                "whatsapp":  _Cfg.WHATSAPP_ENABLED,
            },
        }
    except Exception as exc:
        logger.warning(f"[Notifier] Could not fetch stats: {exc}")
        return {}


def get_notification_config() -> dict:
    """Return current notification configuration (no secrets)."""
    return {
        "email_enabled":    _Cfg.EMAIL_ENABLED,
        "sendgrid_enabled": _Cfg.SENDGRID_ENABLED,
        "whatsapp_enabled": _Cfg.WHATSAPP_ENABLED,
        "email_to":         _Cfg.EMAIL_TO[:3] + "***" if _Cfg.EMAIL_TO else "",
        "whatsapp_to":      _Cfg.TWILIO_WHATSAPP_TO[:6] + "***" if _Cfg.TWILIO_WHATSAPP_TO else "",
        "notify_min_score": _Cfg.NOTIFY_MIN_SCORE,
        "notify_max_per_run": _Cfg.NOTIFY_MAX_PER_RUN,
        "quiet_hours":      f"{_Cfg.QUIET_HOUR_START:02d}:00 – {_Cfg.QUIET_HOUR_END:02d}:00",
        "active_types": {
            "new_job":   _Cfg.NOTIFY_NEW_JOB,
            "submitted": _Cfg.NOTIFY_SUBMITTED,
            "callback":  _Cfg.NOTIFY_CALLBACK,
            "review":    _Cfg.NOTIFY_REVIEW,
            "daily":     _Cfg.NOTIFY_DAILY,
            "analytics": _Cfg.NOTIFY_ANALYTICS,
        },
    }
