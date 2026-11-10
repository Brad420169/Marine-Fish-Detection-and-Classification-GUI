"""SAM 2 video tracking for the fish followed in tracking mode.

Ticking Track? asks how many seconds of video to track (when the SAM model is installed;
otherwise the copy-forward lookahead in review_tracks.py is used). SAM's video tracker
(sam_video.py) then follows the fish through them with a progress bar (Stop keeps what
is done), saving each box as a pending suggestion, and you check them afterwards with
Next (tracking). Correcting a box changes only that box; the rest of the batch is kept.
Accepting the last box offers to track more, starting from it (as corrected).
Track multiple fish queues several fish behind one question and one progress window;
they are tracked one after another over the same stretch of video. When SAM decides the fish is no longer
visible it stops and says so on that frame; it never deletes anything.

Frames that already have a box for this fish (same class as the track, where the fish
should be) use that box, linked to the track rather than copied, and SAM skips them.
What made the box (you, SAM or the detector) does not matter, only its class. SAM only
runs on the frames without one.
"""
import time
from uuid import uuid4

from PyQt6.QtCore import QThread, Qt
from PyQt6.QtWidgets import QInputDialog, QProgressDialog

from review_io import box_identity, model_review_row, original_box, review_box
from review_tracks import GENERATED, VIDEO
from sam_assist import CHECKPOINT
from sam_video import SAMVideoWorker, VideoTracker


def _same_box(a, b, tolerance=1.0):
    return a is not None and b is not None and all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b))


