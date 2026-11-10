"""SAM-assisted annotation on the annotation page: preview, click queue and preloading.

The model itself lives in sam_assist.py; this class owns the page-side state.
Only one background job (a mask, or a batch of track adjustments) runs at a time,
held in ``worker``.
"""
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtWidgets import QCompleter

from sam_assist import CHECKPOINT, SAMPreloadWorker, SAMSession, SAMTrackWorker, SAMWorker, mask_box


class SamAssist:
    def __init__(self, page):
        self.page = page
        self.session = SAMSession()
        self.worker = None        # the running mask or track-adjustment job
        self.busy = False
        self.queue = []           # double-clicks made while SAM was working
        self.points, self.labels = [], []
        self.mask = None
        # Bumped whenever the frame or preview changes, so stale results are ignored.
        self.frame_token = 0
        # Identifies the opened video in the feature cache; replaced on each load.
        self.source_key = object()
        self.preloader = None
        self.preload_failed = False
        self.preload_timer = QTimer(page)
        self.preload_timer.setSingleShot(True)
        self.preload_timer.timeout.connect(self.start_preload)

    @property
    def active(self):
        """A preview is open (at least one click on the current fish)."""
        return bool(self.points)

    def frame_key(self):
        return (self.source_key, self.page.frame_number)

    def new_source(self):
        self.source_key = object()
        self.preload_failed = False

    # Preview

    def clear(self, render=True, clear_queue=True):
        page = self.page
        if clear_queue:
            self.queue.clear()
        self.points, self.labels = [], []
        self.mask = None
        page.image.sam_active = False
        page.sam_panel.hide()
        page.sam_class.clear()
        if render:
            page.render()

    def undo_preview(self):
        self.frame_token += 1  # Ignore any in-flight preview after undo.
        self.clear()
        self.page.status.setText('SAM preview undone.')

    def click(self, x, y, include, extend=False):
        """A double-click on the frame: start a fish, add to it (extend) or trim it."""
        page = self.page
        page.nav.pause()
        self.stop_preload()
        if page.raw is None or page.worker:
            return
        if self.busy:
            self.queue.append((x, y, include, extend))
            page.status.setText(f'SAM working — {len(self.queue)} clicks queued.')
            return
        if self.worker:
            return
        if include and not self.points and not page.tracking.confirm_first():
            return  # a new fish waits until the unchecked track is confirmed
        if include and not extend and self.points:
            # A plain double-click elsewhere starts another fish; keep this one as a pending box.
            if self.mask is not None and self.mask.any():
                box = mask_box(self.mask)
                species = page.sam_class.text().strip()
                self.clear(render=False, clear_queue=False)
                page.add_pending_box(box, species)
                page.image.setFocus()
            else:
                self.clear(render=False, clear_queue=False)
        if not include and not self.points:
            page.status.setText('Start SAM with a double-left-click inside the fish.')
            return
        h, w = page.raw.shape[:2]
        self.points.append((min(w - 1, max(0, x * w)), min(h - 1, max(0, y * h))))
        self.labels.append(int(include))
        self.mask = None
        page.image.sam_active = True
        page.sam_panel.show()
        if len(self.points) == 1:
            completer = QCompleter(page.session.names, page.sam_class)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.popup().setObjectName("samClassSuggestions")
            page.sam_class.setCompleter(completer)
        page.render()
        token = self.frame_token
        # Image features survive cancelled prompts; request tokens still reject stale results.
        worker = SAMWorker(self.session, page.raw, self.frame_key(), self.points, self.labels, page)
        self.worker = worker
        worker.succeeded.connect(lambda mask: self.apply_mask(mask, token))
        worker.failed.connect(self.failed)
        worker.finished.connect(self.finished)
        page.status.setText('SAM is computing the mask. First double-click loads the model; CPU processing may take a while…')
        self.busy = True
        worker.start()

    def apply_mask(self, mask, token):
        page = self.page
        if token != self.frame_token or page.raw is None or mask.shape != page.raw.shape[:2]:
            return
        self.mask = mask
        self.preload_failed = False
        page.sam_ready.setToolTip('')
        self.frame_prepared(self.frame_key())
        page.render()
        page.status.setText('SAM ready. Double-left-click another fish; Shift + double-left-click adds to this fish. '
                            'Double-right-click trims this fish. Type a class in the SAM field and press Enter to '
                            'create the box. ' + self.session.device_description)
        if not mask.any():
            page.status.setText('SAM selected no pixels. Add an include point, or cancel and try again.')

    def failed(self, error):
        self.queue.clear()
        self.page.status.setText(f'SAM failed: {error}. Queued clicks cancelled; completed boxes are kept.')

    def finished(self):
        """A mask or track-adjustment job ended: run the next queued click, or resume preloading."""
        page = self.page
        self.worker.deleteLater()
        self.worker = None
        self.busy = False
        if self.queue and not page.shutting_down:
            if self.points and (self.mask is None or not self.mask.any()):
                self.queue.clear()
                page.status.setText('SAM found no fish. Queued clicks cancelled; completed boxes are kept.')
            else:
                self.click(*self.queue.pop(0))
                return
        self.schedule_preload()

    def finish(self):
        """Enter in the SAM field: turn the preview into a saved, named box."""
        page = self.page
        if self.worker or self.mask is None:
            return
        species = page.sam_class.text().strip()
        if not species:
            page.status.setText('Enter a class name before creating the SAM bounding box.')
            page.sam_class.setFocus()
            return
        try:
            box = mask_box(self.mask)
        except ValueError as exc:
            page.status.setText(str(exc))
            return
        self.clear(render=False)
        index = page.add_pending_box(box, species)
        before_ids = {row.get('annotation_id') for row in page.session.rows}
        page.commit_box(index)
        added = [dict(row) for row in page.session.rows if row.get('annotation_id') not in before_ids]
        if added:
            page.undo_stack.append(dict(frame=page.frame_number, sam_added=added[0]))

    # Track box adjustment

    def adjust_boxes(self, boxes, on_results):
        """Fit suggested track boxes to the fish in the background; one job at a time."""
        page = self.page
        self.stop_preload()
        token = self.frame_token
        worker = SAMTrackWorker(self.session, page.raw, self.frame_key(), boxes, page)
        self.worker = worker
        self.busy = True
        worker.succeeded.connect(lambda results: on_results(results, token))
        worker.finished.connect(self.finished)
        page.status.setText(f'SAM is adjusting {len(boxes)} copied track boxes…')
        worker.start()

    # Preloading nearby frames

    def stop_preload(self):
        self.preload_timer.stop()
        if self.preloader:
            self.preloader.requestInterruption()

    def schedule_preload(self, *_):
        self.stop_preload()
        if not self.page.shutting_down:
            self.preload_timer.start(600)

    def start_preload(self):
        page = self.page
        if (page.shutting_down or not page.isVisible() or page.nav.playing or page.raw is None
                or not page.sam_preload_check.isChecked() or self.preload_failed
                or page.worker or self.worker or self.preloader
                or not CHECKPOINT.is_file()):
            return
        key = self.frame_key()
        self.session.protect(key)
        page.sam_ready.setText('SAM ready for this frame' if self.session.cached(key)
                               else 'Preparing SAM for this frame…')
        worker = SAMPreloadWorker(self.session, page.raw, self.source_key,
                                  page.frame_number, page.total_frames, page.session.source_video, page)
        self.preloader = worker
        worker.prepared.connect(self.frame_prepared)
        worker.failed.connect(self.preload_error)
        worker.finished.connect(self.preload_finished)
        worker.start(QThread.Priority.LowPriority)

    def frame_prepared(self, key):
        if key == self.frame_key():
            self.page.sam_ready.setText('SAM ready for this frame')

    def preload_error(self, error):
        if self.preloader and self.preloader.source_key is not self.source_key:
            return
        self.preload_failed = True
        self.page.sam_ready.setText('SAM preload unavailable; double-click to retry')
        self.page.sam_ready.setToolTip(error)

    def preload_finished(self):
        worker = self.preloader
        interrupted = worker.isInterruptionRequested()
        self.preloader = None
        worker.deleteLater()
        if interrupted and not self.page.shutting_down:
            self.preload_timer.start(600)

    def shutdown(self):
        self.stop_preload()
        if self.preloader:
            self.preloader.wait()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
