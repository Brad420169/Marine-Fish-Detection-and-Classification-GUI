"""Read and write image files at any path, on every platform.

On Windows, cv2.imread and cv2.imwrite cannot open paths with characters outside
the system code page (e.g. a project folder named "Kōna" or a user folder with an
accent): they fail silently, returning None / False. Python opens such paths fine,
so OpenCV only encodes and decodes here, in memory. Same results on Linux and macOS.
"""
from pathlib import Path

import cv2
import numpy as np


def imread(path, flags=cv2.IMREAD_COLOR):
    """Like cv2.imread: the image, or None if the file is missing or unreadable."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if not data.size:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, image, params=()):
    """Like cv2.imwrite: True if the image was written. The format follows the file extension."""
    try:
        ok, encoded = cv2.imencode(Path(path).suffix or '.jpg', image, list(params))
        if not ok:
            return False
        encoded.tofile(str(path))
        return True
    except (OSError, cv2.error):
        return False
