"""Create a lower-frame-rate working video without changing the source file."""
import math
from pathlib import Path
import cv2


def video_fps(path):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
            raise ValueError('Cannot read the video frame rate. Choose a readable video.')
        return fps
    finally:
        cap.release()


def prepare_video(source, destination, target_fps=None, cancelled=lambda: False):
    source, destination = Path(source), Path(destination)
    fps = video_fps(source)
    if target_fps is None:
        return source
    if target_fps not in (30, 24, 15, 5) or target_fps >= fps:
        raise ValueError('Choose a frame rate lower than the source video.')
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError('The working video must be a new file.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + '.tmp.mp4')
    cap = cv2.VideoCapture(str(source))
    writer = None
    try:
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*'mp4v'), target_fps, size)
        if not writer.isOpened():
            raise OSError('Could not create the lower-FPS working video.')
        index = written = 0
        while True:
            if cancelled():
                raise InterruptedError('Video preparation cancelled.')
            ok, frame = cap.read()
            if not ok:
                break
            # Sample at output timestamps; never slow down playback by merely
            # changing the file's FPS header.
            if index >= math.floor(written * fps / target_fps + 1e-8):
                writer.write(frame)
                written += 1
            index += 1
        if not written:
            raise ValueError('The video contains no readable frames.')
        writer.release(); writer = None
        check = cv2.VideoCapture(str(temporary))
        try:
            if not check.isOpened() or int(check.get(cv2.CAP_PROP_FRAME_COUNT)) != written:
                raise OSError('The lower-FPS working video could not be verified.')
        finally:
            check.release()
        temporary.replace(destination)
        return destination
    finally:
        cap.release()
        if writer is not None: writer.release()
        temporary.unlink(missing_ok=True)
