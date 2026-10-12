"""Train and evaluate models from project or external YOLO datasets."""
from pathlib import Path
import sys
import shutil
import tempfile

from PyQt6.QtCore import QProcess, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSlider, QVBoxLayout, QWidget)

from paths import ROOT_DIR
from widgets import settings_slider
from dataset_validation import inspect_dataset, label_path
from training_metrics import parse_metrics
from pages.training_dashboard import TrainingDashboard


class DatasetPathEdit(QLineEdit):
    """Editable dataset path that accepts exactly one local YAML file."""
    file_dropped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText('Drop data.yaml here, or browse for a dataset…')
        self.setToolTip('Drop one local .yaml or .yml file to load and validate the dataset.')
        self.setAccessibleName('YOLO dataset YAML path; supports drag and drop')

    @staticmethod
    def dropped_path(mime):
        urls = mime.urls() if mime.hasUrls() else []
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = Path(urls[0].toLocalFile())
        if path.is_file() and path.suffix.lower() in ('.yaml', '.yml'):
            return str(path)
        return None

    def dragEnterEvent(self, event):
        if self.dropped_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path = self.dropped_path(event.mimeData())
        if path:
            self.setText(path)
            self.file_dropped.emit()
            event.acceptProposedAction()
        else:
            event.ignore()


