"""User-facing instructions shown on the annotation page."""
from review_io import AI_COLOUR, MANUAL_COLOUR, SELECTED_COLOUR

HINT = ('Double-left-click each fish for separate SAM boxes; Shift + double-left-click expands the active fish; '
        'double-right-click trims it. Type a class and press Enter to create its box. Ctrl+Z undoes SAM. '
        'Drag empty space to draw; drag boxes to move. Shift-drag draws over boxes. Ctrl+C copies the selected '
        'box; Ctrl+V pastes at the mouse. Delete removes. Arrows navigate.')

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

# Tab title -> (heading, detail) rows for the Annotation tips panel.
TIPS = (
    ('SAM & save', (
        ('Select fish', '<b>Double-left-click each fish</b> for separate boxes. Clicks queue while SAM works. Earlier fish appear in Annotations.'),
        ('Refine active fish', '<b>Shift + double-left-click</b> adds missed parts. <b>Double-right-click</b> trims unwanted parts of the active fish.'),
        ('Name / save', 'Name the active fish in the SAM field; <b>Enter</b> saves it. Name earlier boxes in Annotations and press Enter for each, or confirm all labelled boxes together.'),
        ('Cancel / undo', '<b>Esc</b> cancels the preview. <b>Ctrl + Z</b> discards the active preview and queued clicks, keeping earlier boxes. After saving, it undoes the newly saved SAM box.'),
    )),
    ('Edit boxes', (
        ('Draw manually', 'Without a SAM preview, drag empty space; <b>Shift + drag</b> draws over a box. Choose species, then <b>Enter</b> saves.'),
        ('Move / resize', 'After saving or cancelling the preview, drag a box to move it. <b>Ctrl + drag</b> an edge or corner to resize.'),
        ('Copy / delete', '<b>Ctrl + C / V</b> copies / pastes at the mouse. <b>Delete</b> removes the selected box; <b>Ctrl + Z</b> undoes edits before confirmation.'),
        ('Exclude area', 'Without a SAM preview, <b>right-drag</b> removes model boxes centred in that area from this frame onward. <b>Ctrl + Z</b> restores them.'),
    )),
    ('Track fish', (
        ('Start', 'Save a box, then tick <b>Track?</b>. The button becomes <b>Next (tracking)</b>: it saves this fish and moves on without completing the frame.'),
        ('Suggestions', 'Three frames ahead are filled from matching AI boxes, or with pending SAM-adjusted boxes. Check each one.'),
        ('Stop', '<b>Finish fish</b> keeps this box and removes later suggestions; <b>Delete</b> ends the track here. Then track the next fish.'),
    )),
    ('Navigate', (
        ('Confirm', '<b>Confirm &amp; Next</b> saves the frame and advances. After saving a box with <b>Enter</b>, press it again to confirm the frame.'),
        ('Browse', 'Click the image, then use <b>Left / Right</b> arrows to browse without confirming. Click the frame counter to jump.'),
        ('Zoom / pan', '<b>Ctrl + scroll</b> zooms; <b>scroll</b> pans vertically; <b>Shift + scroll</b> pans sideways.'),
        ('Playback', 'Play browses without confirming; editing pauses playback. Finish or cancel the SAM preview before navigating.'),
    )),
)


def _swatch(bgr, text):
    return (f'<span style="background-color: #{bgr[2]:02x}{bgr[1]:02x}{bgr[0]:02x};">'
            f'&nbsp;&nbsp;&nbsp;</span>&nbsp;{text}')


BOX_LEGEND = ' &nbsp; '.join((_swatch(AI_COLOUR, 'AI'), _swatch(MANUAL_COLOUR, 'Manual / SAM'),
                              _swatch(SELECTED_COLOUR, 'Selected')))
