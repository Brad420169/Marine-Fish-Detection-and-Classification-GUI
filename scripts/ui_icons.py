"""Bundled, font-independent UI icons. Names correspond to assets/<name>.svg."""
from html import escape

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel

from paths import ROOT_DIR


def icon(name):
    return QIcon(str(ROOT_DIR / 'assets' / f'{name}.svg'))


def icon_label(name, size=18):
    label = QLabel()
    label.setPixmap(icon(name).pixmap(QSize(size, size), label.devicePixelRatioF()))
    return label


def icon_text(name, text, size=16):
    """Rich text for QLabel; escape filenames and other user-supplied text."""
    url = (ROOT_DIR / 'assets' / f'{name}.svg').as_uri()
    return (f'<img src="{escape(url, quote=True)}" width="{size}" height="{size}" '
            f'style="vertical-align: middle;">&nbsp; {escape(text)}')
