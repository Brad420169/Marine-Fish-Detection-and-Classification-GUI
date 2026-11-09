"""The Annotate Data page: review AI detections and annotate fish frame by frame.

ReviewPage owns the current frame's view (detection table, drawn boxes, drawing)
and the save / undo / leave flow. Everything else is a part with its own state:

  session   ReviewSession   CSV rows, run metadata, frame completion   (session.py)
  nav       Navigator       moving between frames, playback            (playback.py)
  editor    BoxEditor       draw, move, resize, copy/paste, delete     (editing.py)
  sam       SamAssist       SAM preview, click queue, preloading       (sam.py)
  tracking  TrackingController  following one fish across frames       (tracking.py)
"""
import json
from dataclasses import dataclass, field
from uuid import uuid4

import cv2
import numpy as np
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QCompleter, QDialog, QHBoxLayout,
                             QInputDialog, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from review_io import ReviewVideoWorker, draw_review, original_box
from review_summary import RefreshWorker, results_need_refresh
from review_tracks import create_track
from ui_icons import icon

from annotation import layout, saving, shortcuts
from annotation.canvas import SpeciesComboBox, pixmap
from annotation.editing import BoxEditor
from annotation.playback import Navigator, VideoReader
from annotation.sam import SamAssist
from annotation.session import ReviewSession
from annotation.tracking import TrackingController
from annotation.undo import UndoConflict, revert_row_changes


@dataclass
class FrameState:
    """Unsaved state of the frame on screen; replaced whenever another frame opens."""
    decisions: list = field(default_factory=list)  # 'confirmed' / 'rejected' per saved row
    box_edits: dict = field(default_factory=dict)  # saved row index -> moved/resized box
    pending: list = field(default_factory=list)    # boxes drawn but not yet saved
    selected: int = 0                              # saved rows first, then pending boxes


