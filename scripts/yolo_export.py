"""Export fully reviewed video frames as a YOLO detection dataset."""
import json
import math
from pathlib import Path
import tempfile

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from model_evaluation import fingerprint, frame_fingerprint, read_confirmation
from review_io import load_review_rows
from review_summary import merge_detections


def reviewed_frames(root):
    root = Path(root)
    source = root / 'original_detections.json'
    reviews = root / 'low_confidence_review.csv'
    if not source.is_file() or not reviews.is_file():
        raise ValueError('This run needs original detections and saved reviews before export.')
    original = json.loads(source.read_text(encoding='utf-8'))
    rows, _ = load_review_rows(reviews)
    completion = read_confirmation(root)
    if completion.get('source') != fingerprint(original):
        return original, rows, []
    total = int(original.get('total_frames', 0))
    verified = sorted(int(number) for number, digest in completion.get('frames', {}).items()
                      if number.isdigit() and 1 <= int(number) <= total
                      and digest == frame_fingerprint(rows, int(number))
                      and all(row['review_status'] in ('confirmed', 'rejected')
                              for row in rows if row['frame_number'] == int(number)))
    return original, rows, verified


def split_counts(total, train_percent, val_percent):
    if total < 3 or not 1 <= val_percent or not 1 <= train_percent or train_percent + val_percent >= 100:
        raise ValueError('Use at least three reviewed frames and leave room for all three splits.')
    train = max(1, min(total - 2, round(total * train_percent / 100)))
    val = max(1, min(total - train - 1, round(total * val_percent / 100)))
    return train, val, total - train - val


def export_yolo_dataset(root, destination, train_percent=70, val_percent=20):
    root, destination = Path(root), Path(destination).expanduser().resolve()
    original, rows, numbers = reviewed_frames(root)
    train_count, val_count, _ = split_counts(len(numbers), train_percent, val_percent)
    if destination.exists():
        raise FileExistsError(f'Dataset folder already exists: {destination}')
    if not destination.parent.is_dir():
        raise FileNotFoundError(f'Choose an existing parent folder: {destination.parent}')
    merged = {int(frame['frame_number']): frame['detections']
              for frame in merge_detections(original, rows)}
    names = sorted({det['species'] for number in numbers for det in merged.get(number, [])})
    if not names:
        raise ValueError('The reviewed frames contain no confirmed fish to train on.')
    class_ids = {name: index for index, name in enumerate(names)}
    source_video = Path(original.get('source_video', ''))
    capture = cv2.VideoCapture(str(source_video)) if source_video.is_file() else None
    if capture is not None and not capture.isOpened():
        capture.release()
        capture = None
    try:
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.yolo-export-') as temporary:
            stage = Path(temporary) / 'dataset'
            for split in ('train', 'val', 'test'):
                (stage / 'images' / split).mkdir(parents=True)
                (stage / 'labels' / split).mkdir(parents=True)
            for index, number in enumerate(numbers):
                split = 'train' if index < train_count else 'val' if index < train_count + val_count else 'test'
                image = root / 'review_frames' / f'frame_{number:06d}.jpg'
                frame = cv2.imread(str(image)) if image.is_file() else None
                if frame is None and capture is not None:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, number - 1)
                    ok, frame = capture.read()
                    if not ok:
                        frame = None
                if frame is None:
                    raise ValueError(f'Raw frame {number} is unavailable. Restore the source video or review frame.')
                height, width = frame.shape[:2]
                if width <= 0 or height <= 0:
                    raise ValueError(f'Frame {number} has invalid dimensions.')
                stem = f'frame_{number:06d}'
                if not cv2.imwrite(str(stage / 'images' / split / f'{stem}.jpg'), frame):
                    raise OSError(f'Could not save frame {number}.')
                labels = []
                for det in merged.get(number, []):
                    x1, y1, x2, y2 = (float(value) for value in det['bbox'])
                    if not all(map(math.isfinite, (x1, y1, x2, y2))):
                        raise ValueError(f'Frame {number} has a non-finite box coordinate.')
                    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
                    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
                    if x2 <= x1 or y2 <= y1:
                        raise ValueError(f'Frame {number} has a box outside the image.')
                    labels.append(f"{class_ids[det['species']]} {(x1+x2)/(2*width):.6f} "
                                  f"{(y1+y2)/(2*height):.6f} {(x2-x1)/width:.6f} {(y2-y1)/height:.6f}")
                (stage / 'labels' / split / f'{stem}.txt').write_text('\n'.join(labels) + ('\n' if labels else ''), encoding='utf-8')
            yaml = ['train: images/train', 'val: images/val', 'test: images/test', 'names:']
            yaml.extend(f'  {index}: {json.dumps(name, ensure_ascii=False)}' for index, name in enumerate(names))
            (stage / 'data.yaml').write_text('\n'.join(yaml) + '\n', encoding='utf-8')
            stage.rename(destination)
    finally:
        if capture is not None:
            capture.release()
    return destination / 'data.yaml', (train_count, val_count, len(numbers) - train_count - val_count)


class ExportWorker(QThread):
    succeeded = pyqtSignal(str, tuple)
    failed = pyqtSignal(str)

    def __init__(self, root, destination, train_percent, val_percent, parent=None):
        super().__init__(parent)
        self.root, self.destination = root, destination
        self.train_percent, self.val_percent = train_percent, val_percent

    def run(self):
        try:
            yaml, counts = export_yolo_dataset(self.root, self.destination,
                                                self.train_percent, self.val_percent)
            self.succeeded.emit(str(yaml), counts)
        except Exception as exc:
            self.failed.emit(str(exc))
