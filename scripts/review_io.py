"""Durable review storage and rendering shared by the UI and video exporter."""
import csv
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

REVIEW_COLUMNS = ['original_species', 'review_status', 'annotation_source', 'annotation_id', 'original_bbox', 'manual_track_id', 'track_keyframe', 'rejection_source']


def review_box(row):
    box = [float(row[k]) for k in ('x1', 'y1', 'x2', 'y2')]
    if not all(map(math.isfinite, box)) or box[0] < 0 or box[1] < 0 or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('Invalid review bounding box.')
    return box


def box_identity(box):
    """Review CSV coordinates are stored to two decimal places."""
    return tuple(round(float(value), 2) for value in box)


def box_iou(first, second):
    """Intersection over union for validated image-coordinate boxes."""
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right-left) * max(0, bottom-top)
    union = ((first[2]-first[0]) * (first[3]-first[1])
             + (second[2]-second[0]) * (second[3]-second[1]) - intersection)
    return intersection / union if union > 0 else 0


def model_review_row(detection, frame_number, fields):
    """Promote an immutable model prediction without losing its merge identity."""
    row = dict.fromkeys(fields, '')
    row.update(frame_number=frame_number, species=detection['species'],
               original_species=detection['species'], confidence=detection.get('confidence', ''),
               track_id=detection.get('track_id') if detection.get('track_id') is not None else '',
               annotation_source='model', review_status='pending', reviewed='')
    row.update(zip(('x1', 'y1', 'x2', 'y2'), detection['bbox']))
    review_box(row)
    return row


def original_box(row):
    """Model identity stays at its original coordinates when its box is edited."""
    value = row.get('original_bbox')
    if not value:
        return review_box(row)
    box = json.loads(value)
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError('Invalid original bounding box.')
    return review_box(dict(zip(('x1', 'y1', 'x2', 'y2'), box)))


def load_review_rows(path):
    """Read a review CSV, filling in defaults for older runs. Returns (rows, fields)."""
    with Path(path).open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            row['frame_number'] = int(row['frame_number'])
            for key in ('x1', 'y1', 'x2', 'y2'):
                float(row[key])
            # Legacy corrections retain their original prediction in notes.
            original = row.get('notes', '')
            row.setdefault('original_species', original.removeprefix('AI predicted: ') if original.startswith('AI predicted: ') else row['species'])
            row.setdefault('review_status', 'confirmed' if row.get('reviewed') == 'yes' else 'pending')
            row.setdefault('annotation_source', 'model')
            row.setdefault('annotation_id', '')
            rows.append(row)
    return rows, list(dict.fromkeys(fields + REVIEW_COLUMNS))


def load_species_names(root, rows):
    """Union the run's saved class list with any species already present in rows."""
    names_file = Path(root) / 'species_names.json'
    names = json.loads(names_file.read_text()) if names_file.exists() else []
    return sorted(set(names) | {r['species'] for r in rows})


def save_rows(path, rows, fields):
    """Replace the CSV only after the complete new version has reached disk."""
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.csv.tmp')
    try:
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def draw_review(frame, rows, selected=-1, context=()):
    canvas = frame.copy()
    # New runs retain all detections on flagged frames, including confident fish.
    flagged_boxes = {tuple(round(v, 2) for v in original_box(r)) for r in rows if r.get('annotation_source') != 'manual'}
    for det in context:
        if tuple(round(float(v), 2) for v in det['bbox']) not in flagged_boxes:
            x1,y1,x2,y2 = map(round, det['bbox'])
            cv2.rectangle(canvas,(x1,y1),(x2,y2),(255,210,0),2)
            cv2.putText(canvas,det['species'],(x1,max(18,y1-6)),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,210,0),1)
    for i,row in enumerate(rows):
        status = row.get('review_status', '')
        if status == 'rejected':
            continue
        x1,y1,x2,y2 = [round(float(row[k])) for k in ('x1','y1','x2','y2')]
        colour = (0,165,255) if i == selected else (0,200,100)
        label = row['species']
        cv2.rectangle(canvas,(x1,y1),(x2,y2),colour,3 if i==selected else 2)
        cv2.putText(canvas,label,(x1,max(18,y1-6)),cv2.FONT_HERSHEY_SIMPLEX,.6,colour,2)
    return canvas


def context_for(root, number):
    path = root / 'review_frames' / f'frame_{number:06d}.json'
    return json.loads(path.read_text()) if path.exists() else []


def original_video_backup(video):
    video = Path(video)
    return video.parent / '.original_video' / video.name


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ReviewVideoWorker(QThread):
    progress = pyqtSignal(int)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, video, root, rows, parent=None):
        super().__init__(parent)
        self.video, self.root, self.rows = Path(video), Path(root), rows

    def run(self):
        # Always redraw from the clean source, never from already painted boxes.
        # Use the same merge as CSVs/charts, including area removals and partial reviews.
        from review_summary import merge_detections
        from pipeline import add_frame_label, format_timestamp
        cap = None
        writer = None
        target = self.video
        temporary = target.with_name(target.stem + '.tmp.mp4')
        try:
            original = json.loads((self.root / 'original_detections.json').read_text(encoding='utf-8'))
            source = Path(original['source_video'])
            if source.resolve() == target.resolve():
                raise ValueError('The annotated output must be separate from the original video.')
            frames = {frame['frame_number']: frame['detections']
                      for frame in merge_detections(original, self.rows)}
            cap = cv2.VideoCapture(str(source))
            if not cap.isOpened():
                raise RuntimeError('The original working video is unavailable. Restore it to regenerate annotations.')
            fps = cap.get(cv2.CAP_PROP_FPS)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            size = (int(cap.get(3)), int(cap.get(4)))
            if fps <= 0 or total <= 0:
                raise RuntimeError('Invalid video properties.')
            if total != int(original.get('total_frames', total)) or abs(fps-float(original['fps'])) > .01:
                raise ValueError('The source video timing no longer matches this run.')
            writer = cv2.VideoWriter(str(temporary),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
            if not writer.isOpened():
                raise RuntimeError('Cannot create reviewed video.')
            for number in range(1,total+1):
                if self.isInterruptionRequested():
                    raise RuntimeError('Video regeneration cancelled.')
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f'Video decode failed at frame {number}.')
                annotations = [dict(zip(('x1','y1','x2','y2'), det['bbox']),
                                    species=det['species'], review_status='confirmed')
                               for det in frames.get(number, [])]
                frame = draw_review(frame, annotations)
                add_frame_label(frame, number, format_timestamp((number-1)/fps))
                writer.write(frame)
                self.progress.emit(round(number/total*100))
            writer.release(); writer = None
            cap.release()
            backup = original_video_backup(target)
            if target.exists() and not backup.exists():
                atomic_copy(target, backup)
            os.replace(temporary,target)
            self.succeeded.emit(str(target))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if cap is not None: cap.release()
            if writer is not None: writer.release()
            temporary.unlink(missing_ok=True)
