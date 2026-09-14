"""
pages/results_page.py
----------------------
Page displayed after a detection run finishes: run details, output
file shortcuts, Max-N example frames, and the summary charts panel.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from paths import open_path
from charts import build_charts, ScrollPassthroughCanvas, _read_summary_csv


class ResultsPage(QWidget):
    """Page displayed after a detection run finishes."""

    def __init__(
        self,
        on_run_another,
        on_projects,
        on_review=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._on_review = on_review

        self.outputs: dict[str, Path] = {}
        self.output_dir: Path | None = None

        # Outer layout – full page scroll area
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        # Centre content with a max width so it doesn't stretch on fullscreen
        content = QWidget()
        scroll.setWidget(content)

        outer_root = QHBoxLayout(content)
        outer_root.setContentsMargins(0, 0, 0, 0)
        outer_root.setSpacing(0)

        inner = QWidget()
        self._inner = inner

        # Use the full available Results-page width.
        # No side stretches: this prevents the chart panel from being
        # unnecessarily narrowed on laptop screens.
        inner.setMinimumWidth(0)
        inner.setMaximumWidth(16777215)
        inner.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        outer_root.addWidget(inner, 1)

        root = QVBoxLayout(inner)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        title = QLabel("Detection Results")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        title.setStyleSheet("color: #ecf8ff;")
        root.addWidget(title)

        subtitle = QLabel(
            "Summary of detections and visualisations from your run."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignLeft)
        subtitle.setStyleSheet(
            "color: #afd0e3; font-size: 13px;"
        )
        root.addWidget(subtitle)

        # ----------------------------------------------------
        # Run information
        # ----------------------------------------------------

        info_group = QGroupBox("Run Details")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(10)

        self.project_label = QLabel("Project: —")
        self.model_label = QLabel("Model: —")
        self.output_label = QLabel("Output folder: —")

        self.output_label.setWordWrap(True)

        info_layout.addWidget(self.project_label)
        info_layout.addWidget(self.model_label)
        info_layout.addWidget(self.output_label)
        self.run_stats_label = QLabel()
        self.run_stats_label.setObjectName("muted")
        self.run_stats_label.setWordWrap(True)
        info_layout.addWidget(self.run_stats_label)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(14)
        root.addLayout(summary_row)
        summary_row.addWidget(info_group,2)

        # ----------------------------------------------------
        # Result files
        # ----------------------------------------------------

        results_group = QGroupBox("Actions")
        results_layout = QVBoxLayout(results_group)
        results_layout.setSpacing(10)

        self.open_video_btn = QPushButton("▶   Open Annotated Video")
        self.open_video_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.open_video_btn.clicked.connect(
            lambda: self._open_result("video")
        )
        results_layout.addWidget(self.open_video_btn)

        self.open_summary_btn = QPushButton("📄   Open Summary CSV")
        self.open_summary_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.open_summary_btn.clicked.connect(
            lambda: self._open_result("summary_csv")
        )
        results_layout.addWidget(self.open_summary_btn)

        self.open_folder_btn = QPushButton("📁   Open Output Folder")
        self.open_folder_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.open_folder_btn.clicked.connect(
            self._open_output_folder
        )
        results_layout.addWidget(self.open_folder_btn)

        summary_row.addWidget(results_group,1)


        # Review Low-Confidence Frames widget
        self.review_group = QGroupBox("Review Detections")
        review_layout = QVBoxLayout(self.review_group)
        review_layout.setSpacing(10)

        caption = QLabel(
            "Inspect the video, correct species labels and annotate missed fish."
        )
        caption.setWordWrap(True)
        review_layout.addWidget(caption)

        self.review_btn = QPushButton("🔎   Start Review")
        self.review_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.review_btn.clicked.connect(self._open_review)
        review_layout.addWidget(self.review_btn)

        summary_row.addWidget(self.review_group,1)




        # ----------------------------------------------------
        # Max-N example frames
        # ----------------------------------------------------

        self.examples_group = QGroupBox("Example Max-N Frames")
        examples_layout = QHBoxLayout(self.examples_group)
        examples_layout.setContentsMargins(12, 16, 12, 12)
        examples_layout.setSpacing(14)

        self._example_labels: list[QLabel] = []
        self._example_captions: list[QLabel] = []
        self._example_pixmaps: list[QPixmap | None] = [None, None]

        for index in range(2):
            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(6)

            image_label = QLabel("No example frame available")
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setMinimumHeight(220)
            image_label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            image_label.setStyleSheet(
                """
                QLabel {
                    background-color: #082b40;
                    border: 1px solid #1b6584;
                    border-radius: 6px;
                    color: #93b5ca;
                }
                """
            )
            card_layout.addWidget(image_label)

            caption = QLabel("")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            caption.setStyleSheet(
                "color: #b7d8ec; font-size: 11px; font-weight: 600;"
            )
            card_layout.addWidget(caption)

            self._example_labels.append(image_label)
            self._example_captions.append(caption)
            examples_layout.addWidget(card, 1)

        self.examples_group.hide()
        root.addWidget(self.examples_group)

        # ----------------------------------------------------
        # Charts panel (hidden until results arrive)
        # ----------------------------------------------------

        self.charts_group = QGroupBox("Detection Summary Charts")
        charts_outer = QVBoxLayout(self.charts_group)
        charts_outer.setContentsMargins(8, 8, 8, 8)

        self._chart_height = 650
        self._chart_canvas: FigureCanvas | None = None
        self._chart_placeholder = QLabel(
            "Charts will appear here once a detection run completes."
        )
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setStyleSheet("color: #93b5ca; font-style: italic;")
        self._chart_placeholder.setFixedHeight(60)
        charts_outer.addWidget(self._chart_placeholder)

        # Chart viewport:
        # - always matches the available Results-page width
        # - never scrolls horizontally
        # - remains tall; the outer Results-page QScrollArea handles vertical scrolling
        self._chart_scroll = QScrollArea()
        self._chart_scroll.setWidgetResizable(True)
        self._chart_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._chart_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chart_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chart_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._chart_scroll.setMinimumHeight(650)
        self._chart_scroll.setMaximumHeight(650)
        self._chart_scroll.hide()
        charts_outer.addWidget(self._chart_scroll)

        self._charts_layout = charts_outer
        root.insertWidget(3,self.charts_group)

        root.addStretch()

        # ----------------------------------------------------
        # Navigation (fixed at bottom, outside scroll)
        # ----------------------------------------------------

        nav_widget = QWidget()
        nav_widget.setStyleSheet("background-color: #062438; border-top: 1px solid #1b6584;")
        navigation = QHBoxLayout(nav_widget)
        navigation.setContentsMargins(32, 12, 32, 16)
        navigation.setSpacing(12)

        back_btn = QPushButton("←  Back")
        back_btn.setObjectName("backButton")
        back_btn.setAccessibleName("Back")
        back_btn.setToolTip("Return to detection setup")
        back_btn.setFixedWidth(110)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(on_run_another)
        navigation.addWidget(back_btn)
        navigation.addStretch()

        outer.addWidget(nav_widget)

    def _resize_chart_canvas(self) -> None:
        """Fit the chart to the available window width with no horizontal scroll."""
        if self._chart_canvas is None:
            return

        available_width = max(1, self._chart_scroll.viewport().width())
        self._chart_canvas.resize(available_width, self._chart_height-20)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_chart_canvas()
        self._resize_example_frames()

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def set_results(
        self,
        outputs: dict[str, Path],
        project_name: str,
        model_name: str,
        output_dir: Path,
    ) -> None:
        """Update the page with the latest detection results."""

        self.outputs = outputs
        self.output_dir = output_dir

        self.project_label.setText(f"Project: {project_name}")
        self.model_label.setText(f"Model: {model_name}")
        self.output_label.setText(f"Output folder: {output_dir}")
        summary = _read_summary_csv(outputs['summary_csv']) if outputs.get('summary_csv') else []
        total = sum(int(row.get('total_detections',0)) for row in summary)
        self.run_stats_label.setText(f"{len(summary)} species    •    {total:,} detections")

        # Disable buttons if a particular output was not produced.
        self.open_video_btn.setEnabled(
            "video" in outputs and outputs["video"].exists()
        )
        self.open_summary_btn.setEnabled(
            "summary_csv" in outputs and outputs["summary_csv"].exists()
        )
        # self.open_flagged_btn.setEnabled(
        #     "flagged_csv" in outputs and outputs["flagged_csv"].exists()
        # )
        self.open_folder_btn.setEnabled(output_dir.exists())

        # Show the saved top Max-N example frames, if available.
        self._refresh_example_frames(outputs)

        # Build charts from the summary CSV
        csv_path = outputs.get("summary_csv")
        if csv_path and csv_path.exists():
            self._refresh_charts(csv_path)

    # ----------------------------------------------------------
    # Max-N example frames
    # ----------------------------------------------------------

    def _refresh_example_frames(self, outputs: dict[str, Path]) -> None:
        """Load up to two saved Max-N example frames into the Results page."""
        found_any = False

        for index in range(2):
            key = f"maxn_frame_{index + 1}"
            path = outputs.get(key)
            label = self._example_labels[index]
            caption = self._example_captions[index]

            self._example_pixmaps[index] = None
            caption.clear()

            if path and Path(path).exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._example_pixmaps[index] = pixmap
                    found_any = True

                    # Filename format:
                    # 01_Brown_Surgeonfish_maxn4_00-16.jpg
                    stem = Path(path).stem
                    parts = stem.split("_")
                    maxn_part = next(
                        (part for part in parts if part.startswith("maxn")),
                        "",
                    )
                    time_part = parts[-1].replace("-", ":") if parts else ""
                    rank_prefix = f"{index + 1}. "
                    caption.setText(
                        f"{rank_prefix}{maxn_part.upper().replace('MAXN', 'Max-N ')}"
                        f"   •   {time_part}"
                    )
                else:
                    label.setText("Could not load example frame")
            else:
                label.setText("No example frame available")

        self.examples_group.setVisible(found_any)
        self._resize_example_frames()

    def _resize_example_frames(self) -> None:
        """Scale saved example frames to the current Results-page width."""
        if not hasattr(self, "_example_labels"):
            return

        for label, pixmap in zip(self._example_labels, self._example_pixmaps):
            if pixmap is None:
                continue

            target_width = max(260, label.width() - 8)
            target_height = 300
            scaled = pixmap.scaled(
                target_width,
                target_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label.setPixmap(scaled)
            label.setFixedHeight(max(220, scaled.height() + 8))

    # ----------------------------------------------------------
    # Charts
    # ----------------------------------------------------------

    def _refresh_charts(self, csv_path: Path) -> None:
        """Render charts from the summary CSV and embed them in the panel."""

        # Remove whatever was in the charts layout before
        if self._chart_canvas is not None:
            old_canvas = self._chart_scroll.takeWidget()
            if old_canvas is not None:
                old_canvas.deleteLater()
            self._chart_canvas = None

        self._chart_placeholder.hide()

        fig = build_charts(csv_path)
        if fig is None:
            self._chart_scroll.hide()
            self._chart_placeholder.setText("No species data found in CSV.")
            self._chart_placeholder.show()
            return

        self._chart_height = max(650, len(fig.axes[0].get_yticklabels()) * 52 + 240)
        self._chart_scroll.setFixedHeight(self._chart_height)
        canvas = ScrollPassthroughCanvas(fig)

        # Fill the chart viewport horizontally. The chart remains tall so the
        # Results page itself can scroll vertically, but never horizontally.
        canvas.setMinimumWidth(0)
        canvas.setMinimumHeight(self._chart_height-20)
        canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self._chart_scroll.setWidget(canvas)
        self._chart_scroll.show()
        self._chart_canvas = canvas
        self._resize_chart_canvas()

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    def _open_result(self, key: str) -> None:
        path = self.outputs.get(key)
        if path:
            open_path(path)

    def _open_review(self) -> None:
        if self._on_review:
            self._on_review(self.outputs, self.output_dir)

    def _open_output_folder(self) -> None:
        if self.output_dir:
            open_path(self.output_dir)
