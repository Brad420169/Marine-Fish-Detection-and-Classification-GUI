"""Keyboard and input routing for the annotation page.

The page installs an application-wide event filter so its shortcuts work whichever
child has focus. ``route(page, obj, event)`` returns True to consume an event,
False to pass it on unhandled-but-allowed, or None to fall through to Qt.
"""
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QWidget

INPUT_EVENTS = (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel)
ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
CTRL = Qt.KeyboardModifier.ControlModifier
NONE = Qt.KeyboardModifier.NoModifier
_CONTINUE = object()  # not handled here; try the page's other shortcuts


def _inside(page, obj):
    """The event is for a widget on this page (not a separate dialog or popup)."""
    return (page.isVisible() and isinstance(obj, QWidget) and obj.window() == page.window()
            and (obj is page or page.isAncestorOf(obj)))


def _typing(obj, kinds=(QLineEdit, QComboBox)):
    """A text field has the event or the focus, so editing keys belong to it."""
    return isinstance(obj, kinds) or isinstance(QApplication.focusWidget(), kinds)


def route(page, obj, event):
    if event.type() not in INPUT_EVENTS or not hasattr(page, 'image') or not _inside(page, obj):
        return None
    sam = page.sam
    if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Z \
            and event.modifiers() == CTRL and sam.active:
        sam.undo_preview()
        return True
    if sam.busy:
        # Queue SAM double-clicks while the previous fish is computing; block other input.
        if (obj is page.image and event.type() == QEvent.Type.MouseButtonDblClick
                and not event.modifiers() & CTRL):
            return False
        return True
    if event.type() != QEvent.Type.KeyPress:
        return None
    key, modifiers = event.key(), event.modifiers()
    if page.tracking.multi is not None and modifiers == NONE and not _typing(obj, QLineEdit):
        if key in ENTER:
            if not event.isAutoRepeat():
                page.tracking.track_selected()
            return True
        if key == Qt.Key.Key_Escape:
            page.tracking.cancel_multi()
            return True
    if modifiers == NONE and key in ENTER and _in_pending_field(page, obj):
        # Enter in a drawn box's species field saves every named box, even while
        # another fish is outlined.
        if not event.isAutoRepeat():
            page.save_named_boxes()
        return True
    if sam.active:
        handled = _sam_preview_key(page, obj, event)
        if handled is not _CONTINUE:
            return handled
    if modifiers == CTRL and key in (Qt.Key.Key_C, Qt.Key.Key_V) and not _typing(obj):
        page.editor.copy() if key == Qt.Key.Key_C else page.editor.paste()
        return True
    if key == Qt.Key.Key_Delete and modifiers == NONE and not _typing(obj):
        page.editor.delete_selected()
        return True
    if (modifiers == CTRL and key == Qt.Key.Key_Z and not _typing(obj)
            and (page.undo_stack or page.frame.pending)):
        page.undo() if page.undo_stack else page.undo_annotation()
        return True
    if modifiers == NONE and key in ENTER:
        handled = _enter_in_species_field(page, obj, event)
        if handled is not None:
            return handled
    # Plain arrows are review navigation, including when a table field has focus.
    # Separate dialogs and open dropdown popups keep their normal keyboard controls.
    if modifiers == NONE and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
        page.nav.go(1 if key == Qt.Key.Key_Right else -1)
        return True
    if modifiers == NONE and key in ENTER and not _typing(obj, QLineEdit):
        if not event.isAutoRepeat():
            page.next_frame()
        return True
    return None


def _sam_preview_key(page, obj, event):
    """Keys while a SAM preview is open. None leaves the key to the focused field."""
    key = event.key()
    if key == Qt.Key.Key_Escape:
        if not page.sam.worker:
            page.sam.clear()
        return True
    if obj is page.sam_class:
        # Consume Enter before saving hides this field and changes focus;
        # otherwise the same event can also confirm the whole frame.
        if key in ENTER:
            if not event.isAutoRepeat():
                page.save_named_boxes()
            return True
        return None
    if key in ENTER:
        page.sam_class.setFocus()
        return True
    if key in (Qt.Key.Key_Delete, Qt.Key.Key_Z):
        return True
    return _CONTINUE


def _in_pending_field(page, obj):
    """Qt can deliver Enter to the editable combo or its line edit; own it for both,
    before saving destroys the pending editor and moves focus."""
    focus = QApplication.focusWidget()
    for index in range(len(page.frame.pending)):
        editor = page.annotations.cellWidget(index, 0)
        if obj in (editor, editor.lineEdit()) or focus in (editor, editor.lineEdit()):
            return True
    return False


def _enter_in_species_field(page, obj, event):
    """Enter in a saved row's species field accepts the name as a draft."""
    focus = QApplication.focusWidget()
    for index in range(page.table.rowCount()):
        editor = page.table.cellWidget(index, 2)
        if obj in (editor, editor.lineEdit()) or focus in (editor, editor.lineEdit()):
            # Accept the edited name as a frame draft, without confirming.
            page.image.setFocus()
            return True
    return None
