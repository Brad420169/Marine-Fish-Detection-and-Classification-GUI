"""Dataset export controls shared by manual and model-assisted annotation."""
from pathlib import Path
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox)
from yolo_export import ExportWorker, reviewed_frames
from widgets import settings_slider

class DatasetExportPage(QWidget):
    exported = pyqtSignal(str)
    saved = pyqtSignal(str)
    _slider = settings_slider

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = None
        self.export_worker = None
        layout = QVBoxLayout(self)
        export_group = QGroupBox('Generate AI training data')
        export_layout = QVBoxLayout(export_group)
        note = QLabel('Only fully confirmed frames are exported. Frames are split in time order to reduce overlap between sets. Empty reviewed frames become background examples.')
        note.setWordWrap(True); export_layout.addWidget(note)
        self.frame_count = QLabel('Open an annotation session to export completed frames.')
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


        self.use_button = QPushButton('Use in Train a Model')
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(lambda: self.exported.emit(self.last_dataset))
        layout.addWidget(self.use_button)
        layout.addStretch()

    def _explain(self, title, explanation):
        QMessageBox.information(self, title, explanation)

    def set_source(self, root):
        if self.export_worker:
            return
        self.root = Path(root)
        self.destination.setText(str(self.root / 'yolo_dataset'))
        self.use_button.setEnabled(False)
        self.export_status.clear()
        try:
            count = len(reviewed_frames(self.root)[2])
            self.frame_count.setText(f'{count} fully completed frames available. Unfinished frames are always excluded.')
        except (OSError, ValueError, KeyError) as exc:
            self.frame_count.setText(str(exc))

    def _split_changed(self):
        test = 100 - self.train_split.value() - self.val_split.value()
        self.test_split.setText(f'Test: {test}% — held back for a final, independent check.' if test > 0 else
                                'Reduce training or validation so the test split has frames.')

    def _choose_destination(self):
        parent = QFileDialog.getExistingDirectory(self, 'Choose parent folder for the new dataset')
        if parent: self.destination.setText(str(Path(parent) / 'fish_yolo_dataset'))

    def _export(self):
        if not self.root:
            QMessageBox.warning(self, 'No run', 'Open an annotation session first.'); return
        destination = self.destination.text().strip()
        if not destination:
            QMessageBox.warning(self, 'Destination missing', 'Choose a new dataset folder.'); return
        if self.export_worker:
            return
        self.export_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self.export_status.setText('Exporting reviewed frames…')
        self.export_worker = ExportWorker(self.root, destination,
                                          self.train_split.value(), self.val_split.value(), self)
        self.export_worker.succeeded.connect(self._exported)
        self.export_worker.failed.connect(lambda error: self.export_status.setText(f'Export failed: {error}'))
        self.export_worker.finished.connect(self._export_finished)
        self.export_worker.start()

    def _exported(self, path, counts):
        yaml = Path(path)
        self.last_dataset = str(yaml)
        self.use_button.setEnabled(True)
        self.saved.emit(str(yaml))
        self.export_status.setText(f'Exported {sum(counts)} frames: {counts[0]} train, {counts[1]} validation, {counts[2]} test. Dataset: {yaml.parent}')

    def _export_finished(self):
        self.export_worker.deleteLater()
        self.export_worker = None
        self.export_button.setEnabled(True)

