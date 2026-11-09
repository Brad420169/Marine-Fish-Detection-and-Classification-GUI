"""Reading video frames, playback, and moving between frames on the annotation page."""
import math
from collections import OrderedDict
from time import perf_counter

import cv2
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QInputDialog


class VideoReader:
    """The raw source video, with recently decoded frames kept in memory.

    Seeking backwards makes the decoder restart from the previous keyframe, which is
    slow on long-GOP footage. So frames are cached as they are read, and a backward
    miss decodes a run of earlier frames at once, making repeated Previous presses
    cheap. Frames are shared, not copied: treat them as read-only.
    """
    CACHE_BYTES = 256 * 1024 * 1024
    BACKFILL = 24   # earlier frames decoded in one pass on a backward miss
    READ_AHEAD = 24  # forward gaps up to this size are decoded rather than seeked

    def __init__(self):
        self.cap = None
        self.total_frames = 0
        self.fps = None
        self._next = 1            # frame number the decoder returns on the next read()
        self._cache = OrderedDict()
        self._cache_bytes = 0

    def open(self, path):
        self.close()
        if not (path and path.is_file()):
            return False
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            return False
        self.cap = cap
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if math.isfinite(fps) and fps > 0 else None
        self._next = 1
        return True

    def read(self, number):
        if not self.cap:
            return None
        frame = self._cache.get(number)
        if frame is not None:
            self._cache.move_to_end(number)
            return frame
        if number < self._next:
            # Backward miss: one seek, then decode a run of frames ending at the target.
            self._seek(max(1, number - self._backfill() + 1))
        elif number - self._next > self.READ_AHEAD:
            self._seek(number)
        while self._next <= number:
            frame = self._decode()
            if frame is None:
                return None
        return self._cache.get(number)

    def _backfill(self):
        """How many frames to decode on a backward miss: no more than the cache can hold."""
        if not self._cache:
            return self.BACKFILL
        frame_bytes = next(reversed(self._cache.values())).nbytes
        return max(1, min(self.BACKFILL, self.CACHE_BYTES // frame_bytes - 1))

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
        if number in self._cache:
            return
        self._cache[number] = frame
        self._cache_bytes += frame.nbytes
        while self._cache_bytes > self.CACHE_BYTES and len(self._cache) > 1:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.nbytes

    def close(self):
        if self.cap:
            self.cap.release()
        self.cap = None
        self.total_frames = 0
        self.fps = None
        self._next = 1
        self._cache.clear()
        self._cache_bytes = 0

    def __bool__(self):
        return self.cap is not None


class Navigator:
    """Previous / Skip / jump / play. Moving never confirms a frame."""

    def __init__(self, page):
        self.page = page
        self.playing = False
        # Without a readable video only frames with review rows can be shown.
        self.rows_only = False
        self.timer = QTimer(page)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.play_next)

    def next_number(self, step):
        """The frame Previous / Skip / Confirm & Next lands on."""
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
        if page.worker or not page.discard_ok():
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
        if accepted and number != page.frame_number and page.discard_ok():
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
