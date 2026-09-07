"""
widgets.py
----------
Reusable UI widgets shared across pages: path pickers, the video
drag-and-drop row, the model weights selector, the labeled slider
used for detection settings, and the project list row.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget
)

from paths import ROOT_DIR
from project_manager import Project


class PathRow(QWidget):
    """Generic path selection row with inline Browse button."""

    def __init__(
        self,
        label: str,
        placeholder: str,
        mode: str = "file",  # 'file' or 'folder'
        file_filter: str = "",
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        lbl = QLabel(label)
        lbl.setFixedWidth(100)
        layout.addWidget(lbl)

        self.field = QLineEdit()
        self.field.setPlaceholderText(placeholder)
        self.field.setReadOnly(True)
        layout.addWidget(self.field, 1)

        self.btn = QPushButton("Browse…")
        self.btn.setFixedWidth(95)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._browse)
        layout.addWidget(self.btn)

    def set_path(self, path: str) -> None:
        self.field.setText(path)

    def get_path(self) -> str:
        return self.field.text().strip()

    def _browse(self) -> None:
        if self.mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "Select Folder")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select File", "", self.file_filter)

        if path:
            self.set_path(path)


class VideoPathRow(PathRow):
    """PathRow specialized for video files with Drag & Drop support."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            label="Video",
            placeholder="Select a .mp4 video file…",
            mode="file",
            file_filter="Video Files (*.mp4 *.avi *.mov *.mkv)",
            parent=parent,
        )
        self.setAcceptDrops(True)

        # Restructure layout to accommodate drag-and-drop box below input row
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Reparent original row layout into a sub-widget
        row_widget = QWidget()
        row_widget.setLayout(self.layout())
        main_layout.addWidget(row_widget)

        # Drag and drop area
        self.drop_label = QLabel()
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setFixedHeight(75)
        self.drop_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._set_idle_style()

        main_layout.addWidget(self.drop_label)
        self.setLayout(main_layout)

    def set_path(self, path: str) -> None:
        super().set_path(path)
        self.drop_label.setText(f"✓   {Path(path).name}")
        self.drop_label.setStyleSheet(
            """
            QLabel {
                border: 2px solid #2E7D32;
                border-radius: 8px;
                background-color: #F1FAF2;
                color: #2E7D32;
                font-size: 13px;
                font-weight: 600;
            }
            """
        )

    def _set_idle_style(self) -> None:
        self.drop_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #AEBCC8;
                border-radius: 8px;
                background-color: #F8FBFD;
                color: #647585;
                font-size: 13px;
                font-weight: 500;
            }
            QLabel:hover {
                border: 2px dashed #0072CE;
                background-color: #EFF8FE;
                color: #005EA8;
            }
            """
        )
        self.drop_label.setText("🎬   Drop video file here")

    def _set_hover_style(self) -> None:
        self.drop_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #0072CE;
                border-radius: 8px;
                background-color: #E4F3FD;
                color: #005EA8;
                font-size: 13px;
                font-weight: 600;
            }
            """
        )
        self.drop_label.setText("↓   Release to load video")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                self._set_hover_style()
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_idle_style()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.set_path(urls[0].toLocalFile())
            event.acceptProposedAction()


