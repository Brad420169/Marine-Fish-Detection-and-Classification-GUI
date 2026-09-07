"""
pages/project_page.py
----------------------
Landing page: create a new project or open an existing one.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QVBoxLayout,
    QWidget
)

from paths import LOGO_PATH
from widgets import PathRow, ProjectListRow
from project_manager import Project, create_project, delete_project, list_projects


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
        # Deferred import: main_window.py imports ProjectPage back for its
        # "Back to Projects" navigation, so this avoids a circular import
        # at module load time.
        from pages.main_window import MainWindow

        self.main_window = MainWindow(project)
        self.main_window.show()
        self.close()
