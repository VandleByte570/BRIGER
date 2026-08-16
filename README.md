# BRIGER — Open WebUI + OpenCode Integration

BRIGER brings together Open WebUI and OpenCode into a single, easy-to-install project with a professional, modular CLI to manage installation, updates, diagnostics, and uninstallation.

Whether you are running locally or in a container, BRIGER provides a reproducible developer experience and a headless OpenCode bridge for automated coding tasks.

---

## Key features

- One-command CLI management (`briger`) for install, update, uninstall, status, and diagnostics.
- Idempotent installer: safe to run multiple times; detects existing installs and offers updates.
- Best-effort dependency installation for common Linux package managers (apt, dnf, yum, pacman, brew).
- Automatic attempts to install OpenCode (via `npm`) when available.
- Non-root-friendly: installs to `/opt/briger` when run as root, otherwise installs to `$HOME/.briger`.
- Modular implementation: installer, updater, uninstaller, and doctor tools are separate and testable modules.
- Preserves Docker / Hugging Face functionality — the installer is a host-side tool and does not modify container files.

---

## Quick start (one-line bootstrap)

This bootstrap step is intentionally small: it only clones (or updates) the repository and installs a tiny launcher that provides the `briger` command. The full installer runs when you execute `briger --install`.

```bash
# One-time bootstrap (safe & minimal)
curl -fsSL https://raw.githubusercontent.com/VandleByte570/BRIGER/main/install.sh | bash
```

After the bootstrap you should have a `briger` command in `/usr/local/bin` or `$HOME/.local/bin`. If the latter is used, make sure `~/.local/bin` is on your PATH.

---

## Full install (recommended)

Run the installer from the `briger` CLI. This is idempotent and will detect an existing installation and offer an update instead of duplicating files.

Interactive install:

```bash
briger --install
# or
briger -i
```

Non-interactive (CI-friendly) install:

```bash
briger --install --yes
# or
briger -i -y
```

You can also target a custom directory (useful for testing):

```bash
briger --install --install-dir /tmp/briger-test --yes
```

---

## CLI Commands

- `briger --install` / `briger -i`  — Install BRIGER (idempotent)
- `briger --update` / `briger -u`   — Update an existing install (git pull + reapply bits)
- `briger --uninstall`              — Uninstall BRIGER (destructive; asks for confirmation)
- `briger --doctor`                 — Run diagnostics (binaries, config, dirs, network)
- `briger --status`                 — Show quick status summary
- `briger --version` / `briger -v`  — Show CLI/version
- `briger --help`                   — Show help

Each command prints clear progress prefixed with `[BRIGER]` so logs are easy to scan.

---

## Examples

Check environment and BRIGER status:

```bash
briger --status
briger --doctor
```

Update an existing installation:

```bash
briger --update
```

Uninstall (interactive):

```bash
briger --uninstall
```

Test installs into a temporary directory (safe):

```bash
# Run from the repository root without bootstrap
PYTHONPATH=. python3 -m briger_cli --install --install-dir /tmp/briger-test --yes
PYTHONPATH=. python3 -m briger_cli --doctor --install-dir /tmp/briger-test
PYTHONPATH=. python3 -m briger_cli --uninstall --install-dir /tmp/briger-test --yes
```

---

## What the installer does (high level)

- Detects OS and architecture.
- Checks for required binaries: `git`, `python3`, `pip`, `node`, `npm` and `opencode`.
- Attempts to install missing packages using a detected package manager (best-effort).
- Clones the BRIGER repository (or runs `git pull` if already present).
- Installs Python requirements for the headless server (`opencode_server/requirements.txt`) using `pip --user`.
- Attempts to install OpenCode via `npm install -g opencode-ai` if `npm` is present.
- Configures the install directory and subdirectories: `data`, `workspace`, `logs`, `.opencode`.
- Installs a tiny `briger` launcher into `/usr/local/bin` or `$HOME/.local/bin`.
- Copies default `config/opencode.json` into the install `.opencode` directory if not present.

