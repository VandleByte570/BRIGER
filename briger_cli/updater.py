"""Updater for BRIGER: pulls latest changes and re-runs installs where
necessary."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .utils import run_cmd, ensure_dir, which


class Updater:
    def __init__(self, install_dir: Optional[str] = None, assume_yes: bool = False):
        if install_dir:
            self.install_dir = Path(install_dir).expanduser().resolve()
        else:
            # default same logic as installer
            from .installer import DEFAULT_SYSTEM_DIR, DEFAULT_USER_DIR, is_root
            self.install_dir = DEFAULT_SYSTEM_DIR if is_root() else DEFAULT_USER_DIR
        self.assume_yes = assume_yes

    def log(self, *parts):
        print("[BRIGER]", *parts)

    def run_update(self) -> int:
        self.log("Updating BRIGER at:", str(self.install_dir))
        if not (self.install_dir / ".git").exists():
            self.log("Not a git repository — cannot update via git. Consider reinstalling.")
            return 1
        code, out, err = run_cmd(["git", "-C", str(self.install_dir), "pull"], capture=True)
        if code != 0:
            self.log("git pull failed:", err.strip()[:300])
            return 1
        self.log("Repository updated (git pull). Reinstalling Python requirements and launcher...")
        # Reinstall requirements and recreate launcher
        from .installer import Installer
        inst = Installer(install_dir=str(self.install_dir), assume_yes=self.assume_yes)
        inst.install_python_requirements()
        inst.create_launcher()
        self.log("Update complete.")
        return 0
