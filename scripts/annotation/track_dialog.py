"""The "Follow this fish with SAM" question asked before SAM video tracking.

It shows how much video there is before and after the frame, which device SAM runs on
(CPU or GPU) and how fast, and a worst-case time that updates as you type. Icons come
from assets/*.svg (via ui.icons), so the dialog looks the same on Windows, macOS and
Linux. The measured speed is remembered between sessions with QSettings (the registry
on Windows, a preferences file on macOS, a config file on Linux).
"""
import platform
import re
import subprocess
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                             QVBoxLayout)

from ui.icons import icon_label
from core.paths import ROOT_DIR

TYPICAL_SECONDS = {'CPU': 1.0, 'GPU': 0.15}  # per frame, until this computer has been measured
_device = None


def _cpu_name():
    """The processor's name on Windows, macOS or Linux, or '' if it cannot be read."""
    try:
        system = platform.system()
        if system == 'Darwin':
            return subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
        if system == 'Linux':
            for line in Path('/proc/cpuinfo').read_text(encoding='utf-8', errors='ignore').splitlines():
                if line.startswith('model name'):
                    return line.split(':', 1)[1].strip()
        return platform.processor().strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ''


def sam_device():
    """('CPU' or 'GPU', a readable name) for the device SAM runs on. Checked once, then cached."""
    global _device
    if _device is None:
        try:
            from core.inference_device import select_device
            device, description = select_device()
        except Exception:
            device, description = 'cpu', ''
        if device.startswith('cuda'):
            name = description.split('—', 1)[-1].split('(')[0].strip() or 'NVIDIA GPU'
            _device = ('GPU', name)
        else:
            _device = ('CPU', tidy_cpu_name(_cpu_name()))
    return _device


def tidy_cpu_name(name):
    """'11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz' -> '11th Gen Intel Core i5-1135G7'."""
    name = re.sub(r'\((R|TM|tm|r)\)|®|™|\s+CPU\b|\s*@.*$', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def _settings():
    return QSettings('QUT', 'MarineFishDetector')


def remembered_speed(kind):
    """Seconds per frame SAM took on this computer last time, or None if never measured."""
    value = _settings().value(f'sam_video/seconds_per_frame/{kind}')
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def remember_speed(kind, seconds):
    _settings().setValue(f'sam_video/seconds_per_frame/{kind}', round(float(seconds), 3))


def duration_text(seconds):
    seconds = round(seconds)
    if seconds < 60:
        return f'{max(seconds, 1)} s'
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} min {seconds:02d} s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h {minutes:02d} min'


def _img(name, size=16):
    url = (ROOT_DIR / 'assets' / f'{name}.svg').as_uri()
    return f'<img src="{url}" width="{size}" height="{size}" style="vertical-align: middle;">'


