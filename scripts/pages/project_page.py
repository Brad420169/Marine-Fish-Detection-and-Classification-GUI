"""
pages/project_page.py
----------------------
Projects page: create, find and open projects. Lives inside the main window's
page stack, so opening a project switches page rather than swapping windows.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from paths import open_path
from widgets import PathRow
from project_manager import Project, create_project, delete_project, list_projects

RECENT_LIMIT = 4


def _run_detections(output_dir: str) -> int:
    """Total detections recorded by one run, read back from its summary CSV."""
    summary = Path(output_dir)/'track_summary.csv'
    if not summary.is_file():
        return 0
    try:
        with summary.open(newline='', encoding='utf-8') as stream:
            return sum(int(row.get('total_detections') or 0) for row in csv.DictReader(stream))
    except (OSError, ValueError):
        return 0


def _run_thumbnail(output_dir: str) -> str:
    examples = sorted(Path(output_dir).glob('maxn_examples/*.jpg'))
    return str(examples[0]) if examples else ''


def short_path(path: str, keep: int = 2) -> str:
    """Trim a long output path to its last few parts so cards stay one line each."""
    parts = Path(path).parts
    return path if len(parts) <= keep + 1 else '…/' + '/'.join(parts[-keep:])


def project_stats(project: Project) -> dict:
    """Headline numbers for a project card, gathered from its runs on disk."""
    detections, thumbnail = 0, ''
    for record in project.runs:
        detections += _run_detections(record.output_dir)
        thumbnail = thumbnail or _run_thumbnail(record.output_dir)
    modified = max((r.timestamp for r in project.runs), default=project.created)
    return {'detections': detections, 'runs': len(project.runs), 'modified': modified,
            'model': project.runs[-1].model_name if project.runs else '', 'thumbnail': thumbnail}


def _parse(stamp: str) -> datetime:
    for form in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(stamp, form)
        except ValueError:
            continue
    return datetime.min


class ActionCard(QFrame):
    """One of the three call-to-action tiles across the top of the page."""
    def __init__(self, symbol, title, subtitle, action, parent=None):
        super().__init__(parent)
        self.setObjectName('actionCard')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action = action
        row = QHBoxLayout(self); row.setContentsMargins(16,14,16,14); row.setSpacing(14)
        badge = QLabel(symbol); badge.setObjectName('actionBadge')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter); badge.setFixedSize(40,40)
        row.addWidget(badge)
        text = QVBoxLayout(); text.setSpacing(2)
        heading = QLabel(title); heading.setObjectName('cardTitle')
        caption = QLabel(subtitle); caption.setObjectName('muted'); caption.setWordWrap(True)
        text.addWidget(heading); text.addWidget(caption)
        row.addLayout(text,1)
        arrow = QLabel('→'); arrow.setObjectName('cardArrow'); row.addWidget(arrow)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.action()


class ProjectCard(QFrame):
    """A project in the Recent Projects list."""
    def __init__(self, project, stats, on_select, on_open, on_menu, parent=None):
        super().__init__(parent)
        self.setObjectName('projectCard')
        self.project = project
        self.on_select = on_select
        self.on_open = on_open
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self); row.setContentsMargins(12,10,12,10); row.setSpacing(14)

        thumb = QLabel(); thumb.setObjectName('projectThumb'); thumb.setFixedSize(104,72)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picture = QPixmap(stats['thumbnail']) if stats['thumbnail'] else QPixmap()
        if picture.isNull():
            thumb.setText('No\npreview')
        else:
            thumb.setPixmap(picture.scaled(104,72,Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation))
        row.addWidget(thumb)

        details = QVBoxLayout(); details.setSpacing(3)
        name = QLabel(project.name); name.setObjectName('cardTitle')
        details.addWidget(name)
        runs = f"{stats['runs']} run" if stats['runs'] == 1 else f"{stats['runs']} runs"
        for text, tip in ((f"🗓  {stats['modified']}", ''),
                          (f"▤  {stats['detections']:,} detections   •   {runs}", ''),
                          (f"🏷  {stats['model'] or 'No runs yet'}", ''),
                          (f"📁  {short_path(project.output_root) if project.output_root else 'No output folder set'}",
                           project.output_root)):
            # Wrapping lets a card shrink with the window instead of being clipped.
            line = QLabel(text); line.setObjectName('muted'); line.setWordWrap(True)
            if tip: line.setToolTip(tip)
            details.addWidget(line)
        row.addLayout(details,1)

        buttons = QVBoxLayout(); buttons.setSpacing(6); buttons.addStretch()
        controls = QHBoxLayout(); controls.setSpacing(6)
        open_button = QPushButton('Open'); open_button.setObjectName('primaryButton')
        open_button.setFixedWidth(84); open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(lambda: on_open(project))
        menu_button = QPushButton('⋮'); menu_button.setObjectName('menuButton')
        menu_button.setFixedWidth(32); menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_button.setToolTip('More actions')
        menu_button.clicked.connect(lambda: on_menu(project, menu_button))
        controls.addWidget(open_button); controls.addWidget(menu_button)
        buttons.addLayout(controls); buttons.addStretch()
        row.addLayout(buttons)

    def set_selected(self, selected):
        self.setProperty('selected', 'yes' if selected else 'no')
        self.style().unpolish(self); self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_select(self.project)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_open(self.project)


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('New project')
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self); layout.setSpacing(10)
        layout.addWidget(QLabel('Name this project and choose where its detection results are stored.'))
        self.name = QLineEdit(); self.name.setPlaceholderText('Enter project name…')
        layout.addWidget(self.name)
        self.output = PathRow('Outputs', 'Select output folder for this project…', mode='folder')
        layout.addWidget(self.output)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Create')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.returnPressed.connect(self.accept)

    def accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self,'No project name','Please enter a project name.');return
        if not self.output.get_path():
            QMessageBox.warning(self,'No output folder','Please select an output folder for this project.');return
        super().accept()


class ProjectsPage(QWidget):
    """Landing page for choosing a project. Emits `opened` once one is chosen."""
    opened = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected = None
        self.show_all = False
        self.cards = []

        root = QVBoxLayout(self); root.setContentsMargins(18,14,18,16); root.setSpacing(14)
        title = QLabel('Projects'); title.setObjectName('pageTitle'); root.addWidget(title)
        subtitle = QLabel('Manage your projects, create new ones, and open existing projects.')
        subtitle.setObjectName('muted'); root.addWidget(subtitle)

        actions = QHBoxLayout(); actions.setSpacing(14); root.addLayout(actions)
        for symbol,heading,caption,handler in (
                ('+','New Project','Create a new project to start analysing videos',self.create),
                ('▤','Open Project','Open the project selected below',self.open_selected),
                ('⌕','Browse Folder','Select a project directory manually',self.browse)):
            actions.addWidget(ActionCard(symbol,heading,caption,handler),1)

        body = QHBoxLayout(); body.setSpacing(14); root.addLayout(body,1)

        recent = QGroupBox(); recent.setObjectName('recentBox')
        recent_layout = QVBoxLayout(recent); recent_layout.setContentsMargins(12,12,12,12); recent_layout.setSpacing(10)
        heading_row = QHBoxLayout()
        heading = QLabel('🕑  Recent Projects'); heading.setObjectName('cardTitle')
        heading_row.addWidget(heading); heading_row.addStretch()
        self.view_all = QPushButton('View All →'); self.view_all.setObjectName('linkButton')
        self.view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_all.clicked.connect(self.toggle_view_all)
        heading_row.addWidget(self.view_all)
        recent_layout.addLayout(heading_row)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget(); self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(0,0,0,0); self.list_layout.setSpacing(10)
        self.list_layout.addStretch()
        self.scroll.setWidget(holder)
        recent_layout.addWidget(self.scroll,1)
        self.empty = QLabel('No projects yet. Use New Project to create one.')
        self.empty.setObjectName('muted'); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_layout.addWidget(self.empty)
        body.addWidget(recent,1)

        # The rail is a fixed-width inspector so the project list keeps the slack.
        rail_holder = QWidget(); rail_holder.setMinimumWidth(320); rail_holder.setMaximumWidth(400)
        rail = QVBoxLayout(rail_holder); rail.setContentsMargins(0,0,0,0); rail.setSpacing(14)
        body.addWidget(rail_holder)

        self.search = QLineEdit(); self.search.setPlaceholderText('🔍   Search projects…')
        self.search.textChanged.connect(self.refresh_list)
        rail.addWidget(self.search)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel('Sort by'))
        self.sort = QComboBox()
        for label,key in (('Last Modified (Newest)','new'),('Last Modified (Oldest)','old'),
                          ('Name (A–Z)','name'),('Most Detections','detections')):
            self.sort.addItem(label,key)
        self.sort.currentIndexChanged.connect(self.refresh_list)
        sort_row.addWidget(self.sort,1)
        rail.addLayout(sort_row)

        filters = QGroupBox('Filter')
        grid = QGridLayout(filters); grid.setVerticalSpacing(8); grid.setHorizontalSpacing(10)
        self.model_filter = QComboBox(); self.model_filter.currentIndexChanged.connect(self.refresh_list)
        self.date_filter = QComboBox()
        for label,days in (('Any time',0),('Last 7 days',7),('Last 30 days',30),('Last year',365)):
            self.date_filter.addItem(label,days)
        self.date_filter.currentIndexChanged.connect(self.refresh_list)
        for row,(label,widget) in enumerate((('Model',self.model_filter),('Date range',self.date_filter))):
            grid.addWidget(QLabel(label),row,0); grid.addWidget(widget,row,1)
        grid.setColumnStretch(1,1)
        rail.addWidget(filters)

        self.details = QGroupBox('Project Details')
        details_layout = QVBoxLayout(self.details); details_layout.setSpacing(8)
        self.details_thumb = QLabel(); self.details_thumb.setObjectName('projectThumb')
        self.details_thumb.setFixedHeight(120); self.details_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.details_thumb.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        details_layout.addWidget(self.details_thumb)
        self.details_text = QLabel(); self.details_text.setObjectName('muted')
        self.details_text.setWordWrap(True); self.details_text.setTextFormat(Qt.TextFormat.RichText)
        details_layout.addWidget(self.details_text)
        details_layout.addStretch()
        rail.addWidget(self.details,1)

        self.refresh()

    # Data

    def refresh(self):
        """Reload projects from disk, keeping the current selection where possible."""
        self.projects = list_projects()
        self.stats = {p.name: project_stats(p) for p in self.projects}
        models = sorted({s['model'] for s in self.stats.values() if s['model']})
        current = self.model_filter.currentText()
        self.model_filter.blockSignals(True)
        self.model_filter.clear(); self.model_filter.addItem('All models')
        self.model_filter.addItems(models)
        index = self.model_filter.findText(current)
        self.model_filter.setCurrentIndex(max(0,index))
        self.model_filter.blockSignals(False)
        names = {p.name for p in self.projects}
        if self.selected and self.selected.name not in names:
            self.selected = None
        self.refresh_list()

    def visible_projects(self):
        term = self.search.text().strip().lower()
        model = self.model_filter.currentText()
        days = self.date_filter.currentData()
        chosen = []
        for project in self.projects:
            stats = self.stats[project.name]
            if term and term not in project.name.lower():
                continue
            if self.model_filter.currentIndex() > 0 and stats['model'] != model:
                continue
            if days and _parse(stats['modified']) < datetime.now()-timedelta(days=days):
                continue
            chosen.append(project)
        key = self.sort.currentData()
        if key == 'name':
            chosen.sort(key=lambda p: p.name.lower())
        elif key == 'detections':
            chosen.sort(key=lambda p: -self.stats[p.name]['detections'])
        else:
            chosen.sort(key=lambda p: _parse(self.stats[p.name]['modified']), reverse=key != 'old')
        return chosen if self.show_all else chosen[:RECENT_LIMIT]

    def refresh_list(self, *_args):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = []
        projects = self.visible_projects()
        for project in projects:
            card = ProjectCard(project, self.stats[project.name],
                               self.select, self.open_project, self.show_menu)
            self.list_layout.insertWidget(self.list_layout.count()-1, card)
            self.cards.append(card)
        self.empty.setVisible(not projects)
        self.empty.setText('No projects yet. Use New Project to create one.' if not self.projects
                           else 'No projects match the current search and filters.')
        if self.selected is None and projects:
            self.selected = projects[0]
        self.refresh_selection()

    def refresh_selection(self):
        for card in self.cards:
            card.set_selected(self.selected is not None and card.project.name == self.selected.name)
        if self.selected is None:
            self.details_thumb.clear(); self.details_thumb.setText('No project selected')
            self.details_text.setText('')
            return
        stats = self.stats[self.selected.name]
        picture = QPixmap(stats['thumbnail']) if stats['thumbnail'] else QPixmap()
        if picture.isNull():
            self.details_thumb.clear(); self.details_thumb.setText('No preview available')
        else:
            self.details_thumb.setPixmap(picture.scaled(self.details_thumb.width() or 240,120,
                                                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                        Qt.TransformationMode.SmoothTransformation))
        runs = f"{stats['runs']} run" if stats['runs'] == 1 else f"{stats['runs']} runs"
        self.details_text.setText(
            f"<b>{self.selected.name}</b><br>"
            f"Created: {self.selected.created}<br>"
            f"Last modified: {stats['modified']}<br>"
            f"Total detections: {stats['detections']:,}<br>"
            f"Total runs: {runs}<br>"
            f"Model: {stats['model'] or '—'}<br>"
            f"Location: {self.selected.output_root or '—'}")

    # Actions

    def select(self, project):
        self.selected = project
        self.refresh_selection()

    def toggle_view_all(self):
        self.show_all = not self.show_all
        self.view_all.setText('Show Recent ←' if self.show_all else 'View All →')
        self.refresh_list()

    def open_project(self, project):
        if not project.output_root:
            folder = QFileDialog.getExistingDirectory(self,f'Select output folder for {project.name}')
            if not folder:
                return
            project.output_root = folder
            project.save()
        self.selected = project
        self.opened.emit(project)

    def open_selected(self):
        if self.selected is None:
            QMessageBox.information(self,'No project selected','Select a project in the list first.');return
        self.open_project(self.selected)

    def create(self):
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            project = create_project(dialog.name.text(), dialog.output.get_path())
        except ValueError as exc:
            QMessageBox.warning(self,'Cannot create project',str(exc));return
        self.refresh()
        self.open_project(project)

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self,'Select a project folder')
        if not folder:
            return
        if not (Path(folder)/'project.json').is_file():
            QMessageBox.warning(self,'Not a project folder',
                                'That folder has no project.json. Choose a folder created by this application.')
            return
        try:
            project = Project.load(Path(folder))
        except (OSError,ValueError,KeyError) as exc:
            QMessageBox.warning(self,'Cannot open project',str(exc));return
        self.open_project(project)

    def show_menu(self, project, anchor):
        menu = QMenu(self)
        menu.addAction('Open').triggered.connect(lambda: self.open_project(project))
        folder = menu.addAction('Open output folder')
        folder.setEnabled(bool(project.output_root))
        folder.triggered.connect(lambda: open_path(project.output_root))
        menu.addSeparator()
        menu.addAction('Delete project').triggered.connect(lambda: self.delete(project))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def delete(self, project):
        if QMessageBox.warning(
                self,'Delete project',
                f"Are you sure you want to delete '{project.name}'?\n\n"
                'Deleted projects are not recoverable.\n\n'
                'Detection output files will not be deleted.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_project(project)
        except Exception as exc:
            QMessageBox.critical(self,'Delete failed',str(exc));return
        if self.selected is not None and self.selected.name == project.name:
            self.selected = None
        self.refresh()
