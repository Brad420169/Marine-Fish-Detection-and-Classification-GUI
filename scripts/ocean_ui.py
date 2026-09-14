"""Shared ocean backdrop and application navigation chrome."""
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from paths import ROOT_DIR


class OceanBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._background = QPixmap(str(ROOT_DIR/'assets'/'app_background.png'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#021b2c'))
        if not self._background.isNull():
            scale = max(self.width()/self._background.width(), self.height()/self._background.height())
            width, height = self._background.width()*scale, self._background.height()*scale
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF((self.width()-width)/2, (self.height()-height)/2, width, height),
                               self._background, QRectF(self._background.rect()))
            painter.fillRect(self.rect(), QColor(0,16,29,150))


class OceanShell(OceanBackground):
    def __init__(self, pages, project, navigate, parent=None):
        """`project` may be None until one is opened from the Projects page."""
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        header = QWidget(); header.setObjectName('oceanHeader')
        row = QHBoxLayout(header); row.setContentsMargins(22,12,24,12)
        logo = QLabel()
        icon = QPixmap(str(ROOT_DIR/'assets'/'icon.png'))
        logo.setPixmap(icon.scaled(46,46,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        row.addWidget(logo)
        brand = QVBoxLayout(); brand.setSpacing(2)
        title = QLabel('Marine Fish Detector'); title.setObjectName('brandTitle')
        subtitle = QLabel('Automated Underwater Fish Analysis'); subtitle.setObjectName('muted')
        brand.addWidget(title);brand.addWidget(subtitle);row.addLayout(brand);row.addStretch()
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setObjectName('muted');row.addWidget(self.details)
        self.set_project(project)
        outer.addWidget(header)
        body = QHBoxLayout(); body.setContentsMargins(12,14,12,12);body.setSpacing(10)
        sidebar = QWidget();sidebar.setObjectName('oceanSidebar');sidebar.setFixedWidth(220)
        nav = QVBoxLayout(sidebar);nav.setContentsMargins(8,16,8,18);nav.setSpacing(8)
        self.buttons = {}
        for key, label in [('projects','▱   Projects'),('detection','▶   Run Detection'),
                           ('results','▥   Results'),('review','⌕   Review Detections')]:
            button = QPushButton(label);button.setObjectName('navButton');button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, target=key:navigate(target))
            nav.addWidget(button);self.buttons[key]=button
        nav.addStretch()
        tagline = QLabel('Explore.\nDetect.\nProtect.');tagline.setObjectName('tagline')
        nav.addWidget(tagline)
        body.addWidget(sidebar);body.addWidget(pages,1);outer.addLayout(body,1)

    def set_project(self, project):
        self.details.setText(f'Project: {project.name}\nCreated: {project.created}' if project
                             else 'No project open\nChoose one from Projects')
