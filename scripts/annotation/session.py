"""The data behind the annotation page: review CSV rows, run metadata and frame completion.

No Qt here, so this can be used and tested without a window.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2

from model_evaluation import confirm_review_frame
from pipeline import format_timestamp
from review_io import context_for, load_review_rows, load_species_names, save_rows


class ReviewSession:
    """One opened detection run or manual annotation session."""

    def __init__(self):
        self.path = None          # low_confidence_review.csv
        self.root = None          # run / session folder
        self.video = None         # annotated output video, if any
        self.source_video = None  # raw video to read frames from
        self.rows, self.fields, self.names = [], [], []
        self.rows_by_frame = {}
        self.flagged_frames = []
        self.review_complete_count = 0
        self.detections_by_frame = {}
        self.manual_session = False
        self.frame_interval = 1
        self.fps = 25.0
        self._completion_cache = None

    # Loading

    def open(self, flagged_csv, output_dir, video_path):
        """Read run metadata. Rows are loaded separately by load_rows()."""
        self.path = Path(flagged_csv) if flagged_csv else None
        self.root = Path(output_dir) if output_dir else None
        self.video = Path(video_path) if video_path else None
        self.rows, self.fields, self.names = [], [], []
        self.detections_by_frame = {}
        self.fps = 25.0
        self._completion_cache = None
        metadata = self.root / 'original_detections.json' if self.root else None
        source = json.loads(metadata.read_text(encoding='utf-8')) if metadata and metadata.is_file() else {}
        self.manual_session = source.get('kind') == 'annotation'
        preferences = self.root / 'annotation_view.json' if self.root else None
        view = json.loads(preferences.read_text()) if preferences and preferences.is_file() else {}
        self.frame_interval = max(1, int(view.get('frame_interval', source.get('frame_interval', 1))))
        self._read_detections()

    def load_rows(self):
        """Load the review CSV and species list. Raises OSError, ValueError or KeyError."""
        if not self.path or not self.root:
            raise ValueError('No review CSV is available.')
        self.rows, self.fields = load_review_rows(self.path)
        self.names = load_species_names(self.root, self.rows)
        return (self.root / 'species_names.json').exists()

    def _read_detections(self):
        """Detector output, frame rate and raw source video from original_detections.json."""
        self.source_video = self.video
        original = self.root / 'original_detections.json' if self.root else None
        if not (original and original.exists()):
            return
        try:
            data = json.loads(original.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        fps = float(data.get('fps') or self.fps)
        if math.isfinite(fps) and fps > 0:
            self.fps = fps
        self.detections_by_frame = {f['frame_number']: f['detections'] for f in data.get('frames', [])}
        candidate = Path(data.get('source_video', ''))
        if candidate.is_file():
            self.source_video = candidate

    def start_frame(self, total_frames, has_video):
        """Where to open: the first unfinished sampled frame, or a pending track box."""
        number = next((n for n in self.flagged_frames
                       if any(r['review_status'] not in ('confirmed', 'rejected')
                              for r in self.rows if r['frame_number'] == n)),
                      self.flagged_frames[0] if self.flagged_frames else 1)
        if has_video:
            number = 1
        elif self.rows_by_frame and not self.flagged_frames:
            number = min(self.rows_by_frame)
        if self.manual_session and has_video:
            completed = set(self.completed_frames())
            pending_track_frames = {r['frame_number'] for r in self.rows
                                    if r.get('manual_track_id') and r['review_status'] == 'pending'}
            sampled = range(1, total_frames + 1, self.frame_interval)
            next_sample = next((n for n in sampled if n not in completed), total_frames)
            number = min(pending_track_frames | {next_sample})
        return number

    # Rows

    def index(self):
        """Rebuild the per-frame lookup and flagged-frame summary after rows change."""
        self.rows_by_frame = defaultdict(list)
        for row in self.rows:
            self.rows_by_frame[row['frame_number']].append(row)
        self.flagged_frames = sorted({r['frame_number'] for r in self.rows
                                      if r.get('annotation_source') != 'manual'})
        self.review_complete_count = sum(all(r['review_status'] in ('confirmed', 'rejected')
                                             for r in self.rows_by_frame[n]) for n in self.flagged_frames)

    def replace(self, rows):
        """Adopt rows that are already saved."""
        self.rows = rows
        self.index()

    def save(self, rows):
        """Write rows to the CSV, then adopt them. Raises OSError on failure."""
        save_rows(self.path, rows, self.fields)
        self.replace(rows)

    def frame_rows(self, number):
        return self.rows_by_frame.get(number, [])

    def context(self, number):
        """Every detector box on a frame, including confident ones never flagged."""
        if self.detections_by_frame:
            return self.detections_by_frame.get(number, [])
        return context_for(self.root, number)

    def timestamp(self, number):
        rows = self.frame_rows(number)
        if rows and rows[0].get('timestamp'):
            return rows[0]['timestamp']
        return format_timestamp((number - 1) / self.fps) if self.fps else ''

    def add_names(self, names):
        self.names = sorted(set(self.names) | set(names))

    def blank_row(self, **values):
        row = {key: '' for key in self.fields}
        row.update(values)
        return row

    # Completion and files

    def completed_frames(self):
        """Frames whose full review is recorded and still matches the CSV (cached)."""
        # File metadata invalidates the cache after saves or external edits.
        signature = [str(self.root.resolve())]
        for name in ('original_detections.json', 'low_confidence_review.csv', 'review_completion.json'):
            path = self.root / name
            try:
                stat = path.stat()
                signature.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                signature.append(None)
        if self._completion_cache is None or self._completion_cache[0] != signature:
            from yolo_export import reviewed_frames
            completed = frozenset(reviewed_frames(self.root)[2])
            self._completion_cache = (signature, completed)
        return self._completion_cache[1]

    def record_completion(self, number):
        """Record a full-frame review. Raises OSError, ValueError or KeyError."""
        confirm_review_frame(self.root, self.rows, number)

    def save_interval(self, value):
        """Remember the frame interval for this session. Raises OSError."""
        target = self.root / 'annotation_view.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'frame_interval': value}), encoding='utf-8')
        temporary.replace(target)
        self.frame_interval = value

    def ensure_frame_image(self, number, raw):
        """Relative path to a frame's raw image, saving it the first time it is annotated."""
        rows = self.frame_rows(number)
        existing = rows[0].get('frame_image') if rows else ''
        if existing and (self.root / existing).is_file():
            return existing
        relative = f'review_frames/frame_{number:06d}.jpg'
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and not cv2.imwrite(str(target), raw):
            raise OSError('Could not write the frame image.')
        context = self.root / 'review_frames' / f'frame_{number:06d}.json'
        if not context.exists():
            context.write_text(json.dumps(self.context(number)), encoding='utf-8')
        return relative

    def saved_frame_image(self, number):
        rows = self.frame_rows(number)
        if rows and rows[0].get('frame_image'):
            saved = self.root / rows[0]['frame_image']
            if saved.is_file():
                return cv2.imread(str(saved))
        return None
