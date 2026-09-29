"""Reviewed-frame YOLO export and guided model training."""
from pathlib import Path
import json
import sys

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSlider, QVBoxLayout, QWidget)

from paths import ROOT_DIR
from yolo_export import ExportWorker, reviewed_frames


class TrainingPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = None
        self.export_worker = None
        self.best_weights = None
        self.job_kind = 'training'
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
        title = QLabel('Train a Fish Detector'); title.setObjectName('pageTitle'); layout.addWidget(title)
        intro = QLabel('1. Confirm frames in Review Detections.  2. Export a YOLO dataset.  3. Train a new model.')
        intro.setWordWrap(True); intro.setObjectName('muted'); layout.addWidget(intro)

        export_group = QGroupBox('1. Export reviewed frames')
        export_layout = QVBoxLayout(export_group)
        note = QLabel('Only fully confirmed frames are exported. Frames are split in time order to reduce overlap between sets. Empty reviewed frames become background examples.')
        note.setWordWrap(True); export_layout.addWidget(note)
        self.frame_count = QLabel('Open a detection run to see reviewed frames.')
        self.frame_count.setObjectName('muted'); export_layout.addWidget(self.frame_count)
        self.train_split, self.train_split_value = self._slider(export_layout, 'Training %', 50, 90, 70,
            'The model learns from these frames. Usually 70–80%.')
        self.val_split, self.val_split_value = self._slider(export_layout, 'Validation %', 5, 40, 20,
            'Checked after each epoch to tune training and detect overfitting. Usually 10–20%.')
        self.test_split = QLabel(); export_layout.addWidget(self.test_split)
        self.train_split.valueChanged.connect(self._split_changed)
        self.val_split.valueChanged.connect(self._split_changed)
        self._split_changed()
        destination_row = QHBoxLayout()
        self.destination = QLineEdit(); self.destination.setPlaceholderText('New dataset folder')
        destination_row.addWidget(self.destination, 1)
        browse = QPushButton('Choose parent folder'); browse.clicked.connect(self._choose_destination)
        destination_row.addWidget(browse); export_layout.addLayout(destination_row)
        self.export_button = QPushButton('Generate AI Training Data')
        self.export_button.setObjectName('primaryButton')
        self.export_button.clicked.connect(self._export); export_layout.addWidget(self.export_button)
        self.export_status = QLabel(); self.export_status.setWordWrap(True); export_layout.addWidget(self.export_status)
        layout.addWidget(export_group)

        train_group = QGroupBox('2. Train with Ultralytics YOLO')
        train_layout = QVBoxLayout(train_group)
        dataset_row = QHBoxLayout()
        self.dataset = QLineEdit(); self.dataset.setPlaceholderText('Select a data.yaml file')
        dataset_row.addWidget(self.dataset, 1)
        choose_yaml = QPushButton('Browse…'); choose_yaml.clicked.connect(self._choose_yaml)
        dataset_row.addWidget(choose_yaml); train_layout.addLayout(dataset_row)
        self.model = QComboBox()
        for name in ('yolo11n.pt', 'yolo11s.pt'):
            self.model.addItem(name, name)
        self.model.setCurrentText('yolo11s.pt')
        self.model.setToolTip('Use the model that annotated the video to fine-tune its fish knowledge, or start from general YOLO11 weights. Only YOLO .pt files work here; RF-DETR .pth files need a different trainer.')
        model_row = QHBoxLayout(); model_row.addWidget(QLabel('Starting model')); model_row.addWidget(self.model, 1)
        choose_model = QPushButton('Choose .pt…'); choose_model.clicked.connect(self._choose_model)
        model_row.addWidget(choose_model)
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
        self.stop.clicked.connect(self.process.kill); buttons.addWidget(self.stop)
        self.evaluate = QPushButton('Evaluate Test Set'); self.evaluate.setEnabled(False)
        self.evaluate.setToolTip('After training, score best.pt once on frames held out of training and validation.')
        self.evaluate.clicked.connect(self._evaluate); buttons.addWidget(self.evaluate)
        train_layout.addLayout(buttons)
        self.dataset.textChanged.connect(self._dataset_changed)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(220)
        self.log.setPlaceholderText('Training progress appears here.'); train_layout.addWidget(self.log)
        layout.addWidget(train_group)
        layout.addStretch()

    def _slider(self, layout, title, minimum, maximum, value, explanation):
        row = QHBoxLayout()
        label = QLabel(title); label.setMinimumWidth(115); row.addWidget(label)
        slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(minimum, maximum); slider.setValue(value)
        slider.setToolTip(explanation); row.addWidget(slider, 1)
        display = QLabel(); display.setMinimumWidth(70); row.addWidget(display)
        help_button = QPushButton('?'); help_button.setFixedWidth(30); help_button.setStyleSheet('padding: 0;')
        help_button.setAccessibleName(f'Explain {title}')
        help_button.clicked.connect(lambda: self._explain(title, explanation)); row.addWidget(help_button)
        slider.valueChanged.connect(lambda number: display.setText(
            ('Auto' if number == 0 else str(number)) if title == 'Batch size' else
            str(number * 32) if title == 'Image size' else f'{number}%' if title.endswith('%') else str(number)))
        slider.valueChanged.emit(value)
        layout.addLayout(row)
        return slider, display

    def _explain(self, title, explanation):
        QMessageBox.information(self, title, explanation)

    def _model_changed(self):
        if self.model.currentData() in ('yolo11n.pt', 'yolo11s.pt'):
            self.model_note.setText('Starting from general COCO-pretrained YOLO11 weights.')
        else:
            self.model_note.setText('Fine-tuning the selected YOLO weights. New weights will be saved separately.')

    def set_run(self, root):
        root = Path(root) if root else None
        if root != self.root:
            self.root = root
            self.dataset.clear()
            while self.model.count() > 2:
                self.model.removeItem(2)
            self.model.setCurrentIndex(1)
            self.model_note.clear()
            if root:
                self.destination.setText(str(root / 'yolo_dataset'))
                yaml = root / 'yolo_dataset' / 'data.yaml'
                if yaml.is_file(): self.dataset.setText(str(yaml))
                source = root / 'original_detections.json'
                if source.is_file():
                    try:
                        weights = Path(json.loads(source.read_text(encoding='utf-8')).get('weights_path', ''))
                    except (OSError, ValueError):
                        weights = Path()
                    if weights.is_file() and weights.suffix.lower() == '.pt':
                        self.model.addItem(f'Annotation model ({weights.name})', str(weights.resolve()))
                        self.model.setCurrentIndex(2)
                        self.model_note.setText('Using the YOLO model that annotated this run. Training will save new weights separately.')
                    elif weights.suffix.lower() == '.pth':
                        self.model_note.setText('This run used RF-DETR weights. Choose a YOLO .pt model for YOLO training.')
                    else:
                        self.model_note.setText('Original weights were not saved with this run. Choose the annotation model .pt file if you have it.')
        try:
            count = len(reviewed_frames(root)[2]) if root else 0
            self.frame_count.setText(f'{count} fully confirmed frames available for export.' if root else 'Open a detection run to export frames.')
        except (OSError, ValueError, KeyError) as exc:
            self.frame_count.setText(str(exc))

    def _split_changed(self):
        test = 100 - self.train_split.value() - self.val_split.value()
        self.test_split.setText(f'Test: {test}% — held back for a final, independent check.' if test > 0 else
                                'Reduce training or validation so the test split has frames.')

    def _choose_destination(self):
        parent = QFileDialog.getExistingDirectory(self, 'Choose parent folder for the new dataset')
        if parent: self.destination.setText(str(Path(parent) / 'fish_yolo_dataset'))

    def _choose_yaml(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose YOLO dataset', '', 'YOLO data (data.yaml *.yaml)')
        if path: self.dataset.setText(path)

    def _choose_model(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose YOLO annotation model', '', 'YOLO weights (*.pt)')
        if path:
            for index in range(self.model.count() - 1, 1, -1):
                if self.model.itemText(index).startswith('Selected model'):
                    self.model.removeItem(index)
            self.model.addItem(f'Selected model ({Path(path).name})', str(Path(path).resolve()))
            self.model.setCurrentIndex(self.model.count() - 1)
            self.model_note.setText('Using the selected YOLO weights. Training will save new weights separately.')

    def _export(self):
        if not self.root:
            QMessageBox.warning(self, 'No run', 'Open a detection run first.'); return
        destination = self.destination.text().strip()
        if not destination:
            QMessageBox.warning(self, 'Destination missing', 'Choose a new dataset folder.'); return
        if self.export_worker:
            return
        self.export_button.setEnabled(False)
        self.export_status.setText('Exporting reviewed frames…')
        self.export_worker = ExportWorker(self.root, destination,
                                          self.train_split.value(), self.val_split.value(), self)
        self.export_worker.succeeded.connect(self._exported)
        self.export_worker.failed.connect(lambda error: self.export_status.setText(f'Export failed: {error}'))
        self.export_worker.finished.connect(self._export_finished)
        self.export_worker.start()

    def _exported(self, path, counts):
        yaml = Path(path)
        self.dataset.setText(str(yaml))
        self.export_status.setText(f'Exported {sum(counts)} frames: {counts[0]} train, {counts[1]} validation, {counts[2]} test. Dataset: {yaml.parent}')

    def _export_finished(self):
        self.export_worker.deleteLater()
        self.export_worker = None
        self.export_button.setEnabled(True)

    def _start(self):
        yaml = Path(self.dataset.text().strip()).expanduser()
        if not yaml.is_file():
            QMessageBox.warning(self, 'Dataset missing', 'Choose an exported data.yaml file.'); return
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
        self.log.clear()
        self.log.appendPlainText('Starting training. Pretrained weights may download on first use.\n')
        self.job_kind = 'training'; self.best_weights = None
        self.start.setEnabled(False); self.stop.setEnabled(True); self.evaluate.setEnabled(False)
        self.process.start(sys.executable, arguments)

    def _evaluate(self):
        if not self.best_weights or not self.best_weights.is_file():
            QMessageBox.warning(self, 'Model missing', 'Train a model first, or locate its best.pt in the runs folder.'); return
        if self.process.state() != QProcess.ProcessState.NotRunning: return
        self.job_kind = 'evaluation'
        self.log.appendPlainText('\nEvaluating held-out test frames…\n')
        self.start.setEnabled(False); self.stop.setEnabled(True); self.evaluate.setEnabled(False)
        self.process.start(sys.executable, [str(ROOT_DIR / 'scripts' / 'train_yolo.py'),
                                            self.dataset.text().strip(), '--evaluate', str(self.best_weights)])

    def _read_output(self):
        output = bytes(self.process.readAllStandardOutput()).decode(errors='replace')
        if output:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(output)
            self.log.ensureCursorVisible()

    def _dataset_changed(self):
        self.best_weights = None
        self.evaluate.setEnabled(False)

    def _process_error(self, error):
        self.log.appendPlainText(f'Process error: {error.name}')
        if error == QProcess.ProcessError.FailedToStart:
            self.start.setEnabled(True)
            self.stop.setEnabled(False)

    def _finished(self, code, status):
        self._read_output()
        if code == 0 and self.job_kind == 'training':
            for line in self.log.toPlainText().splitlines():
                if line.startswith('BEST_WEIGHTS='):
                    self.best_weights = Path(line.removeprefix('BEST_WEIGHTS='))
            self.log.appendPlainText(f'\nTraining finished. Best model: {self.best_weights}')
        elif code == 0:
            self.log.appendPlainText('\nTest evaluation finished. See the metrics above.')
        else:
            self.log.appendPlainText(f'\n{self.job_kind.title()} stopped or failed (exit {code}).')
        self.start.setEnabled(True); self.stop.setEnabled(False)
        self.evaluate.setEnabled(bool(self.best_weights and self.best_weights.is_file()))
