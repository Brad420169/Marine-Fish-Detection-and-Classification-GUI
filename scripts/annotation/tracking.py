"""Following one fish across frames on the annotation page.

The track bookkeeping (lookahead, gap filling, ending a track) is in review_tracks.py;
this class owns the page-side state: which fish is being followed, the Track? prompt,
Next (tracking), Finish fish, and SAM adjustment of suggested boxes. When the SAM model
is installed, SAM's video tracker supplies the boxes instead (video_follow.py).
"""
from PyQt6.QtWidgets import QMessageBox

from review_io import box_identity, original_box, review_box
from review_tracks import COPIED, INTERPOLATED, advance_tracks, create_track, end_track

from annotation.video_follow import VideoFollow

# Lookahead boxes SAM's image model may refit when their frame opens (not SAM video boxes).
REFITTABLE = (INTERPOLATED, COPIED)


class TrackingController:
    LOOKAHEAD = 3

    def __init__(self, page):
        self.page = page
        self.active = None              # track id followed by Next (tracking)
        self.prompt_key = None          # the box the Track? prompt refers to (see _row_key)
        self.reopened = set()           # completed frames a suggestion reopened
        self.adjusted = set()           # suggested boxes SAM already tried once
        self.video = VideoFollow(page)

    def reset(self):
        self.video.new_source()
        self.active = None
        self.prompt_key = None
        self.reopened = set()
        self.adjusted = set()

    # Track? prompt

    def offer(self, row):
        """Show the Track? prompt for a saved box (just saved, or clicked)."""
        self.prompt_key = None if row.get('review_status') == 'rejected' else _row_key(row)

    def hide_prompt(self):
        self.prompt_key = None
        self.page.track_prompt.hide()

    def _prompt_source(self):
        if self.prompt_key is None:
            return None
        return next((row for row in self.page.current()
                     if _row_key(row) == self.prompt_key and row.get('review_status') != 'rejected'), None)

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
        start = None
        try:
            rows = page.session.rows
            if enabled:
                start = review_box(source)
                if source.get('manual_track_id'):
                    self.active = source['manual_track_id']  # already tracked: follow it from this box
                else:
                    rows, self.active = create_track(rows, source)
            elif source.get('manual_track_id'):
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
        if enabled and self.video.enabled and self.active and start is not None:
            self.video.follow(page.frame_number, start, ask=True)  # asks how many frames, then tracks them
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
            self.video.stop()
            page.show_frame()
            page.status.setText('End of the video. Tracking finished.')
            return
        accepted = next((r for r in page.session.frame_rows(number)
                         if r.get('manual_track_id') == self.active and r.get('review_status') != 'rejected'), None)
        if accepted and self.video.enabled:
            self.video.follow(number, review_box(accepted))
        page.nav.advance()
        if self.video.following(self.active):
            if page.frame_number == self.video.lost_at:
                page.status.setText(self.video.lost_message())
            elif self.video.waiting(page.frame_number):
                page.status.setText('SAM is following the fish to this frame…')
        elif self.active and not self.is_active():
            page.status.setText('The track stopped before this frame (competing AI boxes or a rejected detection). '
                                'Draw this fish and tick Track? to follow it again, or carry on with Confirm & Next.')

    def finish(self):
        """Stop following the active fish on this frame; its box here is kept."""
        page = self.page
        track, number = self.active, page.frame_number
        if not track or not page.save_frame(complete=False, track=track, advance=False):
            return
        self.video.stop()
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
            self.video.stop()

    def look_ahead(self, rows, track=None):
        """Suggest this frame's tracks on the next frames (only ``track`` in tracking mode)."""
        page = self.page
        if track and self.video.enabled:
            return rows  # SAM's video tracker supplies this fish's boxes
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
                 if row.get('manual_track_id') and row.get('track_keyframe') in REFITTABLE
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
        for identity, box, error, not_found in results:
            if identity not in by_id:
                continue
            index, row = by_id[identity]
            if (row.get('track_keyframe') not in REFITTABLE or row.get('review_status') != 'pending'
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


def _row_key(row):
    """Identifies a saved box across saves: its annotation id, or an AI box's frame and original position."""
    if row.get('annotation_id'):
        return row['annotation_id']
    return ('model', row['frame_number'], box_identity(original_box(row)))
