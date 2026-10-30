"""SAM click routing, frame safety, and annotation persistence; no model download."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from app import set_app_font
from annotation_session import create_session
from pages.review_page import ReviewPage
from review_io import load_review_rows
from sam_assist import SAMSession, mask_box
from yolo_export import reviewed_frames


class SAMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        set_app_font(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        video = self.root / 'fish.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 5, (160, 100))
        assert writer.isOpened()
        for _ in range(3):
            writer.write(np.full((100, 160, 3), 40, dtype=np.uint8))
        writer.release()
        project = SimpleNamespace(name='test', output_root=str(self.root), add_item=lambda *args: None)
        self.session_root = create_session(project, video)
        self.page = ReviewPage(lambda: None)
        self.page.resize(1200, 800)
        self.page.load_run(self.session_root / 'low_confidence_review.csv', self.session_root, video)
        self.page.names = ['Fish']
        self.page.show()
        self.app.processEvents()
        self.mask = np.zeros((100, 160), dtype=bool)
        self.mask[20:40, 30:60] = True
        self.page.sam_session.predict = Mock(return_value=self.mask)
        self.page.image.setFocus()
        self.app.processEvents()

    def tearDown(self):
        self.page.shutdown()
        self.app.removeEventFilter(self.page)
        self.page.hide()
        self.page.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_completion_summary_reuses_validation_and_tracks_saved_changes(self):
        self.page._completion_cache = None
        with patch('yolo_export.reviewed_frames', wraps=reviewed_frames) as validate:
            self.page.update_controls()
            self.page.update_controls()
            self.assertEqual(validate.call_count, 1)
            self.page.record_frame_completion()
            self.page.update_controls()
            self.assertEqual(validate.call_count, 2)
            self.assertIn('1 frames complete', self.page.progress.text())
            self.page.update_controls()
            self.assertEqual(validate.call_count, 2)
            # An external source change must also invalidate the cached count.
            source = self.session_root / 'original_detections.json'
            import json
            data = json.loads(source.read_text())
            data['benchmark_change'] = True
            source.write_text(json.dumps(data))
            self.page.update_controls()
            self.assertEqual(validate.call_count, 3)
            self.assertIn('0 frames complete', self.page.progress.text())

    def test_completion_cache_invalidates_when_saved_annotations_change(self):
        index = self.page.add_pending_box([2, 3, 12, 13], 'Fish')
        self.page.commit_box(index)
        self.page.record_frame_completion()
        self.page.update_controls()
        self.assertIn('1 frames complete', self.page.progress.text())
        from review_io import save_rows
        rows, fields = load_review_rows(self.page.path)
        rows[0]['species'] = 'Changed externally'
        save_rows(self.page.path, rows, fields)
        self.page.update_controls()
        self.assertIn('0 frames complete', self.page.progress.text())
        self.assertEqual(reviewed_frames(self.session_root)[2], [])

    def test_mask_preview_preserves_raw_pixels_and_unmasked_area(self):
        raw = self.page.raw.copy()
        expected = raw.copy()
        expected[self.mask] = (raw[self.mask] * .55 + np.array([70, 220, 100]) * .45).astype(np.uint8)
        self.page.sam_mask = self.mask
        self.page.render()
        np.testing.assert_array_equal(self.page.raw, raw)
        np.testing.assert_array_equal(self.page.canvas[~self.mask], raw[~self.mask])
        self.assertLessEqual(np.abs(self.page.canvas.astype(int) - expected.astype(int)).max(), 1)

    def wait_sam(self):
        deadline = time.monotonic() + 5
        while self.page.suggestion_worker and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.page.suggestion_worker)

    def prompt(self, button=Qt.MouseButton.LeftButton):
        image = self.page.image
        self.page.sam_checkbox.setChecked(True)
        QTest.mouseClick(image, button, pos=QPoint(image.width()//2, image.height()//2))
        self.wait_sam()

    def test_include_exclude_and_class_enter_saves_box_not_frame(self):
        image = self.page.image
        image.on_exclude = Mock()
        image.zoom = 2
        image.redraw()
        self.page.sam_checkbox.setChecked(True)
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            QTest.mouseClick(image, button, pos=QPoint(image.width()//2, image.height()//2))
            self.wait_sam()
        self.assertEqual(self.page.sam_labels, [1, 1, 0])
        self.assertAlmostEqual(self.page.sam_points[0][0], 80, delta=1)
        self.assertAlmostEqual(self.page.sam_points[0][1], 50, delta=1)
        image.on_exclude.assert_not_called()
        self.assertEqual(self.page.pending, [])  # No bounding box until a class is entered.
        self.assertEqual(load_review_rows(self.page.path)[0], [])
        self.page.sam_class.setFocus()
        QTest.keyClicks(self.page.sam_class, 'Fish')
        self.assertTrue(image.sam_enabled)
        QTest.keyClick(self.page.sam_class, Qt.Key.Key_Left)
        self.assertEqual(self.page.frame_number, 1)
        QTest.keyClick(self.page.sam_class, Qt.Key.Key_Return)
        rows, _ = load_review_rows(self.page.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['species'], 'Fish')
        self.assertEqual([float(rows[0][k]) for k in ('x1', 'y1', 'x2', 'y2')], [30, 20, 60, 40])
        self.assertEqual(reviewed_frames(self.session_root)[2], [])
        self.page.confirm_frame()
        self.assertEqual(reviewed_frames(self.session_root)[2], [1])

    def test_saved_annotations_survive_sam(self):
        index = self.page.add_pending_box([2, 3, 12, 13], 'Fish')
        self.page.commit_box(index)
        previous = load_review_rows(self.page.path)[0][0].copy()
        self.prompt()
        self.page.sam_class.setText('Fish')
        self.page.finish_sam()
        rows, _ = load_review_rows(self.page.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], previous)

    def test_failed_prediction_releases_worker_and_allows_retry(self):
        self.page.sam_session.predict.side_effect = RuntimeError('test inference error')
        self.prompt()
        self.assertFalse(self.page.sam_busy)
        self.assertIn('test inference error', self.page.status.text())
        self.assertEqual(load_review_rows(self.page.path)[0], [])
        self.page.sam_session.predict.side_effect = None
        self.prompt()
        self.assertTrue(self.page.sam_mask.any())

    def test_negative_click_cannot_start_annotation(self):
        self.prompt(Qt.MouseButton.RightButton)
        self.page.sam_session.predict.assert_not_called()
        self.assertEqual(self.page.sam_points, [])

    def test_empty_name_and_empty_mask_do_not_create_boxes(self):
        self.prompt()
        self.page.finish_sam()
        self.assertEqual(self.page.pending, [])
        self.assertIsNotNone(self.page.sam_mask)
        self.page.sam_class.setText('Fish')
        self.page.sam_mask[:] = False
        self.page.finish_sam()
        self.assertEqual(load_review_rows(self.page.path)[0], [])

    def test_cancel_and_frame_change_clear_preview(self):
        self.prompt()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 1)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 2)
        self.assertEqual(self.page.sam_points, [])
        self.prompt()
        QTest.keyClick(self.page.image, Qt.Key.Key_Escape)
        self.assertIsNone(self.page.sam_mask)
        self.assertEqual(load_review_rows(self.page.path)[0], [])

    def test_stale_result_is_ignored(self):
        self.page.apply_sam_mask(self.mask, self.page.sam_frame_token - 1)
        self.assertIsNone(self.page.sam_mask)

    def test_ctrl_drag_resizes_with_sam_enabled_and_with_preview(self):
        image = self.page.image
        self.page.sam_checkbox.setChecked(True)
        self.page.add_pending_box([40, 25, 80, 50], 'Fish')
        for preview in (False, True):
            if preview:
                self.prompt()
            self.page.sam_session.predict.reset_mock()
            points = list(self.page.sam_points)
            box = list(self.page.pending[0])
            pix = image.pixmap()
            x0 = (image.width() - pix.width()) / 2
            y0 = (image.height() - pix.height()) / 2
            start = QPoint(round(x0 + box[2] / 160 * pix.width()),
                           round(y0 + box[3] / 100 * pix.height()))
            end = start + QPoint(15, 15)
            QTest.mousePress(image, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ControlModifier, start)
            self.assertIsNotNone(self.page.drag_box)
            QTest.mouseMove(image, end)
            QTest.mouseRelease(image, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.ControlModifier, end)
            self.assertGreater(self.page.pending[0][2], box[2])
            self.assertGreater(self.page.pending[0][3], box[3])
            self.assertEqual(self.page.sam_points, points)
            self.page.sam_session.predict.assert_not_called()

    def test_ctrl_a_toggles_sam_but_selects_text_in_class_field(self):
        image = self.page.image
        QTest.keyClick(image, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self.page.sam_checkbox.isChecked())
        self.assertTrue(image.sam_enabled)
        self.prompt()
        self.page.sam_class.setText('Fish')
        self.page.sam_class.setFocus()
        QTest.keyClick(self.page.sam_class, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.sam_class.selectedText(), 'Fish')
        self.assertTrue(self.page.sam_checkbox.isChecked())
        image.setFocus()
        QTest.keyClick(image, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(self.page.sam_checkbox.isChecked())
        self.assertFalse(image.sam_enabled)
        self.assertIsNone(self.page.sam_mask)

    def test_checkbox_off_restores_manual_mode_and_clears_preview(self):
        self.assertFalse(self.page.image.sam_enabled)
        self.prompt()
        self.page.sam_checkbox.setChecked(False)
        self.assertFalse(self.page.image.sam_enabled)
        self.assertIsNone(self.page.sam_mask)
        QTest.keyPress(self.page.image, Qt.Key.Key_S)
        self.assertFalse(self.page.image.sam_enabled)
        QTest.keyRelease(self.page.image, Qt.Key.Key_S)

    def test_ctrl_z_discards_preview_from_class_field(self):
        self.prompt()
        self.page.sam_class.setFocus()
        QTest.keyClick(self.page.sam_class, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertIsNone(self.page.sam_mask)
        self.assertEqual(self.page.sam_points, [])
        self.assertTrue(self.page.sam_checkbox.isChecked())
        self.prompt()
        self.assertIsNotNone(self.page.sam_mask)

    def test_ctrl_z_removes_saved_sam_box_preserving_other_annotations(self):
        self.page.commit_box(self.page.add_pending_box([2, 3, 12, 13], 'Fish'))
        before = load_review_rows(self.page.path)[0]
        self.prompt()
        self.page.sam_class.setText('Fish')
        self.page.finish_sam()
        self.assertEqual(len(load_review_rows(self.page.path)[0]), 2)
        QTest.keyClick(self.page.image, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(load_review_rows(self.page.path)[0], before)
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(reviewed_frames(self.session_root)[2], [])

    def test_backend_groups_prompts_and_reuses_image(self):
        import torch
        model = Mock()
        model.return_value = [SimpleNamespace(masks=SimpleNamespace(data=torch.tensor(self.mask[None])))]
        session = SAMSession()
        checkpoint = self.root / 'sam2.1_t.pt'
        checkpoint.touch()
        with patch('sam_assist.CHECKPOINT', checkpoint), \
                patch('ultralytics.models.sam.SAM2Predictor', return_value=model), \
                patch('inference_device.select_device', return_value=('cpu', 'CPU')):
            for _ in range(2):
                session.predict(self.page.raw, 1, [(40, 30), (5, 5)], [1, 0])
            model.set_image.assert_called_once()
            self.assertEqual(model.call_args.kwargs['points'], [[(40, 30), (5, 5)]])
            self.assertEqual(model.call_args.kwargs['labels'], [[1, 0]])
            session.predict(self.page.raw, 2, [(40, 30)], [1])
            self.assertEqual(model.set_image.call_count, 2)

    def test_mask_box_includes_last_pixel(self):
        self.assertEqual(mask_box(self.mask), [30, 20, 60, 40])
        with self.assertRaises(ValueError):
            mask_box(np.zeros_like(self.mask))


if __name__ == '__main__':
    unittest.main()
