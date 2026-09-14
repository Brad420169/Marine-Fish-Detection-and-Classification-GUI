"""Recompute aggregate results from immutable detections and saved review decisions."""
from collections import Counter, defaultdict, deque
import csv
import json
from pathlib import Path
import shutil
import tempfile

import cv2

from pipeline import SUMMARY_FIELDS, extract_maxn_example_frames, format_timestamp
from review_io import save_rows


def box_key(frame, box):
    return (int(frame), *(round(float(value), 2) for value in box))


def merge_detections(original, reviews):
    """Apply each review once; rows still awaiting a decision keep their original prediction."""
    lookup = defaultdict(deque)
    manual = []
    for row in reviews:
        if row.get("annotation_source") == "manual":
            manual.append(row)
            continue
        key = box_key(row['frame_number'], [row[k] for k in ('x1', 'y1', 'x2', 'y2')])
        lookup[key].append(row)
    merged = []
    for frame in original['frames']:
        detections = []
        for source in frame['detections']:
            det = dict(source)
            matches = lookup.get(box_key(frame['frame_number'], det['bbox']))
            review = matches.popleft() if matches else None
            if review:
                status = review.get('review_status', 'confirmed' if review.get('reviewed') == 'yes' else 'pending')
                if status == 'rejected':
                    continue
                if status == 'confirmed':
                    if not review['species'].strip():
                        raise ValueError('A confirmed detection has an empty species name.')
                    det['species'] = review['species'].strip()
            detections.append(det)
        merged.append({'frame_number': frame['frame_number'], 'detections': detections})
    if any(lookup.values()):
        raise ValueError('Some review rows do not match the original detections. Results were not changed.')
    by_frame = {frame['frame_number']: frame for frame in merged}
    seen_ids = set()
    for row in manual:
        if row.get('review_status') != 'confirmed':
            continue
        annotation_id = row.get('annotation_id')
        if not annotation_id or annotation_id in seen_ids:
            raise ValueError('Manual annotations must have distinct IDs.')
        seen_ids.add(annotation_id)
        number = int(row['frame_number'])
        box = [float(row[k]) for k in ('x1','y1','x2','y2')]
        if number < 1 or number > round(original['fps'] * original['duration']) or box[2] <= box[0] or box[3] <= box[1] or not row['species'].strip():
            raise ValueError('Invalid manual annotation.')
        frame = by_frame.setdefault(number, {'frame_number':number,'detections':[]})
        frame['detections'].append(dict(species=row['species'].strip(), confidence=None,
            track_id=None, bbox=box, annotation_source='manual', annotation_id=annotation_id))
    return [by_frame[n] for n in sorted(by_frame)]


def summarize(original, frames):
    fps = float(original['fps'])
    if fps <= 0:
        raise ValueError('Invalid run FPS.')
    stats = {}
    for frame in frames:
        number = int(frame['frame_number'])
        counts = Counter(d['species'] for d in frame['detections'])
        for species, count in counts.items():
            item = stats.setdefault(species, {'counts': [], 'confidence': [], 'tracks': set(), 'total': 0})
            item['counts'].append((number, count))
        for det in frame['detections']:
            item = stats[det['species']]
            item['total'] += 1
            if det.get('confidence') is not None:
                item['confidence'].append(float(det['confidence']))
            if det.get('track_id') is not None:
                item['tracks'].add(det['track_id'])
    rows = []
    for species, item in stats.items():
        counts = item['counts']
        peak_frame, peak = max(counts, key=lambda pair: pair[1])
        first, last = min(n for n, _ in counts), max(n for n, _ in counts)
        rows.append(dict(
            dataset=Path(original['video']).stem, video=original['video'],
            video_duration_seconds=round(original['duration'], 1), species=species,
            max_n=peak, max_n_timestamp=format_timestamp((peak_frame-1)/fps),
            max_n_frame=peak_frame, first_seen=format_timestamp((first-1)/fps),
            last_seen=format_timestamp((last-1)/fps), visible_seconds=round(len(counts)/fps,1),
            observation_span_seconds=round((last-first+1)/fps,1),
            unique_tracks=len(item['tracks']), total_detections=item['total'],
            mean_confidence=round(sum(item['confidence'])/len(item['confidence']),4) if item['confidence'] else ''))
    return sorted(rows, key=lambda row: (-row['max_n'], -row['visible_seconds']))


