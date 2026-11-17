"""Start a manual session or reopen project annotations."""
import time

from PyQt6.QtCore import pyqtSignal, Qt, QThread
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QFrame, QSpinBox, QScrollArea, QMessageBox, QProgressDialog)
from annotation_session import create_session, find_session, set_interval
from ui_icons import icon, icon_label
from widgets import VideoPathRow


class SessionWorker(QThread):
    progress = pyqtSignal(int, int)  # source frames read, total (while preparing a lower-FPS video)

    def __init__(self, project, video, interval, fps, parent):
        super().__init__(parent)
        self.args = (project, video, interval, fps)
        self.root = None
        self.error = None

    def run(self):
        try:
            self.root = create_session(*self.args, cancelled=self.isInterruptionRequested,
                                       on_progress=self.progress.emit)
        except Exception as exc:
            self.error = exc


def _duration(seconds):
    seconds = max(0, round(seconds))
    return f'{seconds // 60} min {seconds % 60:02d} s' if seconds >= 60 else f'{seconds} s'


class PreparationProgress:
    """Fills the progress dialog while a lower-FPS working video is written."""

    def __init__(self, dialog, fps):
        self.dialog, self.fps, self.started = dialog, fps, None

    def update(self, done, total):
        dialog = self.dialog
        now = time.monotonic()
        if self.started is None:
            self.started = (now, done)
        if dialog.maximum() != total:
            dialog.setRange(0, total)  # leaves the busy animation for a real bar
        start, first = self.started
        rate = (done - first) / (now - start) if now > start and done > first else 0
        left = f' · about {_duration((total - done) / rate)} left' if rate and done < total else ''
        dialog.setLabelText(f'Preparing a {self.fps:g} FPS working video for annotation…\n'
                            f'{done * 100 // total}% · frame {done:,} of {total:,}{left}')
        dialog.setValue(min(done, total))


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
        self.video.field.textChanged.connect(self._update_start_button)
        self.video.fps_choice.currentIndexChanged.connect(self._update_start_button)
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
        self._update_start_button()
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

    def _existing_session(self):
        if not self.project or not self.video.get_path():
            return None
        try:
            return find_session(self.project, self.video.get_path(), self.video.target_fps())
        except (OSError, ValueError):
            return None

    def _update_start_button(self, *_):
        """Say Resume when this video already has a session at this frame rate."""
        existing = self._existing_session()
        self.new.setText('Resume annotating  →' if existing else 'Start annotating  →')
        self.new.setToolTip('This video already has a session at this frame rate; it opens straight away.'
                            if existing else '')
        self.new.setEnabled(bool(self.project) and bool(self.video.get_path()))

    def create(self):
        if not self.project or not self.video.get_path(): return
        existing = self._existing_session()
        if existing:  # already prepared: open it rather than preparing the video again
            try:
                set_interval(existing, self.interval.value())
            except OSError as exc:
                QMessageBox.warning(self, 'Cannot update frame interval', str(exc))
            self.opened.emit(str(existing))
            return
        self.new.setEnabled(False)
        try:
            fps = self.video.target_fps()
            worker = SessionWorker(self.project, self.video.get_path(), self.interval.value(), fps, self)
            progress = QProgressDialog('Opening the video…', 'Cancel', 0, 0, self)
            progress.setWindowTitle('Start annotating')
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setMinimumWidth(460)
            progress.setAutoClose(False)
            progress.setAutoReset(False)  # the bar stays full until the session opens
            progress.canceled.connect(worker.requestInterruption)
            # Keep a reference: PyQt holds connected methods weakly, so a temporary would be collected.
            tracker = PreparationProgress(progress, fps) if fps is not None else None
            if tracker:
                worker.progress.connect(tracker.update)
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
            self._update_start_button()
        self.set_project(self.project)
        self.opened.emit(str(root))

    def open_record(self, record):
        if isinstance(record, dict): self.opened.emit(record['path'])
        else: self.run_opened.emit(record)
