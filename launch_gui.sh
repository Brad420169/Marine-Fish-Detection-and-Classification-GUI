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

"$PIXI" run python3 "$GUI"
read -p "Press Enter to close..."
