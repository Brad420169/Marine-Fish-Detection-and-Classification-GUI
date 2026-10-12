"""Live validation curves for the active YOLO training run."""
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, PercentFormatter
from PyQt6.QtWidgets import QGroupBox, QLabel, QProgressBar, QVBoxLayout

from charts import ScrollPassthroughCanvas, _theme_axes
from training_metrics import METRICS


class TrainingDashboard(QGroupBox):
    def __init__(self, parent=None):
        super().__init__('Live training · validation metrics', parent)
        layout = QVBoxLayout(self)
        self.status = QLabel('Ready — start training to see live learning curves.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        note = QLabel('Updated after each epoch’s validation. Detection uses mAP rather than conventional accuracy; higher scores are better. Missing values remain gaps.')
        note.setObjectName('muted'); note.setWordWrap(True)
        layout.addWidget(note)
        self.figure = Figure(figsize=(9, 5), facecolor='#062438')
        self.figure.subplots_adjust(left=.09, right=.96, bottom=.10, top=.90, hspace=.65, wspace=.32)
        self.canvas = ScrollPassthroughCanvas(self.figure)
        self.canvas.setMinimumHeight(440)
        self.canvas.setAccessibleName('Live precision, recall, mAP at 50 and mAP at 50 to 95 by epoch')
        layout.addWidget(self.canvas)
        self.plots = []
        for index, (key, title, color) in enumerate(METRICS):
            axis = self.figure.add_subplot(2, 2, index + 1)
            _theme_axes(axis)
            axis.set_ylim(0, 1.05)
            axis.set_xlim(1, 2)
            axis.set_xlabel('Epoch', fontsize=9)
            axis.yaxis.set_major_formatter(PercentFormatter(1))
            axis.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
            axis.grid(axis='y', color='#31617b', alpha=.45)
            line, = axis.plot([], [], color=color, linewidth=2, marker='o', markersize=3)
            axis.set_title(f'{title}  —', loc='left', fontsize=12, color='#eef9ff')
            self.plots.append((key, title, axis, line))
        self.history = {}
        self.total = 0

    def reset(self, total):
        self.history.clear()
        self.total = total
        self.progress.setRange(0, total)
        self.progress.setFormat(f'Epoch 0 / {total}')
        self.progress.setValue(0)
        self.status.setText('Training — waiting for the first validation results…')
        self._draw()

    def update_epoch(self, epoch, values):
        self.history[epoch] = values
        self.progress.setValue(min(epoch, self.total))
        self.progress.setFormat(f'Epoch {epoch} / {self.total}')
        self.status.setText(f'Training · epoch {epoch} validated')
        self._draw()

    def finish(self, success):
        last = max(self.history, default=0)
        self.status.setText(f'{"Completed" if success else "Stopped or failed"} · {last} validated epochs. ' +
                            ('Curves retained for review.' if last else 'No validation metrics received.'))

    def _draw(self):
        epochs = sorted(self.history)
        for key, title, axis, line in self.plots:
            values = [self.history[e].get(key, float('nan')) for e in epochs]
            line.set_data(epochs, values)
            axis.set_xlim(1, max(2, max(epochs, default=1)))
            latest = self.history[epochs[-1]].get(key) if epochs else None
            axis.set_title(f'{title}  {latest:.1%}' if latest is not None else f'{title}  —', loc='left', fontsize=12, color='#eef9ff')
        self.canvas.draw_idle()
