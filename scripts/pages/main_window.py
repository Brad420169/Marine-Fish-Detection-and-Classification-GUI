"""
pages/main_window.py
----------------------
Per-project window: detection setup (video/model/settings), the
run/cancel/progress controls, past runs, and hosting the Results
page once a run completes.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget
)

from paths import ICON_PATH
from widgets import VideoPathRow, WeightsRow, LabeledSlider
from charts import _read_summary_csv
from pipeline import RunConfig
from worker import PipelineWorker
from project_manager import Project, RunRecord
from pages.results_page import ResultsPage
from pages.review_page import ReviewPage


class MainWindow(QMainWindow):

    def __init__(self, project: Project) -> None:
        super().__init__()

        self.project = project
        self.setWindowTitle(f"Marine Fish Detector  —  {project.name}")

        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.resize(1100, 800)
        self.setMinimumWidth(700)

        self.worker: PipelineWorker | None = None
        self.outputs: dict[str, Path] = {}
        self._processing_device = "—"

        # Page navigation

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)

        # Detection page

        self.detection_page = QScrollArea()
        self.detection_page.setWidgetResizable(True)
        self.detection_page.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.detection_page.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.detection_page.setFrameShape(
            QScrollArea.Shape.NoFrame
        )

        central = QWidget()
        self.detection_page.setWidget(central)
        self._detection_central = central

        self.pages.addWidget(self.detection_page)

        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        # Header Area
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 4)

        back_btn = QPushButton("←  Projects")
        back_btn.setObjectName("backButton")
        back_btn.setFixedWidth(110)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._back_to_projects)
        header_row.addWidget(back_btn)

        header_center = QVBoxLayout()
        header_center.setSpacing(2)

        title = QLabel("Marine Fish Detector")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #003B70;")
        header_center.addWidget(title)

        project_label = QLabel(f"Project: {project.name}    •    Created: {project.created}")
        project_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        project_label.setStyleSheet("color: #6B7785; font-size: 11px; font-weight: 400;")
        header_center.addWidget(project_label)

        header_row.addLayout(header_center, 1)

        header_spacer = QWidget()
        header_spacer.setFixedWidth(110)
        header_row.addWidget(header_spacer)

        root.addLayout(header_row)

        # Video Input Box
        video_group = QGroupBox("Video Input")
        video_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        video_layout = QVBoxLayout(video_group)
        self.video_row = VideoPathRow()
        video_layout.addWidget(self.video_row)
        root.addWidget(video_group)

        # Model Input Box
        model_group = QGroupBox("Model Input")
        model_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        model_layout = QVBoxLayout(model_group)
        self.weights_row = WeightsRow()
        model_layout.addWidget(self.weights_row)
        root.addWidget(model_group)

        # Settings Group
        settings_group = QGroupBox("Settings")
        settings_group.setMinimumHeight(135)
        settings_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(8)
        settings_layout.setContentsMargins(12, 16, 12, 10)

        self.conf_slider = LabeledSlider(
            "Detection confidence",
            default_val=25,
            tooltip=(
                "Minimum confidence required for a detection to be accepted. "
                "Lower values find more fish but may include more false positives; "
                "higher values are more selective."
            ),
        )
        self.iou_slider = LabeledSlider(
            "Overlap sensitivity",
            default_val=80,
            tooltip=(
                "Controls how overlapping detection boxes are handled (IoU threshold). "
                "Higher values keep more overlapping boxes; lower values suppress "
                "overlapping duplicate detections more aggressively."
            ),
        )
        self.review_conf_slider = LabeledSlider(
            "Flag for review below",
            default_val=50,
            tooltip=(
                "Accepted detections below this confidence are added to the review CSV "
                "so they can be checked manually. This does not change which detections "
                "the model accepts."
            ),
        )

        settings_layout.addWidget(self.conf_slider)
        settings_layout.addWidget(self.iou_slider)
        settings_layout.addWidget(self.review_conf_slider)
        root.addWidget(settings_group)

        # Past Runs.  Keep this widget alive and refresh its contents so a
        # run that has just completed appears immediately when the user returns
        # from the Results page.
        self.runs_group = QGroupBox("Past Runs")
        self.runs_layout = QVBoxLayout(self.runs_group)
        root.addWidget(self.runs_group)
        self._refresh_runs_list()

        progress_label = QLabel("Detection progress")
        progress_label.setStyleSheet(
            "color: #52606D; font-size: 11px; font-weight: 600;"
        )
        root.addWidget(progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Ready")
        root.addWidget(self.progress_bar)

        self.processing_stats = QLabel(
            "Device: —    •    Speed: —    •    Elapsed: 00:00    •    Remaining: —"
        )
        self.processing_stats.setStyleSheet(
            "color: #6B7785; font-size: 11px;"
        )
        self.processing_stats.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        root.addWidget(self.processing_stats)

        # Processing Console Box
        status_label = QLabel("Processing status")
        status_label.setStyleSheet("color: #52606D; font-size: 11px; font-weight: 600;")
        root.addWidget(status_label)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("statusConsole")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(100)
        self.log_box.setFont(QFont("Consolas", 10))
        self.log_box.setPlaceholderText("Waiting for detection to start...")
        self.log_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.log_box, 1)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.run_btn = QPushButton("▶   Run Detection")
        self.run_btn.setObjectName("runButton")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn, 2)

        self.cancel_btn = QPushButton("✕   Cancel")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn, 1)

        root.addLayout(btn_row)

        # Results page

        self.results_page = ResultsPage(
            on_run_another=self._show_detection_page,
            on_projects=self._back_to_projects,
            on_review=self._open_review,
        )
        self.pages.addWidget(self.results_page)

        # Review page (low-confidence detection review)

        self.review_page = ReviewPage(on_back=self._show_results_page)
        self.pages.addWidget(self.review_page)

    # Navigation

    def _refresh_runs_list(self) -> None:
        """Rebuild the Past Runs buttons from the project's current run data."""
        while self.runs_layout.count():
            item = self.runs_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for record in reversed(self.project.runs):
            label = (
                f"Run {record.run_number}  •  "
                f"{record.model_name}  •  {record.timestamp}"
            )
            btn = QPushButton(label)
            btn.setAccessibleName(
                f"Open results for run {record.run_number}, "
                f"model {record.model_name}, completed {record.timestamp}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda checked=False, r=record: self._open_run(r)
            )
            self.runs_layout.addWidget(btn)

        self.runs_group.setVisible(bool(self.project.runs))

    def _show_detection_page(self) -> None:
        """Return from results to the detection setup page."""

        # A run may have been recorded while this page was hidden.
        self._refresh_runs_list()
        self.pages.setCurrentWidget(self.detection_page)

        self.video_row.field.clear()
        self.video_row._set_idle_style()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")

        self.processing_stats.setText(
            "Device: —"
            "    •    Speed: —"
            "    •    Elapsed: 00:00"
            "    •    Remaining: —"
        )

        self.log_box.clear()
        self.outputs = {}

    def _back_to_projects(self) -> None:
        # Deferred import: project_page.py imports MainWindow back to launch
        # a project, so this avoids a circular import at module load time.
        from pages.project_page import ProjectPage

        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Detection running",
                "A detection is still running.\n\nCancel it and return to Projects?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            self.worker.cancel()

        self.project_page = ProjectPage()
        self.project_page.show()
        self.close()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not hasattr(self, '_content_ratio'):
            w = self.width()
            cw = self._detection_central.width()
            if w > 0 and cw > 0:
                self._content_ratio = cw / w

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, '_content_ratio'):
            max_w = int(self.width() * self._content_ratio)
            self._detection_central.setMaximumWidth(max_w)

    def _open_run(self, record: RunRecord) -> None:
        output_dir = Path(record.output_dir)
        summary_csv = output_dir / "track_summary.csv"

        # The annotated video is named from the ORIGINAL input video's stem,
        # not from the run/output directory name. Reconstruct it from the
        # summary CSV when reopening a past run, with a glob fallback for
        # older runs.
        video_path: Path | None = None
        summary_rows = _read_summary_csv(summary_csv)
        if summary_rows:
            source_video = summary_rows[0].get("video", "")
            if source_video:
                candidate = output_dir / f"{Path(source_video).stem}_annotated.mp4"
                if candidate.exists():
                    video_path = candidate

        if video_path is None:
            annotated_videos = sorted(output_dir.glob("*_annotated.mp4"))
            if annotated_videos:
                video_path = annotated_videos[0]

        outputs = {
            "summary_csv": summary_csv,
            "flagged_csv": output_dir / "low_confidence_review.csv",
        }
        if video_path is not None:
            outputs["video"] = video_path

        # Newer runs may also contain saved Max-N example frames.
        examples_dir = output_dir / "maxn_examples"
        if examples_dir.exists():
            example_images = sorted(examples_dir.glob("*.jpg"))[:2]
            for index, image_path in enumerate(example_images, start=1):
                outputs[f"maxn_frame_{index}"] = image_path
        self.results_page.set_results(
            outputs=outputs,
            project_name=self.project.name,
            model_name=record.model_name,
            output_dir=output_dir,
        )
        self.pages.setCurrentWidget(self.results_page)

    def _open_review(self, outputs: dict, output_dir: Path) -> None:
        """Launch the low-confidence review page for the current results."""
        self.review_page.load_run(
            flagged_csv=outputs.get("flagged_csv"),
            output_dir=output_dir,
            video_path=outputs.get("video"),
        )
        self.pages.setCurrentWidget(self.review_page)

    def _show_results_page(self) -> None:
        """Return from the review page to Results."""
        self.pages.setCurrentWidget(self.results_page)

    # Run Detection

    def _run(self) -> None:
        video = self.video_row.get_path()
        weights = self.weights_row.get_path()
        missing = [
            name for name, value in [
                ("Video", video),
                ("Model weights", weights),
                ("Project output folder", self.project.output_root),
            ]
            if not value
        ]

        if missing:
            QMessageBox.warning(
                self,
                "Missing inputs",
                "Please provide:\n\n" + "\n".join(f"• {item}" for item in missing),
            )
            return

        model_name = Path(weights).stem
        resolved_output = self.project.resolve_output_dir(
            Path(self.project.output_root), model_name
        )

        self.log_box.clear()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")

        self._processing_device = "—"
        self.processing_stats.setText(
            "Device: —"
            "    •    Speed: —"
            "    •    Elapsed: 00:00"
            "    •    Remaining: —"
        )

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        config = RunConfig(
            video_path=Path(video),
            weights_path=Path(weights),
            output_dir=resolved_output,
            confidence=self.conf_slider.value(),
            iou=self.iou_slider.value(),
            review_confidence=self.review_conf_slider.value(),
        )

        self.worker = PipelineWorker(config)

        self.worker.progress.connect(self._on_progress)
        self.worker.device.connect(self._on_device)
        self.worker.log.connect(self._on_log)
        self.worker.completed.connect(self._on_finished)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.error.connect(self._on_error)

        self.worker.start()

        self._pending_model_name = model_name
        self._pending_output_dir = resolved_output

    @staticmethod
    def _format_processing_time(seconds: float) -> str:
        seconds = max(0, int(seconds))

        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)

        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        return f"{minutes:02d}:{secs:02d}"

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()

        self.cancel_btn.setEnabled(False)
        self._on_log("Cancelling detection...")

    # Worker Signals

    def _on_progress(
        self,
        current: int,
        total: int,
        speed: float,
        elapsed: float,
        remaining: float,
    ) -> None:
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

        pct = int(current / total * 100) if total else 0

        self.progress_bar.setFormat(
            f"{current}/{total} frames   •   {pct}%"
        )

        elapsed_text = self._format_processing_time(elapsed)
        remaining_text = self._format_processing_time(remaining)

        self.processing_stats.setText(
            f"Device: {self._processing_device}"
            f"    •    Speed: {speed:.2f} FPS"
            f"    •    Elapsed: {elapsed_text}"
            f"    •    Remaining: ~{remaining_text}"
        )

    def _on_device(self, device: str) -> None:
        self._processing_device = device

        self.processing_stats.setText(
            f"Device: {device}"
            "    •    Speed: —"
            "    •    Elapsed: 00:00"
            "    •    Remaining: —"
        )

    def _on_log(self, msg: str) -> None:
        self.log_box.appendPlainText(msg)
        scrollbar = self.log_box.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_finished(self, outputs: dict) -> None:
        # Convert returned output paths into Path objects.
        self.outputs = {
            key: Path(value)
            for key, value in outputs.items()
        }

        # Save the completed run to the project.
        self.project.record_run(
            model_name=self._pending_model_name,
            output_dir=self._pending_output_dir,
        )
        # Keep the hidden Detection page in sync immediately; it will already
        # contain the new run when the user navigates back from Results.
        self._refresh_runs_list()

        # Finish the detection UI state.
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        self.progress_bar.setValue(
            self.progress_bar.maximum()
        )

        self.progress_bar.setFormat(
            "Detection complete   •   100%"
        )

        self.worker = None

        # Send the run information to the Results page.
        self.results_page.set_results(
            outputs=self.outputs,
            project_name=self.project.name,
            model_name=self._pending_model_name,
            output_dir=self._pending_output_dir,
        )

        # Switch from Detection page -> Results page.
        self.pages.setCurrentWidget(
            self.results_page
        )

    def _on_cancelled(self) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setFormat("Detection cancelled")
        self.worker = None

    def _on_error(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setFormat("Error")
        self._on_log(f"ERROR: {msg}")

        QMessageBox.critical(self, "Detection Error", msg)
        self.worker = None