class ReviewPage(QWidget):
    results_refreshed = pyqtSignal()
    export_requested = pyqtSignal()

    # The app-wide sheet gives every QPushButton a 30px minimum and wide padding, which
    # squashes a small square button, so these opt out of all of it explicitly.
    # The size has to be set here rather than with setFixedSize: applying a stylesheet
    # re-derives the widget's size constraints from the sheet and discards it.
    DECISION_STYLE = ('QPushButton{{min-width:26px;max-width:26px;min-height:20px;max-height:20px;'
                      'padding:0px;font-size:14px;font-weight:700;'
                      'border:1px solid #39718e;border-radius:4px;'
                      'background:#0b3048;color:#93b8cb;}}'
                      'QPushButton:hover{{border:1px solid {colour};color:{colour};}}'
                      'QPushButton:checked{{background:{colour};border:1px solid {colour};color:#FFFFFF;}}')

    def __init__(self, on_back, parent=None):
        super().__init__(parent)
        self.on_back = on_back
        self.session = ReviewSession()
        self.video_reader = VideoReader()
        self.frame = FrameState()
        self.frame_number = 1
        self.total_frames = 0
        self.raw = None             # the current video frame (BGR)
        self.canvas = None          # raw with boxes drawn, as shown
        self.worker = None          # results refresh or video regeneration
        self.loading = False        # suppress redraws while tables are rebuilt
        self.shutting_down = False
        self.after_refresh = None
        self.undo_stack = []
        self.nav = Navigator(self)
        self.editor = BoxEditor(self)
        self.sam = SamAssist(self)
        self.tracking = TrackingController(self)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.render)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        QApplication.instance().installEventFilter(self)
        layout.build(self)

    # Loading a run or session

    def load_run(self, flagged_csv, output_dir, video_path):
        session = self.session
        self.sam.new_source()
        self.sam.schedule_preload()
        self.nav.pause()
        self.undo_stack.clear()
        self.tracking.reset()
        self.editor.copied = None
        self.video_reader.close()
        session.open(flagged_csv, output_dir, video_path)
        self.back.setText('Back to Overview' if session.manual_session else 'Back to Results')
        self.table.setHorizontalHeaderLabels(['Saved species' if session.manual_session else 'Original AI prediction',
                                              'Confidence', 'Reviewed species', 'Decision'])
        self.table.setColumnHidden(1, session.manual_session)
        self.frame = FrameState()
        self.total_frames = 0
        self.image.reset_view()
        try:
            has_names = session.load_rows()
            self.status.setText('Ready. Changes are saved when you confirm the frame.' if has_names else
                                'Saved decisions are loaded. Older runs may have an incomplete species list.')
        except (OSError, ValueError, KeyError) as exc:
            session.rows, session.names = [], []
            QMessageBox.critical(self, 'Cannot load review', str(exc))
        session.index()
        if self.video_reader.open(session.source_video):
            self.total_frames = self.video_reader.total_frames
            if self.video_reader.fps:
                session.fps = self.video_reader.fps
        self.nav.rows_only = not self.total_frames
        if not self.total_frames:
            # No readable video: frames with review rows are all that can be reviewed.
            self.total_frames = max(session.rows_by_frame, default=0)
        self.frame_number = session.start_frame(self.total_frames, bool(self.video_reader))
        self.show_frame()

    # The frame on screen

    def current(self):
        """Saved review rows on the current frame."""
        return self.session.frame_rows(self.frame_number)

    def frame_context(self):
        return self.session.context(self.frame_number)

    def load_frame_image(self):
        frame = self.video_reader.read(self.frame_number)
        return frame if frame is not None else self.session.saved_frame_image(self.frame_number)

    def show_frame(self):
        self.sam.stop_preload()
        self.sam.frame_token += 1
        self.sam.clear(render=False)
        self.tracking.hide_prompt()
        self.editor.reset()
        self.loading = True
        self.table.setRowCount(0)
        self.annotations.setRowCount(0)
        self.canvas = None
        rows = self.current()
        # A row is confirmed unless it is crossed out, so an untouched frame can be
        # accepted wholesale with Confirm & Next.
        self.frame = FrameState(decisions=['rejected' if row['review_status'] == 'rejected' else 'confirmed'
                                           for row in rows])
        for i, row in enumerate(rows):
            self.add_table_row(i, row)
            self.table.setRowHidden(i, row.get('reviewed') == 'yes')
        self.table.setColumnWidth(0, 230)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 230)
        self.raw = self.load_frame_image()
        self.update_controls()
        self.loading = False
        self.render()
        ai_tracks = sum(bool(row.get('manual_track_id')) and row.get('annotation_source') == 'model'
                        and row.get('review_status') == 'pending' for row in rows)
        if ai_tracks:
            self.status.setText(f'Using matching AI detections for {ai_tracks} tracked fish. Check before confirming.')
        self.sam.schedule_preload()
        QTimer.singleShot(0, self.tracking.adjust_suggestions)

    def update_controls(self):
        session = self.session
        summary = f'{session.review_complete_count} of {len(session.flagged_frames)} flagged frames reviewed'
        if session.manual_session and session.root:
            try:
                summary = f'{len(session.completed_frames())} frames complete • interval {session.frame_interval}'
            except (OSError, ValueError, KeyError):
                summary = 'Completion unavailable'
        flagged = ' • flagged' if self.frame_number in session.flagged_frames else ''
        zoom = f' • {self.image.zoom:.1f}× zoom' if self.image.zoom > 1 else ''
        self.progress.setText(
            f'<a href="jump" style="color: #46d8ff;">Frame {self.frame_number} / {self.total_frames}</a> • '
            f'{session.timestamp(self.frame_number)}{flagged}{zoom} • {summary}')
        idle = not self.worker
        self.previous.setEnabled(idle and self.nav.next_number(-1) != self.frame_number)
        self.skip.setEnabled(idle and self.nav.next_number(1) != self.frame_number)
        self.confirm.setEnabled(idle and self.raw is not None)
        tracking = self.tracking.is_active()
        self.confirm.setText('Next (tracking)' if tracking else 'Confirm && Next')
        self.confirm.setToolTip('Save this fish\'s box and go to the next frame. The frame is not marked complete.'
                                if tracking else 'Accept every box on this frame, mark it complete and go on.')
        self.finish_fish.setVisible(tracking)
        self.finish_fish.setEnabled(idle and self.raw is not None)
        self.play_button.setEnabled(bool(self.video_reader) and idle and self.total_frames > 1)
        self.image.flagged = self.frame_number in session.flagged_frames
        self.image.update()

    def drafts(self):
        """Saved rows on this frame with the on-screen species, decisions and box edits applied."""
        result = []
        for i, row in enumerate(self.current()):
            copy = dict(row)
            copy['species'] = self.table.cellWidget(i, 2).currentText().strip()
            copy['review_status'] = self.frame.decisions[i]
            if i in self.frame.box_edits:
                if copy.get('annotation_source') != 'manual' and not copy.get('original_bbox'):
                    copy['original_bbox'] = json.dumps(original_box(row))
                copy.update(zip(('x1', 'y1', 'x2', 'y2'), self.frame.box_edits[i]))
            result.append(copy)
        return result

    def pending_rows(self):
        """Boxes drawn on this frame but not yet written to the review CSV."""
        result = []
        for i, box in enumerate(self.frame.pending):
            species = self.annotations.cellWidget(i, 0).currentText().strip()
            result.append(dict(zip(('x1', 'y1', 'x2', 'y2'), box), species=species or f'New box {i+1}',
                               review_status='confirmed', annotation_source='manual'))
        return result

    def all_rows(self):
        """Saved detections on this frame, then boxes drawn but not yet confirmed."""
        return self.drafts() + self.pending_rows()

    def render(self, *args):
        if self.loading:
            return
        self.tracking.position_prompt()
        if self.raw is None:
            self.track_prompt.hide()
            self.image.clear_frame()
            self.image.setText('No image available.' if self.total_frames else 'No flagged detections.')
            self.crop.clear()
            return
        rows = self.all_rows()
        selected = self.frame.selected
        self.canvas = draw_review(self.raw, rows, selected, self.frame_context(), by_source=True)
        if self.sam.mask is not None:
            # Native uint8 blending avoids large float64 arrays for full-resolution masks.
            tint = np.empty_like(self.canvas)
            tint[:] = (70, 220, 100)
            cv2.addWeighted(self.canvas, 0.55, tint, 0.45, 0, dst=tint)
            cv2.copyTo(tint, self.sam.mask.astype(np.uint8), self.canvas)
        for (x, y), include in zip(self.sam.points, self.sam.labels):
            colour = (80, 230, 100) if include else (70, 70, 255)
            cv2.circle(self.canvas, (round(x), round(y)), 5, colour, -1)
            cv2.putText(self.canvas, '+' if include else '-', (round(x) + 7, round(y) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
        self.image.set_frame(self.canvas)
        if 0 <= selected < len(rows):
            row = rows[selected]
            h, w = self.raw.shape[:2]
            x1, y1, x2, y2 = [round(float(row[k])) for k in ('x1', 'y1', 'x2', 'y2')]
            crop = self.raw[max(0, y1 - 25):min(h, y2 + 25), max(0, x1 - 25):min(w, x2 + 25)]
            if crop.size:
                self.crop.setPixmap(pixmap(crop).scaled(max(1, self.crop.width()), max(1, self.crop.height()),
                                                        Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation))
        else:
            self.crop.clear()
            self.crop.setText('Selected fish')

    def zoom(self):
        self.nav.pause()
        if self.canvas is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Full-resolution review — scroll to inspect')
        dialog.resize(1000, 700)
        label = QLabel()
        label.setPixmap(pixmap(self.canvas))
        scroll = QScrollArea()
        scroll.setWidget(label)
        QVBoxLayout(dialog).addWidget(scroll)
        dialog.exec()

    # Detection table (saved rows)

    def add_table_row(self, index, row):
        """Build one row of the detection table. Rows are only ever appended, so the
        indexes captured by the row's own controls stay correct."""
        self.table.insertRow(index)
        for col, text in enumerate(('Manual annotation' if row.get('annotation_source') == 'manual' else row['original_species'],
                                    str(row['confidence']) if row['confidence'] != '' else 'N/A')):
            self.table.setCellWidget(index, col, QLabel(text))
        species = self.species_box(row['species'])
        self.table.setCellWidget(index, 2, species)
        self.table.setCellWidget(index, 3, self.decision_widget(index))
        species.activated.connect(lambda _, i=index: self.select_row(i, 2))
        species.currentTextChanged.connect(self.render)
        self.style_decision(index)

    def decision_widget(self, index):
        """Tick to confirm the row, cross to drop the detection."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(7)
        group = QButtonGroup(holder)
        group.setExclusive(True)
        for icon_name, status, tip, colour in (('check', 'confirmed', 'Confirm this species', '#36b88e'),
                                               ('close', 'rejected', 'Not a fish — drop this detection', '#dd6675')):
            choice = QPushButton()
            choice.setIcon(icon(icon_name))
            choice.setCheckable(True)
            choice.setAccessibleName(tip)
            choice.setCursor(Qt.CursorShape.PointingHandCursor)
            choice.setToolTip(tip)
            choice.setStyleSheet(self.DECISION_STYLE.format(colour=colour))
            choice.setChecked(self.frame.decisions[index] == status)
            # clicked, not toggled: only a real click should change the decision.
            choice.clicked.connect(lambda _, i=index, s=status: self.set_decision(i, s))
            group.addButton(choice)
            row.addWidget(choice)
        holder.caption = QLabel()
        row.addWidget(holder.caption)
        row.addStretch()
        return holder

    def set_decision(self, index, status):
        self.frame.decisions[index] = status
        self.frame.selected = index
        self.table.selectRow(index)
        self.style_decision(index)
        self.render()

    def style_decision(self, index):
        """Strike out a crossed row so it reads as removed before it is saved."""
        rejected = self.frame.decisions[index] == 'rejected'
        for col in (0, 1):
            label = self.table.cellWidget(index, col)
            font = label.font()
            font.setStrikeOut(rejected)
            label.setFont(font)
            label.setStyleSheet('color: #98A2AD;' if rejected else '')
        self.table.cellWidget(index, 2).setEnabled(not rejected)
        caption = self.table.cellWidget(index, 3).caption
        row = self.current()[index]
        if rejected:
            text = ('Track stopped' if row.get('manual_track_id') else
                    'Annotation deleted' if row.get('annotation_source') == 'manual' else 'Removed — not a fish')
        else:
            text = 'Confirmed'
        caption.setText(text)
        caption.setStyleSheet(f"color: {'#dd6675' if rejected else '#36b88e'};")

    def species_box(self, text=''):
        species = SpeciesComboBox()
        species.setEditable(True)
        species.addItems(self.session.names)
        species.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        species.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        species.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        species.completer().setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        species.lineEdit().installEventFilter(species)
        if text:
            species.setCurrentText(text)
        else:
            species.setCurrentIndex(-1)
        return species

    def select_row(self, row, col):
        self.frame.selected = row
        self.render()

    def select_annotation(self, row, _col=0):
        self.frame.selected = self.table.rowCount() + row
        self.render()

    def edit_detection(self, index):
        """Reopen a resolved row when its on-image annotation is selected."""
        self.table.setRowHidden(index, False)
        self.frame.selected = index
        self.table.selectRow(index)
        self.table.scrollTo(self.table.model().index(index, 2))
        self.image.setFocus()
        self.render()

    # Drawn boxes (Annotations panel)

    def add_pending_box(self, box, text='', index=None, tracking=False):
        """Insert a drawn or restored draft without opening the tracking prompt."""
        self.tracking.hide_prompt()
        pending = self.frame.pending
        index = len(pending) if index is None else min(index, len(pending))
        self.loading = True
        pending.insert(index, list(box))
        self.annotations.insertRow(index)
        species = self.species_box(text)
        species.setProperty('tracking', tracking)
        species.currentTextChanged.connect(self.render)
        # Rows shift as boxes are committed or removed, so look the row up by widget.
        species.activated.connect(lambda: self.select_annotation(self.annotation_row(species)))
        self.annotations.setCellWidget(index, 0, species)
        remove = QPushButton()
        remove.setIcon(icon('close'))
        remove.setAccessibleName('Remove this pending annotation')
        remove.setToolTip('Remove this pending annotation')
        remove.setStyleSheet('padding: 0; min-width: 24px; min-height: 28px;')
        remove.clicked.connect(lambda: self.remove_box(remove))
        self.annotations.setCellWidget(index, 1, remove)
        self.annotations.scrollToBottom()
        # Show the new box enlarged on the right, as a selected detection would be.
        self.frame.selected = self.table.rowCount() + index
        self.annotations.selectRow(index)
        self.loading = False
        self.render()
        return index

    def annotation_row(self, widget):
        """Which Annotations row a control currently belongs to, or -1 if it is gone."""
        for index in range(self.annotations.rowCount()):
            if widget in (self.annotations.cellWidget(index, 0), self.annotations.cellWidget(index, 1)):
                return index
        return -1

    def pending_tracked(self, index):
        return bool(self.annotations.cellWidget(index, 0).property('tracking'))

    def remove_box(self, button, record_undo=True):
        index = self.annotation_row(button)
        if index < 0:
            return
        pending = self.frame.pending
        if record_undo:
            self.undo_stack.append(dict(frame=self.frame_number, index=index, pending=list(pending[index]),
                                        species=self.annotations.cellWidget(index, 0).currentText(),
                                        tracking=self.pending_tracked(index)))
        # Deleting the row destroys its controls, whose dying signals would otherwise
        # redraw from a half-updated table.
        self.loading = True
        self.annotations.removeRow(index)
        pending.pop(index)
        self.frame.selected = min(self.frame.selected, max(0, self.table.rowCount() + len(pending) - 1))
        self.loading = False
        self.render()

    def commit_box(self, index, track=None):
        """Save one drawn box immediately, so pressing Enter moves it straight into the
        reviewed-species table instead of waiting for Confirm & Next."""
        self.nav.pause()
        if self.worker or index < 0 or self.raw is None:
            return
        session = self.session
        if track is None:
            track = self.pending_tracked(index)
        species = self.annotations.cellWidget(index, 0).currentText().strip()
        if not species:
            QMessageBox.warning(self, 'Species required', 'Choose or type a species name for this box.')
            return
        if species not in session.names and QMessageBox.question(
                self, 'Unknown species', f'"{species}" is not in the available species list. Save it anyway?'
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            image = session.ensure_frame_image(self.frame_number, self.raw)
        except OSError as exc:
            QMessageBox.critical(self, 'Annotation not saved', str(exc))
            return
        new = saving.manual_row(session, self.frame_number, species, self.frame.pending[index], image)
        rows = session.rows + [new]
        try:
            if track:
                rows, self.tracking.active = create_track(rows, new['annotation_id'])
                new = next(r for r in rows if r.get('annotation_id') == new['annotation_id'])
            session.save(rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, 'Annotation not saved', str(exc))
            return
        # Move the box from the Annotations panel into the table as one atomic step:
        # the two are briefly inconsistent, and dying widgets emit as they go.
        self.loading = True
        self.annotations.removeRow(index)
        self.frame.pending.pop(index)
        self.undo_stack.clear()
        session.add_names([species])
        position = len(self.current()) - 1
        self.frame.decisions.append('confirmed')
        self.add_table_row(position, new)
        self.frame.selected = position
        self.table.selectRow(position)
        self.loading = False
        if track:
            self.status.setText('Tracking this fish. Next (tracking) saves its box and moves on without marking the '
                                'frame complete; SAM adjusts each suggested box. Finish fish or Delete stops tracking.')
        else:
            self.status.setText(f'Saved "{species}" on frame {self.frame_number}. '
                                'Confirm the frame, then open Results to update the summary and charts.')
        self.tracking.offer(new['annotation_id'])
        self.update_controls()
        self.render()
        self.image.setFocus()

    # Undo

    def undo(self):
        """Ctrl+Z: undo the newest saved deletion, area removal, removed box or SAM box."""
        self.nav.pause()
        if self.worker or not self.undo_stack:
            return
        entry = self.undo_stack[-1]
        changing_frame = entry['frame'] != self.frame_number
        if changing_frame and not self.discard_ok():
            return
        if 'sam_added' in entry:
            self._undo_sam_box(entry)
            return
        if 'changes' in entry:
            try:
                rows = revert_row_changes(self.session.rows, entry)
            except UndoConflict as exc:
                QMessageBox.warning(self, 'Cannot undo deletion', str(exc))
                return
            try:
                self.session.save(rows)
            except OSError as exc:
                QMessageBox.critical(self, 'Deletion not undone', str(exc))
                return
        if changing_frame:
            self.frame_number = entry['frame']
            self.show_frame()
        frame = self.frame
        if 'area_states' in entry:
            while self.table.rowCount() > entry['table_count']:
                self.table.removeRow(self.table.rowCount() - 1)
                frame.decisions.pop()
            for index, decision, box_edit, hidden in entry['area_states']:
                frame.decisions[index] = decision
                if box_edit is not None:
                    frame.box_edits[index] = list(box_edit)
                self.table.setCellWidget(index, 3, self.decision_widget(index))
                self.style_decision(index)
                self.table.setRowHidden(index, hidden)
        elif 'pending' in entry:
            self.add_pending_box(entry['pending'], entry['species'], entry['index'], entry.get('tracking', False))
        else:
            index = entry['index']
            self.loading = True
            self.table.cellWidget(index, 2).setCurrentText(entry['species'])
            frame.decisions[index] = entry['decision']
            if entry['box_edit'] is not None:
                frame.box_edits[index] = list(entry['box_edit'])
            self.loading = False
            # Rebuild the decision buttons so their checked state is restored too.
            self.table.setCellWidget(index, 3, self.decision_widget(index))
            self.style_decision(index)
            self.table.setRowHidden(index, False)
            frame.selected = index
            self.table.selectRow(index)
        self.undo_stack.pop()
        self.image.setFocus()
        self.status.setText('Deletion undone. The frame has not been confirmed.')
        self.update_controls()
        self.render()

    def _undo_sam_box(self, entry):
        row = entry['sam_added']
        if row not in self.session.rows or row not in self.current():
            QMessageBox.warning(self, 'Cannot undo SAM', 'This annotation has changed since it was created.')
            return
        index = self.current().index(row)
        rows = [r for r in self.session.rows if r.get('annotation_id') != row['annotation_id']]
        try:
            self.session.save(rows)
        except OSError as exc:
            QMessageBox.critical(self, 'SAM not undone', str(exc))
            return
        frame = self.frame
        self.loading = True
        self.table.removeRow(index)
        frame.decisions.pop(index)
        frame.box_edits = {i - (i > index): box for i, box in frame.box_edits.items() if i != index}
        self.tracking.hide_prompt()
        frame.selected = max(0, index - 1)
        self.loading = False
        self.undo_stack.pop()
        self.update_controls()
        self.render()
        self.status.setText('SAM bounding box undone.')

    def undo_annotation(self):
        """Remove the newest unsaved box on this frame, without touching saved rows."""
        if self.worker or not self.frame.pending:
            return
        self.remove_box(self.annotations.cellWidget(len(self.frame.pending) - 1, 1), record_undo=False)
        self.status.setText('Last drawn annotation removed. Ctrl+Z undoes remaining unsaved boxes.')

    # Input

    def eventFilter(self, obj, event):
        handled = shortcuts.route(self, obj, event)
        return super().eventFilter(obj, event) if handled is None else handled

    def discard_ok(self):
        if self.sam.worker:
            return False
        changed = (self.sam.active or bool(self.frame.pending) or bool(self.frame.box_edits)
                   or any(a['species'] != b['species']
                          or (a['review_status'] != b['review_status'] and b['review_status'] != 'pending')
                          or (b['review_status'] == 'pending' and a['review_status'] != 'confirmed')
                          for a, b in zip(self.drafts(), self.current())))
        return not changed or QMessageBox.question(
            self, 'Unsaved edits', 'Leave this frame and discard unconfirmed edits?') == QMessageBox.StandardButton.Yes

    def select_interval(self):
        self.nav.pause()
        value, accepted = QInputDialog.getInt(
            self, 'Frame interval',
            'Advance this many frames after saving or skipping (tracking still copies to the next video frame):',
            self.session.frame_interval, 1, max(1, self.total_frames))
        if accepted and self.save_interval(value):
            self.update_controls()

    def save_interval(self, value):
        try:
            self.session.save_interval(value)
        except OSError as exc:
            QMessageBox.warning(self, 'Cannot save interval', str(exc))
            return False
        return True

    # Saving

    def confirm_frame(self):
        if self.tracking.is_active():
            self.tracking.next()
        else:
            self.save_frame(complete=True)

    def save_frame(self, complete, track=None, advance=True):
        """Save this frame's edits; with ``complete`` also mark it reviewed and move on.

        With ``track``, that fish's box on this frame is accepted and only its track
        looks ahead; the frame itself is not marked complete. Returns True when the
        edits are saved (or there were none) and the caller may continue.
        """
        self.nav.pause()
        if self.worker or self.sam.worker:
            return False
        if self.sam.active:
            self.sam_class.setFocus()
            self.status.setText('Enter a class and create the SAM box, or cancel SAM before confirming the frame.')
            return False
        if self.raw is None:
            return True
        session, frame, current = self.session, self.frame, self.current()
        drafts, pending = self.drafts(), self.pending_rows()
        if not complete:
            drafts = saving.keep_untouched_pending(current, drafts, frame.box_edits, track)
            if not pending and drafts == current and not track:
                return True
        tracked = any(row.get('manual_track_id') for row in current) or \
            any(self.pending_tracked(i) for i in range(len(frame.pending)))
        if (complete or track) and tracked and session.frame_interval != 1 and not self.save_interval(1):
            return False
        if not drafts and not pending:
            if self.record_frame_completion():
                self.undo_stack.clear()
                self.nav.advance()
            return False
        if any(not r['species'] for r in drafts if r['review_status'] != 'rejected'):
            QMessageBox.warning(self, 'Species required', 'Choose or enter a species name, or cross the row out to drop it.')
            return False
        if any(not self.annotations.cellWidget(i, 0).currentText().strip() for i in range(len(frame.pending))):
            QMessageBox.warning(self, 'Species required', 'Choose a species for every box you have drawn, or remove it.')
            return False
        unknown = saving.unknown_species(drafts, pending, session.names)
        if unknown and QMessageBox.question(
                self, 'Unknown species', 'These names are not in the available species list. Save them anyway?\n\n'
                + '\n'.join(unknown)) != QMessageBox.StandardButton.Yes:
            return False
        saving.mark_reviewed(drafts, complete)
        additions = []
        if pending:
            try:
                image = session.ensure_frame_image(self.frame_number, self.raw)
            except OSError as exc:
                QMessageBox.critical(self, 'Changes not saved', str(exc))
                return False
            for index, row in enumerate(pending):
                track_id = ''
                if self.pending_tracked(index):
                    track_id = self.tracking.active = str(uuid4())
                additions.append(saving.manual_row(session, self.frame_number, row['species'],
                                                   [row[k] for k in ('x1', 'y1', 'x2', 'y2')], image, track_id))
        rows = saving.merge_frame(session.rows, current, drafts, additions)
        try:
            if (complete or (track and advance)) and any(
                    r.get('manual_track_id') and int(r['frame_number']) == self.frame_number for r in rows):
                rows = self.tracking.look_ahead(rows, track)
        except ValueError as exc:
            QMessageBox.warning(self, 'Track not saved', str(exc))
            return False
        try:
            session.save(rows)
        except OSError as exc:
            QMessageBox.critical(self, 'Changes not saved', str(exc))
            return False
        # Clear the drawn boxes while the tables still match the old rows.
        frame.pending = []
        self.annotations.setRowCount(0)
        self.undo_stack.clear()
        session.add_names(r['species'] for r in pending)
        if not complete:
            if not track:
                self.show_frame()
            return True
        if not self.record_frame_completion():
            self.show_frame()
            return False
        self._write_reviewed_image()
        self.nav.advance()
        return True

    def _write_reviewed_image(self):
        session = self.session
        try:
            folder = session.root / 'reviewed_frames'
            folder.mkdir(exist_ok=True)
            target = folder / f'frame_{self.frame_number:06d}.jpg'
            temporary = target.with_name(target.stem + '.tmp.jpg')
            if not cv2.imwrite(str(temporary), draw_review(self.raw, self.current(), context=self.frame_context())):
                raise OSError('Could not write reviewed image.')
            temporary.replace(target)
            self.status.setText('Frame complete. Ready for dataset export.' if session.manual_session else
                                'Frame confirmed. Results will update when you leave Annotate Data.')
        except OSError as exc:
            self.status.setText(f'CSV saved; reviewed image failed: {exc}. You can confirm again to retry.')

    def record_frame_completion(self):
        try:
            self.session.record_completion(self.frame_number)
            return True
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, 'Review completion not saved',
                                 f'Evaluation completion could not be recorded: {exc}. Any saved detection edits '
                                 'are retained. Confirm this frame again to retry.')
            return False

    # Leaving the page

    def leave(self):
        if self.worker or self.sam.worker:
            return
        callback = self.on_back
        if not self.session.manual_session:
            answer = QMessageBox.question(
                self, 'Update annotated video', 'Regenerate video with updated annotations?\n\n'
                'Yes updates the video, CSV and plots. No updates only the CSV and plots.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                callback = self.regenerate_video
        if self.leave_to(callback):
            callback()

    def leave_to(self, callback):
        """Save drafts before leaving. False if leaving must wait (or was cancelled)."""
        self.nav.pause()
        if self.sam.active and not self.sam.worker:
            if QMessageBox.question(self, 'Unfinished SAM annotation',
                                    'Discard the SAM preview and leave?') != QMessageBox.StandardButton.Yes:
                return False
            self.sam.clear()
        if self.worker or self.sam.worker or not self.save_frame(complete=False):
            return False
        root = self.session.root
        if root and not self.session.manual_session and results_need_refresh(root):
            self.refresh_results(callback)
            return False
        return True

    def _start_job(self, worker, message):
        self.worker = worker
        for widget in (self.back, self.previous, self.skip, self.confirm, self.table, self.annotations):
            widget.setEnabled(False)
        self.status.setText(message)
        worker.finished.connect(self.job_finished)

    def regenerate_video(self):
        session = self.session
        if not session.video:
            QMessageBox.warning(self, 'Cannot regenerate video', 'No annotated output video is associated with this run.')
            return
        self.after_refresh = self.on_back
        worker = ReviewVideoWorker(session.video, session.root, list(session.rows), self)
        worker.progress.connect(lambda percent: self.status.setText(f'Regenerating video… {percent}%'))
        worker.succeeded.connect(lambda _: self.status.setText('Video, CSV and plots updated.'))
        worker.failed.connect(self.video_failed)
        self._start_job(worker, 'Regenerating video with updated annotations…')
        worker.start()

    def video_failed(self, error):
        self.after_refresh = None
        QMessageBox.critical(self, 'Cannot regenerate video',
                             f'{error}\n\nYour annotations, CSV and plots are saved. The previous video has not been replaced.')

    def refresh_results(self, callback):
        self.nav.pause()
        self.after_refresh = callback
        worker = RefreshWorker(self.session.root, self.session.path, self)
        worker.succeeded.connect(self.refreshed)
        worker.failed.connect(self.refresh_failed)
        self._start_job(worker, 'Refreshing results from saved review decisions…')
        worker.start()

    def refreshed(self, species, missing_images):
        message = f'Results refreshed: {species} species. Summary CSV, reviewed detections and charts updated.'
        if missing_images:
            message += ' Some Max-N images could not be rebuilt because the raw source is unavailable.'
        self.status.setText(message)
        self.results_refreshed.emit()

    def refresh_failed(self, error):
        self.after_refresh = None
        QMessageBox.critical(self, 'Cannot refresh results', error)

    def job_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        for widget in (self.back, self.table, self.annotations):
            widget.setEnabled(True)
        self.show_frame()
        callback, self.after_refresh = self.after_refresh, None
        if callback:
            callback()

    def shutdown(self):
        self.shutting_down = True
        self.sam.shutdown()
        self.nav.pause()
        self._layout_timer.stop()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        self.video_reader.close()

    # Qt events

    def hideEvent(self, event):
        self.sam.stop_preload()
        self.nav.pause()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.loading:
            self._layout_timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.sam.schedule_preload()
        QTimer.singleShot(0, self.tracking.adjust_suggestions)
        self._layout_timer.start(0)
