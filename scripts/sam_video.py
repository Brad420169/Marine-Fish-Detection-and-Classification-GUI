"""Follow one fish through a video with SAM 2's video tracker.

SAM 2 remembers how the fish looked on recent frames and uses that memory to find
it on the next one, and it scores whether the fish is visible at all. Ultralytics'
video predictor normally runs a whole video from frame 1; VideoTracker feeds it
frames one at a time instead, so tracking can start on any frame.

Frames that already have a box for the fish (any box of the track's class where the
fish should be: drawn by you, by SAM or by the detector) use that box and skip SAM; SAM only runs on the frames without one, starting each time from the last good box.

For speed SAM only sees a square window around the fish, about four times its size,
re-centred on the fish every frame and run at 512 px instead of the full frame at
1024 px. On a 1080p survey video this was about 8x faster on a CPU (under a second
per frame) with the same boxes. If the fish swims close and fills the window, SAM
starts again from its latest box with a larger window. Tracking runs in SAMVideoWorker.
"""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from review_io import box_iou
from sam_assist import CHECKPOINT

INPUT_SIZE = 512      # SAM's input size for the window (it is trained at 1024 on whole frames)
WINDOW_FACTOR = 4.0   # window side as a multiple of the fish's longer side
MIN_WINDOW = 384      # smallest window, in video pixels
GROW_AT = 0.5         # restart with a bigger window once the fish is this fraction of it
AI_OVERLAP = 0.3      # an existing box must overlap the fish's last box this much to be the same fish
AI_MARGIN = 0.05      # two boxes closer than this in overlap are too close to tell apart


def mask_box(mask):
    """Box around the largest connected part of a mask, or None if it is empty.

    SAM's outline sometimes spills onto shadow or a neighbouring fish as a separate
    blob; keeping the largest part keeps the box on the fish.
    """
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return None
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in stats[largest, :4])
    return [x, y, x + w, y + h]


def match_detection(previous, boxes):
    """The box that is clearly the same fish as ``previous``, or None.

    It must overlap the fish's last box by AI_OVERLAP; if two boxes overlap it about
    equally (two fish together), neither is chosen and SAM decides instead.
    """
    scored = sorted(((box_iou(previous, box), index) for index, box in enumerate(boxes)), reverse=True)
    if not scored or scored[0][0] < AI_OVERLAP:
        return None
    if len(scored) > 1 and scored[1][0] >= AI_OVERLAP and scored[0][0] - scored[1][0] < AI_MARGIN:
        return None
    return [float(v) for v in boxes[scored[0][1]]]


def window_side(box, frame_width, frame_height):
    """Side of the square window for a fish this size, within the frame."""
    side = max(box[2] - box[0], box[3] - box[1]) * WINDOW_FACTOR
    return int(min(max(side, MIN_WINDOW), frame_width, frame_height))


