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
# Resolve relative destinations before changing into the cloned repository.
[[ "$INSTALL_DIR" = /* ]] || INSTALL_DIR="$PWD/$INSTALL_DIR"
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
STEP='Downloading Community Fish Detector'
echo "$STEP..."
(
mkdir -p "$PROJECT_DIR/models"
cd -- "$PROJECT_DIR/models"
url="https://github.com/filippovarini/community-fish-detector/releases/download/cfd-1.00-yolov12x/cfd-yolov12x-1.00.pt"
expected="0b259afb3dca1d1d7f9d842ebf136884b2e3706b151172b266bb24e07f2b8e98"
target="Fish_Only_YOLO12.pt"

checksum() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "Install sha256sum or shasum to verify the download." >&2
        return 1
    fi
}

if [[ -e "$target" ]]; then
    if [[ "$(checksum "$target")" == "$expected" ]]; then
        echo "$target is already installed and verified."
        exit 0
    fi
    echo "$target already exists with a different checksum; move it aside before retrying." >&2
    exit 1
fi

temp=$(mktemp "./.fish-download.XXXXXX")
trap 'rm -f -- "$temp"' EXIT
if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --output "$temp" "$url"
elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --output-document="$temp" "$url"
else
    echo "Install curl or wget, then rerun this script." >&2
    exit 1
fi
if [[ "$(checksum "$temp")" != "$expected" ]]; then
    echo "Checksum mismatch; downloaded file discarded. Please retry." >&2
    exit 1
fi
# A hard link publishes the complete file without overwriting an existing model.
ln -- "$temp" "$target"
echo "Installed $PWD/$target"
)
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
