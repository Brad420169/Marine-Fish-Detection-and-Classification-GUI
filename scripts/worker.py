"""
worker.py
---------
QThread that runs the detection pipeline off the main UI thread so the
window stays responsive during inference.
"""
from __future__ import annotations

import time

import torch
from PyQt6.QtCore import QThread, pyqtSignal

from pipeline import RunConfig, run_pipeline


class PipelineWorker(QThread):
    """Runs run_pipeline() in a background thread."""

    progress = pyqtSignal(int, int, float, float, float)
    device = pyqtSignal(str)
    log = pyqtSignal(str)
    completed = pyqtSignal(dict)
    cancelled = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, config: RunConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._cancelled = False
        self._start_time = 0.0

    def cancel(self) -> None:
        self._cancelled = True

    def _emit_progress(self, current: int, total: int) -> None:
        elapsed = time.perf_counter() - self._start_time

        speed = current / elapsed if elapsed > 0 else 0.0

        if speed > 0 and total > current:
            remaining = (total - current) / speed
        else:
            remaining = 0.0

        self.progress.emit(
            current,
            total,
            speed,
            elapsed,
            remaining,
        )

    def run(self) -> None:
        try:
            if torch.cuda.is_available():
                device_name = torch.cuda.get_device_name(0)
                self.device.emit(f"CUDA — {device_name}")
            else:
                self.device.emit("CPU")

            self._start_time = time.perf_counter()

            outputs = run_pipeline(
                config=self.config,
                on_progress=self._emit_progress,
                on_log=lambda msg: self.log.emit(msg),
                is_cancelled=lambda: self._cancelled,
            )

            if self._cancelled:
                self.cancelled.emit()
            else:
                self.completed.emit(outputs)

        except Exception as exc:
            self.error.emit(str(exc))