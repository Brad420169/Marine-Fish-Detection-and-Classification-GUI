"""
pages/review_page.py
---------------------
Interactive low-confidence review page: steps through every flagged
detection frame one at a time. Each frame shows the raw image with the
flagged detection box(es) drawn on it. Clicking a box (or its matching
field below) lets the user type the correct species name; pressing
Enter with nothing changed confirms the AI's prediction was right.

Frame source:
    - Preferred: a raw frame saved by pipeline.py under
      "<output_dir>/review_frames/frame_NNNNNN.jpg" at run time.
    - Fallback (older runs saved before this existed): seek the
      annotated output video for that frame number. This still works,
      but the model's own baked-in box/label will be visible under the
      review overlay.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import cv2

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCompleter, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
)

from pipeline import FLAGGED_FIELDS

# BGR colours (OpenCV order) for drawing detection boxes; rotated per
# detection index within a frame. The selected/editing box always uses
# the highlight colour instead.
_BOX_PALETTE_BGR = [
    (206, 114, 0),
    (0, 150, 136),
    (46, 125, 46),
    (0, 119, 232),
    (158, 63, 123),
]
_HIGHLIGHT_BGR = (0, 165, 255)


class ClickableImageLabel(QLabel):
    """QLabel that reports click position in its own local coordinates."""

    clicked_at = pyqtSignal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        self.clicked_at.emit(pos.x(), pos.y())
        super().mousePressEvent(event)


class ReviewPage(QWidget):
    """Frame-by-frame reviewer for low-confidence detections."""

    # Fixed height of the detections panel, so its footprint never changes
    # with the number of detections in a frame (see __init__).
    _DETECTION_PANEL_HEIGHT = 120

    def __init__(self, on_back, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._on_back = on_back

        # Run state
        self._flagged_csv: Path | None = None
        self._output_dir: Path | None = None
        self._video_path: Path | None = None
        self._video_cap: cv2.VideoCapture | None = None

        self._rows: list[dict[str, Any]] = []
        self._frame_row_indices: dict[int, list[int]] = {}
        self._frames: list[int] = []
        self._known_species: list[str] = []

        self._current_index = 0
        self._selected_local_index = -1
        self._species_edits: list[QLineEdit] = []

        self._full_pixmap: QPixmap | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)

        content = QWidget()
        self.scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(18, 24, 18, 16)
        root.setSpacing(14)

        title = QLabel("Review Low-Confidence Detections")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet("color: #003B70;")
        root.addWidget(title)

        hint = QLabel(
            "Click a box (or its field below) to correct a wrong species name. "
            "Press Enter or Next if the AI got it right — it just had low confidence."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B7785; font-size: 12px;")
        root.addWidget(hint)

        self.progress_label = QLabel("")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setStyleSheet(
            "color: #52606D; font-size: 12px; font-weight: 600;"
        )
        root.addWidget(self.progress_label)

        # Empty state
        self.empty_label = QLabel(
            "No low-confidence detections were flagged for this run."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            "color: #8A99A6; font-style: italic; padding: 40px;"
        )
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        # Image
        self.image_label = ClickableImageLabel()
        self.image_label.setMinimumHeight(260)
        # Ignored (not Expanding): a QLabel's sizeHint tracks whatever
        # pixmap is currently set on it. With Expanding, the layout would
        # partly size the label off that sizeHint, which itself was just
        # set from the label's own last size — a feedback loop that grows
        # the image a little on every frame change. Ignored tells the
        # layout to never consult the label's sizeHint/minimumSizeHint, so
        # it always just fills the space actually available.
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.image_label.setStyleSheet(
            """
            QLabel {
                background-color: #11161D;
                border: 1px solid #D7E0E8;
                border-radius: 6px;
                color: #8A99A6;
            }
            """
        )
        self.image_label.clicked_at.connect(self._on_image_clicked)
        root.addWidget(self.image_label, 1)

        # Detections list for the current frame.
        #
        # This panel is deliberately a FIXED height: its rows vary with how
        # many detections a frame has, and if it were allowed to grow, a
        # frame with three boxes would leave less room for the image than a
        # frame with one — which is what made some frames render smaller
        # than others. A fixed footprint means the image always gets the
        # same amount of space, so every frame is displayed at one size.
        # Extra rows scroll inside the panel instead of pushing it taller.
        self.detections_group = QGroupBox("Detections in this frame")
        self.detections_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        group_layout = QVBoxLayout(self.detections_group)
        group_layout.setContentsMargins(8, 12, 8, 8)
        group_layout.setSpacing(0)

        self._rows_scroll = QScrollArea()
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._rows_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._rows_scroll.setFixedHeight(self._DETECTION_PANEL_HEIGHT)
        group_layout.addWidget(self._rows_scroll)

        rows_container = QWidget()
        self._rows_layout = QVBoxLayout(rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch()
        self._rows_scroll.setWidget(rows_container)

        root.addWidget(self.detections_group)

        # Fixed nav bar at bottom
        nav_widget = QWidget()
        nav_widget.setStyleSheet(
            "background-color: #F4F7FA; border-top: 1px solid #D7E0E8;"
        )
        nav_outer = QVBoxLayout(nav_widget)
        nav_outer.setContentsMargins(0, 0, 0, 0)
        nav_outer.setSpacing(0)

        top_nav = QHBoxLayout()
        top_nav.setContentsMargins(32, 12, 32, 6)
        top_nav.setSpacing(12)

        back_btn = QPushButton("←  Back to Results")
        back_btn.setObjectName("backButton")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._back_to_results)
        top_nav.addWidget(back_btn)
        top_nav.addStretch()

        self.prev_btn = QPushButton("←  Previous")
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setToolTip("Previous frame (Left arrow)")
        self.prev_btn.clicked.connect(self._go_prev)
        top_nav.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next  →")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setToolTip("Next frame (Right arrow or Enter)")
        self.next_btn.clicked.connect(self._go_next)
        # Set directly rather than via objectName("primaryButton") — that
        # relies on the app-wide stylesheet cascading correctly onto this
        # button, which wasn't happening reliably here.
        self.next_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #0072CE;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-weight: 700;
                padding: 4px 16px;
            }
            QPushButton:hover { background-color: #005FAE; }
            QPushButton:pressed { background-color: #004B87; }
            QPushButton:disabled { background-color: #9DBCD5; color: #EEF3F7; }
            """
        )
        top_nav.addWidget(self.next_btn)

        nav_outer.addLayout(top_nav)

        outer.addWidget(nav_widget)

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def load_run(
        self,
        flagged_csv: Path | None,
        output_dir: Path | None,
        video_path: Path | None,
    ) -> None:
        """Load a run's flagged detections for review."""

        if self._video_cap is not None:
            self._video_cap.release()
            self._video_cap = None

        self._flagged_csv = Path(flagged_csv) if flagged_csv else None
        self._output_dir = Path(output_dir) if output_dir else None
        self._video_path = Path(video_path) if video_path else None

        self._rows = []
        if self._flagged_csv and self._flagged_csv.exists():
            try:
                with self._flagged_csv.open(newline="", encoding="utf-8") as f:
                    for raw_row in csv.DictReader(f):
                        row = dict(raw_row)
                        row.setdefault("frame_image", "")
                        row.setdefault("reviewed", "")
                        try:
                            row["frame_number"] = int(float(row.get("frame_number", 0)))
                        except (TypeError, ValueError):
                            continue
                        self._rows.append(row)
            except OSError:
                pass

        self._frame_row_indices = {}
        for idx, row in enumerate(self._rows):
            self._frame_row_indices.setdefault(row["frame_number"], []).append(idx)
        self._frames = sorted(self._frame_row_indices.keys())

        self._known_species = sorted({
            r["species"] for r in self._rows if r.get("species")
        })

        self._current_index = 0
        self._selected_local_index = -1

        has_frames = bool(self._frames)
        self.empty_label.setVisible(not has_frames)
        self.image_label.setVisible(has_frames)
        self.detections_group.setVisible(has_frames)
        self.prev_btn.setVisible(has_frames)
        self.next_btn.setVisible(has_frames)
        self.progress_label.setVisible(has_frames)

        if has_frames:
            self._load_current_frame()

    # ------------------------------------------------------------
    # Frame navigation
    # ------------------------------------------------------------

    def _current_frame_number(self) -> int | None:
        if 0 <= self._current_index < len(self._frames):
            return self._frames[self._current_index]
        return None

    def _load_current_frame(self) -> None:
        self._selected_local_index = -1
        self._rebuild_detection_rows()
        self._render_current_frame()
        self._update_progress_label()
        self.setFocus()

    def _commit_current_frame(self) -> None:
        frame_number = self._current_frame_number()
        if frame_number is None:
            return

        row_indices = self._frame_row_indices.get(frame_number, [])
        changed = False

        for idx, edit in zip(row_indices, self._species_edits):
            row = self._rows[idx]
            original_species = row.get("species", "")
            new_species = edit.text().strip()

            if new_species and new_species != original_species:
                if not row.get("notes"):
                    row["notes"] = f"AI predicted: {original_species}"
                row["species"] = new_species
                row["reviewed"] = "yes"
                changed = True
            elif row.get("reviewed") != "yes":
                row["reviewed"] = "yes"
                changed = True

        if changed:
            self._write_csv()

    def _write_csv(self) -> None:
        if not self._flagged_csv:
            return
        try:
            with self._flagged_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FLAGGED_FIELDS)
                writer.writeheader()
                writer.writerows(self._rows)
        except OSError:
            pass

    def _go_next(self) -> None:
        self._commit_current_frame()
        if self._current_index < len(self._frames) - 1:
            self._current_index += 1
            self._load_current_frame()
        else:
            self._back_to_results()

    def _go_prev(self) -> None:
        self._commit_current_frame()
        if self._current_index > 0:
            self._current_index -= 1
            self._load_current_frame()

    def _back_to_results(self) -> None:
        self._commit_current_frame()
        if self._video_cap is not None:
            self._video_cap.release()
            self._video_cap = None
        self._on_back()

    def _update_progress_label(self) -> None:
        total = len(self._frames)
        current = self._current_index + 1 if total else 0

        reviewed_frames = sum(
            1
            for fn in self._frames
            if all(
                self._rows[i].get("reviewed") == "yes"
                for i in self._frame_row_indices[fn]
            )
        )

        self.progress_label.setText(
            f"Frame {current} of {total} flagged   •   "
            f"{reviewed_frames} of {total} reviewed"
        )
        self.prev_btn.setEnabled(self._current_index > 0)
        self.next_btn.setText(
            "Finish ✓" if self._current_index >= total - 1 else "Next  →"
        )

    # ------------------------------------------------------------
    # Detection rows panel
    # ------------------------------------------------------------

    def _rebuild_detection_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._species_edits = []

        frame_number = self._current_frame_number()
        if frame_number is None:
            return

        row_indices = self._frame_row_indices.get(frame_number, [])

        for i, idx in enumerate(row_indices):
            row = self._rows[idx]
            color = _BOX_PALETTE_BGR[i % len(_BOX_PALETTE_BGR)]
            hex_color = "#%02X%02X%02X" % (color[2], color[1], color[0])

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(10)

            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {hex_color}; border-radius: 3px;"
            )
            row_layout.addWidget(swatch)

            try:
                confidence = float(row.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0

            info = QLabel(
                f"AI predicted: <b>{row.get('species', '?')}</b>  "
                f"({confidence:.2f} confidence)"
            )
            row_layout.addWidget(info, 1)

            edit = QLineEdit()
            edit.setPlaceholderText("Correct species name…")
            edit.setText(row.get("species", ""))
            edit.setFixedWidth(220)
            if self._known_species:
                completer = QCompleter(self._known_species, edit)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                edit.setCompleter(completer)
            edit.returnPressed.connect(self._go_next)
            edit.installEventFilter(self)
            row_layout.addWidget(edit)

            self._species_edits.append(edit)
            self._rows_layout.addWidget(row_widget)

        # Keep rows top-aligned inside the fixed-height panel.
        self._rows_layout.addStretch()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.FocusIn and obj in self._species_edits:
            idx = self._species_edits.index(obj)
            if idx != self._selected_local_index:
                self._selected_local_index = idx
                self._render_current_frame()
        return super().eventFilter(obj, event)

    def _select_detection(self, local_index: int) -> None:
        self._selected_local_index = local_index
        self._render_current_frame()
        if 0 <= local_index < len(self._species_edits):
            edit = self._species_edits[local_index]
            edit.setFocus()
            edit.selectAll()

    # ------------------------------------------------------------
    # Image rendering
    # ------------------------------------------------------------

    def _load_raw_frame(self, frame_number: int):
        row_indices = self._frame_row_indices.get(frame_number, [])
        frame_image_rel = ""
        if row_indices:
            frame_image_rel = self._rows[row_indices[0]].get("frame_image", "") or ""

        if frame_image_rel and self._output_dir:
            candidate = self._output_dir / frame_image_rel
            if candidate.exists():
                frame = cv2.imread(str(candidate))
                if frame is not None:
                    return frame

        return self._load_fallback_frame(frame_number)

    def _load_fallback_frame(self, frame_number: int):
        if not self._video_path or not self._video_path.exists():
            return None

        if self._video_cap is None:
            cap = cv2.VideoCapture(str(self._video_path))
            if not cap.isOpened():
                return None
            self._video_cap = cap

        self._video_cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number - 1))
        ok, frame = self._video_cap.read()
        return frame if ok else None

    def _render_current_frame(self) -> None:
        frame_number = self._current_frame_number()
        if frame_number is None:
            self._full_pixmap = None
            self.image_label.setText("No frame to display.")
            return

        frame_bgr = self._load_raw_frame(frame_number)
        if frame_bgr is None:
            self._full_pixmap = None
            self.image_label.setText(
                "Could not load this frame's image.\n"
                "The source video may have moved."
            )
            return

        canvas = frame_bgr.copy()
        row_indices = self._frame_row_indices.get(frame_number, [])
        for i, idx in enumerate(row_indices):
            self._draw_box(
                canvas, self._rows[idx], i,
                selected=(i == self._selected_local_index),
            )

        self._full_pixmap = self._to_qpixmap(canvas)
        self._update_image_display()

    @staticmethod
    def _draw_box(canvas, row: dict, index: int, selected: bool) -> None:
        try:
            x1, y1, x2, y2 = (
                int(round(float(row["x1"]))),
                int(round(float(row["y1"]))),
                int(round(float(row["x2"]))),
                int(round(float(row["y2"]))),
            )
        except (KeyError, TypeError, ValueError):
            return

        try:
            confidence = float(row.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0

        species = str(row.get("species", "?"))
        color = _HIGHLIGHT_BGR if selected else _BOX_PALETTE_BGR[index % len(_BOX_PALETTE_BGR)]
        thickness = 4 if selected else 2

        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)

        label = f"{species} {confidence:.2f}"
        font_scale, font_thickness = 0.6, 2
        (tw, th), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness
        )
        label_y1 = max(0, y1 - th - baseline - 6)
        label_x2 = min(canvas.shape[1] - 1, x1 + tw + 10)

        cv2.rectangle(canvas, (x1, label_y1), (label_x2, y1), color, -1)
        cv2.putText(
            canvas, label, (x1 + 5, max(th, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255),
            font_thickness, cv2.LINE_AA,
        )

    @staticmethod
    def _to_qpixmap(frame_bgr) -> QPixmap:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb = frame_rgb.copy()
        h, w, ch = frame_rgb.shape
        qimg = QImage(frame_rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _update_image_display(self) -> None:
        if self._full_pixmap is None:
            return

        label_size = self.image_label.size()
        if label_size.width() <= 2 or label_size.height() <= 2:
            return

        scaled = self._full_pixmap.scaled(
            label_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _on_image_clicked(self, local_x: float, local_y: float) -> None:
        # Derive the mapping from what is actually on screen right now,
        # rather than from values cached at render time, so a click is
        # always mapped against the pixmap the user is really looking at.
        if self._full_pixmap is None:
            return

        displayed = self.image_label.pixmap()
        if displayed is None or displayed.isNull():
            return

        scale = displayed.width() / max(1, self._full_pixmap.width())
        if scale <= 0:
            return

        offset_x = max(0, (self.image_label.width() - displayed.width()) / 2)
        offset_y = max(0, (self.image_label.height() - displayed.height()) / 2)

        img_x = (local_x - offset_x) / scale
        img_y = (local_y - offset_y) / scale

        frame_number = self._current_frame_number()
        if frame_number is None:
            return

        row_indices = self._frame_row_indices.get(frame_number, [])
        for i, idx in enumerate(row_indices):
            row = self._rows[idx]
            try:
                x1, y1, x2, y2 = (
                    float(row["x1"]), float(row["y1"]),
                    float(row["x2"]), float(row["y2"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if x1 <= img_x <= x2 and y1 <= img_y <= y2:
                self._select_detection(i)
                return

    # ------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_image_display()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        focused = QApplication.focusWidget()
        editing = focused in self._species_edits

        if not editing and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._go_next()
            return
        if not editing and key == Qt.Key.Key_Right:
            self._go_next()
            return
        if not editing and key == Qt.Key.Key_Left:
            self._go_prev()
            return

        super().keyPressEvent(event)