---

## Docker installation (build & run)

BRIGER includes all code needed to run inside a container. Below is an example Dockerfile and a minimal set of commands to build and run BRIGER as a container. This example preserves the repository's Docker/Hugging Face-friendly design and does not alter your existing Dockerfile.

Example Dockerfile (illustrative - adapt to your distro/base image):

```dockerfile
# Example Dockerfile for BRIGER
# Use a slim Python base; adapt the Node/npm install steps to your base image
FROM python:3.11-slim

# Install system dependencies (git, curl, build tools) and Node.js/npm (example uses NodeSource)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    build-essential \
    wget \
    gnupg2 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) from NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install global OpenCode (optional - the installer can also do this)
RUN npm install -g opencode-ai || true

# Create app directory
WORKDIR /app

# Copy repository into image (or use a multi-stage build)
COPY . /app

# Install Python server requirements
RUN pip install --no-cache-dir -r opencode_server/requirements.txt || true

# Ensure entrypoint is executable (repository contains entrypoint.sh)
RUN chmod +x /app/entrypoint.sh || true

# Expose UI and opencode server ports (adjust to your config)
EXPOSE 7860 4096

# Default entrypoint uses the existing repository entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
CMD []
```

Build and run the image (example):

```bash
# Build the Docker image (run from repository root)
docker build -t briger:latest .

# Run the container and map ports + persistent data directory
docker run -it --rm \
  -p 7860:7860 \
  -p 4096:4096 \
  -v /path/on/host/briger_data:/app/data \
  --name briger briger:latest
```

Notes for Docker users

- The example Dockerfile is intentionally generic. If your base image already provides Node.js/npm or other dependencies, adapt the steps accordingly.
- The installer's host-side CLI is designed for local installs; containers typically run the server directly (Dockerfile above runs the repository's entrypoint.sh which starts supervisord).
- If you prefer the container to run with an existing installation path (e.g., `/opt/briger`), mount a host directory into the container and point the environment variables accordingly.

---

## Troubleshooting

- `briger` command not found after bootstrap:
  - Ensure `~/.local/bin` or `/usr/local/bin` is on your PATH. Add to your shell rc if needed:
    ```bash
    export PATH="$HOME/.local/bin:$PATH"
    ```

- OpenCode not installed automatically:
  - Install Node.js and npm on your system, then run:
    ```bash
    npm install -g opencode-ai
    ```

- Permission errors when installing system packages:
  - Running the installer as a non-root user will default to a user-local install (`$HOME/.briger`).
  - For system-wide installs, run the bootstrap and `briger --install` using `sudo`.

- Network issues when cloning/updating:
  - Ensure `github.com` is reachable and that firewalls/proxies allow HTTPS (port 443).

---

## Supported platforms

Primary targets: Linux (x86_64, arm64). The installer performs best-effort detection of package managers and will attempt automated installs for common distros.

If your distribution is not supported by the automated installer, the CLI will print precise instructions you can follow to complete any missing steps manually.

---

## Preservation of container usage

BRIGER's CLI and installer are host-side tools that do not alter the repository's Dockerfile or entrypoint. Container images and Hugging Face Space setups continue to work unchanged. The installer reuses the repository's configuration files (for example `config/opencode.json`) rather than hard-coding development paths.

---

## Contributing

Contributions are welcome. If you want to improve installation support, add shell completion, or add CI tests for the CLI:

1. Fork the repository and create a branch.
2. Add tests under a `tests/` directory where appropriate.
3. Open a pull request describing your changes.

---

## License & Code of Conduct

Please add your project license and code of conduct to the repository root if you plan to make BRIGER public.

---

If you want, I can also add pretty badges, CI workflows, argcomplete-based shell completion scripts (bash/zsh/fish), and a CONTRIBUTING.md with developer instructions — tell me which you'd like next and I'll add them.
