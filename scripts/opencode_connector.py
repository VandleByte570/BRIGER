#!/usr/bin/env python3
"""
scripts/opencode_connector.py

Connector that waits for the local OpenCode FastAPI to become healthy,
collects installed skills, writes a JSON status file and optionally posts a
registration payload to a local WebUI internal endpoint.

This script is designed to be started from entrypoint.sh in the container.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple

import httpx


APP_DIR = Path(os.getenv("APP_DIR", "/app"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
OPENCODE_HOST = os.getenv("OPENCODE_SERVER_HOSTNAME", "127.0.0.1")
OPENCODE_PORT = os.getenv("OPENCODE_SERVER_PORT", "4096")
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")
WEBUI_REGISTER_URL = os.getenv("WEBUI_REGISTER_URL", "")  # optional
STATUS_FILE = DATA_DIR / "opencode_status.json"
SKILL_DIR = Path(os.getenv("OPENCODE_CONFIG_DIR", "/app/.opencode")) / "skills"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def write_status(ready: bool, message: str, skills: List[str] | None = None) -> None:
    payload = {
        "ready": bool(ready),
        "message": str(message),
        "timestamp": int(time.time()),
        "skills": skills or [],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write(STATUS_FILE, payload)
    except Exception:
        # fallback to simple write
        STATUS_FILE.write_text(json.dumps(payload, indent=2))


def list_skills() -> List[str]:
    out: List[str] = []
    try:
        if SKILL_DIR.exists() and SKILL_DIR.is_dir():
            for p in sorted(SKILL_DIR.iterdir()):
                if p.suffix.lower() == ".md":
                    out.append(p.name)
    except Exception:
        # ignore listing errors
        pass
    return out


def wait_for_health(timeout: int = 60) -> Tuple[bool, str]:
    base = f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"
    auth = (USERNAME, PASSWORD) if PASSWORD else None
    # short timeout per request
    client = httpx.Client(timeout=5.0, auth=auth)
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = client.get(f"{base}/health")
            if r.status_code == 200:
                return True, "healthy"
            # if 401/403, still return with message so operator can fix creds
            if r.status_code in (401, 403):
                return False, f"unauthorized ({r.status_code})"
        except Exception as exc:  # pragma: no cover - network runtime
            last_exc = exc
        time.sleep(1)
    return False, f"timeout after {timeout}s (last_error={repr(last_exc)})"


def try_register_with_webui(payload: dict) -> bool:
    if not WEBUI_REGISTER_URL:
        return False
    try:
        # register with short timeout
        r = httpx.post(WEBUI_REGISTER_URL, json=payload, timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


def main() -> int:
    write_status(False, "starting", skills=list_skills())

    ok, msg = wait_for_health(timeout=int(os.getenv("OPENCODE_CONNECT_TIMEOUT", "60")))
    skills = list_skills()
    if not ok:
        write_status(False, f"opencode not healthy: {msg}", skills=skills)
        print(f"[opencode_connector] OpenCode not healthy: {msg}", file=sys.stderr)
        return 1

    payload = {
        "host": OPENCODE_HOST,
        "port": OPENCODE_PORT,
        "username": USERNAME,
        "has_password": bool(PASSWORD),
        "skills": skills,
    }

    write_status(True, "opencode healthy", skills=skills)
    registered = try_register_with_webui(payload)
    if registered:
        write_status(True, "registered with webui", skills=skills)
        print("[opencode_connector] Registered with WebUI")
    else:
        # not fatal; WebUI can read the status file
        write_status(True, "healthy (webui registration skipped/failed)", skills=skills)
        print("[opencode_connector] WebUI registration skipped or failed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
