APP_STYLE = """
/* Base & Structural Elements */
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QScrollBar:vertical { background-color: #F4F7FA; }
QWidget { font-family: "Segoe UI"; font-size: 13px; color: #1F2937; }
QScrollArea { border: none; }
QLabel { color: #263746; }
QToolTip { background-color: #263746; color: #FFFFFF; border: none; padding: 5px; }

/* Group Box */
QGroupBox { background-color: #FFFFFF; border: 1px solid #D7E0E8; border-radius: 8px; margin-top: 10px; padding: 12px 8px 8px 8px; font-weight: 600; color: #003B70; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 5px; color: #003B70; font-weight: 700; }

/* Inputs & Combos */
QLineEdit, QComboBox { min-height: 30px; background-color: #FFFFFF; border: 1px solid #C5D0DA; border-radius: 5px; padding-left: 8px; color: #263746; }
QLineEdit { padding-right: 8px; }
QLineEdit:hover, QComboBox:hover { border: 1px solid #8EAAC0; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #0072CE; }
QLineEdit:read-only { background-color: #FBFCFD; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox QAbstractItemView { background-color: #FFFFFF; border: 1px solid #C5D0DA; selection-background-color: #0072CE; selection-color: #FFFFFF; }

/* Standard Buttons */
QPushButton { min-height: 30px; background-color: #FFFFFF; color: #263746; border: 1px solid #BDC8D2; border-radius: 5px; padding: 4px 12px; }
QPushButton:hover { background-color: #EDF5FB; border: 1px solid #7FA7C9; }
QPushButton:pressed { background-color: #DCECF7; }
QPushButton:disabled { background-color: #F2F4F6; color: #A0A8AF; border: 1px solid #D7DCE1; }

/* Primary & Action Buttons */
QPushButton#primaryButton { background-color: #0072CE; color: #FFFFFF; border: none; border-radius: 6px; font-weight: 700; }
QPushButton#primaryButton:hover, QPushButton#runButton:hover { background-color: #005FAE; }
QPushButton#primaryButton:pressed, QPushButton#runButton:pressed { background-color: #004B87; }

QPushButton#runButton { min-height: 44px; background-color: #0072CE; color: #FFFFFF; border: none; border-radius: 7px; font-size: 14px; font-weight: 700; }
QPushButton#runButton:disabled { background-color: #9DBCD5; color: #EEF3F7; }

QPushButton#cancelButton { min-height: 44px; background-color: #FFFFFF; color: #B42318; border: 1px solid #D92D20; border-radius: 7px; font-weight: 600; }
QPushButton#cancelButton:hover { background-color: #FFF1F0; }
QPushButton#cancelButton:pressed { background-color: #FFE1DE; }
QPushButton#cancelButton:disabled { background-color: #F4F5F6; color: #AAAAAA; border: 1px solid #D5D9DD; }

QPushButton#backButton { background-color: transparent; color: #005EA8; border: 1px solid #AABFCE; border-radius: 5px; font-weight: 600; }
QPushButton#backButton:hover { background-color: #E8F3FA; border: 1px solid #0072CE; }

/* Progress Bar */
QProgressBar { min-height: 25px; background-color: #E6EBF0; border: none; border-radius: 6px; text-align: center; color: #263746; font-weight: 600; }
QProgressBar::chunk { background-color: #0072CE; border-radius: 6px; }

/* Sliders */
QSlider::groove:horizontal, QSlider::add-page:horizontal { height: 6px; background-color: #D7DEE5; border-radius: 3px; }
QSlider::sub-page:horizontal { background-color: #0072CE; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0px; background-color: #FFFFFF; border: 2px solid #0072CE; border-radius: 8px; }
QSlider::handle:horizontal:hover { background-color: #E8F4FC; border: 2px solid #005EA8; }

/* Status Console */
QPlainTextEdit#statusConsole { background-color: #111827; color: #D9E3EC; border: 1px solid #29384A; border-radius: 7px; padding: 8px; selection-background-color: #0072CE; }
QPlainTextEdit#statusConsole:focus { border: 1px solid #3C526A; }

/* Project List */
QListWidget { background-color: #FFFFFF; border: 1px solid #CBD5DE; border-radius: 6px; padding: 5px; outline: none; }
QListWidget::item { min-height: 31px; padding: 5px 8px; border-radius: 4px; }
QListWidget::item:hover { background-color: #EDF5FB; }
QListWidget::item:selected { background-color: #0072CE; color: #FFFFFF; }

/* Scrollbar */
QScrollBar:vertical { width: 10px; margin: 0px; }
QScrollBar::handle:vertical { background-color: #B7C2CC; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background-color: #8FA0AE; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""
