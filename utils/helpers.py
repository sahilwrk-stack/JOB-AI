"""
Shared utilities: LLM gateway, JSON parsing, retry logic.

LLM policy: **Ollama only** — local OpenAI-compatible endpoint (default http://localhost:11434/v1).
No OpenAI cloud, Groq, or other external LLM APIs. The ``openai`` Python package is used only as an HTTP client to Ollama.
"""

import os
import json
import re
import time
from pathlib import Path
from typing import Any
from functools import wraps

from dotenv import load_dotenv
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()

# ── Logger setup ────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/agent.log")

logger.remove()
logger.add(
    LOG_FILE,
    level=LOG_LEVEL,
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name} | {message}",
)


def _safe_console_sink(msg):
    try:
        print(msg, end="")
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), end="")


logger.add(
    _safe_console_sink,
    level=LOG_LEVEL,
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> | {message}",
)


# ── LLM Client (Ollama) ─────────────────────────────────────────────────────

def _ollama_origin_from_env() -> str:
    """Base origin for Ollama native API (host:port, no /v1)."""
    base = (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1").strip().rstrip("/")
    if base.endswith("/v1"):
        origin = base[:-3].rstrip("/")
    else:
        origin = base.replace("/v1", "").rstrip("/") or "http://localhost:11434"
    return origin


def _ollama_tags_urls_to_try() -> list[str]:
    """
    Probe /api/tags. On Windows, ``localhost`` often resolves to IPv6 first while
    Ollama listens on IPv4 only — try 127.0.0.1 as well.

    Optional ``OLLAMA_PROBE_URL`` — full URL (e.g. http://host.docker.internal:11434/api/tags)
    for Docker/WSL when Ollama runs on the Windows host.
    """
    custom = (os.getenv("OLLAMA_PROBE_URL") or "").strip()
    origin = _ollama_origin_from_env()
    primary = f"{origin}/api/tags"
    candidates: list[str] = []
    if custom:
        candidates.append(custom)
    candidates.append(primary)
    if "localhost" in origin:
        candidates.append(primary.replace("localhost", "127.0.0.1", 1))
    elif "127.0.0.1" in origin:
        candidates.append(primary.replace("127.0.0.1", "localhost", 1))
    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _ollama_tcp_endpoints() -> list[tuple[str, int]]:
    """Host/port pairs for a no-HTTP TCP check (avoids broken HTTP proxies)."""
    from urllib.parse import urlparse

    origin = _ollama_origin_from_env()
    u = urlparse(origin)
    host = u.hostname or "127.0.0.1"
    port = u.port or 11434
    pairs = [(host, port)]
    if host == "localhost":
        pairs.append(("127.0.0.1", port))
    elif host == "127.0.0.1":
        pairs.append(("localhost", port))
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _ollama_urlopen(url: str, timeout: float):
    """
    Open URL without going through HTTP(S)_PROXY env vars.
    Corporate/system proxies often break ``127.0.0.1`` / ``localhost`` and falsely
    report Ollama as offline.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "omniscient-ai/ollama-check"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
    )
    return opener.open(req, timeout=timeout)


def ollama_server_reachable(timeout: float = 3.0) -> bool:
    import socket
    import urllib.error

    for url in _ollama_tags_urls_to_try():
        try:
            with _ollama_urlopen(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except (OSError, TimeoutError, urllib.error.URLError) as e:
            logger.debug("Ollama HTTP probe failed for %s: %s", url, e)
            continue

    # TCP fallback: port open ⇒ Ollama almost certainly up (even if HTTP layer odd)
    t = min(2.0, timeout)
    for host, port in _ollama_tcp_endpoints():
        try:
            with socket.create_connection((host, port), timeout=t):
                logger.info("Ollama reachable via TCP %s:%s (HTTP probe failed; using port check)", host, port)
                return True
        except OSError as e:
            logger.debug("Ollama TCP probe failed for %s:%s: %s", host, port, e)
            continue
    return False


def _build_provider_chain() -> list[str]:
    """Local Ollama only — no cloud LLM fallback."""
    return ["ollama"]


def get_llm_diagnostics() -> dict:
    chain = _build_provider_chain()
    llm_ready = ollama_server_reachable()
    status = "brain_online" if llm_ready else "brain_offline"
    return {
        "status": status,
        "llm_ready": llm_ready,
        "ollama_local": True,
        "provider_chain": chain,
        "llm_policy": "ollama_local_only",
        "llm_provider_env": os.getenv("LLM_PROVIDER", "ollama"),
        "llm_model": os.getenv("LLM_MODEL", "llama3.2"),
        "ollama_base_url": (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1").strip(),
    }


def get_agent_diagnostics() -> dict:
    """Payload for GET /api/agent-diagnostics (Flask + FastAPI + setup scripts)."""
    import sqlite3

    diag = get_llm_diagnostics()
    db_path = os.getenv("DB_PATH", "db/agent_memory.db")
    try:
        conn = sqlite3.connect(db_path)
        app_count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        conn.close()
        db_ok = True
    except Exception:
        app_count = 0
        db_ok = False

    ai_stats = {
        "total_auto_applied": 0,
        "total_rejected": 0,
        "avg_confidence": None,
        "learning_status": "—",
    }
    try:
        from core.learning import aggregate_application_ai_stats

        ai_stats = aggregate_application_ai_stats()
    except Exception:
        pass

    return {
        "llm": diag,
        "database": {"ok": db_ok, "applications": app_count, "path": db_path},
        "env": {
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", ""),
            "LLM_MODEL": os.getenv("LLM_MODEL", ""),
            "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "AUTO_APPLY_ENABLED": os.getenv("AUTO_APPLY_ENABLED", "false"),
            "CANDIDATE_NAME": os.getenv("CANDIDATE_NAME", ""),
        },
        "status": "brain_online" if diag.get("llm_ready") else "brain_offline",
        "total_auto_applied": ai_stats.get("total_auto_applied", 0),
        "total_rejected": ai_stats.get("total_rejected", 0),
        "avg_confidence": ai_stats.get("avg_confidence"),
        "learning_status": ai_stats.get("learning_status", "—"),
        "recruiter_metrics": ai_stats,
    }


def _resolve_model(task: str = "general") -> str:
    task = (task or "general").lower().strip()
    default = os.getenv("LLM_MODEL", "llama3.2")
    model_fast = os.getenv("LLM_MODEL_FAST", default)
    model_reasoning = os.getenv("LLM_MODEL_REASONING", default)
    model_json = os.getenv("LLM_MODEL_JSON", default)
    if task == "fast":
        return model_fast
    if task == "reasoning":
        return model_reasoning
    if task == "json":
        return model_json
    return default


def get_llm_client(provider: str | None = None):
    """
    OpenAI-compatible client for **local Ollama** only (no cloud API keys).

    ``provider`` is ignored. Base URL defaults to ``http://localhost:11434/v1``;
    override with ``OLLAMA_BASE_URL`` in the environment.
    """
    from openai import OpenAI

    base = (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1").strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return OpenAI(
        base_url=base,
        api_key="ollama",
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
    return call_llm_advanced(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        task="general",
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def call_llm_advanced(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    task: str = "general",
    max_tokens: int = 4096,
) -> str:
    model = _resolve_model(task)

    if not ollama_server_reachable(timeout=1.5):
        logger.warning(
            "Ollama server not reachable (%s) — LLM calls may fail until `ollama serve` is running.",
            (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1").strip(),
        )

    try:
        client = get_llm_client()
        logger.info(f"🤖 [AI REQUEST] provider=ollama model={model} task={task}")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise RuntimeError(f"Ollama LLM call failed: {exc}") from exc


def extract_json(text: str) -> dict | list:
    if not text or not text.strip():
        logger.warning("🧩 [JSON PARSE] ⚠️  Empty string received — nothing to parse.")
        return {}

    original = text
    text = re.sub(r"```(?:json|JSON)?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    prose_prefixes = [
        r"^(?:here is|here's|the following is|result:|output:)[^\{]*",
        r"^(?:json|JSON)\s*:",
    ]
    for pat in prose_prefixes:
        text = re.sub(pat, "", text, flags=re.IGNORECASE).strip()

    try:
        result = json.loads(text)
        logger.info("🧩 [JSON PARSE] ✅ Direct parse succeeded.")
        return result
    except json.JSONDecodeError as e:
        logger.debug(f"🧩 [JSON PARSE] Direct parse failed ({e}), trying extraction...")

    for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            try:
                result = json.loads(candidate)
                logger.info("🧩 [JSON PARSE] ✅ Extracted JSON object from partial response.")
                return result
            except json.JSONDecodeError:
                continue

    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()
    try:
        result = json.loads(cleaned)
        logger.info("🧩 [JSON PARSE] ✅ Parsed after control-char cleanup.")
        return result
    except json.JSONDecodeError:
        pass

    logger.error(
        "🧩 [JSON PARSE] ❌ ALL parse strategies failed.\n"
        f"   RAW LLM OUTPUT ({len(original)} chars):\n"
        f"   {'─'*60}\n"
        f"   {original[:800]}\n"
        f"   {'─'*60}"
    )
    return {}


def safe_json_call(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> dict | list:
    try:
        best_of_n = max(1, int(os.getenv("LLM_JSON_BEST_OF_N", "1")))
        logger.info(f"🤖 [AI REQUEST] Sending JSON prompt to LLM model... (best_of_n={best_of_n})")

        json_system = system_prompt.strip() + (
            "\n\nSTRICT OUTPUT REQUIREMENT:\n"
            "- Return valid JSON only.\n"
            "- No markdown code fences.\n"
            "- No explanation text."
        )

        parsed_candidates: list[dict | list] = []
        for _ in range(best_of_n):
            raw = call_llm_advanced(
                system_prompt=json_system,
                user_prompt=user_prompt,
                temperature=temperature,
                task="json",
                max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            )
            logger.info(
                f"✅ [AI RESPONSE] Raw response received "
                f"({len(raw) if raw else 0} chars). "
                f"Preview: {(raw or '')[:120].strip()!r}"
            )
            result = extract_json(raw)
            if result:
                parsed_candidates.append(result)

        if parsed_candidates:
            result = max(
                parsed_candidates,
                key=lambda r: len(r) if isinstance(r, (dict, list)) else 0,
            )
        else:
            result = {}
        if result:
            keys = list(result.keys()) if isinstance(result, dict) else f"list[{len(result)}]"
            logger.info(f"🧩 [JSON PARSE] ✅ Final parsed keys: {keys}")
        else:
            logger.warning("🧩 [JSON PARSE] ⚠️  Parsed result is empty — check raw output above.")
        return result
    except Exception as exc:
        logger.error(f"🤖 [AI REQUEST] ❌ LLM call failed: {exc}")
        return {}


def timer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        logger.debug(f"{fn.__name__} completed in {elapsed:.2f}s")
        return result

    return wrapper
