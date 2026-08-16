"""Interactive setup wizard for BRIGER.

This module implements a terminal-based interactive installer that guides
users through system checks, AI provider configuration, OpenCode, GodMode,
OpenWebUI, skills selection, storage, and security settings.

The wizard is intentionally implemented without external UI dependencies so
it works in most terminals and environments. It reuses the Installer,
Updater, and Doctor modules so installation logic is not duplicated.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .installer import Installer
from .updater import Updater
from .doctor import Doctor
from .utils import confirm, ensure_dir, user_home


class Wizard:
    def __init__(self, install_dir: Optional[str] = None, assume_yes: bool = False, noninteractive: bool = False):
        self.installer = Installer(install_dir=install_dir, assume_yes=assume_yes)
        self.updater = Updater(install_dir=str(self.installer.install_dir), assume_yes=assume_yes)
        self.doctor = Doctor(install_dir=str(self.installer.install_dir))
        self.assume_yes = assume_yes
        self.noninteractive = noninteractive
        self.config: Dict[str, Any] = {}

    # ---------- UI helpers ----------
    def _title(self, title: str) -> None:
        cols = shutil.get_terminal_size((80, 20)).columns
        bar = "═" * min(max(len(title) + 4, 40), cols - 2)
        print("╔" + bar + "╗")
        print(f"║  {title.center(len(bar) - 2)}  ║")
        print("╚" + bar + "╝")

    def _section(self, heading: str) -> None:
        print()
        print(heading)
        print("""────────────────────────────────""")

    def _prompt(self, prompt: str, default: Optional[str] = None, secret: bool = False) -> str:
        if self.noninteractive or self.assume_yes:
            return default or ""
        try:
            if secret:
                # Fall back to plain input if getpass is unavailable
                from getpass import getpass

                return getpass(prompt + (" " if default is None else f" [{default}] ")) or (default or "")
            else:
                res = input(prompt + (" " if default is None else f" [{default}] "))
                return res.strip() or (default or "")
        except (EOFError, KeyboardInterrupt):
            print("\n[BRIGER] Input cancelled.")
            sys.exit(2)

    def _choose(self, prompt: str, options: Iterable[str], default_index: int = 0) -> int:
        options = list(options)
        if self.noninteractive or self.assume_yes:
            return default_index
        print(prompt)
        for i, opt in enumerate(options, start=1):
            print(f"  {i}) {opt}")
        while True:
            choice = self._prompt(f"Choose [1-{len(options)}]", str(default_index + 1))
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return idx
            except ValueError:
                pass
            print("Invalid choice — try again.")

    def _multiselect(self, prompt: str, items: List[str], default_selected: Optional[List[int]] = None) -> List[int]:
        selected = set(default_selected or [])
        if self.noninteractive or self.assume_yes:
            return list(selected)
        print(prompt)
        while True:
            for i, item in enumerate(items, start=1):
                mark = "[x]" if (i - 1) in selected else "[ ]"
                print(f"  {i:2d}. {mark} {item}")
            print("Commands: number to toggle, a=all, n=none, c=continue")
            cmd = self._prompt("Select>")
            if cmd.lower() == "a":
                selected = set(range(len(items)))
                continue
            if cmd.lower() == "n":
                selected = set()
                continue
            if cmd.lower() == "c":
                break
            try:
                idx = int(cmd) - 1
                if 0 <= idx < len(items):
                    if idx in selected:
                        selected.remove(idx)
                    else:
                        selected.add(idx)
            except ValueError:
                print("Invalid command")
        return sorted(selected)

    # ---------- Steps ----------
    def step_system_check(self) -> None:
        self._title("BRIGER SETUP — System Check")
        system, arch = self.installer.detect_platform()
        deps = self.installer.check_dependencies()
        # Disk and RAM detections
        try:
            import psutil

            ram = f"{int(psutil.virtual_memory().total / (1024 ** 3))} GB"
            disk = psutil.disk_usage(str(self.installer.install_dir.parent))
            disk_free = f"{int(disk.free / (1024 ** 3))} GB"
        except Exception:
            ram = "unknown"
            disk_free = "unknown"
        print()
        print(f"OS             {system} {arch}")
        print(f"Git            {'✓' if deps.get('git') else '✗'}")
        print(f"Python         {'✓' if deps.get('python3') else '✗'}")
        print(f"Node.js        {'✓' if deps.get('node') else '✗'}")
        print(f"npm            {'✓' if deps.get('npm') else '✗'}")
        print(f"Disk free      {disk_free}")
        print(f"RAM            {ram}")
        # Existing BRIGER install
        existing = self.installer.detect_existing_install()
        print(f"Existing BRIGER {'found' if existing else 'not found'} at {self.installer.install_dir}")
        if not (self.noninteractive or self.assume_yes):
            if not confirm("Continue with setup?", assume_yes=self.assume_yes):
                print("Aborted by user.")
                sys.exit(1)
        self.config["system"] = {"os": system, "arch": arch, "ram": ram, "disk_free": disk_free}

    def step_ai_provider(self) -> None:
        self._title("AI Provider")
        providers = [
            "OpenAI",
            "Anthropic",
            "Google Gemini",
            "OpenRouter",
            "Hugging Face",
            "Custom OpenAI-compatible API",
            "Configure later",
        ]
        idx = self._choose("Select your AI provider:", providers, default_index=0)
        provider = providers[idx]
        self.config["ai_provider"] = {"provider": provider}
        if provider != "Configure later":
            api_key = self._prompt("Enter API key (input hidden):", default=None, secret=True)
            if api_key:
                # Store secrets inside install_dir/.opencode/.secret (protected file)
                secrets_dir = self.installer.install_dir / ".opencode"
                ensure_dir(secrets_dir)
                secret_file = secrets_dir / "ai_secret.txt"
                # write with restrictive mode
                with open(secret_file, "w", encoding="utf-8") as fh:
                    fh.write(api_key)
                try:
                    os.chmod(secret_file, 0o600)
                except Exception:
                    pass
                self.config["ai_provider"]["secret_file"] = str(secret_file)
            base_url = self._prompt("Base URL (leave blank for default):", default="")
            if base_url:
                self.config["ai_provider"]["base_url"] = base_url
            model = self._prompt("Model name (optional):", default="")
            if model:
                self.config["ai_provider"]["model"] = model

    def step_opencode(self) -> None:
        self._title("OpenCode")
        has = bool(shutil.which("opencode"))
        if has:
            self._section("OpenCode")
            print("Status: ✓ Installed")
            use_existing = True
            if not (self.noninteractive or self.assume_yes):
                use_existing = confirm("Use existing opencode installation?", assume_yes=self.assume_yes)
            self.config["opencode"] = {"use_existing": use_existing}
        else:
            self._section("OpenCode: not found")
            if self.noninteractive or self.assume_yes:
                self.config["opencode"] = {"install": True}
            else:
                if confirm("OpenCode is not installed. Install via npm now?", assume_yes=self.assume_yes):
                    self.config["opencode"] = {"install": True}
                else:
                    self.config["opencode"] = {"install": False}

    def step_godmode(self) -> None:
        self._title("GodMode")
        # Detect skills/godmode markers in repo
        gm_dir = self.installer.install_dir / "opencode" / "skills"
        has_god = False
        if gm_dir.exists():
            for p in gm_dir.glob("*godmode*"):
                has_god = True
                break
        print("GodMode status:", "Detected" if has_god else "Not detected in repo")
        opts = ["Enable", "Disable", "Configure", "Skip"]
        idx = self._choose("Choose action for GodMode:", opts, default_index=0)
        self.config["godmode"] = {"action": opts[idx]}

    def step_openwebui(self) -> None:
        self._title("OpenWebUI")
        # Check for typical UI files (webui folder or similar)
        ui_dir = self.installer.install_dir / "webui"
        detected = ui_dir.exists()
        print("OpenWebUI detected:" , detected)
        opts = ["Enable OpenWebUI", "Disable OpenWebUI", "Configure later"]
        idx = self._choose("Choose action:", opts, default_index=0)
        self.config["openwebui"] = {"action": opts[idx]}

    def step_skills(self) -> None:
        self._title("BRIGER Skills")
        skills_dirs = [
            self.installer.install_dir / "opencode" / "skills",
            self.installer.install_dir / ".opencode" / "skills",
        ]
        skills: List[str] = []
        for d in skills_dirs:
            if d.exists():
                for f in sorted(d.glob("*.md")):
                    skills.append(f.stem)
        if not skills:
            print("No skills detected in repository.")
            self.config["skills"] = {"selected": []}
            return
        selected_idx = self._multiselect("Select skills to enable (toggle entries):", skills)
        selected = [skills[i] for i in selected_idx]
        self.config["skills"] = {"selected": selected}

    def step_storage(self) -> None:
        self._title("Storage")
        default = str(self.installer.install_dir / "data")
        print(f"Default location: {default}")
        opts = ["Default location", "Custom location", "Existing BRIGER directory"]
        idx = self._choose("Select storage option:", opts, default_index=0)
        if idx == 0:
            self.config["storage"] = {"path": default}
        elif idx == 1:
            path = self._prompt("Enter path:", default=default)
            self.config["storage"] = {"path": path}
        else:
            path = self._prompt("Enter existing BRIGER path:", default=default)
            self.config["storage"] = {"path": path}

    def step_security(self) -> None:
        self._title("Security")
        print("Shell command execution defaults:")
        opts = ["Ask for confirmation", "Allow", "Disable"]
        idx = self._choose("Choose shell execution policy:", opts, default_index=0)
        shell_policy = opts[idx]
        print("Network access defaults:")
        opts = ["Allow", "Restrict"]
        idx = self._choose("Network access:", opts, default_index=0)
        network_policy = opts[idx]
        print("Secrets storage:")
        opts = ["Protected (file)", "Environment variables", "Configure later"]
        idx = self._choose("Secrets storage:", opts, default_index=0)
        secrets_policy = opts[idx]
        self.config["security"] = {
            "shell_policy": shell_policy,
            "network_policy": network_policy,
            "secrets_policy": secrets_policy,
        }

    def review(self) -> bool:
        self._title("Review Configuration")
        print("BRIGER CONFIGURATION")
        print("════════════════════════════════")
        ai = self.config.get("ai_provider", {}).get("provider", "Not configured")
        print(f"AI Provider      {ai}")
        print(f"OpenCode         {self.config.get('opencode', {})}")
        print(f"GodMode          {self.config.get('godmode', {})}")
        print(f"OpenWebUI        {self.config.get('openwebui', {})}")
        skills = self.config.get("skills", {}).get("selected", [])
        print(f"Skills           {len(skills)} selected")
        print(f"Storage          {self.config.get('storage', {}).get('path', 'default')}")
        print(f"Security         {self.config.get('security', {})}")
        print("""
