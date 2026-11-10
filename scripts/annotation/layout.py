"""Builds the annotation page's widgets and connects them to the page's parts.

Widgets are stored on the page (page.table, page.sam_class, ...) because several
parts read them. The parts (page.sam, page.tracking, page.editor, page.nav) must
exist before build() runs.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QTableWidget, QTabWidget, QVBoxLayout, QWidget)

from ui_icons import icon

from annotation import help_text
from annotation.canvas import ImageLabel

ACCENT = '#45d8f6'
FRAME_STYLE = 'background: #021b2b; color: #b1d5e9; border: 1px solid #24779b; border-radius: 7px;'


def button(layout, text, action):
    widget = QPushButton(text)
    widget.clicked.connect(action)
    layout.addWidget(widget)
    return widget


def build(page):
    root = QVBoxLayout(page)
    root.setContentsMargins(16, 8, 16, 12)
    root.setSpacing(8)
    root.addLayout(_header(page))

    images = QHBoxLayout()
    images.addWidget(_annotations_panel(page))
    page.image = _canvas(page)
    images.addWidget(page.image, 1)
    images.addWidget(_preview_panel(page))
    root.addLayout(images, 1)

    review_panels = QHBoxLayout()
    review_panels.addWidget(_detections_panel(page), 3)
    review_panels.addWidget(_tips_panel(), 2)
    root.addLayout(review_panels)
    page.status = QLabel()
    page.status.setWordWrap(True)
    root.addWidget(page.status)
    root.addLayout(_navigation(page))
    QShortcut(QKeySequence('Alt+Right'), page, activated=lambda: page.nav.go(1))
    QShortcut(QKeySequence('Alt+Left'), page, activated=lambda: page.nav.go(-1))


def _header(page):
    """One row: box legend | title over the frame counter | Play and Export."""
    bar = QGridLayout()
    bar.setContentsMargins(0, 0, 0, 0)
    bar.setVerticalSpacing(0)
    page.box_legend = QLabel(help_text.BOX_LEGEND)
    page.box_legend.setToolTip('Box colours show where each box came from.')
    bar.addWidget(page.box_legend, 0, 0, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    title = QLabel('Annotate Data')
    title.setObjectName('pageTitle')
    title.setToolTip(help_text.SHORTCUTS)
    bar.addWidget(title, 0, 1, Qt.AlignmentFlag.AlignCenter)
    page.progress = QLabel()
    page.progress.setTextFormat(Qt.TextFormat.RichText)
    page.progress.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                          | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
    page.progress.linkActivated.connect(page.nav.jump)
    page.progress.setToolTip('Click the frame number to jump to any frame.')
    bar.addWidget(page.progress, 1, 1, Qt.AlignmentFlag.AlignCenter)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    actions.addStretch()
    page.play_button = button(actions, 'Play', page.nav.toggle)
    page.play_button.setIcon(icon('play'))
    button(actions, 'Export dataset', page.export_requested.emit)
    bar.addLayout(actions, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)
    bar.setColumnStretch(0, 1)
    bar.setColumnStretch(2, 1)
    return bar


def _annotations_panel(page):
    panel = QGroupBox('Annotations')
    panel.setFixedWidth(210)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(10, 18, 10, 10)
    for number, title, detail in help_text.STEPS:
        step = QHBoxLayout()
        step.setSpacing(8)
        badge = QLabel(number)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(24, 24)
        badge.setStyleSheet(f'background: #12465b; color: {ACCENT}; border-radius: 12px; font-weight: bold;')
        step.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(f'<b>{title}</b>')
        text.setToolTip(detail)
        text.setWordWrap(True)
        step.addWidget(text, 1)
        layout.addLayout(step)
    layout.addSpacing(4)

    page.sam_preload_check = QCheckBox('Preload nearby frames')
    page.sam_preload_check.setChecked(True)
    page.sam_preload_check.setToolTip('Prepare this frame and the next two for faster SAM clicks. '
                                      'Turn off to save CPU and battery.')
    page.sam_preload_check.toggled.connect(page.sam.schedule_preload)
    layout.addWidget(page.sam_preload_check)
    page.sam_ready = QLabel('SAM loads on demand')
    page.sam_ready.setWordWrap(True)
    layout.addWidget(page.sam_ready)
    page.sam_panel = QWidget(panel)
    sam_layout = QVBoxLayout(page.sam_panel)
    sam_layout.setContentsMargins(0, 0, 0, 0)
    page.sam_class = QLineEdit()
    page.sam_class.setPlaceholderText('Active fish species, Enter')
    page.sam_class.setAccessibleName('SAM annotation class name')
    page.sam_class.returnPressed.connect(page.save_named_boxes)
    sam_layout.addWidget(page.sam_class)
    layout.addWidget(page.sam_panel)
    page.sam_panel.hide()

    page.annotations = QTableWidget(0, 2)
    page.annotations.setHorizontalHeaderLabels(['Species', ''])
    page.annotations.setColumnWidth(0, 115)
    page.annotations.setColumnWidth(1, 38)
    page.annotations.verticalHeader().hide()
    page.annotations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    page.annotations.cellClicked.connect(page.select_annotation)
    page.annotations.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(page.annotations, 1)
    page.multi_button = QPushButton('Track multiple fish')
    page.multi_button.setIcon(icon('play'))
    page.multi_button.setToolTip('Click this, click each fish you want, then press Enter. You are asked for how '
                                 'many seconds once, and SAM tracks the fish one after another.')
    page.multi_button.setStyleSheet('QPushButton { padding: 6px 4px; }')  # the panel is narrow
    page.multi_button.clicked.connect(lambda: page.tracking.multi_clicked())
    layout.addWidget(page.multi_button)
    layout.addWidget(_track_prompt(page, panel))
    return panel


def _track_prompt(page, parent):
    page.track_prompt = QWidget(parent)
    page.track_prompt.setStyleSheet('background: #073047; color: white; border-radius: 4px;')
    column = QVBoxLayout(page.track_prompt)
    column.setContentsMargins(5, 2, 5, 4)
    column.setSpacing(4)
    row = QHBoxLayout()
    column.addLayout(row)
    row.addWidget(QLabel('Track?'))
    page.track_yes = button(row, '', lambda: page.tracking.set_saved(True))
    page.track_no = button(row, '', lambda: page.tracking.set_saved(False))
    for widget, name, asset in ((page.track_yes, 'Track annotation', 'check'),
                                (page.track_no, 'Do not track', 'close')):
        widget.setIcon(icon(asset))
        widget.setAccessibleName(name)
        widget.setToolTip(name)
        widget.setCheckable(True)
        widget.setFixedSize(30, 28)
        widget.setStyleSheet('QPushButton { padding: 0; } QPushButton:checked { border: 2px solid #46d8ff; }')
    # Shown for a tracked fish with unchecked boxes: accepts all of them at once.
    page.confirm_track = QPushButton('Confirm track')
    page.confirm_track.setIcon(icon('check'))
    page.confirm_track.setToolTip("Accept all of this fish's unchecked boxes and finish its track. "
                                  'Fix any wrong boxes first.')
    page.confirm_track.setStyleSheet('QPushButton { background: #1c7a5e; color: white; padding: 3px 8px; }')
    page.confirm_track.clicked.connect(lambda: page.tracking.confirm_prompted())
    column.addWidget(page.confirm_track)
    page.track_prompt.hide()
    return page.track_prompt


def _canvas(page):
    image = ImageLabel()
    image.setMinimumSize(360, 220)
    image.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    image.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    image.setStyleSheet(FRAME_STYLE)
    image.on_interact = page.nav.pause
    image.on_click = page.editor.select_at
    image.on_draw = page.editor.draw
    image.on_exclude = page.editor.exclude_area
    image.on_sam = page.sam.click
    image.on_zoom = page.update_controls
    image.on_resize_start = page.editor.begin_resize
    image.on_drag_start = page.editor.begin_drag
    image.on_drag_move = page.editor.move
    image.on_drag_end = page.editor.end_drag
    return image


def _preview_panel(page):
    page.crop = QLabel('Select a fish to preview')
    page.crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
    page.crop.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    page.crop.setStyleSheet(FRAME_STYLE)
    preview = QGroupBox('Zoom / Preview')
    preview.setFixedWidth(210)
    layout = QVBoxLayout(preview)
    layout.addWidget(page.crop, 1)
    hint = QLabel(help_text.PREVIEW_HINT)
    hint.setWordWrap(True)
    layout.addWidget(hint)
    button(layout, 'Full resolution', page.zoom)
    return preview


def _detections_panel(page):
    page.table = QTableWidget(0, 4)
    page.table.setHorizontalHeaderLabels(['Original AI prediction', 'Confidence', 'Reviewed species', 'Decision'])
    page.table.setToolTip('Resolved rows are hidden. Click a box on the image to reopen its species editor.')
    page.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    page.table.verticalHeader().setDefaultSectionSize(36)
    page.table.setFixedHeight(140)
    page.table.cellClicked.connect(page.select_row)
    page.table.horizontalHeader().setStretchLastSection(True)
    panel = QGroupBox('Detections in this frame')
    QVBoxLayout(panel).addWidget(page.table)
    return panel


def _tips_panel():
    panel = QGroupBox('Annotation tips')
    tabs = QTabWidget()
    tabs.setStyleSheet('QTabWidget::pane { border: 0; }'
                       'QTabBar::tab { padding: 4px 12px; color: #b1d2e7; background: #082c40;'
                       ' border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }'
                       f'QTabBar::tab:selected {{ color: {ACCENT}; background: #12465b; font-weight: bold; }}')
    for title, rows in help_text.TIPS:
        tips = QLabel(help_text.tips_html(rows))
        tips.setTextFormat(Qt.TextFormat.RichText)
        tips.setWordWrap(True)
        tips.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        tips.setContentsMargins(4, 6, 4, 2)
        tabs.addTab(tips, title)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(8, 14, 8, 6)
    layout.addWidget(tabs)
    return panel


def _navigation(page):
    nav = QHBoxLayout()
    page.back = button(nav, 'Back to Results', page.leave)
    page.previous = button(nav, 'Previous', lambda: page.nav.go(-1))
    page.previous.setToolTip('Save your edits on this frame and go back one frame (Left arrow).')
    page.finish_fish = button(nav, 'Finish fish', page.tracking.finish)
    page.finish_fish.setToolTip('Stop following this fish here. Its box on this frame is kept; '
                                'later suggestions are removed.')
    page.finish_fish.hide()
    page.next_button = button(nav, 'Next', page.next_frame)
    page.next_button.setIcon(icon('arrow_right'))
    page.next_button.setObjectName('confirmButton')
    return nav
