"""SAM 2.1 assistance with a bounded background image-feature cache."""
from collections import OrderedDict
from threading import Lock
from pathlib import Path
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

CHECKPOINT = Path(__file__).resolve().parents[1] / 'models/sam/sam2.1_t.pt'


def mask_box(mask):
    """Return an exclusive-right/bottom box in original image coordinates."""
    y, x = np.nonzero(mask)
    if not len(x):
        raise ValueError('SAM did not select any pixels. Add an include point or cancel and try again.')
    return [int(x.min()), int(y.min()), int(x.max()) + 1, int(y.max()) + 1]


class SAMSession:
    """Bounded image-feature cache; background encoding never owns the prompt decoder."""
    CACHE_SIZE = 3

    def __init__(self):
        self.predictor = None
        self.encoder = None
        self.frame_key = None
        self.device_description = ''
        self.cache = OrderedDict()
        self.cache_lock = Lock()
        self.encoder_lock = Lock()
        self.protected_key = None

    def cached(self, frame_key):
        with self.cache_lock:
            return frame_key in self.cache

    def _prepare(self, frame, frame_key):
        # One encoder job at a time. A cached foreground click bypasses this lock.
        with self.encoder_lock:
            with self.cache_lock:
                if frame_key in self.cache:
                    self.cache.move_to_end(frame_key)
                    return self.cache[frame_key]
            import torch
            from ultralytics.models.sam import SAM2Predictor
            from inference_device import select_device
            if not CHECKPOINT.is_file():
                raise FileNotFoundError('SAM checkpoint missing. Run "pixi run setup-sam" from the application folder.')
            if self.encoder is None:
                device, self.device_description = select_device()
                self.encoder = SAM2Predictor(overrides=dict(
                    model=str(CHECKPOINT), device=device, imgsz=1024, conf=0.0,
                    task='segment', mode='predict', save=False, verbose=False))
            with torch.inference_mode():
                self.encoder.reset_image()
                self.encoder.set_image(frame)
                features = self.encoder.features
            with self.cache_lock:
                self.cache[frame_key] = features
                self.cache.move_to_end(frame_key)
                while len(self.cache) > self.CACHE_SIZE:
                    victim = next(key for key in self.cache if key != self.protected_key)
                    del self.cache[victim]
            return features

    def prepare(self, frame, frame_key):
        """Encode without creating annotations or running the mask decoder."""
        self._prepare(frame, frame_key)

    def protect(self, frame_key):
        with self.cache_lock:
            self.protected_key = frame_key

    def predict(self, frame, frame_key, points, labels, box=None):
        import torch
        from ultralytics.models.sam import SAM2Predictor
        self.protect(frame_key)
        with self.cache_lock:
            features = self.cache.get(frame_key)
        if features is None:
            features = self._prepare(frame, frame_key)
        if self.predictor is None:
            # Separate predictor state, shared read-only model weights: no second model in RAM.
            predictor = SAM2Predictor(overrides=dict(
                model=str(CHECKPOINT), device=str(self.encoder.device), imgsz=1024, conf=0.0,
                task='segment', mode='predict', save=False, verbose=False))
            predictor.setup_model(model=self.encoder.model, verbose=False)
            self.predictor = predictor
        with torch.inference_mode():
            if frame_key != self.frame_key:
                self.frame_key = None
                self.predictor.reset_image()
                self.predictor.setup_source(frame)
                self.predictor.features = features
                self.frame_key = frame_key
            prompts = dict(points=[points], labels=[labels]) if points else {}
            if box is not None:
                prompts['bboxes'] = [box]
            results = self.predictor(source=None, **prompts)
        if not results or results[0].masks is None or not len(results[0].masks.data):
            raise ValueError('SAM found no mask. Add another include point or cancel and try again.')
        mask = results[0].masks.data[0].cpu().numpy().astype(bool)
        if mask.shape != frame.shape[:2]:
            raise ValueError('SAM returned a mask with the wrong image dimensions.')
        return mask


