"""Following one fish across frames on the annotation page.

The track bookkeeping (lookahead, gap filling, ending a track) is in review_tracks.py;
this class owns the page-side state: which fish is being followed, the Track? prompt,
Next (tracking), Finish fish, and SAM adjustment of suggested boxes.
"""
from PyQt6.QtWidgets import QMessageBox

from review_io import review_box
from review_tracks import COPIED, GENERATED, advance_tracks, create_track, end_track


class TrackingController:
    LOOKAHEAD = 3

    def __init__(self, page):
        self.page = page
        self.active = None              # track id followed by Next (tracking)
        self.prompt_annotation = None   # just-saved annotation the Track? prompt refers to
        self.reopened = set()           # completed frames a suggestion reopened
        self.adjusted = set()           # suggested boxes SAM already tried once

    def reset(self):
        self.active = None
        self.prompt_annotation = None
        self.reopened = set()
        self.adjusted = set()

    # Track? prompt

    def offer(self, annotation_id):
        """Show the Track? prompt for a box that was just saved."""
        self.prompt_annotation = annotation_id

    def hide_prompt(self):
        self.prompt_annotation = None
        self.page.track_prompt.hide()

    def _prompt_source(self):
        return next((row for row in self.page.current()
                     if row.get('annotation_id') == self.prompt_annotation
                     and row.get('review_status') != 'rejected'), None)

    def position_prompt(self):
        page = self.page
        source = self._prompt_source()
        if page.loading or source is None:
            page.track_prompt.hide()
            return
        enabled = bool(source.get('manual_track_id'))
        page.track_yes.setChecked(enabled)
        page.track_no.setChecked(not enabled)
        page.track_yes.setEnabled(bool(page.video_reader) and page.frame_number < page.total_frames)
        page.track_prompt.setToolTip(f'Track {source["species"]} into the next frame. Optional; off by default.')
        page.track_prompt.show()

    def set_saved(self, enabled):
        """Tracking is optional and applies only to the just-saved annotation."""
        page = self.page
        if page.worker:
            return
        source = self._prompt_source()
        if source is None:
            page.track_prompt.hide()
            return
        try:
            rows = page.session.rows
            if enabled and not source.get('manual_track_id'):
                rows, self.active = create_track(rows, self.prompt_annotation)
            elif not enabled and source.get('manual_track_id'):
                if source['manual_track_id'] == self.active:
                    self.active = None
                rows = [dict(row, manual_track_id='', track_keyframe='')
                        if row is source else row for row in rows]
            page.session.save(rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(page, 'Tracking not saved', str(exc))
            self.position_prompt()
            return
        page.undo_stack.clear()
        self.position_prompt()
        page.update_controls()
        page.image.setFocus()

    # Tracking mode

    def is_active(self):
        """True when this frame shows a live box for the fish being tracked."""
        page = self.page
        if not self.active or page.raw is None:
            return False
        decisions = page.frame.decisions
        return any(row.get('manual_track_id') == self.active
                   and i < len(decisions) and decisions[i] != 'rejected'
                   for i, row in enumerate(page.current()))

    def next(self):
        """Save the tracked fish's box and move on; the frame is not marked complete."""
        page = self.page
        number = page.frame_number
        if not page.save_frame(complete=False, track=self.active):
            return
        self.restore_reopened()
        if number >= page.total_frames or not page.video_reader:
            self.active = None
            page.show_frame()
            page.status.setText('End of the video. Tracking finished.')
            return
        page.nav.advance()
        if self.active and not self.is_active():
            page.status.setText('The track stopped before this frame (competing AI boxes or a rejected detection). '
                                'Draw this fish and tick Track? to follow it again, or carry on with Confirm & Next.')

    def finish(self):
        """Stop following the active fish on this frame; its box here is kept."""
        page = self.page
        track, number = self.active, page.frame_number
        if not track or not page.save_frame(complete=False, track=track, advance=False):
            return
        try:
            page.session.save(end_track(page.session.rows, track, number))
        except OSError as exc:
            QMessageBox.critical(page, 'Track not finished', str(exc))
            return
        self.active = None
        self.restore_reopened()
        page.undo_stack.clear()
        page.show_frame()
        page.status.setText('Finished this fish. Its box here is kept and later suggestions were removed. '
                            'Go back and track the next fish, or Confirm & Next to complete frames.')

    def box_deleted(self, row):
        if row.get('manual_track_id') and row['manual_track_id'] == self.active:
            self.active = None

    def look_ahead(self, rows, track=None):
        """Suggest this frame's tracks on the next frames (only ``track`` in tracking mode)."""
        page = self.page
        number = page.frame_number
        completed = page.session.completed_frames()
        rows = advance_tracks(rows, number, page.total_frames if page.video_reader else number,
                              page.session.detections_by_frame.get(number + 1, []),
                              detections_by_frame=page.session.detections_by_frame,
                              lookahead=self.LOOKAHEAD, completed_frames=completed,
                              track_ids={track} if track else None)
        if track:
            self.reopened |= {n for n in range(number + 1, number + 1 + self.LOOKAHEAD) if n in completed}
        return rows

    def restore_reopened(self):
        """Re-complete frames a track reopened, once every box on them is reviewed again."""
        session = self.page.session
        for number in sorted(self.reopened):
            rows = [r for r in session.rows if int(r['frame_number']) == number]
            if any(r['review_status'] not in ('confirmed', 'rejected') for r in rows):
                continue
            try:
                session.record_completion(number)
            except (OSError, ValueError, KeyError):
                continue
            self.reopened.discard(number)

    # SAM adjustment of suggested boxes

    def adjust_suggestions(self):
        """When a frame opens, let SAM fit each untouched suggested box to the fish once."""
        page = self.page
        if (page.shutting_down or not page.isVisible() or page.nav.playing or page.raw is None
                or page.worker or page.sam.worker or page.sam.active):
            return
        edits = page.frame.box_edits
        boxes = [(row['annotation_id'], review_box(row)) for index, row in enumerate(page.current())
                 if row.get('manual_track_id') and row.get('track_keyframe') in GENERATED
                 and row.get('review_status') == 'pending' and index not in edits
                 and row['annotation_id'] not in self.adjusted]
        if boxes:
            page.sam.adjust_boxes(boxes, self.apply_adjustments)

    def apply_adjustments(self, results, token):
        page = self.page
        if token != page.sam.frame_token:
            return
        by_id = {row.get('annotation_id'): (index, row) for index, row in enumerate(page.current())}
        edits = page.frame.box_edits
        adjusted, failures, lost = 0, [], []
        for result in results:
            identity, box, error = result[:3]
            not_found = len(result) > 3 and result[3]
            if identity not in by_id:
                continue
            index, row = by_id[identity]
            if (row.get('track_keyframe') not in GENERATED or row.get('review_status') != 'pending'
                    or index in edits or identity in self.adjusted):
                continue
            copied = row.get('track_keyframe') == COPIED
            self.adjusted.add(identity)
            if box is not None:
                edits[index] = box
                adjusted += 1
            elif copied and not_found:
                # No AI match and SAM found no distinct fish. On reef footage SAM can't
                # reliably tell a hidden fish from empty background, so warn, don't delete.
                lost.append(index)
            else:
                failures.append(error)
        page.render()
        message = f'SAM adjusted {adjusted} tracked boxes. Check each fish before confirming.' if adjusted else ''
        if lost:
            message += (f' SAM could not find the fish in {len(lost)} copied box{"es" if len(lost) > 1 else ""}; '
                        'it may have left. Delete the box to end its track, or adjust it and carry on.')
        if failures:
            message += f' {len(failures)} boxes kept their copied position; adjust or delete them manually. {failures[0]}'
        page.status.setText(message.strip())
