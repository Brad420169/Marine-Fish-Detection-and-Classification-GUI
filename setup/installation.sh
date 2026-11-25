#!/usr/bin/env bash
# Install Marine Fish Detection GUI after installing Pixi. Rerunning resumes an earlier attempt.
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
# Keep in sync with requires-pixi in pixi.toml: older Pixi rejects pixi.lock as out of date.
MIN_PIXI=0.84.2
pixi_version=$(pixi --version | awk '{print $2}')
if [[ "$(printf '%s\n' "$MIN_PIXI" "$pixi_version" | sort -V | head -n1)" != "$MIN_PIXI" ]]; then
    printf 'Pixi %s or newer is required (found %s). Update Pixi with:\n\n    pixi self-update\n\nthen rerun this installer.\n' "$MIN_PIXI" "$pixi_version" >&2
    exit 1
fi
echo "Pixi $pixi_version is available."
INSTALL_DIR="${INSTALL_DIR:-$HOME/MarineApps}"
# Resolve relative destinations before changing into the cloned repository.
[[ "$INSTALL_DIR" = /* ]] || INSTALL_DIR="$PWD/$INSTALL_DIR"
PROJECT_DIR="$INSTALL_DIR/Marine-Fish-Detection-and-Classification-GUI"
REPO_URL=https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
RESUME=false
if [[ -e "$PROJECT_DIR" || -L "$PROJECT_DIR" ]]; then
    # Every step after cloning is safe to repeat, so an earlier clone of this repository is reused as is.
    if [[ "$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null)" == "$REPO_URL" ]]; then
        RESUME=true
        echo "Existing installation found at $PROJECT_DIR; resuming installation there."
    else
        echo "Destination already exists and is not a copy of this app: $PROJECT_DIR. Choose another INSTALL_DIR." >&2
        exit 1
    fi
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
if [[ "$RESUME" == false ]]; then
    STEP='Cloning Git repository'
    echo "$STEP..."
    mkdir -p "$INSTALL_DIR"
    git clone "$REPO_URL" "$PROJECT_DIR"
    echo 'Repository cloned.'
fi
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
STEP='Installing SAM 2.1 Tiny model for SAM-assisted annotation'
SAM_MODEL="$PROJECT_DIR/models/sam/sam2.1_t.pt"
echo "$STEP (75 MiB) into $SAM_MODEL..."
# scripts/sam/assist.py downloads to models/sam/ in this checkout and skips an existing file.
pixi run --locked setup-sam
if [[ ! -f "$SAM_MODEL" ]]; then
    echo "SAM model not found at $SAM_MODEL. Run 'pixi run setup-sam' from $PROJECT_DIR to retry." >&2
    exit 1
fi
echo 'SAM model installed.'
STEP='Setting up desktop shortcut'
echo "$STEP..."
bash "setup/$SHORTCUT"
echo 'Desktop shortcut done.'
printf 'Installation complete: %s\n' "$PROJECT_DIR"
echo 'You can now open Marine Fish Detection GUI using the desktop shortcut.'