def refine_track_box(session, frame, frame_key, box):
    """Use a nearby box prompt; reject empty, distant or drastically resized masks.

    A ValueError means SAM could not find the fish here; other errors mean SAM did not run.
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(float, box)
    width, height = x2-x1, y2-y1
    if width <= 0 or height <= 0:
        raise ValueError('The copied box has no area.')
    dx, dy = max(4, width*.3), max(4, height*.3)
    search = [max(0, x1-dx), max(0, y1-dy), min(w, x2+dx), min(h, y2+dy)]
    mask = session.predict(frame, frame_key, [], [], box=search)
    result = mask_box(mask)
    left, top, right, bottom = result
    area_ratio = (right-left)*(bottom-top)/(width*height)
    overlap = max(0, min(right, x2)-max(left, x1))*max(0, min(bottom, y2)-max(top, y1))
    if (not .2 <= area_ratio <= 3 or overlap == 0 or
            left < search[0] or top < search[1] or right > search[2] or bottom > search[3]):
        raise ValueError('SAM result moved too far or changed size too much.')
    return result


class SAMTrackWorker(QThread):
    succeeded = pyqtSignal(object)

    def __init__(self, session, frame, frame_key, boxes, parent=None):
        super().__init__(parent)
        self.session, self.frame, self.frame_key = session, frame.copy(), frame_key
        self.boxes = [(identity, list(box)) for identity, box in boxes]

    def run(self):
        results = []
        for identity, box in self.boxes:
            if self.isInterruptionRequested():
                break
            try:
                adjusted = refine_track_box(self.session, self.frame, self.frame_key, box)
                results.append((identity, adjusted, '', False))
            except ValueError as exc:
                results.append((identity, None, str(exc), True))   # fish not found
            except Exception as exc:
                results.append((identity, None, str(exc), False))  # SAM unavailable
        self.succeeded.emit(results)


class SAMPreloadWorker(QThread):
    prepared = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session, frame, source_key, number, total, video, parent=None):
        super().__init__(parent)
        self.session, self.frame = session, frame.copy()
        self.source_key, self.number, self.total = source_key, number, total
        self.video = video

    def run(self):
        import cv2
        capture = None
        try:
            for number in range(self.number, min(self.total, self.number + 2) + 1):
                if self.isInterruptionRequested():
                    break
                key = (self.source_key, number)
                if not self.session.cached(key):
                    if number == self.number:
                        frame = self.frame
                    else:
                        if not self.video:
                            break
                        if capture is None:
                            capture = cv2.VideoCapture(str(self.video))
                        if round(capture.get(cv2.CAP_PROP_POS_FRAMES)) != number - 1:
                            capture.set(cv2.CAP_PROP_POS_FRAMES, number - 1)
                        ok, frame = capture.read()
                        if not ok:
                            break
                    if self.isInterruptionRequested():
                        break
                    self.session.prepare(frame, key)
                if not self.isInterruptionRequested():
                    self.prepared.emit(key)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if capture is not None:
                capture.release()


class SAMWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session, frame, frame_key, points, labels, parent=None):
        super().__init__(parent)
        self.session, self.frame, self.frame_key = session, frame.copy(), frame_key
        self.points, self.labels = list(points), list(labels)

    def run(self):
        try:
            self.succeeded.emit(self.session.predict(self.frame, self.frame_key, self.points, self.labels))
        except Exception as exc:
            self.failed.emit(str(exc))


def download_checkpoint():
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    if CHECKPOINT.is_file():
        print(f'SAM checkpoint already installed: {CHECKPOINT}')
        return
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    print('Downloading SAM 2.1 Tiny checkpoint. Please stand by...', flush=True)
    url = 'https://github.com/ultralytics/assets/releases/download/v8.4.0/sam2.1_t.pt'
    with tempfile.TemporaryDirectory(dir=CHECKPOINT.parent) as temp:
        downloaded = Path(temp) / CHECKPOINT.name
        with urllib.request.urlopen(url, timeout=60) as response, downloaded.open('wb') as output:
            shutil.copyfileobj(response, output)
        if not zipfile.is_zipfile(downloaded):
            raise ValueError('SAM download was not a valid checkpoint archive.')
        downloaded.replace(CHECKPOINT)
    print(f'SAM ready: {CHECKPOINT} ({CHECKPOINT.stat().st_size / 1024**2:.1f} MiB)')


if __name__ == '__main__':
    download_checkpoint()