class VideoFollow:
    DEFAULT_SECONDS = 1.0

    def __init__(self, page):
        self.page = page
        self.tracker = None    # VideoTracker for the open video (keeps SAM's memory between jobs)
        self.worker = None
        self.run = 0           # bumped on every restart or stop, so older results are ignored
        self.track = None      # track id being followed
        self.results = {}      # frame number -> box, or None where SAM lost the fish
        self.sources = {}      # frame number -> 'manual' (your box used), 'ai' (detector's) or 'sam'
        self.species = ''      # the track's class; only AI boxes of this class are used
        self.want = 0          # last frame of the batch
        self.lost_at = None
        self.restart = None    # (frame, box) to start from once the running job stops
        self.deferred = {}     # boxes for the frame on screen, held until its edits are saved
        self.track_seconds = self.DEFAULT_SECONDS  # last length asked for, in seconds of video
        self.queue = []        # (track, box) of fish still to track in this batch (Track multiple fish)
        self.fish_count = 0    # fish in this batch when several were chosen, else 0
        self.fish_index = 0
        self.multi_span = None  # (frame the fish were chosen on, last frame) for a multi-fish batch
        self.fish_results = []  # (species, frames tracked, frame lost or None) per finished fish
        self.batch = None      # (first, last) frame of the batch being computed
        self.progress = None   # QProgressDialog while a batch runs
        self.seconds = None    # measured seconds per frame, for the estimate
        self._last_step = None

    @property
    def enabled(self):
        page = self.page
        return bool(page.video_reader) and CHECKPOINT.is_file()

    def following(self, track):
        return self.enabled and track is not None and track == self.track

    def waiting(self, number):
        """SAM is still working towards this frame for the fish being followed."""
        return (self.following(self.page.tracking.active) and self.worker is not None
                and number not in self.results and (self.lost_at is None or number < self.lost_at))

    # Starting and steering

    def follow(self, number, box, ask=False):
        """The followed fish's box on ``number`` was accepted. Returns True if SAM starts tracking.

        ``ask`` (the Track? tick) always asks how many frames, then tracks from this box.
        """
        track = self.page.tracking.active
        if not self.enabled or track is None:
            return False
        if ask:
            count = self._ask(number)
            if count:
                self._restart(track, number, box, number + count)
            return bool(count)
        if track == self.track:
            if number < self.want or self.worker:
                return False  # mid-batch: accepted or corrected, only this box changes
            # The last box of the batch: offer more frames, continuing from this box.
            count = self._ask(number, more=True)
            if not count:
                return False
            if _same_box(self.results.get(number), box) and self.lost_at is None:
                self._extend(number + count)  # SAM's own box: keep its memory
            else:
                self._restart(track, number, box, number + count)  # corrected: start from your box
            return True
        count = self._ask(number)
        if count:
            self._restart(track, number, box, number + count)
            return True
        return False

    def fps(self):
        page = self.page
        return page.video_reader.fps or page.session.fps or 25.0

    def follow_many(self, number, fish):
        """Track several fish ((track, box) each, boxed on ``number``) over the same stretch of
        video, one after another, behind one question. Returns True if tracking starts."""
        if not self.enabled or not fish:
            return False
        count = self._ask(number, fish=len(fish))
        if not count:
            return False
        self.queue, self.fish_results = list(fish[1:]), []
        self.fish_count, self.fish_index = len(fish), 1
        self.multi_span = (number, number + count)
        track, box = fish[0]
        self._restart(track, number, box, number + count)
        return True

    def _next_fish(self):
        """The current fish of a multi-fish batch is done: start the next one."""
        self.fish_results.append(self._fish_result())
        track, box = self.queue.pop(0)
        self.fish_index += 1
        number, until = self.multi_span
        self._restart(track, number, box, until)

    def _fish_result(self):
        start = self.multi_span[0] if self.multi_span else None
        done = sum(1 for n, box in self.results.items() if box is not None and n != start)
        return self.species or 'fish', done, self.lost_at

    def _ask(self, number, more=False, fish=1):
        """Ask how many seconds of video to track; returns the number of frames (0 = cancelled)."""
        page = self.page
        left = page.total_frames - number
        if left < 1:
            return 0
        fps = self.fps()
        available = round(left / fps, 1) or 0.1
        speed = (f'SAM takes about {self.seconds:.1f} s per frame on this computer' if self.seconds
                 else 'SAM takes about a second per frame on a CPU')
        if fish > 1:
            question = f'Track these {fish} fish for how many seconds?'
        else:
            question = 'Track this fish for how many more seconds?' if more else 'Track this fish with SAM for how many seconds?'
        label = (f'{question}\n{available:.1f} s of video left ({fps:g} frames per second).\n'
                 f'{speed}; frames that already have a box are near-instant.')
        seconds, accepted = QInputDialog.getDouble(page, 'Track with SAM', label,
                                                   min(self.track_seconds, available), 0.1, available, 1)
        if not accepted:
            return 0
        self.track_seconds = seconds
        return max(1, min(left, round(seconds * fps)))

    def _restart(self, track, number, box, until):
        self.run += 1
        self.track, self.results, self.lost_at, self.deferred = track, {number: list(box)}, None, {}
        self.sources = {}
        self.species = next((r['species'] for r in self.page.session.frame_rows(number)
                             if r.get('manual_track_id') == track), '')
        self.want = until
        self._remove_suggestions(track, after=number)
        if self.worker:
            self.restart = (number, box)
            self.worker.requestInterruption()  # the restart happens when it stops
        else:
            self._launch(number, box)

    def _launch(self, number, box):
        page = self.page
        if self.tracker is None:
            self.tracker = VideoTracker(page.session.source_video, page.total_frames)
        self.tracker.reset(number, box)
        self.tracker.candidates = self._candidates(number + 1, self.want)
        self._start_worker()

    def _extend(self, until):
        before, self.want = self.want, max(self.want, until)
        if self.tracker:
            self.tracker.candidates.update(self._candidates(before + 1, self.want))
        if self.worker:
            self.worker.until = self.want
        elif self.lost_at is None and self.tracker and self.tracker.next <= self.want:
            self._start_worker()

    def _start_worker(self):
        page = self.page
        page.sam.stop_preload()  # both are CPU-heavy
        worker = SAMVideoWorker(self.tracker, self.want, page)
        worker.tracked.connect(lambda number, box, score, source, run=self.run:
                               self._tracked(run, number, box, score, source))
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.worker = worker
        self.batch = (self.tracker.next, min(self.want, page.total_frames))
        self._show_progress()
        self._last_step = None  # the first frame also loads the model, so it is not timed
        worker.start(QThread.Priority.LowPriority)
        page.update_controls()

    # Progress bar

    def _show_progress(self):
        first, last = self.batch
        total = (last - first + 1) / self.fps()
        if self.progress is None:
            dialog = QProgressDialog('', 'Stop', 0, 1, self.page)
            dialog.setWindowTitle('Tracking with SAM')
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.canceled.connect(self.cancel)
            self.progress = dialog
        self.progress.setRange(0, last - first + 1)
        self.progress.setValue(0)
        self.progress.setLabelText(f'{self._fish_label()}SAM is following the fish: 0.0 / {total:.1f} s '
                                   f'(frames {first}–{last})…')
        self.progress.show()

    def _update_progress(self, number):
        if not self.progress or not self.batch:
            return
        now = time.monotonic()
        if self._last_step is not None:
            step = now - self._last_step
            self.seconds = step if self.seconds is None else 0.7 * self.seconds + 0.3 * step
        self._last_step = now
        first, last = self.batch
        fps = self.fps()
        dialog = self.progress  # setValue runs pending events, which may finish the batch
        left = (f' · about {(last - number) * self.seconds:.0f} s to go'
                if self.seconds and number < last else '')
        dialog.setLabelText(f'{self._fish_label()}SAM is following the fish: '
                            f'{(number - first + 1) / fps:.1f} / {(last - first + 1) / fps:.1f} s '
                            f'(frame {number}){left}')
        dialog.setValue(number - first + 1)

    def _fish_label(self):
        return f'Fish {self.fish_index} of {self.fish_count}. ' if self.fish_count > 1 else ''

    def _close_progress(self):
        if self.progress:
            self.progress.hide()  # hide, not close: closing would emit canceled
            self.progress.deleteLater()
        self.progress = None

    def cancel(self):
        """Stop pressed: keep the boxes SAM has made, compute no more."""
        done = [n for n, box in self.results.items() if box is not None]
        self.want = max(done, default=self.want)
        self.restart = None
        self.queue = []  # Stop ends the whole batch, not just this fish
        if self.worker:
            self.worker.requestInterruption()

    def stop(self):
        """Stop following (Finish fish, a deleted box, another run opened)."""
        self.run += 1
        self.track, self.results, self.lost_at, self.restart, self.deferred = None, {}, None, None, {}
        self.queue, self.fish_count, self.multi_span = [], 0, None
        self._close_progress()
        if self.worker:
            self.worker.requestInterruption()

    def new_source(self):
        self.stop()
        if self.tracker and not self.worker:
            self.tracker.close()
        self.tracker = None

    def shutdown(self):
        self.stop()
        if self.worker:
            self.worker.wait()
        if self.tracker:
            self.tracker.close()

    # Results

    def _candidates(self, first, last):
        """Existing boxes of the track's class on these frames, as (box, kind).

        Any box not on another fish's track counts, whatever made it; the kind only says
        which saved row to link: 'manual' (drawn by you or SAM) or 'ai' (saved AI rows and
        confident detections never flagged for review). This fish's own unchecked
        suggestions are left out, since this run replaces them.
        """
        session, track, wanted = self.page.session, self.track, self.species.strip().casefold()
        candidates = {}
        for number in range(first, min(last, self.page.total_frames) + 1):
            boxes, represented = [], set()
            for row in session.frame_rows(number):
                manual = row.get('annotation_source') == 'manual'
                if not manual:
                    represented.add(box_identity(original_box(row)))
                if row.get('review_status') == 'rejected' or row['species'].strip().casefold() != wanted:
                    continue
                owner = row.get('manual_track_id') or ''
                if manual and (owner == '' or (owner == track and not _untouched(row))):
                    boxes.append((review_box(row), 'manual'))
                elif not manual and owner in ('', track):
                    boxes.append((review_box(row), 'ai'))
            for detection in session.detections_by_frame.get(number, ()):
                if (box_identity(detection['bbox']) not in represented
                        and str(detection['species']).strip().casefold() == wanted):
                    boxes.append(([float(v) for v in detection['bbox']], 'ai'))
            if boxes:
                candidates[number] = boxes
        return candidates

    def _tracked(self, run, number, box, score, source='sam'):
        if run != self.run or number in self.results or number > self.want:
            return  # stale, the frame the fish was boxed on, or past a Stop
        self._take(number, box, score, source)
        # Last: on a modal progress dialog setValue runs pending events (even the job
        # finishing), so all state for this frame must already be recorded.
        self._update_progress(number)

    def _take(self, number, box, score, source):
        page = self.page
        self.results[number] = box
        self.sources[number] = source
        if box is None:
            self.lost_at = number
            if page.frame_number == number:
                page.status.setText(self.lost_message())
            page.update_controls()
            return
        if number == page.frame_number and page.has_unsaved_edits():
            self.deferred[number] = (box, source)
            page.status.setText("This fish's box for this frame is ready. It appears once your edits here are saved.")
            return
        self._save(number, box, source)
        if number == page.frame_number:
            page.show_frame()
            page.status.setText({'manual': 'Using your box for this fish here. Check it, then Next.',
                                 'ai': 'Using the AI box for this fish here. Check it, then Next.'}.get(
                source, f'SAM followed the fish to this frame (score {score:.1f}). Check the box, then Next.'))
        else:
            page.update_controls()

    def apply_deferred(self):
        """Called as a frame opens: save SAM's box for it if one was held back."""
        held = self.deferred.pop(self.page.frame_number, None)
        if held is not None:
            self._save(self.page.frame_number, *held)

    def _save(self, number, box, source):
        """Put the followed fish's box for ``number`` in the saved rows.

        An existing box is linked to the track (your box or the AI row gains the track
        id; a confident detection that was never flagged becomes a row). A SAM box is
        added as a new pending suggestion. Either replaces an earlier unreviewed
        suggestion there.
        """
        page, track = self.page, self.track
        session = page.session
        rows = session.rows
        on_frame = [r for r in rows if r.get('manual_track_id') == track and r['frame_number'] == number]
        if any(not _untouched(r) for r in on_frame):
            return  # this fish already has a reviewed or linked box here
        keep = [r for r in rows if not any(r is old for old in on_frame)]
        if source in ('manual', 'ai'):
            keep = self._link(keep, number, box, source)
            if keep is None:
                return
        else:
            previous = max((r for r in rows if r.get('manual_track_id') == track and r['frame_number'] < number
                            and r.get('review_status') != 'rejected'), key=lambda r: r['frame_number'], default=None)
            if previous is None:
                return
            new = dict(previous, frame_number=number, annotation_id=str(uuid4()), annotation_source='manual',
                       original_bbox='', original_species='', confidence='', track_id='', rejection_source='',
                       review_status='pending', reviewed='', timestamp=session.timestamp(number), frame_image='',
                       track_keyframe=VIDEO)
            new.update(zip(('x1', 'y1', 'x2', 'y2'), box))
            keep.append(new)
        try:
            session.save(keep)
        except (OSError, ValueError) as exc:
            page.status.setText(f'Tracked box not saved: {exc}')

    def _link(self, rows, number, box, kind):
        """Rows with the existing box on ``number`` linked to the followed fish, or None if it is gone."""
        identity = box_identity(box)
        for index, row in enumerate(rows):
            if (row['frame_number'] == number and (row.get('annotation_source') == 'manual') == (kind == 'manual')
                    and row.get('review_status') != 'rejected' and row.get('manual_track_id') in ('', None, self.track)
                    and box_identity(review_box(row)) == identity):
                rows[index] = dict(row, manual_track_id=self.track)
                return rows
        if kind == 'manual':
            return None
        session = self.page.session
        detection = next((d for d in session.detections_by_frame.get(number, ())
                          if box_identity(d['bbox']) == identity), None)
        if detection is None:
            return None
        new = model_review_row(detection, number, session.fields)
        new.update(manual_track_id=self.track, timestamp=session.timestamp(number))
        return rows + [new]

    def _remove_suggestions(self, track, after):
        """Drop this fish's unreviewed suggestions after ``after`` and unlink its unreviewed
        AI boxes there (tracking is starting over from this box)."""
        session = self.page.session
        rows = [dict(r, manual_track_id='') if (r.get('manual_track_id') == track and r['frame_number'] > after
                                                 and r.get('annotation_source') != 'manual'
                                                 and r.get('reviewed') != 'yes') else r
                for r in session.rows
                if not (r.get('manual_track_id') == track and r['frame_number'] > after and _untouched(r))]
        if rows != session.rows:
            try:
                session.save(rows)
            except OSError as exc:
                self.page.status.setText(f'Old suggestions not removed: {exc}')

    def _failed(self, error):
        self.page.status.setText(f'SAM video tracking stopped: {error}')
        self.stop()

    def stop_progress_message(self):
        if not self.batch:
            return ''
        first, last = self.batch
        done = sorted(n for n, box in self.results.items() if box is not None and first <= n <= last)
        message = (f'Tracked the fish on {len(done)} frame{"s" if len(done) != 1 else ""}'
                   + (f' ({done[0]}–{done[-1]})' if done else ''))
        from_yours = sum(self.sources.get(n) == 'manual' for n in done)
        from_ai = sum(self.sources.get(n) == 'ai' for n in done)
        if done:
            message += (f': {from_yours} from your boxes,' if from_yours else ':') + \
                f' {from_ai} from AI boxes, {len(done) - from_yours - from_ai} by SAM'
        message += '.'
        if self.lost_at is not None:
            message += f' It thinks the fish left or is hidden at frame {self.lost_at}.'
        return message + ' Check each box with Next (tracking); correct any that are off.'

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        page = self.page
        if self.restart:
            (number, box), self.restart = self.restart, None
            self._launch(number, box)
        elif (self.track and self.lost_at is None and self.tracker
              and self.tracker.next <= min(self.want, page.total_frames)
              and worker and not worker.isInterruptionRequested()):
            self._start_worker()  # more frames were asked for while it was finishing
        elif self.queue and self.track:
            self._next_fish()
        else:
            self._close_progress()
            if self.fish_count > 1 and self.track:
                page.status.setText(self.multi_message())
                page.tracking.active = None  # check every fish with plain Next
                self.fish_count, self.multi_span = 0, None
            elif self.track:
                page.status.setText(self.stop_progress_message())
            page.sam.schedule_preload()
        page.update_controls()

    def multi_message(self):
        results = self.fish_results + [self._fish_result()]
        number, until = self.multi_span
        message = (f'Tracked {len(results)} fish over {(until - number) / self.fps():.1f} s '
                   f'(frames {number + 1}–{until}).')
        lost = [f'{species} at frame {at}' for species, _, at in results if at is not None]
        if lost:
            message += ' SAM lost ' + ', '.join(lost) + '.'
        return message + ' Check the boxes with Next, then click each fish and press Confirm track.'

    def lost_message(self):
        return ('SAM thinks the fish has left or is hidden on this frame. Draw it and tick Track? to keep '
                'following it, or go back a frame and press Finish fish.')


def _untouched(row):
    """A suggested box nobody has reviewed yet."""
    return (row.get('track_keyframe') in GENERATED and row.get('review_status') == 'pending'
            and row.get('reviewed') != 'yes')
