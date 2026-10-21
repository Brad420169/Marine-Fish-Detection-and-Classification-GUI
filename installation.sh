#!/usr/bin/env bash
# Install a fresh copy of Marine Fish Detection GUI after installing Pixi.
set -euo pipefail
STEP='Detecting OS environment'
trap 'status=$?; if (( status != 0 )); then printf "Installation failed during: %s (exit code %s). See the error above.\n" "$STEP" "$status" >&2; fi' EXIT
echo "$STEP..."
case "$(uname -s):$(uname -m)" in
    Linux:x86_64)
        echo 'Linux detected (x86_64).'
        SHORTCUT=create_linux_shortcut.sh
        ;;
    Darwin:arm64)
        echo 'macOS detected (Apple Silicon).'
        if (( $(sw_vers -productVersion | cut -d. -f1) < 13 )); then
            echo 'macOS 13 or newer is required.' >&2
            exit 1
        fi
        SHORTCUT=create_macos_shortcut.sh
        ;;
    *) echo 'Supported platforms: x86_64 Linux and Apple Silicon macOS (native ARM64 terminal).' >&2; exit 1 ;;
esac
STEP='Checking Pixi and installation directory'
echo "$STEP..."
export PATH="$HOME/.pixi/bin:$PATH"
if ! command -v pixi >/dev/null 2>&1; then
    echo 'Install Pixi first, then reopen Terminal. See the README.' >&2
    exit 1
fi
echo 'Pixi is available.'
INSTALL_DIR="${INSTALL_DIR:-$HOME/MarineApps}"
PROJECT_DIR="$INSTALL_DIR/Marine-Fish-Detection-and-Classification-GUI"
if [[ -e "$PROJECT_DIR" || -L "$PROJECT_DIR" ]]; then
    echo "Destination already exists: $PROJECT_DIR. Choose another INSTALL_DIR or use the existing installation." >&2
    exit 1
fi
STEP='Checking Git and Git LFS'
echo "$STEP..."
if git --version >/dev/null 2>&1 && git lfs version >/dev/null 2>&1; then
    echo 'Git and Git LFS are already installed.'
else
    STEP='Installing Git and Git LFS'
    echo "$STEP..."
    pixi global install git git-lfs
    git --version
    git lfs version
    echo 'Git and Git LFS installed successfully.'
fi
STEP='Cloning Git repository'
echo "$STEP..."
mkdir -p "$INSTALL_DIR"
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git "$PROJECT_DIR"
echo 'Repository cloned.'
cd "$PROJECT_DIR"
STEP='Downloading model files with Git LFS'
echo "$STEP..."
git lfs install --local
git lfs pull
echo 'Git LFS model download complete.'
STEP='Installing Pixi environment'
echo "$STEP (approximately 8.3 GB installed on Linux; macOS size varies). Please stand by..."
echo 'Allow at least 15 GB free disk space including download caches. Downloads may take several minutes.'
pixi install --locked
echo 'Pixi environment installed.'
STEP='Setting up desktop shortcut'
echo "$STEP..."
bash "setup/$SHORTCUT"
echo 'Desktop shortcut done.'
printf 'Installation complete: %s\n' "$PROJECT_DIR"
echo 'You can now open Marine Fish Detection GUI using the desktop shortcut.'
