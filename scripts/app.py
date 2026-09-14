"""
app.py
------
PyQt6 GUI for marine fish detection and tracking.

Run with:
    pixi run GUI
    (from within the project root directory (auto_fish_detect_gui/))
"""
from __future__ import annotations

print("Loading...", flush=True)

import sys

from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import QApplication

from paths import ICON_PATH
from style import APP_STYLE
from pages.main_window import MainWindow

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QUT.MarineFishDetector")


def main() -> None:
    app = QApplication(sys.argv)
    # Matches marine-fish-gui.desktop, so Linux desktops tie the window to the
    # launcher instead of showing a second, generic icon beside the pinned one.
    # Ignored on Windows and macOS, which identify the app by the lines above.
    app.setDesktopFileName("marine-fish-gui")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_STYLE)

    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    else:
        print(f"Icon not found: {ICON_PATH}")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
