#!/usr/bin/env bash
# Run after pixi install on Apple Silicon macOS.
set -euo pipefail
if [[ "$(uname -s)" != Darwin ]]; then
    echo 'This script is for macOS.' >&2
    exit 1
fi
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PIXI="$(command -v pixi || true)"
if [[ -z "$PIXI" ]]; then PIXI="$HOME/.pixi/bin/pixi"; fi
if [[ ! -x "$PIXI" ]]; then
    echo 'Install Pixi first, then reopen your terminal.' >&2
    exit 1
fi
if [[ ! -x "$PROJECT_DIR/.pixi/envs/default/bin/python" ]]; then
    echo 'Run pixi install in the repository first (Apple Silicon macOS).' >&2
    exit 1
fi
APP_DIR="$HOME/Desktop/Marine Fish Detection GUI.app"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
{
    printf '#!/bin/bash\nset -e\n'
    printf 'cd -- %q\n' "$PROJECT_DIR"
    printf 'exec %q run --manifest-path %q GUI\n' "$PIXI" "$PROJECT_DIR/pixi.toml"
} > "$APP_DIR/Contents/MacOS/launch"
chmod +x "$APP_DIR/Contents/MacOS/launch"
cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>launch</string>
<key>CFBundleIconFile</key><string>icon.icns</string>
<key>CFBundleIdentifier</key><string>com.brad.marinefishgui</string>
<key>CFBundleName</key><string>Marine Fish Detection GUI</string>
<key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
PLIST
ICON_TEMP="$(mktemp -d)"
trap 'rm -rf -- "$ICON_TEMP"' EXIT
mkdir "$ICON_TEMP/icon.iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$PROJECT_DIR/assets/icon.png" --out "$ICON_TEMP/icon.iconset/icon_${size}x${size}.png" >/dev/null
    sips -z "$((size * 2))" "$((size * 2))" "$PROJECT_DIR/assets/icon.png" --out "$ICON_TEMP/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICON_TEMP/icon.iconset" -o "$APP_DIR/Contents/Resources/icon.icns"
echo "Created $APP_DIR"
