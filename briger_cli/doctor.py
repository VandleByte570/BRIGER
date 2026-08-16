"""Diagnostics / doctor tool for BRIGER."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional

from .utils import which, run_cmd, user_home


class Doctor:
    def __init__(self, install_dir: Optional[str] = None):
        if install_dir:
            self.install_dir = Path(install_dir).expanduser().resolve()
        else:
            from .installer import DEFAULT_SYSTEM_DIR, DEFAULT_USER_DIR, is_root
            self.install_dir = DEFAULT_SYSTEM_DIR if is_root() else DEFAULT_USER_DIR

    def log(self, *parts):
        print("[BRIGER]", *parts)

    def check_binary(self, name: str) -> bool:
        p = which(name)
        self.log(f"{name}:", "found at " + p if p else "not found")
        return bool(p)

    def check_network(self, host: str = "github.com", port: int = 443) -> bool:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            self.log(f"Network to {host}:{port}: ok")
            return True
        except Exception as exc:
            self.log(f"Network to {host}:{port}: failed ({exc})")
            return False

    def status(self) -> int:
        self.log("BRIGER install dir:", str(self.install_dir))
        print(self.run())
        return 0

    def run(self) -> str:
        lines = []
        lines.append(f"Install dir: {self.install_dir}")
        lines.append("Binaries:")
        for b in ("git", "python3", "pip3", "node", "npm", "opencode"):
            lines.append(f" - {b}: {'present' if which(b) else 'missing'}")
        # Check directories
        for name in ("data", "workspace", "logs"):
            p = self.install_dir / name
            lines.append(f" - {name}: {'exists' if p.exists() else 'missing'} ({p})")
        # Network
        net_ok = self.check_network()
        lines.append(f"Network (github.com): {'ok' if net_ok else 'failed'}")
        return "\n".join(lines)

    def run(self) -> int:
        # Print a human-readable report and return success/failure
        self.log("Checking BRIGER installation and environment...")
        ok = True
        binaries = ["git", "python3", "pip3", "node", "npm", "opencode"]
        for b in binaries:
            found = which(b)
            self.log(f" - {b}:", found or "missing")
            if not found:
                ok = False
        for name in ("data", "workspace", "logs"):
            p = self.install_dir / name
            self.log(f" - {name}:", "present" if p.exists() else "missing", "->", p)
            if not p.exists():
                ok = False
        net = self.check_network()
        if not net:
            ok = False
        self.log("Doctor result:", "OK" if ok else "ISSUES FOUND")
        return 0 if ok else 2