class TrackDialog(QDialog):
    """Ask how far SAM may look, at most, in each direction."""

    def __init__(self, parent, before, after, fps, fish=1, default=30.0, device=('CPU', ''),
                 seconds_per_frame=None):
        super().__init__(parent)
        self.before, self.after, self.fps, self.fish = before, after, fps, fish
        self.kind, self.device_name = device
        self.measured = seconds_per_frame is not None
        self.seconds_per_frame = seconds_per_frame or TYPICAL_SECONDS.get(self.kind, 1.0)
        self.setWindowTitle('Track with SAM')
        self.setMinimumWidth(640)  # wide enough that most lines fit without wrapping
        column = QVBoxLayout(self)
        column.setSpacing(10)
        self._column = column

        what = f'these {fish} fish' if fish > 1 else 'this fish'
        heading = QLabel(f'Follow {what} with SAM')
        heading.setStyleSheet('font-size: 16px; font-weight: bold;')
        column.addWidget(heading)
        explain = QLabel(('SAM follows each fish' if fish > 1 else 'SAM follows the fish') +
                         ' back to where it first appears, then forward until it leaves the screen, '
                         'and stops by itself.')
        explain.setWordWrap(True)
        column.addWidget(explain)

        self.timeline = QLabel(
            f'{_img("video")}&nbsp; Video around this frame: &nbsp;{_img("arrow_left", 14)}&nbsp;'
            f'<b>{before:.1f} s before</b> &nbsp;·&nbsp; <b>{after:.1f} s after</b>&nbsp;{_img("arrow_right", 14)}')
        self.timeline.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.timeline)

        row = QHBoxLayout()
        label = QLabel('<b>Stop looking after:</b>')
        row.addWidget(label)
        self.seconds = QDoubleSpinBox()
        self.seconds.setDecimals(1)
        self.seconds.setSingleStep(0.5)
        longest = max(before, after, 0.1)
        self.seconds.setRange(0.1, longest)
        self.seconds.setValue(min(default, longest))
        self.seconds.setSuffix(' s')
        self.seconds.setMinimumWidth(110)
        self.seconds.setAccessibleName('Seconds of video to look through in each direction, at most')
        label.setBuddy(self.seconds)
        row.addWidget(self.seconds)
        row.addWidget(QLabel('of video in each direction'))
        row.addStretch()
        column.addLayout(row)

        self.device_line = self._icon_row(column, 'chip')
        self.estimate_line = self._icon_row(column, 'clock')
        self.seconds.valueChanged.connect(self._update)
        self.seconds.valueChanged.connect(lambda: QTimer.singleShot(0, self.fit_to_content))  # the estimate can wrap
        self._update()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Track')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)
        self.seconds.setFocus()
        self.seconds.selectAll()

    def showEvent(self, event):
        super().showEvent(event)
        # Wrapped labels only know their height once their width is set, so size the
        # window to its content after it is laid out (otherwise the last lines are clipped).
        QTimer.singleShot(0, self.fit_to_content)

    def fit_to_content(self):
        self._column.activate()
        height = self._column.totalHeightForWidth(self.width()) if self._column.hasHeightForWidth() \
            else self.sizeHint().height()
        height = max(height, self.sizeHint().height())
        self.setMinimumHeight(height)
        if self.height() < height:
            self.resize(self.width(), height)

    def _icon_row(self, column, asset):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(icon_label(asset, 18), 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel()
        text.setWordWrap(True)
        row.addWidget(text, 1)
        column.addLayout(row)
        return text

    def frames_to_check(self):
        """Frames SAM may look at: each direction is capped by the video that is there."""
        limit = self.seconds.value()
        return (round(min(limit, self.before) * self.fps) + round(min(limit, self.after) * self.fps)) * self.fish

    def _update(self):
        name = f' ({self.device_name})' if self.device_name else ''
        if self.measured:
            speed = f'about {self.seconds_per_frame:.1f} s per frame on this computer'
        else:
            speed = f'speed not measured yet (usually about {self.seconds_per_frame:g} s per frame)'
        self.device_line.setText(f'<b>Running on: {self.kind}</b>{name} · {speed}')
        worst = self.frames_to_check() * self.seconds_per_frame
        limit = self.seconds.value()
        reach = (f'Checks up to {min(limit, self.before):.1f} s back and {min(limit, self.after):.1f} s forward'
                 + (f' for each of {self.fish} fish' if self.fish > 1 else '') + '. ')
        if self.measured:
            self.estimate_line.setText(f'{reach}<b>Up to about {duration_text(worst)}</b>, '
                                       'less if the fish leaves early or already has boxes.')
        else:
            self.estimate_line.setText(f'{reach}<b>Roughly {duration_text(worst)} at most.</b> '
                                       'The first run also loads SAM (a few seconds).')

    @classmethod
    def ask(cls, parent, before, after, fps, fish=1, default=30.0, device=('CPU', ''), seconds_per_frame=None):
        """Show the dialog. Returns (seconds each way, accepted)."""
        dialog = cls(parent, before, after, fps, fish, default, device, seconds_per_frame)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.seconds.value(), accepted
