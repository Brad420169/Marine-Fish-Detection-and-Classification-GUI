"""The data behind the annotation page: review rows, run metadata and frame completion.

No Qt here, so this can be used and tested without a window.
"""
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2

from pipeline import format_timestamp
from review_io import context_for, load_species_names
from review_store import ReviewStore


class ReviewSession:
    """One opened detection run or manual annotation session."""

    def __init__(self):
        self.store = None         # the run's review database (review_store.py)
        self.root = None          # run / session folder
        self.video = None         # annotated output video, if any
        self.source_video = None  # raw video to read frames from
        self.rows, self.fields, self.names = [], [], []
        self.rows_by_frame = {}
        self.flagged_frames = []
        self.ai_total = 0       # AI boxes to review (low-confidence ones, and any a track pulled in)
        self.ai_reviewed = 0    # of those, ticked or crossed
        self.detections_by_frame = {}
        self.manual_session = False
        self.frame_interval = 1
        self.fps = 25.0

    # Loading

    def open(self, output_dir, video_path):
        """Read run metadata. Rows are loaded separately by load_rows()."""
        self.close()
        self.root = Path(output_dir) if output_dir else None
        self.video = self.source_video = Path(video_path) if video_path else None
        self.rows, self.fields, self.names = [], [], []
        self.detections_by_frame = {}
        self.fps = 25.0
        source = self._read_json('original_detections.json')
        view = self._read_json('annotation_view.json')
        self.manual_session = source.get('kind') == 'annotation'
        self.frame_interval = max(1, int(view.get('frame_interval', source.get('frame_interval', 1))))
        fps = float(source.get('fps') or self.fps)
        if math.isfinite(fps) and fps > 0:
            self.fps = fps
        self.detections_by_frame = {f['frame_number']: f['detections'] for f in source.get('frames', [])}
        candidate = Path(source.get('source_video', ''))
        if source.get('source_video') and candidate.is_file():
            self.source_video = candidate

    def _read_json(self, name):
        """A metadata file in the run folder, or {} when missing or unreadable."""
        path = self.root / name if self.root else None
        if not (path and path.is_file()):
            return {}
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def close(self):
        if self.store:
            self.store.close()
        self.store = None

    def load_rows(self):
        """Load the saved reviews and species list. Raises OSError, ValueError or KeyError.

        Opening an older run migrates its review CSV into review.sqlite first.
        """
        if not self.root:
            raise ValueError('No run folder is open.')
        self.store = ReviewStore(self.root)
        self.rows, self.fields = self.store.load(), self.store.fields
        self.names = load_species_names(self.root, self.rows)
        return (self.root / 'species_names.json').exists()

    def start_frame(self, total_frames, has_video):
        """Where to open: the first box still waiting for a check, else where you left off."""
        unchecked = [r['frame_number'] for r in self.rows if r['review_status'] == 'pending']
        if unchecked:
            return min(unchecked)
        if has_video:
            return max(self.rows_by_frame, default=1) if self.manual_session else 1
        if self.flagged_frames:
            return next((n for n in self.flagged_frames
                         if any(r['review_status'] not in ('confirmed', 'rejected') for r in self.frame_rows(n))),
                        self.flagged_frames[0])
        return min(self.rows_by_frame, default=1)

    # Rows

    def index(self):
        """Rebuild the per-frame lookup and flagged-frame summary after rows change."""
        self.rows_by_frame = defaultdict(list)
        for row in self.rows:
            self.rows_by_frame[row['frame_number']].append(row)
        self.flagged_frames = sorted({r['frame_number'] for r in self.rows
                                      if r.get('annotation_source') != 'manual'})
        ai = [r for r in self.rows if r.get('annotation_source') != 'manual']
        self.ai_total = len(ai)
        self.ai_reviewed = sum(r.get('reviewed') == 'yes' or r['review_status'] in ('confirmed', 'rejected')
                               for r in ai)

    def save(self, rows, complete=None):
        """Save rows, then adopt them. Raises OSError on failure.

        With ``complete`` (a frame number), that frame is recorded as fully reviewed
        in the same transaction.
        """
        if not self.store:
            raise OSError('No saved reviews are open for this run.')
        self.store.save(rows, complete)
        self.rows = rows
        self.index()

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
        """Frames whose full review is recorded and still matches the saved boxes."""
        return self.store.completed_frames() if self.store else []

    def record_completion(self, number):
        """Record a full-frame review of saved rows. Raises OSError."""
        self.store.record_completion(number, self.frame_rows(number))

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
