#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
GUI="./scripts/app.py"
PIXI="$HOME/.pixi/bin/pixi" 

if [ ! -f "$GUI" ]; then
    echo "Marine Detector GUI not found:"
    echo "$GUI"
    read -p "Press Enter to close..."
    exit 1
fi

if [ ! -x "$PIXI" ]; then
    echo "pixi not found at $PIXI"
    read -p "Press Enter to close..."
    exit 1
fi

# Only hold the window open on failure. A clean exit needs no terminal, which lets
# the Linux launcher run windowed; macOS still keeps the Terminal up to show errors.
if ! "$PIXI" run python3 "$GUI"; then
    echo "The app exited with an error."
    read -p "Press Enter to close..."
    exit 1
fi
