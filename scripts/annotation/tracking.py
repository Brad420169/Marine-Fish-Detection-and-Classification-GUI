"""Following one fish across frames on the annotation page.

The track bookkeeping (lookahead, gap filling, ending a track) is in review_tracks.py;
this class owns the page-side state: which fish is being followed, the Track? prompt
and Confirm track, Next (tracking), Finish fish, and SAM adjustment of suggested boxes. When the SAM model
is installed, SAM's video tracker supplies the boxes instead (video_follow.py).
"""
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMessageBox

from ui_icons import icon

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
        self.adjusted = set()           # suggested boxes SAM already tried once
        self.multi = None               # while choosing fish for Track multiple fish: their row keys
        self.video = VideoFollow(page)

    def reset(self):
        self.video.new_source()
        self.cancel_multi(quiet=True)
        self.active = None
        self.prompt_key = None
        self.adjusted = set()

    # Track? prompt

    def offer(self, row):
        """Show the Track? prompt for a saved box (just saved, or clicked)."""
        if self.multi is not None:
            self.toggle_multi(row)  # choosing fish for Track multiple fish
            return
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
        if page.loading or source is None or self.multi is not None:
            page.track_prompt.hide()
            return
        enabled = bool(source.get('manual_track_id'))
        page.track_yes.setChecked(enabled)
        page.track_no.setChecked(not enabled)
        page.track_yes.setEnabled(bool(page.video_reader) and page.frame_number < page.total_frames)
        page.track_prompt.setToolTip(f'Track {source["species"]} into the next frames. Optional; off by default.')
        unchecked = len(self.unchecked_boxes(source.get('manual_track_id')))
        page.confirm_track.setVisible(bool(unchecked))
        page.confirm_track.setText(f'Confirm track ({unchecked})')
        page.confirm_track.setEnabled(not page.worker and not self.video.worker)
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

    # Track multiple fish

    def multi_clicked(self):
        """The Track multiple fish button: start choosing fish, or track the chosen ones."""
        if self.multi is None:
            self.start_multi()
        else:
            self.track_selected()

    def start_multi(self):
        page = self.page
        if page.worker or self.video.worker or page.raw is None:
            return
        if not self.video.enabled:
            QMessageBox.information(page, 'Track multiple fish',
                                    'This needs the source video and the SAM model ("pixi run setup-sam").')
            return
        if page.frame.pending or page.sam.active:
            page.status.setText('Save your boxes first: type a species for each, then press Enter.')
            return
        self.multi = set()
        self.hide_prompt()
        self._show_multi()

    def toggle_multi(self, row):
        if row.get('review_status') == 'rejected':
            return
        self.multi ^= {_row_key(row)}
        self._show_multi()

    def multi_rows(self):
        if not self.multi:
            return []
        return [r for r in self.page.current() if _row_key(r) in self.multi and r.get('review_status') != 'rejected']

    def _show_multi(self):
        page, count = self.page, len(self.multi_rows())
        page.multi_button.setIcon(QIcon())  # room for the text in the narrow panel
        page.multi_button.setText(f'Track {count} fish (Enter)' if count else 'Select fish, then Enter')
        page.status.setText(f'{count} fish selected. Click more fish (click again to unselect), then press Enter. '
                            'Esc cancels.' if count else
                            'Select fish: click each fish\'s box, then press Enter. Esc cancels.')
        page.render()

    def cancel_multi(self, quiet=False):
        if self.multi is None:
            return
        self.multi = None
        page = self.page
        page.multi_button.setText('Track multiple fish')
        page.multi_button.setIcon(icon('play'))
        if not quiet:
            page.status.setText('Track multiple fish cancelled.')
            page.render()

    def track_selected(self):
        """Enter: give each chosen fish a track (or keep its own), ask for seconds once, track them all."""
        page = self.page
        chosen = self.multi_rows()
        if not chosen:
            page.status.setText('Click at least one fish first, or press Esc to cancel.')
            return
        rows, fish = page.session.rows, []
        try:
            for source in chosen:
                if source.get('manual_track_id'):
                    track = source['manual_track_id']
                else:
                    rows, track = create_track(rows, source)
                fish.append((track, review_box(source)))
            page.session.save(rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(page, 'Tracking not saved', str(exc))
            return
        self.cancel_multi(quiet=True)
        page.undo_stack.clear()
        page.render()
        if not self.video.follow_many(page.frame_number, fish):
            page.status.setText(f'{len(fish)} fish marked for tracking. Tick Track? on any of them to track it.')
        page.update_controls()

    # Confirming a track

    def unchecked_boxes(self, track):
        """This fish's boxes nobody has checked yet (SAM or AI suggestions)."""
        if not track:
            return []
        return [r for r in self.page.session.rows
                if r.get('manual_track_id') == track and r.get('review_status') == 'pending']

    def confirm_prompted(self):
        """Confirm track button: accept every box of the clicked fish's track."""
        source = self._prompt_source()
        if source is not None and source.get('manual_track_id'):
            self.confirm_track(source['manual_track_id'])

    def confirm_track(self, track):
        """Accept all of a fish's unchecked boxes and finish its track. Returns True when saved."""
        page = self.page
        if page.worker or self.video.worker or not page.save_frame(refresh=False):
            return False
        unchecked = {id(r) for r in self.unchecked_boxes(track)}
        rows = [dict(r, review_status='confirmed', reviewed='yes') if id(r) in unchecked else r
                for r in page.session.rows]
        frames = [int(r['frame_number']) for r in rows
                  if r.get('manual_track_id') == track and r.get('review_status') != 'rejected']
        if frames:
            rows = end_track(rows, track, max(frames))  # nothing more is suggested for this fish
        try:
            page.session.save(rows)
        except OSError as exc:
            QMessageBox.critical(page, 'Track not confirmed', str(exc))
            return False
        if track == self.active or track == self.video.track:
            self.active = None
            self.video.stop()
        page.undo_stack.clear()
        page.show_frame()
        page.status.setText(f'Track confirmed: {len(unchecked)} box{"es" if len(unchecked) != 1 else ""} accepted.')
        return True

    def confirm_first(self):
        """Before a new box is started: a fish with unchecked tracked boxes must be confirmed
        first. Returns True when it is fine to go on."""
        page = self.page
        pending = [r for r in page.session.rows if r.get('manual_track_id') and r.get('review_status') == 'pending']
        if not pending:
            return True
        track = pending[0]['manual_track_id']
        count = sum(r['manual_track_id'] == track for r in pending)
        species = next((r['species'] for r in page.session.rows if r.get('manual_track_id') == track), 'fish')
        box = QMessageBox(QMessageBox.Icon.Question, 'Confirm the track first',
                          f'The "{species}" track still has {count} unchecked box{"es" if count != 1 else ""}. '
                          'Confirm that track before adding another box?\n\n'
                          'Check and fix its boxes first if any are wrong.', parent=page)
        confirm = box.addButton('Confirm track', QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is confirm and self.confirm_track(track)

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
        """Save the tracked fish's box (accepted) and move on."""
        page = self.page
        number = page.frame_number
        if not page.save_frame(track=self.active):
            return
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
                                'Draw this fish and tick Track? to follow it again, or carry on with Next.')

    def finish(self):
        """Stop following the active fish on this frame; its box here is kept."""
        page = self.page
        track, number = self.active, page.frame_number
        if not track or not page.save_frame(track=track, advance=False):
            return
        self.video.stop()
        try:
            page.session.save(end_track(page.session.rows, track, number))
        except OSError as exc:
            QMessageBox.critical(page, 'Track not finished', str(exc))
            return
        self.active = None
        page.undo_stack.clear()
        page.show_frame()
        page.status.setText('Finished this fish. Its box here is kept and later suggestions were removed. '
                            'Click one of its boxes and press Confirm track to accept the rest.')

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
        return advance_tracks(rows, number, page.total_frames if page.video_reader else number,
                              page.session.detections_by_frame.get(number + 1, []),
                              detections_by_frame=page.session.detections_by_frame,
                              lookahead=self.LOOKAHEAD, track_ids={track} if track else None)

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
