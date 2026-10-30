"""Single-frame SAM 2.1 assistance; inference stays off the GUI thread."""
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
    def __init__(self):
        self.predictor = None
        self.frame_key = None
        self.device_description = ''

    def predict(self, frame, frame_key, points, labels):
        import torch
        from ultralytics.models.sam import SAM2Predictor
        from inference_device import select_device

        if not CHECKPOINT.is_file():
            raise FileNotFoundError('SAM checkpoint missing. Run "pixi run setup-sam" from the application folder.')
        if self.predictor is None:
            device, self.device_description = select_device()
            self.predictor = SAM2Predictor(overrides=dict(
                model=str(CHECKPOINT), device=device, imgsz=1024, conf=0.0,
                task='segment', mode='predict', save=False, verbose=False))
        with torch.inference_mode():
            if frame_key != self.frame_key:
                self.predictor.reset_image()
                self.predictor.set_image(frame)
                self.frame_key = frame_key
            # An outer object dimension groups every positive/negative click into ONE fish.
            results = self.predictor(source=None, points=[points], labels=[labels])
        if not results or results[0].masks is None or not len(results[0].masks.data):
            raise ValueError('SAM found no mask. Add another include point or cancel and try again.')
        mask = results[0].masks.data[0].cpu().numpy().astype(bool)
        if mask.shape != frame.shape[:2]:
            raise ValueError('SAM returned a mask with the wrong image dimensions.')
        return mask


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
