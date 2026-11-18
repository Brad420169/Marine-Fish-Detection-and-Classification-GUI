"""Combine multiple training datasets: the panel at the top of the Train a Model page."""
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QProgressBar, QPushButton, QVBoxLayout)

from dataset_combine import CombineWorker
from widgets import CheckableComboBox
from yolo_export import dataset_label, unique_path


class CombineDatasetsPanel(QGroupBox):
    """Tick two or more of the project's exported datasets and merge them into one."""
    combined = pyqtSignal(str)  # the new data.yaml

    def __init__(self, parent=None):
        super().__init__('Combine multiple training datasets', parent)
        self.worker = None
        column = QVBoxLayout(self)
        note = QLabel('Annotated several videos? Tick their exported datasets to merge them into one new dataset, '
                      'so the model learns from all of them. Each keeps its own training / validation / test split, '
                      'and classes with the same name are merged.')
        note.setWordWrap(True); column.addWidget(note)
        self.choice = CheckableComboBox('Click to tick the datasets to combine')
        self.choice.setToolTip('Exported datasets in this project. Tick two or more.')
        self.choice.changed.connect(self._choice_changed)
        column.addWidget(self.choice)
        row = QHBoxLayout()
        self.destination = QLineEdit(); self.destination.setPlaceholderText('New combined dataset folder')
        row.addWidget(self.destination, 1)
        browse = QPushButton('Choose parent folder'); browse.clicked.connect(self._choose_destination)
        row.addWidget(browse); column.addLayout(row)
        buttons = QHBoxLayout()
        self.button = QPushButton('Combine Datasets')
        self.button.setObjectName('primaryButton')
        self.button.clicked.connect(self.combine)
        buttons.addWidget(self.button, 1)
        self.stop = QPushButton('Stop'); self.stop.setEnabled(False)
        self.stop.clicked.connect(lambda: self.worker and self.worker.requestInterruption())
        buttons.addWidget(self.stop)
        column.addLayout(buttons)
        self.progress = QProgressBar(); self.progress.setFormat('%v of %m images (%p%)')
        self.progress.hide(); column.addWidget(self.progress)
        self.status = QLabel(); self.status.setWordWrap(True); column.addWidget(self.status)
        self._choice_changed()

    def set_datasets(self, items):
        """The project's exported datasets, as (name, data.yaml path) pairs."""
        if not self.worker:
            self.choice.set_items([(dataset_label(path), path) for _, path in items if Path(path).is_file()])

    def _choice_changed(self):
        ticked = self.choice.checked_data()
        self.button.setEnabled(len(ticked) >= 2 and not self.worker)
        self.button.setText(f'Combine {len(ticked)} Datasets' if len(ticked) >= 2 else 'Combine Datasets')
        if ticked and not self.destination.text().strip():
            self.destination.setText(str(unique_path(Path(ticked[0]).parent.parent / 'combined_yolo_dataset')))

    def _choose_destination(self):
        parent = QFileDialog.getExistingDirectory(self, 'Choose parent folder for the combined dataset')
        if parent: self.destination.setText(str(unique_path(Path(parent) / 'combined_yolo_dataset')))

    def combine(self):
        ticked = self.choice.checked_data()
        destination = self.destination.text().strip()
        if len(ticked) < 2:
            QMessageBox.warning(self, 'Choose datasets', 'Tick at least two datasets to combine.'); return
        if not destination:
            QMessageBox.warning(self, 'Destination missing', 'Choose a folder for the combined dataset.'); return
        if self.worker:
            return
        self.worker = CombineWorker(ticked, destination, self)
        self.worker.progress.connect(self._progressed)
        self.worker.succeeded.connect(self._combined)
        self.worker.failed.connect(lambda error: self.status.setText(f'Not combined: {error}'))
        self.worker.finished.connect(self._finished)
        self.progress.setRange(0, 0)  # busy until the first image is copied
        self.progress.show()
        self.status.setText(f'Checking and combining {len(ticked)} datasets…')
        for widget in (self.button, self.choice):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker.start()

    def _progressed(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _combined(self, path, counts, names):
        self.status.setText(
            f'Combined {sum(counts.values())} images: {counts["train"]} train, {counts["val"]} validation, '
            f'{counts["test"]} test, with {len(names)} classes ({", ".join(names)}). It is loaded for training below. '
            f'Dataset: {Path(path).parent}')
        self.combined.emit(path)

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.progress.hide()
        self.choice.setEnabled(True)
        self.stop.setEnabled(False)
        self._choice_changed()
