#!/usr/bin/env bash
# Run after pixi install. Safe to rerun after moving the repository.
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
    echo 'This script is for Linux.' >&2
    exit 1
fi
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
PIXI="$(command -v pixi || true)"
if [[ -z "$PIXI" ]]; then PIXI="$HOME/.pixi/bin/pixi"; fi
if [[ ! -x "$PIXI" ]]; then
    echo 'Install Pixi first, then reopen your terminal.' >&2
    exit 1
fi
if [[ ! -x "$PROJECT_DIR/.pixi/envs/default/bin/python" ]]; then
    echo 'Run pixi install in the repository first.' >&2
    exit 1
fi
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
DESKTOP_DIR="${DESKTOP_DIR:-$HOME/Desktop}"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
# Python handles desktop-entry quoting without passing paths through a shell.
"$PROJECT_DIR/.pixi/envs/default/bin/python" - "$PROJECT_DIR" "$PIXI" "$APPLICATIONS_DIR" "$DESKTOP_DIR" <<'PY'
from pathlib import Path
import sys

project, pixi, applications, desktop = map(Path, sys.argv[1:])

def value(text):
    return str(text).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

def argument(text):
    # Exec has its own quoting layer, on top of desktop-entry string escaping.
    text = str(text).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        text = text.replace(char, '\\' + char)
    return value('"' + text + '"')

entry = '\n'.join([
    '[Desktop Entry]', 'Type=Application', 'Name=Marine Fish Detection GUI',
    'Comment=Marine fish detection and classification',
    f'Exec={argument(pixi)} run --manifest-path {argument(project / "pixi.toml")} GUI',
    f'Path={value(project)}', f'Icon={value(project / "assets/icon.png")}',
    'Terminal=false', 'StartupWMClass=marine-fish-gui', 'Categories=Science;', '',
])
for directory in (applications, desktop):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'marine-fish-gui.desktop'
    target.write_text(entry, encoding='utf-8')
    target.chmod(0o755)
    print(f'Created {target}')
PY
if command -v gio >/dev/null 2>&1; then
    gio set "$DESKTOP_DIR/marine-fish-gui.desktop" metadata::trusted true 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" 2>/dev/null || true
fi
echo 'Done. If prompted, right-click the desktop icon and choose Allow Launching.'
