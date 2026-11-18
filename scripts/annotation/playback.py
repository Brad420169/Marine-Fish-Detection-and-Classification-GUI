"""Reading video frames, playback, and moving between frames on the annotation page."""
import math
import threading
from collections import OrderedDict
from time import perf_counter

import cv2
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QInputDialog


def _cache_budget():
    """Bytes of decoded frames to keep: a tenth of the computer's memory, 256 MB to 1 GB.

    A 4K frame is 25 MB, so 256 MB holds only ten of them; more room keeps stepping
    backwards smooth on 4K footage without crowding out SAM.
    """
    try:
        import psutil
        total = psutil.virtual_memory().total
    except Exception:
        total = 0
    return int(min(max(total // 10, 256 * 1024 * 1024), 1024 * 1024 * 1024))


class VideoReader:
    """The raw source video, with decoded frames kept in memory around the current one.

    Video files store most frames as changes from the previous one, so reading forwards
    is cheap but going back means seeking to a keyframe and decoding forwards again. So
    frames are cached as they are read, and when you step backwards a second decoder
    (in a background thread) prepares the next block of earlier frames before you reach
    them. When the cache is full, the frames furthest from the current one are dropped.
    Frames are shared, not copied: treat them as read-only.
    """
    CACHE_BYTES = 256 * 1024 * 1024  # replaced per machine by _cache_budget() on open
    BACKFILL = 24    # earlier frames decoded in one pass when going backwards
    READ_AHEAD = 24  # forward gaps up to this size are decoded rather than seeked

    def __init__(self):
        self.cap = None
        self.path = None
        self.total_frames = 0
        self.fps = None
        self._next = 1            # frame number the decoder returns on the next read()
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._budget = self.CACHE_BYTES
        self._focus = 1           # the frame last asked for; eviction keeps frames near it
        self._direction = 1       # +1 stepping forwards, -1 backwards: prepare frames that way
        self._lock = threading.Lock()
        self._prefetch = None     # (thread, first, last, stop event) while a block is decoded
        self._prefetch_capture = None

    def open(self, path):
        self.close()
        if not (path and path.is_file()):
            return False
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            return False
        self.cap, self.path = cap, path
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if math.isfinite(fps) and fps > 0 else None
        self._next = 1
        self._budget = _cache_budget()
        return True

    def read(self, number):
        if not self.cap:
            return None
        if number != self._focus:
            self._direction = 1 if number > self._focus else -1
        self._focus = number
        frame = self._cached(number)
        if frame is None:
            self._wait_for_prefetch(number)
            frame = self._cached(number)
        if frame is None:
            if number < self._next:
                # Backward miss: one seek, then decode a run of frames ending at the target.
                self._seek(max(1, number - self._backfill() + 1))
            elif number - self._next > self.READ_AHEAD:
                self._seek(number)
            while self._next <= number:
                if self._decode() is None:
                    return None
            frame = self._cached(number)
        # Prepare the frames around this one, so stepping either way stays instant. Usually
        # they are cached already (or the main decoder is in place), so this rarely does work.
        self._prefetch_around(number)
        return frame

    def _cached(self, number):
        with self._lock:
            frame = self._cache.get(number)
            if frame is not None:
                self._cache.move_to_end(number)
            return frame

    def _capacity(self):
        """How many frames fit in the budget (from the size of one cached frame)."""
        with self._lock:
            if not self._cache:
                return self.BACKFILL * 2
            return max(2, self._budget // next(reversed(self._cache.values())).nbytes)

    def _backfill(self):
        """How many frames to decode on a backward miss: no more than half the cache holds,
        so the frames just stepped through are kept too."""
        return max(1, min(self.BACKFILL, self._capacity() // 2))

    def _seek(self, number):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, number - 1)
        self._next = number

    def _decode(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        self._remember(self._next, frame)
        self._next += 1
        return frame

    def _remember(self, number, frame):
        with self._lock:
            if number in self._cache:
                return
            self._cache[number] = frame
            self._cache_bytes += frame.nbytes
            while self._cache_bytes > self._budget and len(self._cache) > 1:
                # Drop the frame furthest from the one being looked at.
                victim = max(self._cache, key=lambda n: abs(n - self._focus))
                self._cache_bytes -= self._cache.pop(victim).nbytes

    # Preparing earlier frames in the background

    def _prefetch_around(self, number):
        """Start decoding the next block in the direction you are stepping, if it is missing.
        Going forwards that is only needed when the main decoder is not already in place to
        read it cheaply (for example just after stepping backwards)."""
        if self._prefetch and self._prefetch[0].is_alive():
            return
        block = self._backfill()
        reach = 2 * block  # look two blocks ahead, so decoding starts in good time
        with self._lock:
            if self._direction < 0:
                missing = next((n for n in range(number - 1, max(0, number - reach - 1), -1)
                                if n not in self._cache), None)
            else:
                missing = next((n for n in range(number + 1, min(self.total_frames, number + reach) + 1)
                                if n not in self._cache), None)
        if missing is None or (self._direction > 0 and self._next == missing):
            return  # already there, or the main decoder reads it next
        if self._direction < 0:
            first, last = max(1, missing - block + 1), missing
        else:
            first, last = missing, min(self.total_frames, missing + block - 1)
        stop = threading.Event()
        thread = threading.Thread(target=self._decode_block, args=(first, last, stop), daemon=True)
        self._prefetch = (thread, first, last, stop)
        thread.start()

    def _decode_block(self, first, last, stop):
        try:
            if self._prefetch_capture is None:
                self._prefetch_capture = cv2.VideoCapture(str(self.path))
            capture = self._prefetch_capture
            capture.set(cv2.CAP_PROP_POS_FRAMES, first - 1)
            for number in range(first, last + 1):
                if stop.is_set():
                    return
                ok, frame = capture.read()
                if not ok:
                    return
                self._remember(number, frame)
        except cv2.error:
            return

    def _wait_for_prefetch(self, number):
        """If the frame wanted is being prepared right now, wait for it rather than seek again."""
        if self._prefetch and self._prefetch[0].is_alive() and self._prefetch[1] <= number <= self._prefetch[2]:
            self._prefetch[0].join()

    def _stop_prefetch(self):
        if self._prefetch:
            thread, _, _, stop = self._prefetch
            stop.set()
            thread.join()
        self._prefetch = None
        if self._prefetch_capture is not None:
            self._prefetch_capture.release()
        self._prefetch_capture = None

    def close(self):
        self._stop_prefetch()
        if self.cap:
            self.cap.release()
        self.cap = self.path = None
        self.total_frames = 0
        self.fps = None
        self._next = 1
        with self._lock:
            self._cache.clear()
            self._cache_bytes = 0

    def __bool__(self):
        return self.cap is not None


class Navigator:
    """Previous / Next / jump / play. Moving saves your edits on the frame first."""

    def __init__(self, page):
        self.page = page
        self.playing = False
        # Without a readable video only frames with review rows can be shown.
        self.rows_only = False
        self.timer = QTimer(page)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.play_next)

    def next_number(self, step):
        """The frame Previous / Next lands on."""
        page = self.page
        number = page.frame_number
        if self.rows_only:
            frames = sorted({r['frame_number'] for r in page.session.rows})
            if step > 0:
                later = [n for n in frames if n > number]
                return later[0] if later else number
            earlier = [n for n in frames if n < number]
            return earlier[-1] if earlier else number
        if not page.total_frames:
            return number
        return max(1, min(page.total_frames, number + step * page.session.frame_interval))

    def go(self, step):
        page = self.page
        self.pause()
        if page.worker or not page.save_frame(refresh=False):
            return
        page.frame_number = self.next_number(step)
        page.show_frame()

    def advance(self):
        """Move on after a save, with nothing left to discard."""
        self.page.frame_number = self.next_number(1)
        self.page.show_frame()

    def jump(self, _link=None):
        page = self.page
        self.pause()
        if page.worker or not page.total_frames:
            return
        number, accepted = QInputDialog.getInt(
            page, 'Jump to frame', f'Video frame number (1–{page.total_frames}):',
            page.frame_number, 1, page.total_frames, 1)
        if accepted and number != page.frame_number and page.save_frame(refresh=False):
            page.frame_number = number
            page.show_frame()

    def pause(self):
        page = self.page
        was_playing = self.playing
        self.timer.stop()
        self.playing = False
        page.play_button.setText('Play')
        page.table.setEnabled(not bool(page.worker))
        page.annotations.setEnabled(not bool(page.worker))
        if was_playing:
            page.sam.schedule_preload()

    def toggle(self):
        page = self.page
        page.sam.stop_preload()
        if self.playing:
            self.pause()
            return
        if not page.video_reader or page.worker or not page.discard_ok():
            return
        if page.frame_number >= page.total_frames:
            page.frame_number = 1
        page.show_frame()
        self.playing = True
        page.play_button.setText('Pause')
        page.table.setEnabled(False)
        page.annotations.setEnabled(False)
        self.timer.start(max(1, round(1000 / page.session.fps)))

    def play_next(self):
        page = self.page
        if not self.playing or page.worker:
            return
        if page.frame_number >= page.total_frames:
            self.pause()
            return
        started = perf_counter()
        page.frame_number += 1
        page.show_frame()
        if page.raw is None:
            self.pause()
            page.status.setText(f'Playback stopped: cannot read frame {page.frame_number}.')
            return
        if page.frame_number >= page.total_frames:
            self.pause()
            return
        # Never skip review frames. Slow machines may play below the source rate.
        self.timer.start(max(1, round(1000 / page.session.fps - (perf_counter() - started) * 1000)))
