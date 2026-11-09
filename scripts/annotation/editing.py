"""Mouse and clipboard editing of boxes on the annotation page.

Coordinates arrive as fractions of the frame (0–1) from the canvas.
"""
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QMessageBox

from review_io import original_box, review_box
from review_rejections import exclude_detection_area
from review_tracks import stop_track

from annotation.saving import model_row
from annotation.undo import row_changes


class BoxEditor:
    def __init__(self, page):
        self.page = page
        self.drag = None      # (index, start x, start y, original box, edges being resized)
        self.copied = None    # (species, width, height) for Ctrl+V

    def reset(self):
        self.drag = None

    def _frame_size(self):
        h, w = self.page.raw.shape[:2]
        return w, h

    def _to_pixels(self, x1, y1, x2, y2):
        """A box given as frame fractions, in pixels and clamped inside the frame."""
        w, h = self._frame_size()
        return [max(0, min(w, x1 * w)), max(0, min(h, y1 * h)), max(0, min(w, x2 * w)), max(0, min(h, y2 * h))]

    def _select(self, index):
        """Select a box by its index in page.all_rows(): saved rows first, then drawn boxes."""
        page = self.page
        saved = page.table.rowCount()
        if index < saved:
            page.edit_detection(index)
        else:
            page.select_annotation(index - saved)
        page.image.setFocus()

    # Draw, select, move and resize

    def draw(self, x1, y1, x2, y2):
        page = self.page
        page.nav.pause()
        if page.raw is None or page.worker:
            return
        box = [round(v, 2) for v in self._to_pixels(x1, y1, x2, y2)]
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            return
        page.add_pending_box(box)

    def select_at(self, x, y):
        """Click: select the smallest box under the cursor, promoting an unflagged AI box if needed."""
        page = self.page
        if page.raw is None or page.worker:
            return
        w, h = self._frame_size()
        point = (x * w, y * h)
        hits = []
        for i, row in enumerate(page.all_rows()):
            if row.get('review_status') == 'rejected':
                continue
            x1, y1, x2, y2 = [float(row[k]) for k in ('x1', 'y1', 'x2', 'y2')]
            if x1 <= point[0] <= x2 and y1 <= point[1] <= y2:
                hits.append(((x2 - x1) * (y2 - y1), i))
        if hits:
            self._select(min(hits)[1])
            return
        # Confident model boxes are drawn as context even when never flagged.
        # Promote a clicked box to an editable review row; retain its model identity.
        represented = {tuple(round(v, 2) for v in original_box(r))
                       for r in page.current() if r.get('annotation_source') != 'manual'}
        hits = []
        for det in page.frame_context():
            box = tuple(round(float(v), 2) for v in det['bbox'])
            if box in represented:
                continue
            x1, y1, x2, y2 = box
            if x1 <= point[0] <= x2 and y1 <= point[1] <= y2:
                hits.append(((x2 - x1) * (y2 - y1), det))
        if not hits:
            return
        det = min(hits, key=lambda hit: hit[0])[1]
        try:
            image = page.session.ensure_frame_image(page.frame_number, page.raw)
        except OSError as exc:
            QMessageBox.critical(page, 'Cannot edit detection', str(exc))
            return
        new = model_row(page.session, page.frame_number, det, image)
        page.loading = True
        page.session.rows.append(new)
        page.session.rows_by_frame.setdefault(page.frame_number, []).append(new)
        page.frame.decisions.append('confirmed')
        index = len(page.current()) - 1
        page.add_table_row(index, new)
        page.loading = False
        page.edit_detection(index)

    def begin_drag(self, x, y):
        page = self.page
        page.nav.pause()
        if page.worker or page.raw is None:
            return False
        self.select_at(x, y)
        rows = page.all_rows()
        selected = page.frame.selected
        if not 0 <= selected < len(rows):
            return False
        row = rows[selected]
        box = review_box(row)
        w, h = self._frame_size()
        if row.get('review_status') == 'rejected' or not (box[0] <= x * w <= box[2] and box[1] <= y * h <= box[3]):
            return False
        self.drag = (selected, x * w, y * h, box, ())
        return True

    def begin_resize(self, x, y):
        """Ctrl+drag near an edge or corner: resize the smallest box there."""
        page = self.page
        page.nav.pause()
        if page.worker or page.raw is None or not page.image.ready():
            return False
        w, h = self._frame_size()
        _, _, vw, vh = page.image.region
        tx = 8 * vw / page.image.pixmap().width()
        ty = 8 * vh / page.image.pixmap().height()
        hits = []
        for index, row in enumerate(page.all_rows()):
            if row.get('review_status') == 'rejected':
                continue
            box = review_box(row)
            if not (box[0] - tx <= x * w <= box[2] + tx and box[1] - ty <= y * h <= box[3] + ty):
                continue
            edges = []
            horizontal = min((0, 2), key=lambda edge: abs(box[edge] - x * w))
            vertical = min((1, 3), key=lambda edge: abs(box[edge] - y * h))
            if abs(box[horizontal] - x * w) <= tx:
                edges.append(horizontal)
            if abs(box[vertical] - y * h) <= ty:
                edges.append(vertical)
            if edges:
                hits.append(((box[2] - box[0]) * (box[3] - box[1]), index, box, edges))
        if not hits:
            return False
        _, index, box, edges = min(hits)
        self._select(index)
        self.drag = (index, x * w, y * h, box, edges)
        return True

    def move(self, x, y):
        page = self.page
        if self.drag is None or page.raw is None or page.worker:
            return
        index, start_x, start_y, box, edges = self.drag
        w, h = self._frame_size()
        dx = max(-box[0], min(w - box[2], x * w - start_x))
        dy = max(-box[1], min(h - box[3], y * h - start_y))
        moved = [round(box[0] + dx, 2), round(box[1] + dy, 2), round(box[2] + dx, 2), round(box[3] + dy, 2)]
        if edges:
            moved = list(box)
            for edge in edges:
                value = box[edge] + (x * w - start_x if edge % 2 == 0 else y * h - start_y)
                low = 0 if edge < 2 else box[edge - 2] + 2
                high = box[edge + 2] - 2 if edge < 2 else (w if edge == 2 else h)
                moved[edge] = round(max(low, min(high, value)), 2)
        saved = page.table.rowCount()
        if index < saved:
            page.frame.box_edits[index] = moved
        else:
            page.frame.pending[index - saved] = moved
        page.render()

    def end_drag(self):
        self.drag = None

    # Copy and paste

    def copy(self):
        page = self.page
        if page.worker or page.raw is None:
            return
        rows = page.all_rows()
        selected = page.frame.selected
        if not 0 <= selected < len(rows):
            return
        row = rows[selected]
        if row.get('review_status') == 'rejected':
            return
        index = selected - page.table.rowCount()
        species = (page.annotations.cellWidget(index, 0).currentText().strip()
                   if index >= 0 else row['species'])
        if not species:
            page.status.setText('Choose a species before copying this annotation.')
            return
        x1, y1, x2, y2 = (float(row[key]) for key in ('x1', 'y1', 'x2', 'y2'))
        self.copied = (species, x2 - x1, y2 - y1)
        page.status.setText(f'Copied {species}. Move to a frame, point at the fish, and press Ctrl+V.')

    def paste(self):
        page = self.page
        if page.worker or page.raw is None or not self.copied or not page.image.ready():
            return
        point = page.image.mapFromGlobal(QCursor.pos())
        ax, ay = page.image.anchor(point)
        if not page.image.rect().contains(point) or not (0 <= ax <= 1 and 0 <= ay <= 1):
            page.status.setText('Place the mouse over the frame image to paste an annotation.')
            return
        page.nav.pause()
        x, y = page.image.fraction(point)
        w, h = self._frame_size()
        species, width, height = self.copied
        width, height = min(width, w), min(height, h)
        if width < 2 or height < 2:
            return
        left = min(max(x * w - width / 2, 0), w - width)
        top = min(max(y * h - height / 2, 0), h - height)
        page.add_pending_box([round(left, 2), round(top, 2), round(left + width, 2), round(top + height, 2)], species)
        page.image.setFocus()
        page.status.setText(f'Pasted {species}. Adjust the box if needed; Enter saves, Ctrl+Z undoes.')

    # Removing boxes

    def exclude_area(self, x1, y1, x2, y2):
        """Right-drag: remove model boxes centred in an area from this frame onward."""
        page = self.page
        page.nav.pause()
        if page.worker or page.raw is None:
            return
        session, frame = page.session, page.frame
        area = self._to_pixels(x1, y1, x2, y2)
        if area[2] - area[0] < 2 or area[3] - area[1] < 2:
            return
        try:
            rows, count = exclude_detection_area(
                session.rows, area, page.frame_number, session.detections_by_frame, session.fields)
            if not count:
                page.status.setText('No model detections in this area. Manual boxes and tracks are preserved.')
                return
            old_rows = session.rows
            session.save(rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(page, 'Area not excluded', str(exc))
            return
        changed_ids = {id(old) for old, new in zip(old_rows, rows) if old != new}
        old_current = [r for r in old_rows if r['frame_number'] == page.frame_number]
        states = [(i, frame.decisions[i], frame.box_edits.get(i), page.table.isRowHidden(i))
                  for i, row in enumerate(old_current) if id(row) in changed_ids]
        page.undo_stack.append(dict(frame=page.frame_number, area_states=states,
                                    table_count=page.table.rowCount(), **row_changes(old_rows, rows)))
        for index, row in enumerate(page.current()):
            if index >= page.table.rowCount():
                frame.decisions.append('rejected')
                page.add_table_row(index, row)
            if row.get('rejection_source') == 'area':
                frame.decisions[index] = 'rejected'
                frame.box_edits.pop(index, None)
                page.style_decision(index)
                page.table.setRowHidden(index, True)
        frame.selected = -1
        page.image.setFocus()
        page.status.setText(f'Removed {count} model detections in this area from this frame onward. Ctrl+Z undoes this area removal.')
        page.update_controls()
        page.render()

    def delete_selected(self):
        """Persist only this deletion, without confirming the frame or other edits."""
        page = self.page
        page.nav.pause()
        frame = page.frame
        if page.worker or page.raw is None or frame.selected < 0:
            return
        index = frame.selected
        saved = page.table.rowCount()
        if index >= saved:
            pending_index = index - saved
            if pending_index < len(frame.pending):
                page.remove_box(page.annotations.cellWidget(pending_index, 1))
                frame.selected = -1
                page.render()
            return
        old = page.current()[index]
        if old.get('review_status') == 'rejected':
            return
        session = page.session
        old_rows = session.rows
        deleted = dict(old, review_status='rejected', reviewed='yes', rejection_source='user')
        rows = [deleted if r is old else r for r in old_rows]
        try:
            if old.get('manual_track_id'):
                rows = stop_track(rows, old['manual_track_id'], page.frame_number)
            session.save(rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(page, 'Box not deleted', str(exc))
            return
        page.tracking.box_deleted(old)
        page.undo_stack.append(dict(
            frame=page.frame_number, index=index, **row_changes(old_rows, rows),
            species=page.table.cellWidget(index, 2).currentText(),
            decision=frame.decisions[index], box_edit=frame.box_edits.get(index)))
        # Keep the rejection internally so the original model box cannot reappear.
        # Preserve table indices and unrelated drafts while hiding its visible row.
        page.tracking.restore_reopened()
        frame.decisions[index] = 'rejected'
        frame.box_edits.pop(index, None)
        page.style_decision(index)
        page.table.setRowHidden(index, True)
        frame.selected = -1
        page.table.clearSelection()
        page.status.setText('Box deleted. Ctrl+Z restores it. The frame has not been confirmed.')
        page.update_controls()
        page.render()