class TrainingPage(QWidget):
    model_ready = pyqtSignal(str, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.active_dataset = None
        self.best_weights = None
        self._metric_buffer = ''
        self.job_kind = 'training'
        self._job_output = None
        self._job_work = None
        self._stop_requested = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)

        outer = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget(); scroll.setWidget(content)
        layout = QVBoxLayout(content); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(12)
        title = QLabel('Train a Model'); title.setObjectName('pageTitle'); layout.addWidget(title)
        intro = QLabel('Use a project dataset or open an existing YOLO dataset. No detection run is required.')
        intro.setWordWrap(True); intro.setObjectName('muted'); layout.addWidget(intro)

        train_group = QGroupBox('Training dataset and settings')
        train_layout = QVBoxLayout(train_group)
        self.project_datasets = QComboBox()
        self.project_datasets.addItem('Choose a project dataset…', '')
        self.project_datasets.activated.connect(lambda: self.dataset.setText(self.project_datasets.currentData() or ''))
        train_layout.addWidget(self.project_datasets)
        dataset_row = QHBoxLayout()
        self.dataset = DatasetPathEdit()
        self.dataset.file_dropped.connect(self._inspect_dataset)
        dataset_row.addWidget(self.dataset, 1)
        choose_yaml = QPushButton('Open YOLO dataset…'); choose_yaml.clicked.connect(self._choose_yaml)
        dataset_row.addWidget(choose_yaml); train_layout.addLayout(dataset_row)
        self.dataset_summary = QLabel('Select data.yaml to inspect classes and image splits.')
        self.dataset_summary.setWordWrap(True)
        train_layout.addWidget(self.dataset_summary)
        self.previews = []
        preview_row = QHBoxLayout()
        for _ in range(3):
            preview = QLabel()
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setMinimumWidth(0)
            preview.hide()
            preview_row.addWidget(preview)
            self.previews.append(preview)
        train_layout.addLayout(preview_row)
        self.inspect_button = QPushButton('Validate dataset')
        self._validated_dataset = None
        self._validation_feedback = QTimer(self)
        self._validation_feedback.setSingleShot(True)
        self._validation_feedback.timeout.connect(lambda: self.inspect_button.setText('Validate dataset'))
        self.dataset.textChanged.connect(self._reset_validation_feedback)
        self.inspect_button.clicked.connect(self._validate_clicked)
        train_layout.addWidget(self.inspect_button)
        self.model = QComboBox()
        for name in ('yolo11n.pt', 'yolo11s.pt'):
            self.model.addItem(name, name)
        self.model.setCurrentText('yolo11s.pt')
        self.model.setToolTip('Use the model that annotated the video to fine-tune its fish knowledge, or start from general YOLO11 weights. Only YOLO .pt files work here; RF-DETR .pth files need a different trainer.')
        model_row = QHBoxLayout(); model_row.addWidget(QLabel('Starting model')); model_row.addWidget(self.model, 1)
        choose_model = QPushButton('Choose .pt…'); choose_model.clicked.connect(self._choose_model)
        model_row.addWidget(choose_model)
        self.browse_buttons = [choose_yaml, choose_model]
        help_button = QPushButton('?'); help_button.setFixedWidth(30); help_button.setStyleSheet('padding: 0;')
        help_button.setAccessibleName('Explain starting model')
        help_button.clicked.connect(lambda: self._explain('Starting model', self.model.toolTip()))
        model_row.addWidget(help_button); train_layout.addLayout(model_row)
        self.model_note = QLabel(); self.model_note.setObjectName('muted'); self.model_note.setWordWrap(True)
        self.model.currentIndexChanged.connect(self._model_changed)
        self._model_changed()
        train_layout.addWidget(self.model_note)
        self.epochs, self.epochs_value = self._slider(train_layout, 'Epochs', 1, 300, 100,
            'One epoch sees the training set once. More epochs give more learning time, but may overfit. Try 50–100 first.')
        self.batch, self.batch_value = self._slider(train_layout, 'Batch size', 0, 32, 0,
            'Images processed together. Auto uses GPU memory when CUDA is available; Smaller fixed batches use less memory.')
        self.patience, self.patience_value = self._slider(train_layout, 'Patience', 0, 100, 20,
            'Stop early when validation accuracy has not improved for this many epochs. Zero disables early stopping.')
        self.image_size, self.image_size_value = self._slider(train_layout, 'Image size', 10, 32, 18,
            'Images are resized for training. Larger sizes can help small fish but use more memory. The number shown is pixels on each side.')
        self.run_name = QLineEdit('fish_yolo11s')
        name_row = QHBoxLayout(); name_row.addWidget(QLabel('Run name')); name_row.addWidget(self.run_name, 1)
        train_layout.addLayout(name_row)
        guidance = QLabel('Device is chosen automatically: CUDA GPU, or CPU. Training outputs and best.pt are saved inside the dataset’s runs folder. The test split stays unused during training.')
        guidance.setWordWrap(True); guidance.setObjectName('muted'); train_layout.addWidget(guidance)
        buttons = QHBoxLayout()
        self.start = QPushButton('Start Training'); self.start.setObjectName('primaryButton')
        self.start.clicked.connect(self._start); buttons.addWidget(self.start)
        self.stop = QPushButton('Stop Training'); self.stop.setEnabled(False)
        self.stop.setToolTip('Stop this job and remove its incomplete outputs and temporary files. Completed runs and the dataset are kept.')
        self.stop.clicked.connect(self.stop_job); buttons.addWidget(self.stop)
        self.evaluate = QPushButton('Evaluate Test Set'); self.evaluate.setEnabled(False)
        self.evaluate.setToolTip('After training, score best.pt once on frames held out of training and validation.')
        self.evaluate.clicked.connect(self._evaluate); buttons.addWidget(self.evaluate)
        train_layout.addLayout(buttons)
        self.dataset.textChanged.connect(self._dataset_changed)
        self.dashboard = TrainingDashboard()
        train_layout.addWidget(self.dashboard)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(220)
        self.log.setPlaceholderText('Training progress appears here.'); train_layout.addWidget(self.log)
        self.reuse_buttons = []
        reuse = QHBoxLayout()
        for label, target in [('Use for detection', 'detection'), ('Assist annotation', 'review')]:
            button = QPushButton(label)
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, dest=target: self.model_ready.emit(str(self.best_weights), dest))
            reuse.addWidget(button)
            self.reuse_buttons.append(button)
        train_layout.addLayout(reuse)
        layout.addWidget(train_group)
        layout.addStretch()

    _slider = settings_slider

    def _explain(self, title, explanation):
        QMessageBox.information(self, title, explanation)

    def _model_changed(self):
        if self.model.currentData() in ('yolo11n.pt', 'yolo11s.pt'):
            self.model_note.setText('Starting from general COCO-pretrained YOLO11 weights.')
        else:
            self.model_note.setText('Fine-tuning the selected YOLO weights. New weights will be saved separately.')

    def set_project(self, project):
        self.project = project
        self.dataset.clear()
        self.best_weights = None
        self.active_dataset = None
        self.evaluate.setEnabled(False)
        self.log.clear()
        self.set_inputs_enabled(True)
        self.refresh_datasets()

    def refresh_datasets(self):
        current = self.project_datasets.currentData()
        self.project_datasets.clear()
        self.project_datasets.addItem('Choose a project dataset…', '')
        for item in self.project.items:
            if item['kind'] == 'dataset':
                self.project_datasets.addItem(item['name'], item['path'])
        index = self.project_datasets.findData(current)
        self.project_datasets.setCurrentIndex(max(0, index))

    def set_run(self, root):
        yaml = Path(root) / 'yolo_dataset' / 'data.yaml' if root else None
        if yaml and yaml.is_file():
            self.dataset.setText(str(yaml))

    def _choose_yaml(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose YOLO dataset', '', 'YOLO data (*.yaml *.yml)')
        if path:
            self.dataset.setText(path)
            self._inspect_dataset()

    def _choose_model(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose YOLO annotation model', '', 'YOLO weights (*.pt)')
        if path:
            for index in range(self.model.count() - 1, 1, -1):
                if self.model.itemText(index).startswith('Selected model'):
                    self.model.removeItem(index)
            self.model.addItem(f'Selected model ({Path(path).name})', str(Path(path).resolve()))
            self.model.setCurrentIndex(self.model.count() - 1)
            self.model_note.setText('Using the selected YOLO weights. Training will save new weights separately.')

    def _reset_validation_feedback(self):
        self._validated_dataset = None
        self._validation_feedback.stop()
        self.inspect_button.setText('Validate dataset')

    def _validate_clicked(self):
        already_validated = self._validated_dataset == self.dataset.text().strip()
        # Recheck files so external changes can still surface validation errors.
        if self._inspect_dataset() is not None and already_validated:
            self.inspect_button.setText('Already validated')
            self._validation_feedback.start(2000)

    def _inspect_dataset(self):
        try:
            info = inspect_dataset(self.dataset.text().strip())
            self.dataset_summary.setText(f"{len(info['names'])} classes: {', '.join(info['names'])}\n"
                                         f"Images — train: {info['counts']['train']}, validation: {info['counts']['val']}, test: {info['counts']['test']}")
            self.show_previews(info)
            if self.project:
                path = Path(self.dataset.text()).expanduser().resolve()
                self.project.add_item('dataset', path.parent.name, path)
            self._validated_dataset = self.dataset.text().strip()
            return info
        except (OSError, ValueError, TypeError) as exc:
            self._reset_validation_feedback()
            self.dataset_summary.setText(f'Dataset needs attention: {exc}')
            return None

    def show_previews(self, info):
        import cv2
        from PyQt6.QtGui import QImage, QPixmap
        images = [paths[0] for paths in info['images'].values() if paths]
        for preview, path in zip(self.previews, images):
            frame = cv2.imread(str(path))
            if frame is None:
                preview.setText('Image preview unavailable'); preview.show(); continue
            height, width = frame.shape[:2]
            labels = label_path(path)
            if labels.is_file():
                for line in labels.read_text().splitlines():
                    if not line.strip(): continue
                    cls, x, y, w, h = map(float, line.split())
                    x1, y1 = round((x-w/2)*width), round((y-h/2)*height)
                    x2, y2 = round((x+w/2)*width), round((y+h/2)*height)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 255), 2)
                    cv2.putText(frame, info['names'][int(cls)], (x1, max(15, y1-4)), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 255), 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
            preview.setPixmap(QPixmap.fromImage(image).scaled(220, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            preview.setToolTip(str(path)); preview.setAccessibleName(f'Dataset preview: {path.name}'); preview.show()
        for preview in self.previews[len(images):]: preview.hide()

    def _prepare_job(self, dataset, name):
        """Reserve a new folder so cleanup can never touch an earlier run."""
        if self._job_output is not None or self._job_work is not None:
            self._cleanup_job(remove_outputs=True)
        if self._job_output is not None or self._job_work is not None:
            QMessageBox.warning(self, 'Cleanup incomplete', 'Resolve the cleanup error in the log before starting another job.')
            return None
        try:
            parent = Path(dataset).resolve().parent / 'runs'
            parent.mkdir(parents=True, exist_ok=True)
            index = 1
            while True:
                output = parent / (name if index == 1 else f'{name}{index}')
                try:
                    output.mkdir()
                    break
                except FileExistsError:
                    index += 1
            self._job_output = output
            self._job_work = Path(tempfile.mkdtemp(prefix='marine-training-'))
            self._stop_requested = False
            return ['--output-dir', str(output), '--work-dir', str(self._job_work)]
        except OSError as exc:
            self._cleanup_job(remove_outputs=True)
            QMessageBox.warning(self, 'Cannot start training', str(exc))
            return None

    def stop_job(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._stop_requested = True
            self.stop.setEnabled(False)
            self.log.appendPlainText('Stopping; incomplete run files will be removed after the process exits…')
            self.process.kill()

    def _cleanup_job(self, remove_outputs):
        for attribute in ('_job_work', '_job_output'):
            path = getattr(self, attribute)
            if path is None:
                continue
            if attribute == '_job_output' and not remove_outputs:
                setattr(self, attribute, None)
                continue
            try:
                if path.exists():
                    shutil.rmtree(path)
                setattr(self, attribute, None)
            except OSError as exc:
                self.log.appendPlainText(f'Could not clean up {path}: {exc}. Remove this folder manually before starting another job.')

    def _start(self):
        yaml = Path(self.dataset.text().strip()).expanduser()
        if not yaml.is_file():
            QMessageBox.warning(self, 'Dataset missing', 'Choose an exported data.yaml file.'); return
        if not self._inspect_dataset(): return
        name = self.run_name.text().strip()
        if not name or not name[0].isascii() or not name[0].isalnum() or any(
                not (char.isascii() and (char.isalnum() or char in '_-')) for char in name):
            QMessageBox.warning(self, 'Invalid run name', 'Use letters, numbers, underscores, or hyphens.'); return
        if self.process.state() != QProcess.ProcessState.NotRunning: return
        model = self.model.currentData()
        if model not in ('yolo11n.pt', 'yolo11s.pt') and not Path(model).is_file():
            QMessageBox.warning(self, 'Model missing', 'Choose an existing YOLO .pt weights file.'); return
        arguments = [str(ROOT_DIR / 'scripts' / 'train_yolo.py'), str(yaml.resolve()),
                     '--model', model, '--epochs', str(self.epochs.value()),
                     '--imgsz', str(self.image_size.value() * 32), '--batch', str(self.batch.value()),
                     '--patience', str(self.patience.value()), '--name', name]
        job_arguments = self._prepare_job(yaml, name)
        if job_arguments is None: return
        arguments += job_arguments
        self._metric_buffer = ""
        self.dashboard.reset(self.epochs.value())
        self.log.clear()
        self.log.appendPlainText('Starting training. Pretrained weights may download on first use.\n')
        self.job_kind = 'training'; self.best_weights = None
        self.active_dataset = str(yaml.resolve())
        self.set_inputs_enabled(False)
        self.start.setEnabled(False); self.stop.setEnabled(True); self.evaluate.setEnabled(False)
        self.process.start(sys.executable, arguments)

    def _evaluate(self):
        if not self.best_weights or not self.best_weights.is_file():
            QMessageBox.warning(self, 'Model missing', 'Train a model first, or locate its best.pt in the runs folder.'); return
        if self.process.state() != QProcess.ProcessState.NotRunning: return
        info = self._inspect_dataset()
        if not info: return
        if not info['counts']['test']:
            QMessageBox.warning(self, 'No test split', 'This dataset has no held-out test images.'); return
        job_arguments = self._prepare_job(Path(self.active_dataset), 'test_evaluation')
        if job_arguments is None: return
        self.job_kind = 'evaluation'
        self.set_inputs_enabled(False)
        self.log.appendPlainText('\nEvaluating held-out test frames…\n')
        self.start.setEnabled(False); self.stop.setEnabled(True); self.evaluate.setEnabled(False)
        self.process.start(sys.executable, [str(ROOT_DIR / 'scripts' / 'train_yolo.py'),
                                            self.active_dataset, '--evaluate', str(self.best_weights)] + job_arguments)

    def _read_output(self):
        output = bytes(self.process.readAllStandardOutput()).decode(errors='replace')
        if output:
            if self.job_kind == "training":
                self._consume_metrics(output)
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(output)
            self.log.ensureCursorVisible()

    def _consume_metrics(self, output):
        self._metric_buffer += output
        lines = self._metric_buffer.split('\n')
        self._metric_buffer = lines.pop()[-65536:]
        for line in lines:
            result = parse_metrics(line.strip())
            if result:
                self.dashboard.update_epoch(*result)

    def set_inputs_enabled(self, enabled):
        for button in self.browse_buttons:
            button.setEnabled(enabled)
        for widget in (self.dataset, self.project_datasets, self.inspect_button, self.model,
                       self.run_name, self.epochs, self.batch, self.patience, self.image_size):
            widget.setEnabled(enabled)
        for button in self.reuse_buttons:
            button.setEnabled(enabled and bool(self.best_weights and self.best_weights.is_file()))

    def _dataset_changed(self):
        self.best_weights = None
        self.evaluate.setEnabled(False)
        self.dataset_summary.setText('Dataset changed. Validate it before training.')
        for preview in self.previews: preview.hide()
        for button in self.reuse_buttons:
            button.setEnabled(False)

    def _process_error(self, error):
        self.log.appendPlainText(f'Process error: {error.name}')
        if error == QProcess.ProcessError.FailedToStart:
            self._cleanup_job(remove_outputs=True)
            self.set_inputs_enabled(True)
            if self.job_kind == "training":
                self.dashboard.finish(False)
            self.start.setEnabled(True)
            self.stop.setEnabled(False)

    def _finished(self, code, status):
        self._read_output()
        completed = code == 0 and status == QProcess.ExitStatus.NormalExit and not self._stop_requested
        self._cleanup_job(remove_outputs=not completed)
        if self.job_kind == "training":
            self._consume_metrics("\n")
            self.dashboard.finish(completed)
        if completed and self.job_kind == 'training':
            for line in self.log.toPlainText().splitlines():
                if line.startswith('BEST_WEIGHTS='):
                    self.best_weights = Path(line.removeprefix('BEST_WEIGHTS='))
            self.log.appendPlainText(f'\nTraining finished. Best model: {self.best_weights}')
        elif completed:
            self.log.appendPlainText('\nTest evaluation finished. See the metrics above.')
        else:
            self.log.appendPlainText(f'\n{self.job_kind.title()} stopped or failed (exit {code}).')
            if self._job_output is None and self._job_work is None:
                self.log.appendPlainText('Incomplete run outputs and temporary files cleaned up.')
        self.start.setEnabled(True); self.stop.setEnabled(False)
        self.evaluate.setEnabled(bool(self.best_weights and self.best_weights.is_file()))
        self.set_inputs_enabled(True)
        if self.best_weights and self.best_weights.is_file() and self.project:
            try:
                self.project.add_item('model', self.best_weights.parent.parent.name, self.best_weights,
                                      dataset=self.active_dataset)
            except OSError as exc:
                self.log.appendPlainText(f'Model saved, but project registration failed: {exc}')
