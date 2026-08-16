#!/usr/bin/env bash
# Bootstrap installer to install the BRIGER CLI on a fresh machine.
# This script is intentionally small and only performs the minimal
# actions required to make the `briger` command available.

set -euo pipefail

REPO="https://github.com/VandleByte570/BRIGER.git"

# Determine install directory. Prefer /opt/briger for root installs, else $HOME/.briger
if [ "$(id -u)" -eq 0 ]; then
  INSTALL_DIR="/opt/briger"
else
  INSTALL_DIR="$HOME/.briger"
fi

echo "[BRIGER] Bootstrapping installer..."

echo "[BRIGER] Cloning repository to ${INSTALL_DIR} (or updating)..."
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --rebase || true
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO" "$INSTALL_DIR"
fi

echo "[BRIGER] Installing launcher to /usr/local/bin or ~/.local/bin"
if [ -w "/usr/local/bin" ]; then
  cp "$INSTALL_DIR/briger" /usr/local/bin/briger
  chmod +x /usr/local/bin/briger
  echo "[BRIGER] Installed /usr/local/bin/briger"
else
  mkdir -p "$HOME/.local/bin"
  cp "$INSTALL_DIR/briger" "$HOME/.local/bin/briger"
  chmod +x "$HOME/.local/bin/briger"
  echo "[BRIGER] Installed $HOME/.local/bin/briger"
  if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "[BRIGER] NOTE: $HOME/.local/bin is not on PATH. You may want to add it to your shell rc:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
  fi
fi

echo "[BRIGER] Bootstrap complete. Run 'briger --install' to perform full installation."
