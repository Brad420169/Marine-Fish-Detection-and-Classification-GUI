"""Project entry points and resumable work, independent of pipeline order."""
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QFrame,
                             QLabel, QPushButton, QScrollArea)

from ui_icons import icon, icon_label


class ProjectOverview(QWidget):
    navigate = pyqtSignal(str)
    open_item = pyqtSignal(object)
    remove_item = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('''
            QFrame#overviewCard { background: rgba(5, 37, 57, 240); border: 1px solid #24617a; border-radius: 14px; }
            QFrame#overviewCard:hover { border-color: #49bfdc; background: #08354e; }
            QLabel#overviewCardTitle { font-size: 22px; font-weight: 700; color: #f1faff; }
            QLabel#overviewDescription { color: #b2cede; font-size: 14px; }
            QLabel#overviewSection { font-size: 19px; font-weight: 600; color: #f1faff; }
            QLabel#overviewEyebrow { color: #6bd8ee; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
            QFrame#overviewWork { background: rgba(5, 37, 57, 230); border: 1px solid #20536b; border-radius: 10px; }
            QFrame#overviewWork:hover { border-color: #3886a4; }
            QLabel#overviewWorkName { font-size: 14px; font-weight: 600; }
            QLabel#overviewCount { color: #a4c7d9; background: #0b354d; border-radius: 10px; padding: 4px 10px; }
        ''')
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget(); scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(12)
        eyebrow = QLabel('PROJECT WORKSPACE'); eyebrow.setObjectName('overviewEyebrow')
        layout.addWidget(eyebrow)
        self.title = QLabel('Project Overview'); self.title.setObjectName('pageTitle')
        self.title.setTextFormat(Qt.TextFormat.PlainText); self.title.setWordWrap(True)
        layout.addWidget(self.title)
        subtitle = QLabel('Three ways to get started. One place to bring your work together.')
        subtitle.setObjectName('overviewDescription'); subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        self.cards = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cards.setSpacing(16)
        for title, description, action, target, asset, accent in [
            ('Detect fish', 'Turn underwater footage into detections with an existing model.',
             'Start detection', 'detection', 'play', '#42d3f5'),
            ('Annotate data', 'Label fish by hand or refine model predictions, ready for training.',
             'Start annotating', 'review', 'document', '#6fe0c3'),
            ('Train a model', 'Build your next detector with a project dataset or your own YOLO data.',
             'Choose training data', 'train', 'train', '#b7b0ff')]:
            card = QFrame(); card.setObjectName('overviewCard')
            body = QVBoxLayout(card); body.setContentsMargins(22, 22, 22, 22); body.setSpacing(14)
            badge = icon_label(asset, 26); badge.setFixedSize(48, 48)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f'background: #10384f; border: 1px solid {accent}; border-radius: 12px;')
            body.addWidget(badge)
            heading = QLabel(title); heading.setObjectName('overviewCardTitle'); body.addWidget(heading)
            text = QLabel(description); text.setObjectName('overviewDescription')
            text.setWordWrap(True); text.setMinimumHeight(52); body.addWidget(text)
            body.addStretch()
            button = QPushButton(action + '  →')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(38)
            button.setStyleSheet(f'''
                QPushButton {{ background: #0d3b55; border: 1px solid {accent}; color: #f1faff; font-weight: 600; padding: 6px 12px; }}
                QPushButton:hover {{ background: #175672; }}
                QPushButton:focus {{ border: 2px solid {accent}; }}
                QPushButton:pressed {{ background: #082c43; }}
            ''')
            button.clicked.connect(lambda checked=False, key=target: self.navigate.emit(key))
            body.addWidget(button); self.cards.addWidget(card, 1)
        layout.addLayout(self.cards)
        layout.addSpacing(20)
        section = QHBoxLayout()
        heading = QLabel('Continue your work'); heading.setObjectName('overviewSection')
        section.addWidget(heading); section.addStretch()
        self.count = QLabel(); self.count.setObjectName('overviewCount'); section.addWidget(self.count)
        layout.addLayout(section)
        self.work = QWidget()
        self.work_layout = QVBoxLayout(self.work)
        self.work_layout.setContentsMargins(0, 0, 0, 0); self.work_layout.setSpacing(8)
        layout.addWidget(self.work)
        self.empty = QLabel('A fresh start. Choose a tool above — your saved work will appear here.')
        self.empty.setObjectName('overviewDescription'); self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.cards.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 850
                                else QBoxLayout.Direction.LeftToRight)

    def set_project(self, project):
        self.title.setText(project.name)
        items = [dict(kind='run', name=f'{r.model_name} · Run {r.run_number}', record=r,
                      path=r.output_dir) for r in project.runs] + project.items
        while self.work_layout.count():
            self.work_layout.takeAt(0).widget().deleteLater()
        for item in reversed(items):
            status, asset = {'run': ('Detection results', 'chart'), 'annotation': ('Annotations', 'document'),
                             'dataset': ('Training dataset', 'folder'), 'model': ('Trained model', 'train')}.get(
                                 item['kind'], (item['kind'], 'document'))
            if item['kind'] == 'annotation':
                try:
                    from yolo_export import reviewed_frames
                    _, _, numbers = reviewed_frames(item['path'])
                    status += f" · {len(numbers)} frames complete"
                except (OSError, ValueError, KeyError):
                    status += ' · source unavailable'
            row = QFrame(); row.setObjectName('overviewWork')
            line = QHBoxLayout(row); line.setContentsMargins(18, 12, 18, 12); line.setSpacing(16)
            line.addWidget(icon_label(asset, 24))
            labels = QVBoxLayout(); labels.setSpacing(4)
            name = QLabel(item['name']); name.setTextFormat(Qt.TextFormat.PlainText)
            name.setObjectName('overviewWorkName'); name.setWordWrap(True)
            detail = QLabel(status); detail.setTextFormat(Qt.TextFormat.PlainText); detail.setObjectName('muted')
            detail.setWordWrap(True)
            labels.addWidget(name); labels.addWidget(detail); line.addLayout(labels, 1)
            button = QPushButton('Resume' if item['kind'] == 'annotation' else 'Open')
            button.setMinimumWidth(90); button.setIcon(icon('arrow_right'))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"{button.text()} {item['name']}")
            button.clicked.connect(lambda checked=False, selected=item: self.open_item.emit(selected))
            line.addWidget(button)
            remove = QPushButton()
            remove.setIcon(icon('trash')); remove.setFixedWidth(42)
            remove.setAccessibleName(f"Remove {item['name']} from project")
            remove.setToolTip('Remove from project (keep files)')
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.setStyleSheet('QPushButton { background: transparent; border: 1px solid #43596a; padding: 5px; } QPushButton:hover { background: #592b39; border-color: #ed8a9d; }')
            remove.clicked.connect(lambda checked=False, selected=item: self.remove_item.emit(selected))
            line.addWidget(remove)
            self.work_layout.addWidget(row)
        self.count.setText(f"{len(items)} saved {'item' if len(items) == 1 else 'items'}")
        self.count.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.work.setVisible(bool(items))