def window_origin(box, side, frame_width, frame_height):
    """Top-left of a window of ``side`` centred on the box, kept inside the frame."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (int(round(min(max(cx - side / 2, 0), frame_width - side))),
            int(round(min(max(cy - side / 2, 0), frame_height - side))))


class VideoTracker:
    """SAM 2 video state for one fish. Not thread-safe: use from one job at a time."""

    def __init__(self, video_path, total_frames):
        self.video_path = Path(video_path)
        self.total_frames = total_frames
        self.predictor = None
        self.device_description = ''
        self.start = None     # frame the fish was boxed on (1-based)
        self.next = None      # next frame step() will track
        self.box = None       # latest box, in video pixels; the window follows it
        self.candidates = {}  # frame -> existing boxes of the track's class, as (box, kind) with
                              # kind 'manual' or 'ai' (only for reporting); filled in by the caller
        self._prompt_frame = None  # frame SAM's memory starts from; None = start it on the next SAM frame
        self._side = None          # window side for the current memory
        self._capture = None
        self._position = None      # frame number the capture returns next

    def reset(self, frame_number, box):
        """Start following the fish in ``box`` on ``frame_number``; tracking begins on the next frame."""
        self.start, self.next = frame_number, frame_number + 1
        self.box = [float(v) for v in box]
        self._prompt_frame, self._side = None, None

    def step(self):
        """Track one more frame. Returns (frame number, box or None, score, source).

        An existing box of the track's class where the fish should be is used as is,
        whatever made it; the source is then its kind ('manual' or 'ai').
        Otherwise SAM runs ('sam'), starting from the previous frame's box if it was
        not already running; its box is None when it thinks the fish is not visible.
        The score is SAM's object score: above 0 means visible, near 0 unsure.
        """
        number = self.next
        existing = self.candidates.get(number, [])
        match = match_detection(self.box, [box for box, _ in existing])
        if match is not None:
            kind = next(k for box, k in existing if [float(v) for v in box] == match)
            self.box, self._prompt_frame = match, None  # SAM restarts from here if it is needed later
            self.next = number + 1
            return number, list(match), 10.0, kind
        if self._prompt_frame is None:
            # Start SAM from the last good box (the boxed frame, an existing box, or a regrown window).
            self._prompt_frame, self._side = number - 1, None
            self._sam(number - 1)
        box, score = self._sam(number)
        self.next = number + 1
        return number, box, score, 'sam'

    def _sam(self, number):
        """Run SAM on one frame, prompting with self.box on the frame its memory starts from."""
        import torch
        if self.predictor is None:
            self._load()
        frame = self._read(number)
        if frame is None:
            raise EOFError(f'Cannot read frame {number}.')
        height, width = frame.shape[:2]
        if self._side is None:
            self._side = window_side(self.box, width, height)
        side = self._side
        x0, y0 = window_origin(self.box, side, width, height)
        window = np.ascontiguousarray(frame[y0:y0 + side, x0:x0 + side])
        predictor, index = self.predictor, number - self._prompt_frame
        with torch.inference_mode():
            prompt = None
            if index == 0:
                predictor.inference_state = predictor._init_state(self.total_frames - number + 1)
                prompt = [[self.box[0] - x0, self.box[1] - y0, self.box[2] - x0, self.box[3] - y0]]
            predictor.dataset.frame = index
            predictor.batch = ([''], [window], [''])
            image = predictor.preprocess([window])
            preds = predictor.inference(image, bboxes=prompt)
            outputs = predictor.inference_state['output_dict']
            current = outputs['cond_frame_outputs'].get(index) or outputs['non_cond_frame_outputs'].get(index)
            score = float(current['object_score_logits'].max())
            result = predictor.postprocess(preds, image, [window])[0]
        box = None
        if score > 0 and result.masks is not None and len(result.masks.data):
            found = mask_box(result.masks.data[0].cpu().numpy())
            if found:
                box = [found[0] + x0, found[1] + y0, found[2] + x0, found[3] + y0]
        if box:
            self.box = [float(v) for v in box]
            if max(box[2] - box[0], box[3] - box[1]) > GROW_AT * side and side < min(width, height):
                # The fish swam closer and fills the window: start SAM again from this
                # box, with a window sized for it.
                self._prompt_frame = None
        return box, score

    def _load(self):
        from ultralytics.models.sam import SAM2VideoPredictor
        from inference_device import select_device
        if not CHECKPOINT.is_file():
            raise FileNotFoundError('SAM checkpoint missing. Run "pixi run setup-sam" from the application folder.')
        device, self.device_description = select_device()
        predictor = SAM2VideoPredictor(overrides=dict(
            model=str(CHECKPOINT), device=device, imgsz=INPUT_SIZE, conf=0.0,
            task='segment', mode='predict', save=False, verbose=False))
        predictor.setup_model(model=None, verbose=False)
        # Run at INPUT_SIZE. Ultralytics normally sets these when it opens a source itself.
        size = INPUT_SIZE
        predictor.model.set_imgsz((size, size))
        predictor.imgsz = [size, size]
        predictor._bb_feat_sizes = [(size // 4, size // 4), (size // 8, size // 8), (size // 16, size // 16)]
        predictor.dataset = SimpleNamespace(mode='video', frames=self.total_frames, frame=0)
        self.predictor = predictor

    def _read(self, number):
        if self._capture is None:
            self._capture = cv2.VideoCapture(str(self.video_path))
        if self._position != number:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, number - 1)
        ok, frame = self._capture.read()
        self._position = number + 1 if ok else None
        return frame if ok else None

    def close(self):
        if self._capture is not None:
            self._capture.release()
        self._capture = self._position = None


class SAMVideoWorker(QThread):
    """Track frames until ``until`` (which may be raised while running) or the fish is lost."""
    tracked = pyqtSignal(int, object, float, str)  # frame number, box or None, score, 'manual', 'ai' or 'sam'
    failed = pyqtSignal(str)

    def __init__(self, tracker, until, parent=None):
        super().__init__(parent)
        self.tracker, self.until = tracker, until

    def run(self):
        tracker = self.tracker
        try:
            while (not self.isInterruptionRequested() and tracker.next <= min(self.until, tracker.total_frames)):
                number, box, score, source = tracker.step()
                self.tracked.emit(number, box, score, source)
                if box is None:
                    break  # the fish has gone; stop until someone boxes it again
        except Exception as exc:
            self.failed.emit(str(exc))