────────────────────────────────
""")
        if self.noninteractive or self.assume_yes:
            return True
        choice = self._choose("Apply configuration?", ["Apply configuration", "Go back", "Cancel"], default_index=0)
        return choice == 0

    # ---------- Apply configuration ----------
    def _copy_skills(self, selected: List[str]) -> None:
        # Copy selected skill files from repo into install_dir/.opencode/skills
        target_skills_dir = self.installer.install_dir / ".opencode" / "skills"
        ensure_dir(target_skills_dir)
        source_dirs = [
            self.installer.install_dir / "opencode" / "skills",
            self.installer.install_dir / ".opencode" / "skills",
        ]
        copied = 0
        for sd in source_dirs:
            if not sd.exists():
                continue
            for f in sd.glob("*.md"):
                if f.stem in selected:
                    dst = target_skills_dir / f.name
                    shutil.copy2(f, dst)
                    copied += 1
        print(f"[BRIGER] Installed {copied} skills.")

    def apply(self) -> int:
        steps = []
        # 1. Clone/update repo
        steps.append(("Creating or updating repository", self.installer.clone_or_update_repo))
        # 2. Create dirs
        steps.append(("Configuring directories", self.installer.configure))
        # 3. Python reqs
        steps.append(("Installing Python requirements", self.installer.install_python_requirements))
        # 4. OpenCode
        steps.append(("Configuring OpenCode", lambda: self.installer.install_opencode()))
        # 5. Launcher
        steps.append(("Installing launcher", self.installer.create_launcher))
        # 6. Skills
        skills_selected = self.config.get("skills", {}).get("selected", [])
        if skills_selected:
            steps.append(("Installing skills", lambda: self._copy_skills(skills_selected)))
        # 7. Final diagnostics
        steps.append(("Running diagnostics", lambda: self.doctor.run()))

        total = len(steps)
        for i, (desc, fn) in enumerate(steps, start=1):
            print(f"[{i}/{total}] {desc} ...", end=" ")
            try:
                res = fn()
                # If the step returns a non-zero code, treat as failure
                if isinstance(res, int) and res != 0:
                    print("FAILED")
                    print(f"[BRIGER] Step failed: {desc}")
                    return 1
                print("✓")
            except Exception as exc:
                print("FAILED")
                print(f"[BRIGER] Error during {desc}: {exc}")
                return 1

        print()
        print("╔" + "═" * 46 + "╗")
        print("║" + " BRIGER READY ".center(46) + "║")
        print("╚" + "═" * 46 + "╝")
        print()
        print("Commands:")
        print("    briger status")
        print("    briger doctor")
        print("    briger update")
        return 0

    def run(self) -> int:
        # Orchestrate steps in order
        self.step_system_check()
        self.step_ai_provider()
        self.step_opencode()
        self.step_godmode()
        self.step_openwebui()
        self.step_skills()
        self.step_storage()
        self.step_security()
        if not self.review():
            print("Aborted by user.")
            return 2
        return self.apply()
