"""
start_with_brain.py
Checks Ollama, pulls model if needed, then starts the dashboard server.
Run with: python scripts/start_with_brain.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def resolve_ollama_exe() -> str | None:
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


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def check_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    url = base_url.rstrip("/") + "/api/tags"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(urllib.request.Request(url), timeout=3)
        return True
    except Exception:
        try:
            alt = url.replace("localhost", "127.0.0.1", 1) if "localhost" in url else url.replace("127.0.0.1", "localhost", 1)
            opener.open(urllib.request.Request(alt), timeout=3)
            return True
        except Exception:
            return False


def pull_model(model: str, ollama_exe: str) -> None:
    print(f"[Brain] Pulling model '{model}' (this may take a few minutes on first run)...")
    subprocess.run([ollama_exe, "pull", model], check=True, cwd=str(ROOT))


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    env = load_env()
    provider = env.get("LLM_PROVIDER", "ollama").lower()
    model = env.get("LLM_MODEL", "llama3.2")

    print("=" * 55)
    print("  Omniscient AI — Brain Startup")
    print("=" * 55)

    if provider == "ollama":
        ollama_exe = resolve_ollama_exe()
        if not ollama_exe:
            print("[Brain] ERROR: Ollama not installed.")
            print("[Brain] Download from: https://ollama.com")
            sys.exit(1)

        origin = (
            env.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        ).strip().rstrip("/")
        if origin.endswith("/v1"):
            probe_base = origin[:-3].rstrip("/")
        else:
            probe_base = origin.replace("/v1", "").rstrip("/") or "http://localhost:11434"

        if not check_ollama_running(probe_base):
            print("[Brain] Starting Ollama server...")
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(
                [ollama_exe, "serve"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
            time.sleep(3)

        if check_ollama_running(probe_base):
            print(f"[Brain] Ollama running. Model: {model}")
            try:
                pull_model(model, ollama_exe)
                print(f"[Brain] Model '{model}' ready.")
            except Exception as e:
                print(f"[Brain] Warning: could not pull model: {e}")
        else:
            print("[Brain] Warning: Ollama server did not start. Continuing anyway...")
    else:
        print(f"[Brain] Provider: {provider} — skipping Ollama check.")

    print("[Brain] Starting dashboard server...")
    print("=" * 55)
    server_script = ROOT / "dashboard" / "server.py"
    os.execv(sys.executable, [sys.executable, str(server_script)])


if __name__ == "__main__":
    main()
