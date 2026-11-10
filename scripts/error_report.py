"""Keep the app open when something goes wrong in a button or other Qt callback.

PyQt closes the whole app when Python code called from Qt raises an error. install()
replaces that with a message box that has the details, and appends the traceback to
error_log.txt in the application folder, so saved work stays on screen and the error
can be reported. Errors while the message is open are only logged, so a repeating
error cannot stack up message boxes.
"""
from __future__ import annotations

import sys
import time
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from paths import ROOT_DIR

LOG_PATH = ROOT_DIR / "error_log.txt"
_showing = False


def _log(text: str) -> bool:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
        return True
    except OSError:
        return False


def report(exc_type, exc, tb) -> None:
    global _showing
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(text)
    logged = _log(text)
    if _showing or QApplication.instance() is None:
        return
    _showing = True
    try:
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Something went wrong")
        box.setText(f"That action failed: {exc}\n\nThe app is still running and your saved work is safe. "
                    "If it happens again, please report it with the details below.")
        if logged:
            box.setInformativeText(f"Details were saved to {LOG_PATH}")
        box.setDetailedText(text)
        box.exec()
    finally:
        _showing = False


def install() -> None:
    sys.excepthook = report
