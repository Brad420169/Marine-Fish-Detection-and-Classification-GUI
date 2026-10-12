"""Start a manual session or reopen project annotations."""
from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QFrame, QSpinBox, QScrollArea, QMessageBox, QProgressDialog)
from annotation_session import create_session
from ui_icons import icon, icon_label
from widgets import VideoPathRow


class SessionWorker(QThread):
    def __init__(self, project, video, interval, fps, parent):
        super().__init__(parent)
        self.args = (project, video, interval, fps)
        self.root = None
        self.error = None

    def run(self):
        try:
            self.root = create_session(*self.args, cancelled=self.isInterruptionRequested)
        except Exception as exc:
            self.error = exc


class AnnotationLibrary(QWidget):
    opened = pyqtSignal(str)
    run_opened = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.setStyleSheet('''
            QFrame#annotationSetup, QFrame#annotationSession { background: rgba(5, 37, 57, 235); border: 1px solid #24617a; border-radius: 12px; }
            QFrame#annotationSession:hover { border-color: #49bfdc; }
            QLabel#annotationHeading { font-size: 19px; font-weight: 600; }
            QLabel#annotationName { font-size: 14px; font-weight: 600; }
            QLabel#annotationEyebrow { color: #6fe0c3; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
        ''')
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget(); scroll.setWidget(content)
        layout = QVBoxLayout(content); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(14)
        eyebrow = QLabel('BUILD YOUR TRAINING DATA'); eyebrow.setObjectName('annotationEyebrow'); layout.addWidget(eyebrow)
        title = QLabel('Annotate Data'); title.setObjectName('pageTitle'); layout.addWidget(title)
        text = QLabel('Start with your own footage, or pick up where you left off. No model required.')
        text.setObjectName('muted'); text.setWordWrap(True); layout.addWidget(text)
        layout.addSpacing(6)
        setup = QFrame(); setup.setObjectName('annotationSetup')
        body = QVBoxLayout(setup); body.setContentsMargins(22, 20, 22, 20); body.setSpacing(14)
        heading_row = QHBoxLayout(); heading_row.addWidget(icon_label('video', 24))
        heading = QLabel('Start a new annotation session'); heading.setObjectName('annotationHeading')
        heading_row.addWidget(heading); heading_row.addStretch(); body.addLayout(heading_row)
        self.video = VideoPathRow()
        self.video.drop_label.setFixedHeight(130)
        self.video.field.setPlaceholderText('Browse or drop a video below…')
        self.video.btn.setAccessibleName('Browse for an annotation video')
        body.addWidget(self.video)
        formats = QLabel('MP4, AVI, MOV or MKV · One video per session')
        formats.setObjectName('muted'); body.addWidget(formats)
        settings = QHBoxLayout(); settings.setSpacing(10)
        interval_label = QLabel('Annotate every')
        settings.addWidget(interval_label)
        self.interval = QSpinBox(); self.interval.setRange(1, 100000); self.interval.setValue(1)
        self.interval.setSuffix(' frames'); self.interval.setMinimumWidth(125)
        interval_label.setBuddy(self.interval)
        self.interval.setAccessibleName('Annotation frame interval')
        settings.addWidget(self.interval); settings.addStretch()
        self.new = QPushButton('Start annotating  →'); self.new.setObjectName('primaryButton')
        self.new.setMinimumHeight(36); self.new.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new.setEnabled(False); self.new.clicked.connect(self.create)
        self.video.field.textChanged.connect(lambda path: self.new.setEnabled(bool(path.strip()) and bool(self.project)))
        settings.addWidget(self.new); body.addLayout(settings)
        hint = QLabel('Choose how far to advance after confirming a frame. Playback still shows every frame.')
        hint.setObjectName('muted'); hint.setWordWrap(True); body.addWidget(hint)
        layout.addWidget(setup)
        layout.addSpacing(12)
        heading = QLabel('Continue annotating'); heading.setObjectName('annotationHeading'); layout.addWidget(heading)
        self.sessions = QWidget(); self.session_layout = QVBoxLayout(self.sessions)
        self.session_layout.setContentsMargins(0, 0, 0, 0); self.session_layout.setSpacing(8)
        layout.addWidget(self.sessions)
        self.empty = QLabel('No annotation sessions yet. Add a video above to begin.')
        self.empty.setObjectName('muted'); self.empty.setWordWrap(True); layout.addWidget(self.empty)
        layout.addStretch()

    def set_project(self, project):
        if self.project is not project:
            self.video.field.clear(); self.video._set_idle_style()
        self.project = project
        self.new.setEnabled(bool(self.video.get_path()))
        while self.session_layout.count():
            self.session_layout.takeAt(0).widget().deleteLater()
        records = [item for item in reversed(project.items) if item['kind'] == 'annotation'] + list(reversed(project.runs))
        for record in records:
            manual = isinstance(record, dict)
            name = record['name'] if manual else f'{record.model_name} · Run {record.run_number}'
            row = QFrame(); row.setObjectName('annotationSession')
            line = QHBoxLayout(row); line.setContentsMargins(18, 12, 18, 12); line.setSpacing(16)
            line.addWidget(icon_label('document' if manual else 'search', 24))
            labels = QVBoxLayout(); labels.setSpacing(4)
            title = QLabel(name); title.setTextFormat(Qt.TextFormat.PlainText)
            title.setObjectName('annotationName'); title.setWordWrap(True); labels.addWidget(title)
            detail = QLabel('Manual annotations' if manual else 'Model predictions · ready to review')
            detail.setObjectName('muted'); labels.addWidget(detail); line.addLayout(labels, 1)
            button = QPushButton('Resume' if manual else 'Open'); button.setMinimumWidth(90)
            button.setIcon(icon('arrow_right')); button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f'{button.text()} {name}')
            button.clicked.connect(lambda checked=False, selected=record: self.open_record(selected))
            line.addWidget(button); self.session_layout.addWidget(row)
        self.empty.setVisible(not records)
        self.sessions.setVisible(bool(records))

    def create(self):
        if not self.project or not self.video.get_path(): return
        self.new.setEnabled(False)
        try:
            worker = SessionWorker(self.project, self.video.get_path(), self.interval.value(),
                                   self.video.target_fps(), self)
            progress = QProgressDialog('Preparing annotation video…', 'Cancel', 0, 0, self)
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.canceled.connect(worker.requestInterruption)
            worker.finished.connect(progress.accept)
            worker.start()
            progress.exec()
            if worker.isRunning():
                worker.requestInterruption()
            worker.wait()
            root, error = worker.root, worker.error
            worker.deleteLater()
            if isinstance(error, InterruptedError): return
            if error: raise error
            if root is None: return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Cannot create annotation session', str(exc)); return
        finally:
            self.new.setEnabled(bool(self.video.get_path()))
        self.set_project(self.project)
        self.opened.emit(str(root))

    def open_record(self, record):
        if isinstance(record, dict): self.opened.emit(record['path'])
        else: self.run_opened.emit(record)
