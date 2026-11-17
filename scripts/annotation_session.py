"""Create manual annotation sessions (a run folder with no detections yet)."""
import json
import math
import os
from pathlib import Path
import shutil
from uuid import uuid4

import cv2

from review_io import REVIEW_COLUMNS
from review_store import ReviewStore


def _same_file(a, b):
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def find_session(project, video, target_fps=None):
    """This project's annotation session for ``video`` at that frame rate, or None.

    Reusing it opens the video straight away instead of preparing it again. Sessions
    record the source file's path; older ones only its name, so they match by name.
    Only a session whose working video is still readable counts.
    """
    video = Path(video)
    if not video.is_file():
        return None
    wanted = None
    for item in reversed(project.items):
        if item['kind'] != 'annotation':
            continue
        root = Path(item['path'])
        try:
            meta = json.loads((root / 'original_detections.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        source = meta.get('original_video')
        if meta.get('kind') != 'annotation' or not (
                _same_file(source, video) if source else meta.get('video') == video.name):
            continue
        if not Path(meta.get('source_video', '')).is_file():
            continue
        if wanted is None:
            if target_fps is not None:
                wanted = float(target_fps)
            else:
                cap = cv2.VideoCapture(str(video))
                wanted = cap.get(cv2.CAP_PROP_FPS)
                cap.release()
        if abs(float(meta.get('fps') or 0) - wanted) < 0.01:
            return root
    return None


def set_interval(root, interval):
    """Change a session's frame interval (the annotation page reads annotation_view.json)."""
    target = Path(root) / 'annotation_view.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'frame_interval': interval}), encoding='utf-8')
    temporary.replace(target)


def create_session(project, video, interval=1, target_fps=None, cancelled=lambda: False, on_progress=None):
    """``on_progress(done, total)`` reports preparing a lower-FPS working video (if one is made)."""
    video = Path(video).resolve()
    if interval < 1 or not video.is_file():
        raise ValueError('Choose an existing video and a positive frame interval.')
    video_name = video.name
    original_video = str(video)
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
            video = prepare_video(video, root / 'working_video.mp4', target_fps, cancelled, on_progress)
            cap = cv2.VideoCapture(str(video))
            try:
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = cap.get(cv2.CAP_PROP_FPS)
            finally:
                cap.release()
        # Empty predictions are an annotation source, never a completed detection run.
        original = dict(kind='annotation', video=video_name, source_video=str(video),
                        original_video=original_video, target_fps=target_fps,
                        fps=fps, total_frames=total, duration=total / fps, frames=[],
                        frame_interval=interval, class_names=[])
        (root / 'original_detections.json').write_text(json.dumps(original), encoding='utf-8')
        (root / 'species_names.json').write_text('[]', encoding='utf-8')
        fields = ['frame_number', 'timestamp', 'species', 'confidence', 'track_id',
                  'x1', 'y1', 'x2', 'y2', 'reviewed', 'notes', 'frame_image'] + REVIEW_COLUMNS
        ReviewStore.create(root, [], fields).close()
        if cancelled():
            raise InterruptedError('Video preparation cancelled.')
        project.add_item('annotation', video_name, root)
    except Exception:
        shutil.rmtree(root)
        raise
    return root
