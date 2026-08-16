# BRIGER

This repository contains BRIGER — an integration of Open WebUI and OpenCode.

Quick CLI installer

1. Bootstrap (first-time):

   curl -fsSL https://raw.githubusercontent.com/VandleByte570/BRIGER/main/install.sh | bash

   This clones the repository to /opt/briger (if run as root) or $HOME/.briger and installs a small launcher in /usr/local/bin or $HOME/.local/bin.

2. Full install using the CLI:

   briger --install

   Use `briger -i` as a shortcut. Add `--yes` to run non-interactively:

   briger --install --yes

Available commands

- briger --install / -i      Install BRIGER (idempotent). Installs dependencies where possible.
- briger --update / -u       Update an existing BRIGER installation (git pull + re-install bits).
- briger --uninstall         Uninstall BRIGER (destructive; requires confirmation).
- briger --doctor            Run diagnostics to check BRIGER, OpenCode, and dependencies.
- briger --status            Show installation status.
- briger --version           Show briger CLI version.
- briger --help              Show help.

Troubleshooting

- If the `briger` command is not found after bootstrap, ensure $HOME/.local/bin or /usr/local/bin is on your PATH.
- If OpenCode is not installed automatically, install Node.js and npm and run:

    npm install -g opencode-ai

Supported platforms

- Linux x86_64 and arm64 are the primary targets. The installer attempts to detect package managers (apt, dnf, yum, pacman, brew) and will try best-effort automated installs.

