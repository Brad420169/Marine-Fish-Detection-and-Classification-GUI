#!/usr/bin/env bash
# Install a fresh copy of Marine Fish Detection GUI after installing Pixi.
set -euo pipefail
case "$(uname -s):$(uname -m)" in
    Linux:x86_64) SHORTCUT=create_linux_shortcut.sh ;;
    Darwin:arm64) SHORTCUT=create_macos_shortcut.sh ;;
    *) echo 'Supported platforms: x86_64 Linux and Apple Silicon macOS (native ARM64 terminal).' >&2; exit 1 ;;
esac
export PATH="$HOME/.pixi/bin:$PATH"
if ! command -v pixi >/dev/null 2>&1; then
    echo 'Install Pixi first, then reopen Terminal. See the README.' >&2
    exit 1
fi
INSTALL_DIR="${INSTALL_DIR:-$HOME/MarineApps}"
PROJECT_DIR="$INSTALL_DIR/Marine-Fish-Detection-and-Classification-GUI"
if [[ -e "$PROJECT_DIR" || -L "$PROJECT_DIR" ]]; then
    echo "Destination already exists: $PROJECT_DIR. Choose another INSTALL_DIR or use the existing installation." >&2
    exit 1
fi
pixi global install git git-lfs
git --version
git lfs version
mkdir -p "$INSTALL_DIR"
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git "$PROJECT_DIR"
cd "$PROJECT_DIR"
git lfs install --local
git lfs pull
pixi install --locked
bash "setup/$SHORTCUT"
printf 'Installation complete: %s\n' "$PROJECT_DIR"
