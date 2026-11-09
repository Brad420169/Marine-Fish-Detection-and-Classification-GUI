"""Undo records for saved row changes. Plain functions, no Qt.

An entry is a dict. Saved-row changes carry ``added`` (rows appended) and
``changes`` (index, before, after); the page adds what it needs to restore its
widgets (``area_states``, ``pending``, ``sam_added`` or the deleted row's editor state).
"""


class UndoConflict(Exception):
    """The rows changed after the undo entry was recorded."""


def row_changes(old_rows, new_rows):
    """What changed between two versions of the rows, keeping row order stable."""
    return dict(added=[dict(row) for row in new_rows[len(old_rows):]],
                changes=[(i, dict(before), dict(after))
                         for i, (before, after) in enumerate(zip(old_rows, new_rows)) if before != after])


def revert_row_changes(rows, entry):
    """Rows with just the recorded changes reverted, never unrelated edits."""
    rows = list(rows)
    added = entry.get('added', [])
    if added:
        if rows[-len(added):] != added:
            raise UndoConflict('Suppressed annotations have changed since deletion.')
        rows = rows[:-len(added)]
    for index, before, after in entry['changes']:
        if index >= len(rows) or rows[index] != after:
            raise UndoConflict('This annotation has changed since deletion.')
        rows[index] = dict(before)
    return rows
