"""
Install / verify local Ollama for Omniscient Resume AI.

Run from project root:
  python scripts/setup_ollama.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def resolve_ollama_exe() -> str | None:
    """``ollama`` on PATH, or default Windows install under LocalAppData."""
    w = shutil.which("ollama")
    if w:
        return w
    if sys.platform == "win32":
        lp = os.environ.get("LOCALAPPDATA", "")
        if lp:
            cand = Path(lp) / "Programs" / "Ollama" / "ollama.exe"
            if cand.is_file():
                return str(cand)
    return None


def _load_env_minimal() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _urlopen_no_proxy(url: str, data: bytes | None = None, timeout: float = 30.0):
    """Avoid HTTP(S)_PROXY breaking localhost."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if data is not None:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
    else:
        req = urllib.request.Request(url)
    return opener.open(req, timeout=timeout)


def _tags_probe_urls() -> list[str]:
    custom = (_load_env_minimal().get("OLLAMA_PROBE_URL") or os.getenv("OLLAMA_PROBE_URL") or "").strip()
    base = (
        _load_env_minimal().get("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://localhost:11434/v1"
    ).strip().rstrip("/")
    if base.endswith("/v1"):
        origin = base[:-3].rstrip("/")
    else:
        origin = base.replace("/v1", "").rstrip("/") or "http://localhost:11434"
    primary = f"{origin}/api/tags"
    out: list[str] = []
    if custom:
        out.append(custom)
    out.append(primary)
    if "localhost" in origin:
        out.append(primary.replace("localhost", "127.0.0.1", 1))
    elif "127.0.0.1" in origin:
        out.append(primary.replace("127.0.0.1", "localhost", 1))
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _server_reachable() -> tuple[bool, str]:
    last_err = ""
    for url in _tags_probe_urls():
        try:
            r = _urlopen_no_proxy(url, timeout=3.0)
            code = getattr(r, "status", None) or r.getcode()
            if code == 200:
                return True, url
        except Exception as e:
            last_err = f"{url}: {e}"
    return False, last_err


def _chat_completions_smoke(model: str) -> tuple[bool, str]:
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "reply with: OK"}],
            "max_tokens": 32,
        }
    ).encode("utf-8")
    base = (
        _load_env_minimal().get("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://localhost:11434/v1"
    )
    # normalize
    base = str(base).strip().rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/chat/completions"
    try:
        r = _urlopen_no_proxy(url, data=payload, timeout=120.0)
        body = r.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        txt = (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return True, (txt or body)[:200]
    except Exception as e:
        return False, str(e)


def _print_install_instructions() -> None:
    print(
        """
Ollama is not installed or not on PATH.

Install:
  • Windows / macOS / Linux: https://ollama.com/download

After install, re-run:
  python scripts/setup_ollama.py
"""
    )


def main() -> int:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
        load_dotenv()
    except Exception:
        pass

    env = _load_env_minimal()
    model = env.get("LLM_MODEL") or os.getenv("LLM_MODEL", "llama3.2")

    print("=" * 60)
    print("  OLLAMA SETUP + VERIFY")
    print("=" * 60)
    print(f"  LLM_MODEL       = {model}")
    print(f"  OLLAMA_BASE_URL = {env.get('OLLAMA_BASE_URL', os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'))}")
    print()

    ollama_exe = resolve_ollama_exe()
    if not ollama_exe:
        _print_install_instructions()
        print("OLLAMA FAILED — ollama CLI not found")
        return 1

    ok, detail = _server_reachable()
    if not ok:
        print("[setup] Ollama server not reachable; starting `ollama serve`…")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(
                [ollama_exe, "serve"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
            import time

            time.sleep(3)
        except Exception as e:
            print(f"[setup] Could not start ollama serve: {e}")

        ok, detail = _server_reachable()

    if not ok:
        print(f"[setup] Server still not reachable ({detail})")
        print("OLLAMA FAILED — Ollama server not running")
        return 1

    print(f"[setup] Ollama server OK ({detail})")

    print(f"[setup] Pulling model: {model} …")
    pr = subprocess.run([ollama_exe, "pull", model], cwd=str(ROOT))
    if pr.returncode != 0:
        print("OLLAMA FAILED — ollama pull failed")
        return pr.returncode or 1

    chat_ok, chat_detail = _chat_completions_smoke(model)
    if chat_ok:
        print(f"[setup] Chat smoke test OK — preview: {chat_detail!r}")
    else:
        print(f"[setup] Chat smoke test FAILED — {chat_detail}")

    try:
        from utils.helpers import get_agent_diagnostics

        print()
        print("  Agent diagnostics (summary):")
        ad = get_agent_diagnostics()
        print(json.dumps(ad, indent=2)[:2500])
    except Exception as e:
        print(f"  [!!] get_agent_diagnostics failed: {e}")

    if chat_ok:
        print()
        print("OLLAMA READY")
        return 0
    print()
    print("OLLAMA FAILED — chat completions test did not succeed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
