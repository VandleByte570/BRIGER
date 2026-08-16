"""Installer implementation for BRIGER.

This module implements an idempotent installer which attempts to perform
all the tasks required to make BRIGER runnable on a fresh Linux host.

Design goals:
- Work without requiring manual edits
- Prefer system locations (/opt/briger) when running as root
- Fall back to $HOME/.briger for local installs
- Be idempotent and offer updates instead of duplicating files
- Try to install dependencies when safe and possible (apt/dnf/pacman/brew)
- Provide clear progress messages
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .utils import (run_cmd, which, ensure_dir, confirm, is_root, user_home)

REPO_URL = "https://github.com/VandleByte570/BRIGER.git"
DEFAULT_SYSTEM_DIR = Path("/opt/briger")
DEFAULT_USER_DIR = user_home() / ".briger"
LAUNCHER_NAME = "briger"


class Installer:
    def __init__(self, install_dir: Optional[str] = None, assume_yes: bool = False):
        if install_dir:
            self.install_dir = Path(install_dir).expanduser().resolve()
        else:
            self.install_dir = DEFAULT_SYSTEM_DIR if is_root() else DEFAULT_USER_DIR
        self.assume_yes = assume_yes

    def log(self, *parts):
        print("[BRIGER]", *parts)

    def detect_platform(self) -> tuple[str, str]:
        system = platform.system().lower()
        arch = platform.machine().lower()
        # Normalize some common arch names
        if arch in ("x86_64", "amd64"):
            arch = "x86_64"
        if arch in ("aarch64", "arm64"):
            arch = "arm64"
        return system, arch

    def check_dependencies(self) -> dict:
        self.log("Checking dependencies...")
        deps = {
            "git": which("git") is not None,
            "python3": which("python3") is not None,
            "pip3": which("pip3") is not None or which("pip") is not None,
            "node": which("node") is not None,
            "npm": which("npm") is not None,
        }
        for k, v in deps.items():
            self.log(f" - {k}:", "ok" if v else "missing")
        return deps

    def try_install_packages(self, packages: list[str]) -> bool:
        # Try to detect package manager and install packages (best-effort)
        self.log("Attempting to install packages:", ", ".join(packages))
        if which("apt-get"):
            cmd = ["sudo", "apt-get", "update"]
            run_cmd(cmd)
            cmd = ["sudo", "apt-get", "install", "-y"] + packages
            return run_cmd(cmd)[0] == 0
        if which("dnf"):
            cmd = ["sudo", "dnf", "install", "-y"] + packages
            return run_cmd(cmd)[0] == 0
        if which("yum"):
            cmd = ["sudo", "yum", "install", "-y"] + packages
            return run_cmd(cmd)[0] == 0
        if which("pacman"):
            cmd = ["sudo", "pacman", "-Sy", "--noconfirm"] + packages
            return run_cmd(cmd)[0] == 0
        # Homebrew on Linux / mac
        if which("brew"):
            cmd = ["brew", "install"] + packages
            return run_cmd(cmd)[0] == 0
        self.log("No supported package manager found — please install packages manually.")
        return False

    def clone_or_update_repo(self) -> None:
        self.log("Ensuring repository is available at:", str(self.install_dir))
        ensure_dir(self.install_dir.parent)
        if (self.install_dir / ".git").exists():
            # Already a git repo — attempt to pull
            self.log("Repository already present; attempting to update (git pull)...")
            code, out, err = run_cmd(["git", "-C", str(self.install_dir), "pull"], capture=True)
            if code != 0:
                self.log("Warning: git pull failed:", err.strip()[:200])
        elif self.install_dir.exists() and any(self.install_dir.iterdir()):
            # Directory exists but not a git repo — keep contents and warn
            self.log("Directory exists but is not a git repo; skipping clone and using existing files.")
        else:
            # Clone
            ensure_dir(self.install_dir.parent)
            code, out, err = run_cmd(["git", "clone", REPO_URL, str(self.install_dir)], capture=True)
            if code != 0:
                raise RuntimeError("Failed to clone BRIGER repository: " + err)

    def install_opencode(self) -> None:
        # Try to install OpenCode via npm if node/npm present
        if which("opencode"):
            self.log("OpenCode already installed.")
            return
        if not which("npm"):
            self.log("npm not found: cannot install OpenCode automatically. Please install node/npm first.")
            return
        self.log("Installing OpenCode (opencode) via npm...")
        code, out, err = run_cmd(["npm", "install", "-g", "opencode-ai"], capture=True)
        if code != 0:
            self.log("Failed to install opencode via npm:", err.strip()[:300])
            self.log("You can install it manually: npm install -g opencode-ai")
        else:
            self.log("OpenCode installed (npm global).")

    def install_python_requirements(self) -> None:
        # Install python dependencies for opencode_server using pip3 --user (non-root safe)
        req_file = self.install_dir / "opencode_server" / "requirements.txt"
        if not req_file.exists():
            self.log("No requirements.txt found at", str(req_file))
            return
        pip_cmd = which("pip3") or which("pip")
        if not pip_cmd:
            self.log("pip not found: skipping Python dependency installation. You can run: python3 -m pip install -r %s" % str(req_file))
            return
        self.log("Installing Python requirements (may use --user)...")
        # Prefer per-user install to avoid requiring sudo
        code, out, err = run_cmd([pip_cmd, "install", "--user", "-r", str(req_file)], capture=True)
        if code != 0:
            self.log("pip install returned non-zero exit code:", err.strip()[:300])
        else:
            self.log("Python requirements installed (user site).")

    def create_launcher(self) -> None:
        # Install the small executable 'briger' into a location on PATH
        # Prefer /usr/local/bin when possible, else $HOME/.local/bin
        target_dirs = [Path("/usr/local/bin"), user_home() / ".local" / "bin"]
        chosen = None
        for d in target_dirs:
            if d.exists() and os.access(str(d), os.W_OK):
                chosen = d
                break
        if chosen is None:
            # Try to create ~/.local/bin
            chosen = user_home() / ".local" / "bin"
            ensure_dir(chosen)

        launcher_src = Path(__file__).resolve().parents[1] / "briger"
        dest = chosen / LAUNCHER_NAME
        # Copy or update
        try:
            shutil.copy2(launcher_src, dest)
            dest.chmod(0o755)
            self.log(f"Installed launcher to {dest}")
        except Exception as exc:
            self.log("Failed to install launcher:", str(exc))
            raise

    def configure(self) -> None:
        # Create data, workspace, logs directories inside install_dir
        for name in ("data", "workspace", "logs", ".opencode"):
            d = self.install_dir / name
            ensure_dir(d)
            self.log("Ensured directory:", str(d))

    def detect_existing_install(self) -> bool:
        # Detect whether BRIGER already appears installed in the chosen dir
        if (self.install_dir / "opencode_server").exists():
            self.log("Existing BRIGER installation detected at", str(self.install_dir))
            return True
        return False

    def run_install(self) -> int:
        self.log("Checking system...")
        system, arch = self.detect_platform()
        self.log(f"Platform: {system} {arch}")

        deps = self.check_dependencies()

        if not deps["git"]:
            self.log("git is required. Attempting to install git (best effort)...")
            self.try_install_packages(["git"])  # best effort

        existing = self.detect_existing_install()
        if existing:
            if not confirm("BRIGER appears already installed. Do you want to update the existing installation?", assume_yes=self.assume_yes):
                self.log("Installation aborted — existing installation left unchanged.")
                return 0
            # Attempt update flow
            from .updater import Updater
            updater = Updater(install_dir=str(self.install_dir), assume_yes=self.assume_yes)
            return updater.run_update()

        self.log("Installing BRIGER to:", str(self.install_dir))

        # Clone repository or copy files
        self.clone_or_update_repo()

        # Configure directories
        self.configure()

        # Install python dependencies for server
        try:
            self.install_python_requirements()
        except Exception as exc:
            self.log("Warning: Python requirements installation failed:", str(exc))

        # Install OpenCode
        try:
            self.install_opencode()
        except Exception as exc:
            self.log("Warning: OpenCode installation attempt failed:", str(exc))

        # Install launcher
        try:
            self.create_launcher()
        except Exception as exc:
            self.log("Failed to create launcher:", str(exc))

        self.log("Configuring skills and default configuration...")
        # Attempt to copy default opencode config if present in repo to .opencode
        repo_opencode = self.install_dir / "config" / "opencode.json"
        target_opencode = self.install_dir / ".opencode" / "opencode.json"
        try:
            if repo_opencode.exists() and not target_opencode.exists():
                shutil.copy2(repo_opencode, target_opencode)
                self.log("Installed default opencode.json configuration.")
        except Exception:
            self.log("Could not install opencode.json; continuing.")

        self.log("Installation complete!")
        self.log(f"To get started: run 'briger --status' or 'briger --doctor'")
        return 0
