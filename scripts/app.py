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

import csv
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QDoubleSpinBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget
)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from pipeline import RunConfig
from worker import PipelineWorker
from project_manager import *
from style import APP_STYLE


# Paths & System Config

ROOT_DIR = Path(__file__).parent.parent
ICON_PATH = ROOT_DIR / "assets" / "icon.ico"
LOGO_PATH = ROOT_DIR / "assets" / "logo.png"

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QUT.MarineFishDetector")


# Helper Functions

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


# Reusable UI Widgets

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


# Project Selection Page

class ProjectPage(QMainWindow):

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Marine Fish Detector")
        self.setMinimumWidth(520)
        self.setFixedHeight(620)

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setSpacing(16)
        root.setContentsMargins(40, 28, 40, 30)

        # Logo
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setFixedHeight(140)
        self._load_logo()
        root.addWidget(self.logo_label)

        # Title
        title = QLabel("Marine Fish Detector")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #003B70;")
        root.addWidget(title)

        subtitle = QLabel("Automated marine species detection and analysis")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #718096; font-size: 11px;")
        root.addWidget(subtitle)

        # Create Project Group
        create_group = QGroupBox("Create new project")
        create_layout = QVBoxLayout(create_group)
        create_layout.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)

        self.new_project_field = QLineEdit()
        self.new_project_field.setPlaceholderText("Enter project name…")
        self.new_project_field.returnPressed.connect(self._create_project)
        name_row.addWidget(self.new_project_field, 1)

        create_btn = QPushButton("Create")
        create_btn.setObjectName("primaryButton")
        create_btn.setFixedWidth(90)
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn.clicked.connect(self._create_project)
        name_row.addWidget(create_btn)
        create_layout.addLayout(name_row)

        self.new_project_output_row = PathRow(
            "Outputs", "Select output folder for this project…", mode="folder"
        )
        create_layout.addWidget(self.new_project_output_row)

        root.addWidget(create_group)

        # Open Existing Project Group
        select_group = QGroupBox("Open existing project")
        select_layout = QVBoxLayout(select_group)
        select_layout.setSpacing(8)

        self.project_list = QListWidget()
        self.project_list.setAlternatingRowColors(False)
        self.project_list.itemDoubleClicked.connect(self._open_selected)
        self.project_list.itemSelectionChanged.connect(self._update_project_row_controls)
        select_layout.addWidget(self.project_list)

        open_btn = QPushButton("Open selected project")
        open_btn.setObjectName("primaryButton")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._open_selected)
        select_layout.addWidget(open_btn)

        root.addWidget(select_group, 1)

        self._refresh_project_list()

    def _load_logo(self) -> None:
        if LOGO_PATH.exists():
            pixmap = QPixmap(str(LOGO_PATH)).scaledToHeight(
                136, Qt.TransformationMode.SmoothTransformation
            )
            self.logo_label.setPixmap(pixmap)
        else:
            self.logo_label.setText("[ Logo — place image at assets/logo.png ]")
            self.logo_label.setStyleSheet(
                """
                border: 2px dashed #AEBCC8;
                border-radius: 8px;
                color: #7A8793;
                """
            )

    def _refresh_project_list(self) -> None:
        self.project_list.clear()

        for project in list_projects():
            runs = len(project.runs)
            label = (
                f"{project.name}    •    Created {project.created}    •    "
                f"{runs} {'run' if runs == 1 else 'runs'}"
            )

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, project)
            item.setSizeHint(item.sizeHint().expandedTo(QSize(0, 34)))
            self.project_list.addItem(item)

            row = ProjectListRow(
                project=project,
                label=label,
                on_delete=self._delete_project,
            )
            self.project_list.setItemWidget(item, row)

    def _update_project_row_controls(self) -> None:
        selected_item = self.project_list.currentItem()

        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            row = self.project_list.itemWidget(item)
            if isinstance(row, ProjectListRow):
                row.set_selected(item is selected_item)

    def _create_project(self) -> None:
        name = self.new_project_field.text().strip()
        output_root = self.new_project_output_row.get_path()

        if not name:
            QMessageBox.warning(self, "No project name", "Please enter a project name.")
            return
        if not output_root:
            QMessageBox.warning(
                self, "No output folder", "Please select an output folder for this project."
            )
            return

        try:
            project = create_project(name, output_root)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot create project", str(e))
            return

        self.new_project_field.clear()
        self.new_project_output_row.set_path("")
        self._refresh_project_list()
        self._launch_main(project)

    def _open_selected(self) -> None:
        item = self.project_list.currentItem()

        if not item:
            QMessageBox.warning(self, "No selection", "Please select or create a project first.")
            return

        project: Project = item.data(Qt.ItemDataRole.UserRole)

        # Backward compatibility for projects created before output_root was stored.
        if not project.output_root:
            output_root = QFileDialog.getExistingDirectory(
                self,
                f"Select output folder for {project.name}",
            )
            if not output_root:
                return
            project.output_root = output_root
            project.save()

        self._launch_main(project)

    def _delete_project(self, project: Project) -> None:
        answer = QMessageBox.warning(
            self,
            "Delete project",
            (
                f"Are you sure you want to delete '{project.name}'?\n\n"
                "Deleted projects are not recoverable.\n\n"
                "Detection output files will not be deleted."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_project(project)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Delete failed",
                str(e),
            )
            return

        self._refresh_project_list()

    def _launch_main(self, project: Project) -> None:
        self.main_window = MainWindow(project)
        self.main_window.show()
        self.close()

# Chart helpers

# Colour palette that matches the app's blue/teal theme
_PALETTE = [
    "#0072CE", "#00A3A1", "#2E7D32", "#E87722", "#7B3F9E",
    "#C62828", "#1565C0", "#00695C", "#6A1B9A", "#AD1457",
    "#1976D2", "#00897B", "#558B2F", "#EF6C00", "#4527A0",
]


def _theme_axes(ax: plt.Axes) -> None:
    """Apply consistent light-theme styling to a matplotlib Axes."""
    ax.set_facecolor("#FAFBFC")
    ax.tick_params(colors="#52606D", labelsize=9)
    ax.xaxis.label.set_color("#52606D")
    ax.yaxis.label.set_color("#52606D")
    ax.title.set_color("#003B70")
    for spine in ax.spines.values():
        spine.set_edgecolor("#D7E0E8")


def _read_summary_csv(csv_path: Path) -> list[dict]:
    """Return rows from track_summary.csv as a list of dicts."""
    rows = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        pass
    return rows


def _timestamp_to_seconds(timestamp: str) -> float:
    """Convert MM:SS or HH:MM:SS timestamp text to seconds."""
    try:
        parts = [float(p) for p in str(timestamp).strip().split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return hours * 3600 + minutes * 60 + seconds
    except (TypeError, ValueError):
        pass
    return 0.0


def build_charts(csv_path: Path) -> Figure | None:
    """
    Build a 4x1 vertical figure of four charts from the summary CSV.
    """
    rows = _read_summary_csv(csv_path)
    if not rows:
        return None

    species      = [r["species"] for r in rows]
    max_n        = [int(r["max_n"]) for r in rows]
    max_n_time   = [r.get("max_n_timestamp", "") for r in rows]
    obs_span     = [float(r["observation_span_seconds"]) for r in rows]
    total_dets   = [int(r["total_detections"]) for r in rows]
    mean_conf    = [float(r["mean_confidence"]) for r in rows]
    vis_span     = [float(r["visible_seconds"]) for r in rows]
    first_seen   = [r.get("first_seen", "") for r in rows]
    last_seen    = [r.get("last_seen", "") for r in rows]
    video_length = max(
        [float(r.get("video_duration_seconds", 0) or 0) for r in rows] or [0]
    )

    n = len(species)
    colours = [_PALETTE[i % len(_PALETTE)] for i in range(n)]

    fig = Figure(figsize=(15, 20), facecolor="#F4F7FA")
    fig.subplots_adjust(
        hspace=0.58,
        left=0.20,
        right=0.98,
        top=0.97,
        bottom=0.055,
    )

    # ── 1. Max-N ────────────────────────────────────────────
    ax1 = fig.add_subplot(4, 1, 1)
    _theme_axes(ax1)
    bars1 = ax1.barh(species, max_n, color=colours, edgecolor="white", height=0.6)
    ax1.set_xlabel("Max fish in one frame")
    ax1.set_title("Peak Abundance (Max-N)", fontsize=11, fontweight="bold", pad=8)
    ax1.invert_yaxis()
    ax1.set_xlim(0, max(max_n) * 1.45 if max_n else 1)
    for bar, val, timestamp in zip(bars1, max_n, max_n_time):
        label = f"{val}  @ {timestamp}" if timestamp else str(val)
        ax1.text(
            bar.get_width() + max(max_n) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            fontsize=8,
            color="#52606D",
        )

    # Explain the inline "@ timestamp" annotation without covering the bars.
    timestamp_legend = mpatches.Patch(
        facecolor="none",
        edgecolor="none",
        label="@ time = video timestamp when Max-N was recorded",
    )
    ax1.legend(
        handles=[timestamp_legend],
        loc="lower right",
        fontsize=7,
        frameon=False,
        handlelength=0,
        handletextpad=0,
        borderaxespad=0.6,
    )

    # ── 2. Observation span ──────────────────────────────────
    # ax2 = fig.add_subplot(2, 2, 2)
    # _theme_axes(ax2)
    # bars2 = ax2.barh(species, obs_span, color=colours, edgecolor="white", height=0.6)
    # ax2.set_xlabel("Seconds")
    # ax2.set_title("Observation Span per Species", fontsize=11, fontweight="bold", pad=8)
    # ax2.invert_yaxis()
    # ax2.set_xlim(0, max(obs_span) * 1.18 if obs_span else 1)
    # for bar, val in zip(bars2, obs_span):
    #     ax2.text(bar.get_width() + max(obs_span) * 0.02, bar.get_y() + bar.get_height() / 2,
    #              f"{val:.1f}s", va="center", fontsize=8, color="#52606D")
    
    # ── 3. Total detections ──────────────────────────────────
    ax3 = fig.add_subplot(4, 1, 2)
    _theme_axes(ax3)
    bars3 = ax3.barh(species, total_dets, color=colours, edgecolor="white", height=0.6)
    ax3.set_xlabel("Detection count")
    ax3.set_title("Total Detections by Species", fontsize=11, fontweight="bold", pad=8)
    ax3.invert_yaxis()
    ax3.set_xlim(0, max(total_dets) * 1.18 if total_dets else 1)
    for bar, val in zip(bars3, total_dets):
        ax3.text(bar.get_width() + max(total_dets) * 0.02, bar.get_y() + bar.get_height() / 2,
                 str(val), va="center", fontsize=8, color="#52606D")
        
    # Visual chart
    ax2 = fig.add_subplot(4, 1, 3)
    _theme_axes(ax2)
    bars2 = ax2.barh(species, vis_span, color=colours, edgecolor="white", height=0.6)
    ax2.set_xlabel("Seconds")
    ax2.set_title("Visible Span per Species", fontsize=11, fontweight="bold", pad=8)
    ax2.invert_yaxis()
    ax2.set_xlim(0, max(vis_span) * 1.18 if vis_span else 1)
    for bar, val in zip(bars2, vis_span):
        ax2.text(bar.get_width() + max(vis_span) * 0.02, bar.get_y() + bar.get_height() / 2,
                 f"{val:.1f}s", va="center", fontsize=8, color="#52606D")



    # ── 4. Mean detection confidence ────────────────────────
    ax4 = fig.add_subplot(4, 1, 4)
    _theme_axes(ax4)

    conf_colours = [
        "#2E7D32" if c >= 0.70 else "#E87722" if c >= 0.50 else "#C62828"
        for c in mean_conf
    ]

    y_pos = np.arange(len(species))

    # Light stems make this a lollipop/dot chart rather than another bar chart.
    for y, val, colour in zip(y_pos, mean_conf, conf_colours):
        ax4.hlines(y, 0, val, color=colour, linewidth=1.2, alpha=0.45)

    ax4.scatter(
        mean_conf,
        y_pos,
        s=55,
        c=conf_colours,
        edgecolors="white",
        linewidths=0.7,
        zorder=3,
    )

    ax4.set_yticks(y_pos)
    ax4.set_yticklabels(species)
    ax4.set_xlabel("Mean confidence score")
    ax4.set_title("Mean Detection Confidence", fontsize=11, fontweight="bold", pad=30)
    ax4.invert_yaxis()
    ax4.set_xlim(0, 1.08)

    ax4.axvline(0.5, color="#C62828", linewidth=0.8, linestyle="--", alpha=0.6)
    ax4.axvline(0.7, color="#2E7D32", linewidth=0.8, linestyle="--", alpha=0.6)

    for y, val in zip(y_pos, mean_conf):
        ax4.text(
            val + 0.015,
            y,
            f"{val:.2f}",
            va="center",
            fontsize=8,
            color="#52606D",
        )

    legend_patches = [
        mpatches.Patch(color="#2E7D32", label="High  (≥ 0.70)"),
        mpatches.Patch(color="#E87722", label="Medium (0.50-0.70)"),
        mpatches.Patch(color="#C62828", label="Low  (< 0.50)"),
    ]

    ax4.legend(
        handles=legend_patches,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=3,
        fontsize=7,
        frameon=False,
        borderaxespad=0.0,
        columnspacing=1.4,
        handletextpad=0.5,
    )

    return fig


# Scroll-passthrough canvas

class ScrollPassthroughCanvas(FigureCanvas):
    """FigureCanvas that forwards wheel events to the nearest QScrollArea
    so the page still scrolls when the mouse is over the chart."""

    def wheelEvent(self, event) -> None:
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.wheelEvent(event)
                return
            parent = parent.parent()
        super().wheelEvent(event)


# Results Page

class ResultsPage(QWidget):
    """Page displayed after a detection run finishes."""

    def __init__(
        self,
        on_run_another,
        on_projects,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

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
        root.setContentsMargins(18, 32, 18, 32)
        root.setSpacing(20)

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        title = QLabel("Detection Complete")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        title.setStyleSheet("color: #003B70;")
        root.addWidget(title)

        subtitle = QLabel(
            "Your video has finished processing successfully."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color: #6B7785; font-size: 13px;"
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

        root.addWidget(info_group)

        # ----------------------------------------------------
        # Result files
        # ----------------------------------------------------

        results_group = QGroupBox("Results")
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

        self.open_flagged_btn = QPushButton("⚑   Open Review CSV")
        self.open_flagged_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.open_flagged_btn.clicked.connect(
            lambda: self._open_result("flagged_csv")
        )
        results_layout.addWidget(self.open_flagged_btn)

        self.open_folder_btn = QPushButton("📁   Open Output Folder")
        self.open_folder_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.open_folder_btn.clicked.connect(
            self._open_output_folder
        )
        results_layout.addWidget(self.open_folder_btn)

        root.addWidget(results_group)

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
                    background-color: #F8FAFC;
                    border: 1px solid #D7E0E8;
                    border-radius: 6px;
                    color: #8A99A6;
                }
                """
            )
            card_layout.addWidget(image_label)

            caption = QLabel("")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            caption.setStyleSheet(
                "color: #52606D; font-size: 11px; font-weight: 600;"
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

        self._chart_canvas: FigureCanvas | None = None
        self._chart_placeholder = QLabel(
            "Charts will appear here once a detection run completes."
        )
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setStyleSheet("color: #8A99A6; font-style: italic;")
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
        self._chart_scroll.setMinimumHeight(1400)
        self._chart_scroll.setMaximumHeight(1400)
        self._chart_scroll.hide()
        charts_outer.addWidget(self._chart_scroll)

        self._charts_layout = charts_outer
        root.addWidget(self.charts_group)

        root.addStretch()

        # ----------------------------------------------------
        # Navigation (fixed at bottom, outside scroll)
        # ----------------------------------------------------

        nav_widget = QWidget()
        nav_widget.setStyleSheet("background-color: #F4F7FA; border-top: 1px solid #D7E0E8;")
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
        self._chart_canvas.resize(available_width, 1360)

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

        # Disable buttons if a particular output was not produced.
        self.open_video_btn.setEnabled(
            "video" in outputs and outputs["video"].exists()
        )
        self.open_summary_btn.setEnabled(
            "summary_csv" in outputs and outputs["summary_csv"].exists()
        )
        self.open_flagged_btn.setEnabled(
            "flagged_csv" in outputs and outputs["flagged_csv"].exists()
        )
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

        canvas = ScrollPassthroughCanvas(fig)

        # Fill the chart viewport horizontally. The chart remains tall so the
        # Results page itself can scroll vertically, but never horizontally.
        canvas.setMinimumWidth(0)
        canvas.setMinimumHeight(1360)
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

    def _open_output_folder(self) -> None:
        if self.output_dir:
            open_path(self.output_dir)

# Main Window

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
        )
        self.pages.addWidget(self.results_page)

    
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


# Application Entry Point

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(APP_STYLE)

    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    else:
        print(f"Icon not found: {ICON_PATH}")

    window = ProjectPage()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()