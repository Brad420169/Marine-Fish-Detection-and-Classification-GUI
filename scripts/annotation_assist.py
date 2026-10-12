"""Suggest boxes for one frame; the editor owns acceptance and saving."""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal


class SuggestionWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, frame, weights, parent=None):
        super().__init__(parent)
        self.frame = frame.copy()
        self.weights = weights

    def run(self):
        try:
            from detectors import load_detector
            detector = load_detector(Path(self.weights), device='cpu')
            self.succeeded.emit(detector.infer(self.frame, conf=0.25, iou=0.8))
        except Exception as exc:
            self.failed.emit(str(exc))
