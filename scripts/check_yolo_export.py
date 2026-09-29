"""Small runnable check: pixi run python scripts/check_yolo_export.py."""
import json
from pathlib import Path
import tempfile

import cv2
import numpy as np

from model_evaluation import confirm_review_frame
from pipeline import SUMMARY_FIELDS
from review_io import save_rows
from review_summary import refresh_results, results_need_refresh
from yolo_export import export_yolo_dataset, reviewed_frames


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        original = {'fps': 1, 'duration': 3, 'total_frames': 3,
                    'video': 'fish.mp4',
                    'source_video': str(root / 'missing.mp4'),
                    'frames': [{'frame_number': 1, 'detections': [
                        {'species': 'Fish', 'bbox': [10, 20, 30, 40], 'confidence': 0.5}]},
                        {'frame_number': 3, 'detections': []}]}
        (root / 'original_detections.json').write_text(json.dumps(original))
        images = root / 'review_frames'; images.mkdir()
        for number in range(1, 4):
            assert cv2.imwrite(str(images / f'frame_{number:06d}.jpg'), np.zeros((100, 100, 3), np.uint8))
        fields = ['frame_number', 'species', 'x1', 'y1', 'x2', 'y2', 'review_status',
                  'annotation_source', 'annotation_id']
        rows = [dict(frame_number=1, species='Stonefish', x1=10, y1=20, x2=30, y2=40,
                     review_status='confirmed', annotation_source='model', annotation_id=''),
                dict(frame_number=3, species='Coral fish', x1=0, y1=0, x2=50, y2=50,
                     review_status='confirmed', annotation_source='manual', annotation_id='new-1')]
        save_rows(root / 'low_confidence_review.csv', rows, fields)
        for number in range(1, 4): confirm_review_frame(root, rows, number)
        assert results_need_refresh(root)
        save_rows(root / 'track_summary.csv', [], SUMMARY_FIELDS)
        refresh_results(root, root / 'low_confidence_review.csv')
        assert not results_need_refresh(root)
        yaml, counts = export_yolo_dataset(root, root / 'dataset')
        assert counts == (1, 1, 1)
        assert 'path:' not in yaml.read_text()
        assert (root / 'dataset/labels/train/frame_000001.txt').read_text().strip() == '1 0.200000 0.300000 0.200000 0.200000'
        assert (root / 'dataset/labels/val/frame_000002.txt').read_text() == ''
        assert (root / 'dataset/labels/test/frame_000003.txt').read_text().strip() == '0 0.250000 0.250000 0.500000 0.500000'
        rows[0]['species'] = 'Changed after confirmation'
        save_rows(root / 'low_confidence_review.csv', rows, fields)
        assert reviewed_frames(root)[2] == [2, 3]
    print('YOLO export check passed')


if __name__ == '__main__':
    main()
