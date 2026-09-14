"""Durable review storage and rendering shared by the UI and video exporter."""
import csv
import json
import os
import shutil
import tempfile
from pathlib import Path

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

REVIEW_COLUMNS = ['original_species', 'review_status', 'annotation_source', 'annotation_id']


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
    flagged_boxes = {tuple(round(float(r[k]), 2) for k in ('x1','y1','x2','y2')) for r in rows if r.get('annotation_source') != 'manual'}
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
        backup = original_video_backup(self.video)
        source = backup if backup.exists() else self.video
        cap = cv2.VideoCapture(str(source))
        writer = None
        target = self.video
        temporary = target.with_name(target.stem + '.tmp.mp4')
        try:
            if not cap.isOpened():
                raise RuntimeError('Cannot open the annotated video.')
            fps = cap.get(cv2.CAP_PROP_FPS)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            size = (int(cap.get(3)), int(cap.get(4)))
            if fps <= 0 or total <= 0:
                raise RuntimeError('Invalid video properties.')
            writer = cv2.VideoWriter(str(temporary),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
            if not writer.isOpened():
                raise RuntimeError('Cannot create reviewed video.')
            grouped = {}
            for row in self.rows:
                grouped.setdefault(int(row['frame_number']), []).append(row)
            for number in range(1,total+1):
                if self.isInterruptionRequested():
                    raise RuntimeError('Video regeneration cancelled.')
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f'Video decode failed at frame {number}.')
                rows = grouped.get(number, [])
                if any(r.get('review_status') in ('confirmed','rejected') for r in rows):
                    raw = cv2.imread(str(self.root / rows[0]['frame_image']))
                    if raw is None or (raw.shape[1], raw.shape[0]) != size:
                        raise RuntimeError(f'Missing or invalid raw image for frame {number}.')
                    frame = draw_review(raw,rows,context=context_for(self.root,number))
                    cv2.putText(frame,f'Frame {number} | Reviewed',(8,24),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),2)
                writer.write(frame)
                self.progress.emit(round(number/total*100))
            writer.release(); writer = None
            cap.release()
            if not backup.exists():
                atomic_copy(self.video, backup)
            os.replace(temporary,target)
            self.succeeded.emit(str(target))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            cap.release()
            if writer is not None:
                writer.release()
            temporary.unlink(missing_ok=True)
