"""Darknet adapter checks without downloading weights or running a large model."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from detectors import load_detector


class DarknetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.weights = Path(self.temp.name) / 'fish.weights'
        self.weights.write_bytes(b'fake')
        self.weights.with_suffix('.cfg').write_text(
            '[net]\nwidth=608\nheight=608\nchannels=3\n[yolo]\nclasses=1\n[yolo]\nclasses=1\n')
        self.weights.with_suffix('.names').write_text('fish\n')

    def detector(self):
        with patch('detectors.cv2.dnn.readNetFromDarknet'), patch('detectors.cv2.dnn_DetectionModel') as constructor:
            detector = load_detector(self.weights, device='cpu')
            constructor.return_value.setInputParams.assert_called_once_with(
                size=(608, 608), scale=1 / 255.0, swapRB=True, crop=False)
            return detector

    def test_boxes_are_clipped_and_tracked_with_thresholds(self):
        detector = self.detector()
        detector.model.detect.return_value = (np.array([0]), np.array([0.9]), np.array([[-5, 10, 30, 60]]))
        frame = np.zeros((50, 80, 3), dtype=np.uint8)
        first = detector.infer(frame, 0.25, 0.8)
        second = detector.infer(frame, 0.25, 0.8)
        self.assertEqual(first.species, ['fish'])
        self.assertEqual(first.xyxy, [[0, 10, 25, 50]])
        self.assertEqual(first.track_ids, second.track_ids)
        self.assertIsNotNone(first.track_ids[0])
        self.assertEqual(first.annotated.shape, frame.shape)
        self.assertFalse(frame.any(), 'annotation must not alter source frames')
        self.assertEqual(detector.model.detect.call_args.kwargs, {'confThreshold': 0.25, 'nmsThreshold': 0.8})

    def test_empty_detections(self):
        detector = self.detector()
        detector.model.detect.return_value = ((), (), ())
        result = detector.infer(np.zeros((50, 80, 3), dtype=np.uint8), 0.25, 0.8)
        self.assertEqual(result.species, [])
        self.assertEqual(result.xyxy, [])
        self.assertEqual(result.track_ids, [])

    def test_missing_config_has_actionable_error(self):
        self.weights.with_suffix('.cfg').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing Darknet configuration'):
            load_detector(self.weights, device='cpu')

    def test_wrong_label_count_rejected(self):
        self.weights.with_suffix('.names').write_text('fish\nshark\n')
        with self.assertRaisesRegex(ValueError, 'must contain 1 class names'):
            load_detector(self.weights, device='cpu')

    def test_missing_labels_use_explicit_placeholders(self):
        self.weights.with_suffix('.names').unlink()
        detector = self.detector()
        self.assertEqual(detector.class_names, ['class_0'])
        self.assertTrue(detector.class_names_missing)

    def test_zip_named_pt_reports_actual_problem(self):
        archive = self.weights.with_suffix('.pt')
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('fish/fish.weights', b'fake')
            z.writestr('fish/fish.cfg', '[net]')
        with self.assertRaisesRegex(ValueError, 'ZIP bundle containing Darknet'):
            load_detector(archive, device='cpu')

    def test_explicit_gpu_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, 'CPU only'):
            load_detector(self.weights, device='cuda:0')


class DarknetImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt6.QtWidgets import QApplication
        from app import set_app_font
        cls.app = QApplication.instance() or QApplication([])
        set_app_font(cls.app)

    def test_import_copies_companions_and_selects_weights(self):
        from widgets import WeightsRow
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'incoming'
            source.mkdir()
            for extension, content in (('.weights', b'weights'), ('.cfg', b'config'), ('.names', b'fish')):
                (source / ('fish' + extension)).write_bytes(content)
            with patch('widgets.ROOT_DIR', root):
                row = WeightsRow()
            with patch('widgets.QFileDialog.getOpenFileName', return_value=(str(source / 'fish.weights'), '')):
                row._upload_weights()
            for extension in ('.weights', '.cfg', '.names'):
                self.assertEqual((root / 'models' / ('fish' + extension)).read_bytes(),
                                 (source / ('fish' + extension)).read_bytes())
            self.assertEqual(row.get_path(), str(root / 'models/fish.weights'))
            row.close()

    def test_import_missing_config_copies_nothing(self):
        from widgets import WeightsRow
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'fish.weights'
            source.write_bytes(b'weights')
            with patch('widgets.ROOT_DIR', root):
                row = WeightsRow()
            with patch('widgets.QFileDialog.getOpenFileName', return_value=(str(source), '')), \
                    patch('widgets.QMessageBox.warning') as warning:
                row._upload_weights()
            warning.assert_called_once()
            self.assertEqual(list((root / 'models').iterdir()), [])
            row.close()


if __name__ == '__main__':
    unittest.main()
