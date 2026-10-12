"""Create annotation sources using the editor's existing durable storage format."""
import json
import math
from pathlib import Path
import shutil
from uuid import uuid4

import cv2

from review_io import REVIEW_COLUMNS, save_rows


def create_session(project, video, interval=1, target_fps=None, cancelled=lambda: False):
    video = Path(video).resolve()
    if interval < 1 or not video.is_file():
        raise ValueError('Choose an existing video and a positive frame interval.')
    video_name = video.name
    cap = cv2.VideoCapture(str(video))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        ok, frame = cap.read()
        if not cap.isOpened() or not ok or total < 1 or not math.isfinite(fps) or fps <= 0:
            raise ValueError('The video could not be read or has invalid timing information.')
    finally:
        cap.release()
    root = Path(project.output_root) / project.name / 'annotations' / uuid4().hex
    root.mkdir(parents=True)
    try:
        if target_fps is not None:
            from video_sampling import prepare_video
            video = prepare_video(video, root / 'working_video.mp4', target_fps, cancelled)
            cap = cv2.VideoCapture(str(video))
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = cap.get(cv2.CAP_PROP_FPS)
            finally:
                cap.release()
        # Empty predictions are an annotation source, never a completed detection run.
        original = dict(kind='annotation', video=video_name, source_video=str(video),
                        fps=fps, total_frames=total, duration=total / fps, frames=[],
                        frame_interval=interval, class_names=[])
        (root / 'original_detections.json').write_text(json.dumps(original), encoding='utf-8')
        (root / 'species_names.json').write_text('[]', encoding='utf-8')
        fields = ['frame_number', 'timestamp', 'species', 'confidence', 'track_id',
                  'x1', 'y1', 'x2', 'y2', 'reviewed', 'notes', 'frame_image'] + REVIEW_COLUMNS
        save_rows(root / 'low_confidence_review.csv', [], fields)
        if cancelled():
            raise InterruptedError('Video preparation cancelled.')
        project.add_item('annotation', video_name, root)
    except Exception:
        shutil.rmtree(root)
        raise
    return root
