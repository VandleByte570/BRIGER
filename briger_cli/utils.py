"""Utility helpers used by the BRIGER CLI modules."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional


def read_version() -> str:
    # Try to read version from a VERSION file in repo root, else fallback
    try:
        here = Path(__file__).resolve().parents[1]
        version_file = here / "VERSION"
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "0.0.0"


def run_cmd(cmd: Iterable[str], check: bool = False, capture: bool = False, env=None, timeout: int | None = None) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(list(cmd), capture_output=capture, text=True, env=env or os.environ.copy(), timeout=timeout)
        stdout = completed.stdout if capture else ""
        stderr = completed.stderr if capture else ""
        return completed.returncode, stdout, stderr
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or str(exc)


def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        resp = input(f"{prompt} [y/N]: ")
    except EOFError:
        return False
    return resp.strip().lower() in ("y", "yes")


def is_root() -> bool:
    return os.geteuid() == 0


def user_home() -> Path:
    return Path(os.path.expanduser("~"))
