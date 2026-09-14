"""Ocean palette and shared desktop styles."""
APP_STYLE = """
QWidget { font-family: "Segoe UI", "DejaVu Sans"; font-size: 13px; color: #e8f5ff; }
QMainWindow, QDialog, QMessageBox { background: #031e30; }
QWidget#oceanHeader { background: rgba(0, 22, 39, 205); border-bottom: 1px solid #126184; }
QWidget#oceanSidebar { background: rgba(0, 23, 40, 180); border: 1px solid #126184; border-radius: 12px; }
QLabel { background: transparent; }
QLabel#brandTitle { font-size: 21px; font-weight: 700; color: #ffffff; }
QLabel#pageTitle { font-size: 28px; font-weight: 700; color: #ffffff; }
QLabel#muted { color: #b1d2e7; }
QLabel#tagline { color: #45d8f6; font-size: 19px; font-weight: 700; }
QScrollArea, QStackedWidget, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QGroupBox { background: rgba(3, 36, 56, 220); border: 1px solid #14749a; border-radius: 10px; margin-top: 0; padding: 34px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: padding; subcontrol-position: top left; top: 10px; left: 12px; padding: 0 6px; color: #ecf8ff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 30px; background: #153e58; border: 1px solid #537e98; border-radius: 6px; padding: 2px 8px; color: #f2f9ff; selection-background-color: #087eb9; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #27c6f7; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #0a3049; color: #effaff; selection-background-color: #0079bd; selection-color: white; }
QPushButton { min-height: 30px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #143e58,stop:1 #092a41); color: #effaff; border: 1px solid #267aa0; border-radius: 7px; padding: 5px 14px; }
QPushButton:hover { background: #155376; border-color: #35cfff; }
QPushButton:pressed { background: #006794; }
QPushButton:disabled { background: #0a283b; color: #64859b; border-color: #24485d; }
QPushButton#primaryButton, QPushButton#runButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #00a9f2,stop:1 #0065c5); border: 1px solid #19c0ff; font-weight: 700; color: white; }
QPushButton#primaryButton:hover, QPushButton#runButton:hover { background: #008dea; }
QPushButton#runButton, QPushButton#cancelButton { min-height: 42px; font-size: 15px; font-weight: 700; }
QPushButton#runButton:disabled { background: #12405a; color: #709caf; border-color: #306078; }
QPushButton#confirmButton { background: #00bce5; color: #002037; border-color: #5ceaff; font-weight: 700; }
QPushButton#navButton { text-align: left; background: transparent; border: none; min-height: 42px; padding: 8px 14px; font-size: 14px; }
QPushButton#navButton:hover { background: #0c4262; }
QPushButton#navButton:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #008dcc,stop:1 #00539b); border-left: 4px solid #14d5f6; font-weight: 700; }
QPushButton#navButton:disabled { color: #688a9f; }
QProgressBar { min-height: 25px; background: #294e69; border: none; border-radius: 6px; text-align: center; color: white; font-weight: 600; }
QProgressBar::chunk { background: #009ae8; border-radius: 6px; }
QSlider::groove:horizontal { height: 6px; background: #021d30; border: 1px solid #11658b; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #00aff1; border-radius: 3px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #edfbff; border: 2px solid #07b6ff; border-radius: 8px; }
QPlainTextEdit { background: #021522; color: #b8d9ed; border: 1px solid #3c6c88; border-radius: 8px; padding: 8px; }
QListWidget, QTableWidget { background: #052338; alternate-background-color: #0b3047; border: 1px solid #176789; border-radius: 6px; gridline-color: #154762; color: #e8f5ff; selection-background-color: #086996; selection-color: white; }
QTableWidget QLabel { padding: 2px 7px; }
QHeaderView { background: #052338; }
QHeaderView::section { background: #0c354f; color: #cce9fa; border: none; border-right: 1px solid #19516b; border-bottom: 1px solid #247092; padding: 7px; }
QTableCornerButton::section { background: #0c354f; border: none; }
QListWidget::item { padding: 9px; border-radius: 5px; }
QListWidget#projectList::item { min-height: 48px; padding: 4px; }
QListWidget::item:selected { background: #006fba; }
/* File and folder browsers use model views, not QListWidget/QTableWidget. */
QFileDialog QAbstractItemView { background: #052338; alternate-background-color: #0b3047; color: #e8f5ff; border: 1px solid #176789; selection-background-color: #086996; selection-color: #ffffff; }
QFileDialog QAbstractItemView::item:hover { background: #0c4262; }
QFileDialog QAbstractItemView::item:selected { background: #086996; color: #ffffff; }
QFileDialog QToolButton { background: #092a41; color: #effaff; border: 1px solid #267aa0; border-radius: 4px; padding: 4px; }
QFileDialog QToolButton:hover { background: #155376; border-color: #35cfff; }
QFileDialog QToolButton:checked { background: #086996; border-color: #35cfff; }
QFileDialog QToolButton:disabled { color: #64859b; border-color: #24485d; }
QCheckBox { spacing: 8px; color: #d3edfc; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 4px; border: 1px solid #70a3bf; background: #082b40; }
QCheckBox::indicator:checked { background: #082b40; border: 1px solid #70a3bf; }
QScrollBar:vertical { background: transparent; width: 9px; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle { background: #28617e; border-radius: 4px; min-height: 25px; min-width: 25px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #103c54; color: #ffffff; padding: 6px; border: 1px solid #19a5d5; }

/* Projects page */
QLabel#cardTitle { font-size: 15px; font-weight: 700; color: #ffffff; }
QLabel#cardArrow { color: #45d8f6; font-size: 17px; font-weight: 700; }
QFrame#actionCard { background: rgba(6, 44, 68, 225); border: 1px solid #14749a; border-radius: 10px; }
QFrame#actionCard:hover { border: 1px solid #35cfff; background: rgba(10, 62, 93, 235); }
QLabel#actionBadge { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #00a9f2,stop:1 #0065c5);
                     border-radius: 8px; color: white; font-size: 19px; font-weight: 700; }
QFrame#projectCard { background: rgba(5, 38, 59, 220); border: 1px solid #14749a; border-radius: 10px; }
QFrame#projectCard:hover { border: 1px solid #35cfff; }
QFrame#projectCard[selected="yes"] { border: 1px solid #35cfff; background: rgba(8, 62, 94, 235); }
QLabel#projectThumb { background: #05263b; border: 1px solid #176789; border-radius: 6px; color: #7ea7c0; }
QGroupBox#recentBox { padding: 10px; }
QPushButton#linkButton { background: transparent; border: none; color: #45d8f6; font-weight: 700; padding: 2px 4px; }
QPushButton#linkButton:hover { color: #9aeaff; }
QPushButton#menuButton { font-size: 15px; font-weight: 700; min-height: 26px; padding: 2px; }
"""


from paths import ROOT_DIR

APP_STYLE += """
QComboBox::down-arrow { image: url(DOWN_ICON); width: 10px; height: 6px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(UP_ICON); width: 10px; height: 6px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(DOWN_ICON); width: 10px; height: 6px; }
""".replace('DOWN_ICON', (ROOT_DIR/'assets/chevron_down.svg').as_posix()).replace('UP_ICON', (ROOT_DIR/'assets/chevron_up.svg').as_posix())

APP_STYLE += "QCheckBox::indicator:checked { image: url(" + (ROOT_DIR/"assets/check_blue.svg").as_posix() + "); }"
