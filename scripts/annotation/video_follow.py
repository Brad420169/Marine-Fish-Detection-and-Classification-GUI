"""SAM 2 video tracking for the fish followed in tracking mode.

Ticking Track? (when the SAM model is installed; otherwise the copy-forward lookahead
in review/tracks.py is used) asks for a limit, then SAM's video tracker (sam/video.py)
follows the fish for as long as it stays on screen: first backwards from your box to
where the fish appeared, then forwards to where it leaves, each way up to the limit.
SAM decides when the fish has gone, but only after missing it for a short grace period,
so a fish hiding behind coral for a moment is followed out the other side. Frames where
SAM was unsure (a low score, or the fish briefly out of sight) are listed afterwards so
you know where to look.

Boxes are saved already accepted, so nothing needs ticking: browse the frames and move
or resize any box that is off; correcting a box changes only that box. A progress window
shows both passes (Stop keeps what is done). Tick Track? on any box to track again from it.
Track multiple fish queues several fish behind one question and one progress window.

Frames that already have a box for this fish (same class as the track, where the fish
should be) use that box, linked to the track rather than copied, and SAM skips them.
What made the box (you, SAM or the detector) does not matter, only its class. These
boxes also pull SAM back onto the fish, so long runs do not drift.
"""
import time
from uuid import uuid4

from PyQt6.QtCore import QThread, Qt
from PyQt6.QtWidgets import QProgressDialog

from review.io import box_identity, model_review_row, original_box, review_box
from review.tracks import VIDEO, unlink_suggestion, untouched_suggestion
from sam.assist import CHECKPOINT
from annotation.track_dialog import TrackDialog, remember_speed, remembered_speed, sam_device
from sam.video import GRACE_SECONDS, REFOUND_FRAMES, UNSURE_SCORE, SAMVideoWorker, VideoTracker

BACK, FORWARD = -1, 1


def frame_ranges(numbers):
    """'212–220, 305' for a set of frame numbers."""
    numbers, parts, start = sorted(numbers), [], None
    for index, number in enumerate(numbers):
        if start is None:
            start = number
        if index + 1 == len(numbers) or numbers[index + 1] != number + 1:
            parts.append(f'{start}' if start == number else f'{start}–{number}')
            start = None
    return ', '.join(parts)


