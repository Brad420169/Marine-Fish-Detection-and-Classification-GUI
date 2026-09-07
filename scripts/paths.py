"""
paths.py
--------
Shared filesystem paths and the cross-platform "open in OS default
viewer" helper, used by widgets.py and the page modules.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
ICON_PATH = ROOT_DIR / "assets" / "icon.ico"
LOGO_PATH = ROOT_DIR / "assets" / "logo.png"


def open_path(path: Path | None) -> None:
    """Open a file or folder in the OS default viewer."""
    if not path or not Path(path).exists():
        return

    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