class WeightsRow(QWidget):
    """Model selector and manager row."""

    DEFAULT_MODEL = "kona_hawaiiXL.pt"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.models_dir = ROOT_DIR / "models"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Model selector
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        lbl = QLabel("Weights")
        lbl.setFixedWidth(100)
        row1.addWidget(lbl)

        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Disable mouse wheel changing options
        self.combo.wheelEvent = lambda event: event.ignore()
        row1.addWidget(self.combo, 1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedWidth(36)
        self.refresh_btn.setToolTip("Refresh model list")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        row1.addWidget(self.refresh_btn)

        layout.addLayout(row1)

        # Add model button
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        spacer = QWidget()
        spacer.setFixedWidth(100)
        row2.addWidget(spacer)

        self.upload_btn = QPushButton("＋  Add model weights…")
        self.upload_btn.setToolTip("Copy a .pt (YOLO) or .pth (RF-DETR) file into the models folder")
        self.upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upload_btn.clicked.connect(self._upload_weights)
        row2.addWidget(self.upload_btn, 1)

        layout.addLayout(row2)
        self.refresh()

    def refresh(self) -> None:
        current = self.combo.currentText()
        self.combo.clear()

        self.models_dir.mkdir(parents=True, exist_ok=True)

        models = sorted(
            [*self.models_dir.glob("*.pt"), *self.models_dir.glob("*.pth")]
        )
        if not models:
            self.combo.addItem("No .pt/.pth files found in models/")
            self.combo.setEnabled(False)
            return

        self.combo.setEnabled(True)
        for model in models:
            self.combo.addItem(model.name, userData=model)

        if current and self.combo.findText(current) >= 0:
            self.combo.setCurrentText(current)
        elif self.combo.findText(self.DEFAULT_MODEL) >= 0:
            self.combo.setCurrentText(self.DEFAULT_MODEL)

    def _upload_weights(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Model Weights",
            str(self.models_dir),
            "Model Weights (*.pt *.pth);;YOLO Weights (*.pt);;RF-DETR Weights (*.pth)",
        )
        if not path:
            return

        src = Path(path)
        dest = self.models_dir / src.name

        if dest.exists():
            answer = QMessageBox.question(
                self,
                "File already exists",
                f"{src.name} already exists in the models folder.\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            shutil.copy2(src, dest)
        except Exception as e:
            QMessageBox.critical(self, "Copy failed", str(e))
            return

        self.refresh()
        self.combo.setCurrentText(src.name)

    def get_path(self) -> str:
        path = self.combo.currentData()
        return str(path) if path else ""


class LabeledSlider(QWidget):
    """Slider with a directly editable numeric value and explanatory tooltip."""

    def __init__(
        self,
        label_text: str,
        default_val: int = 50,
        min_val: int = 1,
        max_val: int = 99,
        tooltip: str = "",
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.label = QLabel(label_text)
        self.label.setFixedWidth(160)
        if tooltip:
            self.label.setToolTip(tooltip)
        layout.addWidget(self.label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(default_val)
        self.slider.setTickInterval(10)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        if tooltip:
            self.slider.setToolTip(tooltip)
        layout.addWidget(self.slider, 1)

        # Editable boxed value. The slider uses integer hundredths internally,
        # while the user sees and edits a decimal value such as 0.53.
        self.value_box = QDoubleSpinBox()
        self.value_box.setDecimals(2)
        self.value_box.setRange(min_val / 100, max_val / 100)
        self.value_box.setSingleStep(0.01)
        self.value_box.setValue(default_val / 100)
        self.value_box.setFixedWidth(76)
        self.value_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_box.setKeyboardTracking(False)
        if tooltip:
            self.value_box.setToolTip(tooltip)
        layout.addWidget(self.value_box)

        self.slider.valueChanged.connect(self._slider_changed)
        self.value_box.valueChanged.connect(self._box_changed)

    def _slider_changed(self, val: int) -> None:
        """Keep the editable value box in sync with slider movement."""
        new_value = val / 100
        if abs(self.value_box.value() - new_value) > 0.0001:
            self.value_box.blockSignals(True)
            self.value_box.setValue(new_value)
            self.value_box.blockSignals(False)

    def _box_changed(self, val: float) -> None:
        """Move the slider when the user types or clicks a numeric value."""
        slider_value = int(round(val * 100))
        if self.slider.value() != slider_value:
            self.slider.blockSignals(True)
            self.slider.setValue(slider_value)
            self.slider.blockSignals(False)

    def value(self) -> float:
        return self.value_box.value()


class ProjectListRow(QWidget):
    """Project list row with a delete button shown only when selected."""

    def __init__(
        self,
        project: Project,
        label: str,
        on_delete,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(8)

        self.label = QLabel(label)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.label, 1)

        self.delete_btn = QPushButton("🗑")
        self.delete_btn.setToolTip(f"Delete {project.name}")
        self.delete_btn.setFixedSize(30, 26)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.setVisible(False)
        self.delete_btn.setStyleSheet(
            """
            QPushButton {
                border: none;
                background: transparent;
                font-size: 15px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #FDECEC;
                border-radius: 4px;
            }
            """
        )
        self.delete_btn.clicked.connect(lambda: on_delete(project))
        layout.addWidget(self.delete_btn)

    def set_selected(self, selected: bool) -> None:
        self.delete_btn.setVisible(selected)
