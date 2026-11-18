"""Export fully reviewed video frames as a YOLO detection dataset."""
import json
import math
import re
from pathlib import Path
import tempfile

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from core.image_io import imread, imwrite
import review.store as review_store
from review.summary import merge_detections


def folder_name(text, fallback='video'):
    """A folder name that is safe on Windows, macOS and Linux, from a video's name."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(text)).strip(' .') or fallback


def unique_path(path):
    """``path``, or ``path_2``, ``path_3``... if it is already taken."""
    path, number = Path(path), 2
    candidate = path
    while candidate.exists():
        candidate, number = path.with_name(f'{path.name}_{number}'), number + 1
    return candidate


def video_stem(root):
    """The name (without extension) of the video a run or annotation session came from, or ''."""
    try:
        meta = json.loads((Path(root) / 'original_detections.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    return Path(meta.get('video') or meta.get('original_video') or meta.get('source_video') or '').stem


def default_dataset_path(root):
    """Where a run's dataset is exported by default: <video name>_yolo_dataset inside the run."""
    stem = video_stem(root)
    return unique_path(Path(root) / (f'{folder_name(stem)}_yolo_dataset' if stem else 'yolo_dataset'))


def dataset_label(yaml_path):
    """A readable name for a dataset: its folder, or for an older plain "yolo_dataset" folder,
    the video it came from."""
    folder = Path(yaml_path).parent
    if folder.name == 'yolo_dataset':
        stem = video_stem(folder.parent)
        if stem:
            return f'{stem} (yolo_dataset)'
    return folder.name


def checked(row):
    """A box a person accepted: ticked, drawn, fixed or in a confirmed track."""
    return row.get('review_status') == 'confirmed' and (
        row.get('reviewed') == 'yes' or row.get('annotation_source') == 'manual')


def reviewed_frames(root):
    """(original detections, review rows, numbers of frames with at least one checked box)."""
    root = Path(root)
    source = root / 'original_detections.json'
    if not source.is_file() or not review_store.exists(root):
        raise ValueError('This run needs original detections and saved reviews before export.')
    original = json.loads(source.read_text(encoding='utf-8'))
    total = int(original.get('total_frames', 0))
    with review_store.ReviewStore(root) as store:
        rows = store.load()
    verified = sorted({int(r['frame_number']) for r in rows if checked(r) and 1 <= int(r['frame_number']) <= total})
    return original, rows, verified


def split_counts(total, train_percent, val_percent):
    if total < 3 or not 1 <= val_percent or not 1 <= train_percent or train_percent + val_percent >= 100:
        raise ValueError('Check boxes on at least three frames, and leave room for all three splits.')
    train = max(1, min(total - 2, round(total * train_percent / 100)))
    val = max(1, min(total - train - 1, round(total * val_percent / 100)))
    return train, val, total - train - val


SOURCE_MODEL = 'source_model.json'  # beside data.yaml: {"weights": path of the run's detector}


def source_model(data_yaml):
    """The YOLO weights that annotated an exported dataset, or None.

    Read from source_model.json beside data.yaml; datasets exported before that file
    existed are found through their run's original_detections.json when they sit in
    the run's default yolo_dataset folder. Only existing .pt files count (the YOLO
    trainer cannot fine-tune RF-DETR .pth or Darknet .weights).
    """
    folder = Path(data_yaml).expanduser().parent
    weights = ''
    for path, key in ((folder / SOURCE_MODEL, 'weights'), (folder.parent / 'original_detections.json', 'weights_path')):
        try:
            weights = json.loads(path.read_text(encoding='utf-8')).get(key) or ''
        except (OSError, ValueError, AttributeError):
            continue
        if weights:
            break
    weights = Path(weights) if weights else None
    return weights if weights and weights.suffix.lower() == '.pt' and weights.is_file() else None


def export_yolo_dataset(root, destination, train_percent=70, val_percent=20, progress=None, cancelled=None):
    """Write a YOLO dataset of the run's frames with checked boxes. ``progress(done, total)``
    is called after each frame; ``cancelled()`` returning True stops the export (raises
    InterruptedError) and nothing is left behind."""
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
                if cancelled and cancelled():
                    raise InterruptedError('Export stopped; nothing was saved.')
                split = 'train' if index < train_count else 'val' if index < train_count + val_count else 'test'
                image = root / 'review_frames' / f'frame_{number:06d}.jpg'
                frame = imread(str(image)) if image.is_file() else None
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
                if not imwrite(str(stage / 'images' / split / f'{stem}.jpg'), frame):
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
                if progress:
                    progress(index + 1, len(numbers))
            yaml = ['train: images/train', 'val: images/val', 'test: images/test', 'names:']
            yaml.extend(f'  {index}: {json.dumps(name, ensure_ascii=False)}' for index, name in enumerate(names))
            (stage / 'data.yaml').write_text('\n'.join(yaml) + '\n', encoding='utf-8')
            # The detector that annotated the run, so Train a Model can offer to fine-tune it.
            if original.get('weights_path'):
                (stage / SOURCE_MODEL).write_text(json.dumps({'weights': original['weights_path']}), encoding='utf-8')
            stage.rename(destination)
    finally:
        if capture is not None:
            capture.release()
    return destination / 'data.yaml', (train_count, val_count, len(numbers) - train_count - val_count)


class ExportWorker(QThread):
    progress = pyqtSignal(int, int)  # frames written, frames in total
    succeeded = pyqtSignal(str, tuple)
    failed = pyqtSignal(str)

    def __init__(self, root, destination, train_percent, val_percent, parent=None):
        super().__init__(parent)
        self.root, self.destination = root, destination
        self.train_percent, self.val_percent = train_percent, val_percent

    def run(self):
        try:
            yaml, counts = export_yolo_dataset(self.root, self.destination, self.train_percent, self.val_percent,
                                                self.progress.emit, self.isInterruptionRequested)
            self.succeeded.emit(str(yaml), counts)
        except Exception as exc:
            self.failed.emit(str(exc))