def refresh_results(root, review_csv):
    root = Path(root)
    source = root / 'original_detections.json'
    if not source.exists():
        raise ValueError('This older run has only an aggregate summary, which cannot reconstruct '
                         'Max-N, visibility or unique tracks after corrections. Run detection again '
                         'with this version to enable accurate Refresh Results. Existing files are unchanged.')
    original = json.loads(source.read_text(encoding='utf-8'))
    with Path(review_csv).open(newline='', encoding='utf-8') as stream:
        reviews = list(csv.DictReader(stream))
    frames = merge_detections(original, reviews)
    rows = summarize(original, frames)
    # Stage every result before publishing, with rollback if replacement fails.
    with tempfile.TemporaryDirectory(dir=root, prefix='.refresh-') as temporary:
        stage = Path(temporary)
        save_rows(stage/'track_summary.csv', rows, SUMMARY_FIELDS)
        fields = ['frame_number','species','confidence','track_id','x1','y1','x2','y2','annotation_source','annotation_id']
        detections = []
        for frame in frames:
            for det in frame['detections']:
                detections.append(dict(frame_number=frame['frame_number'], species=det['species'],
                    confidence=det['confidence'], track_id=det.get('track_id'),
                    annotation_source=det.get('annotation_source','model'), annotation_id=det.get('annotation_id',''),
                    **dict(zip(('x1','y1','x2','y2'),det['bbox']))))
        save_rows(stage/'reviewed_detections.csv', detections, fields)
        snapshots = {}
        by_frame = {f['frame_number']: f['detections'] for f in frames}
        cap = cv2.VideoCapture(original.get('source_video','')) if Path(original.get('source_video','')).is_file() else None
        try:
            for row in rows:
                number = row['max_n_frame']
                image = root/'review_frames'/f'frame_{number:06d}.jpg'
                raw = cv2.imread(str(image)) if image.exists() else None
                if raw is None and cap is not None and cap.isOpened():
                    cap.set(cv2.CAP_PROP_POS_FRAMES, number-1)
                    ok, raw = cap.read()
                    if not ok: raw = None
                if raw is not None:
                    snapshots[row['species']] = dict(frame_number=number, timestamp=row['max_n_timestamp'],
                                                     frame=raw, detections=by_frame[number])
        finally:
            if cap is not None: cap.release()
        (stage/'maxn_examples').mkdir()
        extract_maxn_example_frames(rows,snapshots,stage)
        if not (root/'track_summary_original.csv').exists():
            shutil.copy2(root/'track_summary.csv',root/'track_summary_original.csv')
        names = ['track_summary.csv','reviewed_detections.csv','maxn_examples']
        backups = stage/'backups'; backups.mkdir()
        published = []
        try:
            for name in names:
                target = root/name
                if target.exists(): target.rename(backups/name)
                published.append(name)
                (stage/name).rename(target)
        except OSError:
            for name in reversed(published):
                target = root/name
                if target.is_dir(): shutil.rmtree(target)
                else: target.unlink(missing_ok=True)
                if (backups/name).exists(): (backups/name).rename(target)
            raise
    return len(rows), len(snapshots) < len(rows)


from PyQt6.QtCore import QThread, pyqtSignal


class RefreshWorker(QThread):
    succeeded = pyqtSignal(int, bool)
    failed = pyqtSignal(str)

    def __init__(self, root, review_csv, parent=None):
        super().__init__(parent)
        self.root, self.review_csv = root, review_csv

    def run(self):
        try:
            self.succeeded.emit(*refresh_results(self.root, self.review_csv))
        except Exception as exc:
            self.failed.emit(str(exc))