class VideoFollow:
    DEFAULT_SECONDS = 30.0  # the limit each way; most fish leave well before it

    def __init__(self, page):
        self.page = page
        self.tracker = None    # VideoTracker for the open video (keeps SAM's memory between jobs)
        self.worker = None
        self.run = 0           # bumped on every restart or stop, so older results are ignored
        self.track = None      # track id being followed
        self.results = {}      # frame number -> box, or None where SAM could not see the fish
        self.sources = {}      # frame number -> 'manual' (your box used), 'ai' (detector's) or 'sam'
        self.unsure = set()    # frames worth a look: SAM unsure, the fish out of sight, or just found again
        self._refound = 0      # frames still to flag after SAM found a hidden fish again
        self.species = ''      # the track's class; only boxes of this class are reused
        self.start = None      # (frame, box) the fish was boxed on
        self.passes = []       # (direction, last frame) still to run for this fish: back, then forward
        self.direction = FORWARD
        self.want = 0          # last frame of the pass running now
        self.appears_at = None  # first frame SAM saw the fish on, when it stopped going back
        self.lost_at = None    # frame the fish left on, when it stopped going forward
        self.restart = None    # (track, frame, box) to start from once the running job stops
        self.deferred = {}     # boxes for the frame on screen, held until its edits are saved
        self.track_seconds = self.DEFAULT_SECONDS  # last limit asked for, in seconds of video
        self.cap = 0           # that limit in frames
        self.queue = []        # (track, box) of fish still to track in this batch (Track multiple fish)
        self.fish_count = 0    # fish in this batch when several were chosen, else 0
        self.fish_index = 0
        self.multi_start = None  # frame the fish were chosen on, for a multi-fish batch
        self.fish_results = []   # per finished fish: (species, frames tracked, appears_at, lost_at, unsure)
        self.batch = None      # (first, last) frame of the pass being computed
        self.progress = None   # QProgressDialog while tracking runs
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
                and number not in self.results)

    def fps(self):
        page = self.page
        return page.video_reader.fps or page.session.fps or 25.0

    # Starting

    def follow(self, number, box, ask=False):
        """Track the active fish from its box on ``number``, after asking for the limit.
        Returns True if SAM starts tracking."""
        track = self.page.tracking.active
        if not self.enabled or track is None:
            return False
        if not self._ask(number):
            return False
        self.queue, self.fish_count, self.multi_start = [], 0, None
        self._restart(track, number, box)
        return True

    def follow_many(self, number, fish):
        """Track several fish ((track, box) each, boxed on ``number``), one after another,
        behind one question. Returns True if tracking starts."""
        if not self.enabled or not fish:
            return False
        if not self._ask(number, fish=len(fish)):
            return False
        self.queue, self.fish_results = list(fish[1:]), []
        self.fish_count, self.fish_index, self.multi_start = len(fish), 1, number
        track, box = fish[0]
        self._restart(track, number, box)
        return True

    def _ask(self, number, fish=1):
        """Ask for the limit (seconds each way). Returns True unless cancelled."""
        page = self.page
        fps = self.fps()
        before, after = (number - 1) / fps, (page.total_frames - number) / fps
        if max(before, after) * fps < 1:
            return False
        device = sam_device()
        if self.seconds is None:
            self.seconds = remembered_speed(device[0])  # measured last time on this computer
        seconds, accepted = TrackDialog.ask(page, before, after, fps, fish, self.track_seconds, device, self.seconds)
        if not accepted:
            return False
        self.track_seconds = seconds
        self.cap = max(1, round(seconds * fps))
        return True

    def _restart(self, track, number, box):
        self.run += 1
        self.track, self.start = track, (number, list(box))
        self.results, self.sources, self.unsure, self.deferred = {number: list(box)}, {}, set(), {}
        self._refound = 0
        self.appears_at = self.lost_at = None
        self.species = next((r['species'] for r in self.page.session.frame_rows(number)
                             if r.get('manual_track_id') == track), '')
        self._remove_suggestions(track, after=number)
        self.passes = self._plan(track, number)
        if self.worker:
            self.restart = (track, number, box)
            self.worker.requestInterruption()  # the restart happens when it stops
        elif not self._next_pass():
            self.page.status.setText('Nothing to track from here: this fish already has boxes before this frame, '
                                     'and this is the end of the video.' if number >= self.page.total_frames else
                                     'Nothing to track from here: this fish already has boxes on the frames around it.')

    def _plan(self, track, number):
        """Back to where this fish's track already has a box (or the limit), then forward."""
        earlier = [r['frame_number'] for r in self.page.session.rows
                   if r.get('manual_track_id') == track and r['frame_number'] < number]
        back = max(max(earlier, default=0) + 1, number - self.cap, 1)
        forward = min(number + self.cap, self.page.total_frames)
        return ([(BACK, back)] if back < number else []) + ([(FORWARD, forward)] if forward > number else [])

    def _next_pass(self):
        """Start the next pass for this fish. Returns False when there is none left."""
        if not self.passes:
            return False
        self.direction, self.want = self.passes.pop(0)
        page = self.page
        if self.tracker is None:
            self.tracker = VideoTracker(page.session.source_video, page.total_frames)
        number, box = self.start
        self.tracker.reset(number, box, self.direction)
        self._refound = 0
        low, high = sorted((number + self.direction, self.want))
        self.tracker.candidates = self._candidates(low, high)
        self._start_worker()
        return True

    def _start_worker(self):
        page = self.page
        page.sam.stop_preload()  # both are CPU-heavy
        grace = max(2, round(GRACE_SECONDS * self.fps()))
        worker = SAMVideoWorker(self.tracker, self.want, grace, page)
        worker.tracked.connect(lambda number, box, score, source, run=self.run:
                               self._tracked(run, number, box, score, source))
        worker.lost.connect(lambda number, run=self.run: self._gone(run, number))
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.worker = worker
        self.batch = (self.tracker.next, self.want)
        self._show_progress()
        self._last_step = None  # the first frame also loads the model, so it is not timed
        worker.start(QThread.Priority.LowPriority)
        page.update_controls()

    # Progress window

    def _pass_label(self):
        fish = f'Fish {self.fish_index} of {self.fish_count}. ' if self.fish_count > 1 else ''
        return fish + ('Back to where it appears' if self.direction == BACK else 'Forward to where it leaves')

    def _show_progress(self):
        first, last = self.batch
        frames = abs(last - first) + 1
        if self.progress is None:
            dialog = QProgressDialog('', 'Stop', 0, 1, self.page)
            dialog.setWindowTitle('Tracking with SAM')
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.canceled.connect(self.cancel)
            self.progress = dialog
        self.progress.setRange(0, frames)
        self.progress.setValue(0)
        self.progress.setLabelText(f'{self._pass_label()}: 0.0 / {frames / self.fps():.1f} s at most…')
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
        fps, done, frames = self.fps(), abs(number - first) + 1, abs(last - first) + 1
        dialog = self.progress  # setValue runs pending events, which may finish the pass
        dialog.setLabelText(f'{self._pass_label()}: {done / fps:.1f} / {frames / fps:.1f} s at most '
                            f'(frame {number}). SAM stops by itself when the fish has gone.')
        dialog.setValue(done)

    def _close_progress(self):
        if self.progress:
            self.progress.hide()  # hide, not close: closing would emit canceled
            self.progress.deleteLater()
        self.progress = None

    # Stopping

    def cancel(self):
        """Stop pressed: keep the boxes SAM has made, compute no more (for any fish)."""
        self.run += 1  # a frame SAM finishes after Stop is not saved
        self.passes, self.queue, self.restart = [], [], None
        if self.worker:
            self.worker.requestInterruption()

    def stop(self):
        """Stop following (a deleted box, another run opened)."""
        self.run += 1
        self.track, self.results, self.restart, self.deferred = None, {}, None, {}
        self.passes, self.queue, self.fish_count, self.multi_start = [], [], 0, None
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
        for number in range(max(first, 1), min(last, self.page.total_frames) + 1):
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
        if run != self.run or number in self.results:
            return  # stale, or the frame the fish was boxed on
        self._take(number, box, score, source)
        # Last: on a modal progress dialog setValue runs pending events (even the job
        # finishing), so all state for this frame must already be recorded.
        self._update_progress(number)

    def _take(self, number, box, score, source):
        page = self.page
        self.results[number] = box
        self.sources[number] = source
        if box is None:
            self.unsure.add(number)  # out of sight: worth a look
            self._refound = REFOUND_FRAMES  # and so are the first frames after it is found again
            return  # no box here; tracking carries on unless the fish stays out of sight
        if source != 'sam':
            self._refound = 0  # your box or the detector's confirms the fish
        elif self._refound or score < UNSURE_SCORE:
            self.unsure.add(number)  # just found again (maybe a different fish) or a weak match
            self._refound = max(0, self._refound - 1)
        if number == page.frame_number and page.has_unsaved_edits():
            self.deferred[number] = (box, source)
            page.status.setText("This fish's box for this frame is ready. It appears once your edits here are saved.")
            return
        self._save(number, box, source)
        if number == page.frame_number:
            page.show_frame()
            page.status.setText({'manual': 'Using your box for this fish here.',
                                 'ai': 'Using the AI box for this fish here.'}.get(
                source, f'SAM followed the fish to this frame (score {score:.1f}).'))
        else:
            page.update_controls()

    def _gone(self, run, number):
        """SAM missed the fish for the whole grace period, starting at ``number``."""
        if run != self.run:
            return
        misses = {n for n, box in self.results.items() if box is None and
                  (n >= number if self.direction == FORWARD else n <= number)}
        self.unsure -= misses  # where it really left is not a frame to check
        if self.direction == FORWARD:
            self.lost_at = number
        else:
            self.appears_at = number + 1

    def apply_deferred(self):
        """Called as a frame opens: save SAM's box for it if one was held back."""
        held = self.deferred.pop(self.page.frame_number, None)
        if held is not None:
            self._save(self.page.frame_number, *held)

    def _save(self, number, box, source):
        """Put the followed fish's box for ``number`` in the saved rows.

        An existing box is linked to the track (your box or the AI row gains the track
        id; a confident detection that was never flagged becomes a row). A SAM box is
        added as a new box. Either replaces an earlier unreviewed suggestion there.
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
            nearest = min((r for r in rows if r.get('manual_track_id') == track and r['frame_number'] != number
                           and r.get('review_status') != 'rejected'),
                          key=lambda r: abs(r['frame_number'] - number), default=None)
            if nearest is None:
                return
            # Saved accepted, so it counts without being ticked; reviewed stays blank until
            # someone edits it, so a restart can still replace it.
            new = dict(nearest, frame_number=number, annotation_id=str(uuid4()), annotation_source='manual',
                       original_bbox='', original_species='', confidence='', track_id='', rejection_source='',
                       review_status='confirmed', reviewed='', timestamp=session.timestamp(number), frame_image='',
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
                # The fish's box here: an unchecked AI box is accepted, like SAM's own boxes.
                status = 'confirmed' if row.get('review_status') == 'pending' else row.get('review_status')
                rows[index] = dict(row, manual_track_id=self.track, review_status=status)
                return rows
        if kind == 'manual':
            return None
        session = self.page.session
        detection = next((d for d in session.detections_by_frame.get(number, ())
                          if box_identity(d['bbox']) == identity), None)
        if detection is None:
            return None
        new = model_review_row(detection, number, session.fields)
        new.update(manual_track_id=self.track, timestamp=session.timestamp(number), review_status='confirmed')
        return rows + [new]

    def _remove_suggestions(self, track, after):
        """Drop this fish's unreviewed suggestions after ``after`` and unlink its unreviewed
        AI boxes there (tracking is starting over from this box)."""
        session = self.page.session
        rows = [unlink_suggestion(r) if (r.get('manual_track_id') == track and r['frame_number'] > after
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

    def _fish_result(self):
        start = self.start[0] if self.start else None
        done = sorted(n for n, box in self.results.items() if box is not None and n != start)
        return self.species or 'fish', done, self.appears_at, self.lost_at, set(self.unsure)

    def stop_progress_message(self):
        _, done, appears_at, lost_at, unsure = self._fish_result()
        message = (f'Tracked the fish on {len(done)} frame{"s" if len(done) != 1 else ""}'
                   + (f' ({done[0]}–{done[-1]})' if done else ''))
        from_yours = sum(self.sources.get(n) == 'manual' for n in done)
        from_ai = sum(self.sources.get(n) == 'ai' for n in done)
        if done:
            message += (f': {from_yours} from your boxes,' if from_yours else ':') + \
                f' {from_ai} from AI boxes, {len(done) - from_yours - from_ai} by SAM'
        message += '.'
        if appears_at is not None:
            message += f' It first appears at frame {appears_at}.'
        if lost_at is not None:
            message += f' It leaves or hides at frame {lost_at}.'
        if unsure:
            message += f' Check frames {frame_ranges(unsure)}: SAM was unsure there.'
        return message + ' The boxes are saved; move or resize any that are off.'

    def multi_message(self):
        results = self.fish_results + [self._fish_result()]
        message = f'Tracked {len(results)} fish.'
        for index, (species, done, appears_at, lost_at, unsure) in enumerate(results, 1):
            span = f'frames {done[0]}–{done[-1]}' if done else 'no frames'
            message += f' Fish {index} ({species}): {span}'
            if unsure:
                message += f', check {frame_ranges(unsure)}'
            message += '.'
        return message + ' The boxes are saved; move or resize any that are off.'

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        page = self.page
        if self.restart:
            (track, number, box), self.restart = self.restart, None
            self.track = None
            self._restart(track, number, box)
        elif self.track and self._next_pass():
            pass  # going back is done: now forward
        elif self.queue and self.track:
            self.fish_results.append(self._fish_result())
            track, box = self.queue.pop(0)
            self.fish_index += 1
            self._restart(track, self.multi_start, box)
        else:
            self._close_progress()
            # The boxes are saved and accepted: leave tracking mode so Next and the arrows
            # just browse them. Tick Track? on any box to carry on from it.
            if self.fish_count > 1 and self.track:
                page.status.setText(self.multi_message())
                self.fish_count, self.multi_start = 0, None
            elif self.track:
                page.status.setText(self.stop_progress_message())
            if page.tracking.active == self.track:
                page.tracking.active = None
            page.render()  # refresh the Track? prompt; keeps any unsaved edits on this frame
            page.sam.schedule_preload()
            if self.seconds:
                remember_speed(sam_device()[0], self.seconds)  # for the next estimate, even after a restart
        page.update_controls()


_untouched = untouched_suggestion
