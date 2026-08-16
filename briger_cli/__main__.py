"""BRIGER CLI

Entry point module for briger_cli package. Implements a modular CLI
with subcommands and flags (install, update, uninstall, doctor, status,
version, help).
"""

from __future__ import annotations

import argparse
import sys

from .installer import Installer
from .updater import Updater
from .uninstaller import Uninstaller
from .doctor import Doctor
from .utils import read_version

VERSION = read_version()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="briger",
        description="BRIGER installer and management CLI",
    )

    p.add_argument("--install", "-i", action="store_true", help="Install BRIGER")
    p.add_argument("--update", "-u", action="store_true", help="Update BRIGER")
    p.add_argument("--uninstall", action="store_true", help="Uninstall BRIGER")
    p.add_argument("--doctor", action="store_true", help="Run diagnostics")
    p.add_argument("--status", action="store_true", help="Show BRIGER status")
    p.add_argument("--version", "-v", action="store_true", help="Show version")
    p.add_argument("--yes", "-y", action="store_true", help="Assume yes for prompts (non-interactive)")
    p.add_argument("--help-cli", action="store_true", help="Show extended help")

    p.add_argument("--install-dir", default=None, help="Optional install directory (default: /opt/briger or $HOME/.briger)")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    installer = Installer(install_dir=args.install_dir, assume_yes=args.yes)

    try:
        if args.version:
            print(VERSION)
            return 0

        if args.install:
            return installer.run_install()

        if args.update:
            updater = Updater(install_dir=installer.install_dir, assume_yes=args.yes)
            return updater.run_update()

        if args.uninstall:
            uninstaller = Uninstaller(install_dir=installer.install_dir, assume_yes=args.yes)
            return uninstaller.run_uninstall()

        if args.doctor:
            doctor = Doctor(install_dir=installer.install_dir)
            return doctor.run()

        if args.status:
            doctor = Doctor(install_dir=installer.install_dir)
            return doctor.status()

        # default: show help
        parser.print_help()
        return 0

    except KeyboardInterrupt:
        print("\n[BRIGER] Operation cancelled by user.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
