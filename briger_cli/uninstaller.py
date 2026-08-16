"""Uninstaller for BRIGER. Removes installed files after confirmation."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .utils import confirm, user_home


class Uninstaller:
    def __init__(self, install_dir: Optional[str] = None, assume_yes: bool = False):
        if install_dir:
            self.install_dir = Path(install_dir).expanduser().resolve()
        else:
            from .installer import DEFAULT_SYSTEM_DIR, DEFAULT_USER_DIR, is_root
            self.install_dir = DEFAULT_SYSTEM_DIR if is_root() else DEFAULT_USER_DIR
        self.assume_yes = assume_yes

    def log(self, *parts):
        print("[BRIGER]", *parts)

    def run_uninstall(self) -> int:
        if not self.install_dir.exists():
            self.log("BRIGER installation not found at:", str(self.install_dir))
            return 1
        self.log("About to remove BRIGER installation at:", str(self.install_dir))
        if not confirm("Are you sure you want to uninstall BRIGER? This will remove %s" % str(self.install_dir), assume_yes=self.assume_yes):
            self.log("Uninstall cancelled.")
            return 2
        try:
            shutil.rmtree(self.install_dir)
            self.log("Removed installation directory:", str(self.install_dir))
        except Exception as exc:
            self.log("Failed to remove installation directory:", str(exc))
            return 1
        # Remove launcher from common locations
        removed = False
        for p in (Path("/usr/local/bin") / "briger", user_home() / ".local" / "bin" / "briger"):
            try:
                if p.exists():
                    p.unlink()
                    self.log("Removed launcher:", str(p))
                    removed = True
            except Exception:
                pass
        if not removed:
            self.log("No launcher found in common locations; you may need to remove it manually.")
        self.log("Uninstall complete.")
        return 0
