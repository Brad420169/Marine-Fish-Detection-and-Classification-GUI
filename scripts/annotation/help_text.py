"""User-facing instructions shown on the annotation page."""
from review_io import AI_COLOUR, MANUAL_COLOUR, SELECTED_COLOUR

ACCENT = '#45d8f6'

# Hover text on the page title: every mouse and keyboard shortcut in one place.
SHORTCUTS = ('<b>Shortcuts</b><br>'
             'Double-click a fish: SAM box · Shift + double-click: add to it · Double-right-click: trim it<br>'
             'Drag empty space: draw a box · Shift + drag: draw over boxes · Drag a box: move · Ctrl + drag edge: resize<br>'
             'Ctrl + C / V: copy / paste box · Delete: remove box · Ctrl + Z: undo · Left / Right: browse frames')

# (badge, title, tooltip) for the numbered steps in the Annotations panel.
STEPS = (
    ('1', 'Double-click a fish', 'Double-left-click each fish separately. Shift + double-left-click expands the active fish; double-right-click trims it.'),
    ('2', 'Enter species name', 'Type the species in the SAM field; Enter creates and saves its box.'),
    ('3', 'Adjust the box', 'Drag to move; Ctrl-drag an edge or corner to resize.'),
    ('4', 'Confirm the frame', 'Check all fish, then Confirm & Next saves edits and advances.'),
)

PREVIEW_HINT = ('<span style="color: #45d8f6;"><b>Ctrl + scroll</b></span> &nbsp; Zoom<br>'
                '<span style="color: #45d8f6;"><b>Scroll</b></span> &nbsp; Pan vertically<br>'
                '<span style="color: #45d8f6;"><b>Shift + scroll</b></span> &nbsp; Pan sideways')

# Tab title -> (heading, detail) rows for the Annotation tips panel. A heading that is a
# number is drawn as a step badge. Tab titles must not contain "&" (Qt shortcut marker).
TIPS = (
    ('Box a fish', (
        ('1', '<b>Double-click a fish.</b> SAM outlines it. Double-click the next fish for its own box.'),
        ('2', '<b>Fix the outline</b> if needed: <b>Shift + double-click</b> adds a missed part, '
              '<b>double-right-click</b> trims.'),
        ('3', '<b>Type the species</b> in the SAM field and press <b>Enter</b> to save the box.'),
        ('Undo', '<b>Esc</b> cancels the outline; <b>Ctrl + Z</b> undoes the last SAM box.'),
    )),
    ('Edit boxes', (
        ('Draw', 'Drag over empty space (<b>Shift + drag</b> over other boxes), pick the species, <b>Enter</b>.'),
        ('Move / resize', 'Drag a box to move it; <b>Ctrl + drag</b> an edge or corner to resize.'),
        ('Copy / delete', '<b>Ctrl + C / V</b> copies and pastes at the mouse; <b>Delete</b> removes; <b>Ctrl + Z</b> undoes.'),
        ('Clear an area', '<b>Right-drag</b> removes AI boxes in that area from this frame onward.'),
    )),
    ('Track with SAM', (
        ('1', '<b>Click any box</b> (AI, SAM or yours) and tick <b>Track?</b>. Choose how many frames.'),
        ('2', '<b>Wait for the bar.</b> Boxes of the same class already there are reused, so SAM only fills '
              'the gaps. <b>Stop</b> keeps what is done.'),
        ('3', '<b>Check each frame</b> with <b>Next (tracking)</b>. Fix a wrong box and SAM redoes the rest from it.'),
        ('4', '<b>Finish fish</b> on the last box. Afterwards <b>Delete</b> removes just one bad box.'),
        ('Tip', 'Name the track like the detector\'s class (e.g. <i>fish</i>) so its AI boxes are reused.'),
    )),
    ('Navigate', (
        ('Confirm', '<b>Confirm &amp; Next</b> saves every box on the frame and moves on.'),
        ('Browse', 'Click the image, then <b>Left / Right</b> to look around without confirming. '
                   'Click the frame number to jump.'),
        ('Zoom', '<b>Ctrl + scroll</b> zooms; <b>scroll</b> and <b>Shift + scroll</b> pan.'),
        ('Play', 'Plays the video without confirming; editing pauses it.'),
    )),
)


def tips_html(rows):
    """Tip rows as a two-column table: a step badge or short heading, then the tip."""
    cells = []
    for heading, detail in rows:
        if heading.isdigit():
            label = (f'<span style="background-color: #12465b; color: {ACCENT}; font-weight: bold;">'
                     f'&nbsp;{heading}&nbsp;</span>')
        else:
            label = f'<span style="color: {ACCENT}; font-weight: bold;">{heading}</span>'
        cells.append(f'<tr><td style="padding: 2px 10px 4px 0;" nowrap>{label}</td>'
                     f'<td style="padding: 2px 0 4px 0;">{detail}</td></tr>')
    return f'<table cellspacing="0">{"".join(cells)}</table>'


def _swatch(bgr, text):
    return (f'<span style="background-color: #{bgr[2]:02x}{bgr[1]:02x}{bgr[0]:02x};">'
            f'&nbsp;&nbsp;&nbsp;</span>&nbsp;{text}')


BOX_LEGEND = ' &nbsp; '.join((_swatch(AI_COLOUR, 'AI'), _swatch(MANUAL_COLOUR, 'Manual / SAM'),
                              _swatch(SELECTED_COLOUR, 'Selected')))
