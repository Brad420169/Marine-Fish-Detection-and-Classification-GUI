"""Tests for reviewing every video frame, not just flagged ones, and inline box drawing."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_review  # Configure the application import path and offscreen Qt.
import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton

from pages.review_page import ReviewPage
from pipeline import FLAGGED_FIELDS
from review_io import save_rows


class AllFramesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'review_frames').mkdir()
        self.frame = np.full((120, 160, 3), 80, dtype=np.uint8)

        self.video = self.root / 'source.mp4'
        writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*'mp4v'), 5, (160, 120))
        for _ in range(30):
            writer.write(self.frame)
        writer.release()

        # Flagged detections on frames 1 and 10 leave unflagged frames in between.
        self.rows = []
        for number in (1, 10):
            image = f'review_frames/frame_{number:06d}.jpg'
            cv2.imwrite(str(self.root / image), self.frame)
            self.rows.append(dict(zip(FLAGGED_FIELDS,
                                      [number, '00:00', 'Tang', .4, '', 10, 10, 60, 60, '', image, ''])))
        self.csv = self.root / 'review.csv'
        save_rows(self.csv, self.rows, FLAGGED_FIELDS)

        (self.root / 'original_detections.json').write_text(json.dumps({
            'fps': 5, 'duration': 6, 'video': 'source.mp4', 'source_video': str(self.video.resolve()),
            'frames': [{'frame_number': 1, 'detections': [
                {'species': 'Tang', 'confidence': .4, 'bbox': [10, 10, 60, 60], 'track_id': 1}]}],
        }))
        (self.root / 'species_names.json').write_text(json.dumps(['Tang']))

        self.page = ReviewPage(lambda: None)
        self.page.load_run(self.csv, self.root, self.video)
        self.addCleanup(self.page.shutdown)
        self.addCleanup(self.page.deleteLater)

    def saved_rows(self):
        with self.csv.open() as stream:
            return list(csv.DictReader(stream))

    def goto(self, number):
        self.page.review_only.setChecked(False)
        self.page.frame_number = number
        self.page.show_frame()

    def test_opens_whole_video_and_counts_all_frames(self):
        self.assertEqual(self.page.total_frames, 30)
        self.assertEqual(self.page.flagged_frames, [1, 10])
        self.assertIn('Frame 1 / 30', self.page.progress.text())

    def test_review_only_mode_jumps_between_flagged_frames(self):
        self.assertTrue(self.page.review_only.isChecked())
        self.assertEqual(self.page.frame_number, 1)
        self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 10)
        self.page.navigate(-1)
        self.assertEqual(self.page.frame_number, 1)

    def test_all_frames_mode_steps_one_frame(self):
        self.page.review_only.setChecked(False)
        self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 2)
        self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 3)
        self.page.navigate(-1)
        self.assertEqual(self.page.frame_number, 2)
        self.assertIsNotNone(self.page.raw)

    def test_navigation_clamps_to_video_bounds(self):
        self.goto(30)
        self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 30)
        self.goto(1)
        self.page.navigate(-1)
        self.assertEqual(self.page.frame_number, 1)

    def test_confirm_on_unflagged_frame_records_completion_without_changing_csv(self):
        before = self.csv.read_bytes()
        self.goto(5)
        self.assertEqual(self.page.current(), [])
        self.page.confirm_frame()
        self.assertEqual(self.page.frame_number, 6)
        self.assertEqual(self.csv.read_bytes(), before)
        from model_evaluation import evaluate_run
        report = evaluate_run(self.root, self.csv)
        self.assertEqual(report['reviewed_frames'], 1)
        self.assertIsNone(report['detection']['recall'])

    def test_skip_does_not_confirm_frame_for_evaluation(self):
        from model_evaluation import evaluate_run
        self.goto(5)
        self.page.navigate(1)
        self.assertEqual(evaluate_run(self.root, self.csv)['reviewed_frames'], 0)

    def test_evaluate_button_opens_saved_report(self):
        self.page.confirm_frame()
        with patch('pages.review_page.EvaluationPage') as dialog:
            self.page.evaluate_button.click()
        report = dialog.call_args.args[0]
        self.assertEqual(report['reviewed_frames'], 1)
        self.assertEqual(report['detection']['precision'], 1)
        dialog.return_value.exec.assert_called_once()

    def test_drawn_box_is_pending_until_confirmed(self):
        before = self.csv.read_bytes()
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        self.assertEqual(len(self.page.pending), 1)
        self.assertEqual(self.page.annotations.rowCount(), 1)
        self.assertEqual(self.csv.read_bytes(), before)

    def test_remove_box_drops_the_pending_annotation(self):
        self.goto(5)
        self.page.draw_box(.1, .1, .3, .3)
        self.page.draw_box(.5, .5, .8, .8)
        self.assertEqual(len(self.page.pending), 2)
        self.page.remove_box(self.page.annotations.cellWidget(0, 1))
        self.assertEqual(len(self.page.pending), 1)
        self.assertEqual(self.page.annotations.rowCount(), 1)
        self.assertEqual(self.page.pending[0][0], round(.5 * 160, 2))

    def test_confirm_saves_drawn_box_on_previously_unflagged_frame(self):
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        self.page.annotations.cellWidget(0, 0).setCurrentText('Tang')
        self.page.confirm_frame()

        rows = self.saved_rows()
        self.assertEqual(len(rows), 3)
        manual = [r for r in rows if r['annotation_source'] == 'manual']
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]['frame_number'], '5')
        self.assertEqual(manual[0]['species'], 'Tang')
        self.assertEqual(manual[0]['review_status'], 'confirmed')
        self.assertEqual(manual[0]['confidence'], '')
        self.assertTrue(manual[0]['annotation_id'])
        self.assertTrue((self.root / 'review_frames' / 'frame_000005.jpg').exists())
        self.assertTrue((self.root / 'review_frames' / 'frame_000005.json').exists())
        self.assertEqual(self.page.flagged_frames, [1, 5, 10])
        self.assertEqual(self.page.frame_number, 6)
        self.assertEqual(self.page.pending, [])

    def test_enter_commits_a_box_into_the_detection_table(self):
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        self.assertEqual(self.page.table.rowCount(), 0)
        combo = self.page.annotations.cellWidget(0, 0)
        combo.setCurrentText('Tang')
        combo.lineEdit().returnPressed.emit()

        # Saved immediately, without waiting for Confirm & Next.
        rows = self.saved_rows()
        self.assertEqual(len(rows), 3)
        manual = [r for r in rows if r['annotation_source'] == 'manual']
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]['species'], 'Tang')
        self.assertEqual(manual[0]['frame_number'], '5')
        # ...and it has moved out of the Annotations panel into the detection table.
        self.assertEqual(self.page.pending, [])
        self.assertEqual(self.page.annotations.rowCount(), 0)
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.table.cellWidget(0, 2).currentText(), 'Tang')
        self.assertEqual(self.page.decisions, ['confirmed'])
        self.assertIn(5, self.page.flagged_frames)
        self.assertEqual(self.page.frame_number, 5)      # stays put, unlike Confirm & Next

    def test_enter_commits_the_right_box_when_several_are_drawn(self):
        self.goto(5)
        self.page.draw_box(.1, .1, .3, .3)
        self.page.draw_box(.5, .5, .8, .8)
        second = self.page.annotations.cellWidget(1, 0)
        second.setCurrentText('Tang')
        second.lineEdit().returnPressed.emit()
        self.assertEqual(len(self.page.pending), 1)
        self.assertEqual(self.page.pending[0][0], round(.1 * 160, 2))   # the first box remains
        saved = [r for r in self.saved_rows() if r['annotation_source'] == 'manual']
        self.assertEqual(float(saved[0]['x1']), round(.5 * 160, 2))
        # The remaining box still commits against its own row after the shift.
        first = self.page.annotations.cellWidget(0, 0)
        first.setCurrentText('Tang')
        first.lineEdit().returnPressed.emit()
        self.assertEqual(self.page.pending, [])
        self.assertEqual(self.page.table.rowCount(), 2)

    def test_enter_with_no_species_warns_and_saves_nothing(self):
        before = self.csv.read_bytes()
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        with patch('pages.review_page.QMessageBox.warning') as warning:
            self.page.annotations.cellWidget(0, 0).lineEdit().returnPressed.emit()
            warning.assert_called_once()
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertEqual(len(self.page.pending), 1)

    def test_crossing_a_saved_manual_annotation_deletes_its_row(self):
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        self.page.annotations.cellWidget(0, 0).setCurrentText('Tang')
        self.page.confirm_frame()
        self.assertEqual(len(self.saved_rows()), 3)

        self.goto(5)
        self.assertEqual(self.page.table.rowCount(), 1)
        tick, cross = self.page.table.cellWidget(0, 3).findChildren(QPushButton)
        self.assertTrue(tick.isChecked())
        cross.click()
        self.page.confirm_frame()

        rows = self.saved_rows()
        self.assertEqual(len(rows), 2)      # the manual row is gone, not kept as rejected
        self.assertTrue(all(r['annotation_source'] != 'manual' for r in rows))
        self.assertNotIn(5, self.page.flagged_frames)

    def test_confirm_requires_a_species_for_every_drawn_box(self):
        before = self.csv.read_bytes()
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        with patch('pages.review_page.QMessageBox.warning') as warning:
            self.page.confirm_frame()
            warning.assert_called_once()
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertEqual(self.page.frame_number, 5)

    def test_drag_on_image_draws_a_box_but_a_plain_click_does_not(self):
        self.goto(5)
        self.page.show()
        self.app.processEvents()
        centre = self.page.image.rect().center()

        QTest.mouseClick(self.page.image, Qt.MouseButton.LeftButton, pos=centre)
        self.assertEqual(len(self.page.pending), 0)

        start, end = centre - QPoint(40, 30), centre + QPoint(40, 30)
        QTest.mousePress(self.page.image, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.page.image, end)
        QTest.mouseRelease(self.page.image, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(len(self.page.pending), 1)
        box = self.page.pending[0]
        self.assertGreater(box[2], box[0])
        self.assertGreater(box[3], box[1])
        self.page.hide()

    def wheel(self, steps, modifier=Qt.KeyboardModifier.NoModifier, pos=None):
        point = QPointF(pos if pos is not None else self.page.image.rect().center())
        self.page.image.wheelEvent(QWheelEvent(
            point, point, QPoint(), QPoint(0, int(120*steps)),
            Qt.MouseButton.NoButton, modifier, Qt.ScrollPhase.NoScrollPhase, False))

    def test_ctrl_scroll_zooms_in_and_out_within_bounds(self):
        self.page.show(); self.app.processEvents()
        self.assertEqual(self.page.image.zoom, 1.0)
        self.wheel(1, Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(self.page.image.zoom, 1.0)
        self.assertIn('zoom', self.page.progress.text())
        for _ in range(20):
            self.wheel(1, Qt.KeyboardModifier.ControlModifier)
        self.assertLessEqual(self.page.image.zoom, self.page.image.MAX_ZOOM)
        for _ in range(40):
            self.wheel(-1, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.image.zoom, 1.0)
        self.page.hide()

    def test_plain_scroll_pans_only_once_zoomed(self):
        self.page.show(); self.app.processEvents()
        before = self.page.image.centre
        self.wheel(-1)
        self.assertEqual(self.page.image.centre, before)
        self.wheel(3, Qt.KeyboardModifier.ControlModifier)
        centred = self.page.image.centre
        self.wheel(-1)
        self.assertNotEqual(self.page.image.centre[1], centred[1])
        self.page.hide()

    def test_zoom_keeps_the_point_under_the_cursor_and_maps_drawing(self):
        self.page.show(); self.app.processEvents()
        spot = QPointF(self.page.image.rect().center() + QPoint(60, 30))
        before = self.page.image.fraction(spot)
        self.wheel(2, Qt.KeyboardModifier.ControlModifier, pos=spot.toPoint())
        after = self.page.image.fraction(spot)
        self.assertAlmostEqual(before[0], after[0], places=2)
        self.assertAlmostEqual(before[1], after[1], places=2)
        # A box drawn while zoomed lands inside the visible region, not the whole frame.
        x0, y0, vw, vh = self.page.image.region
        self.page.image.on_draw(*self.page.image.fraction(spot),
                                *self.page.image.fraction(spot + QPointF(40, 40)))
        box = self.page.pending[0]
        self.assertGreaterEqual(box[0], x0 - 1)
        self.assertLessEqual(box[2], x0 + vw + 1)
        self.assertGreaterEqual(box[1], y0 - 1)
        self.assertLessEqual(box[3], y0 + vh + 1)
        self.page.hide()

    def test_drawn_box_is_selected_and_shown_enlarged(self):
        self.goto(5)
        self.assertTrue(self.page.crop.pixmap().isNull())
        self.page.draw_box(.2, .2, .5, .5)
        self.assertEqual(self.page.selected, 0)
        self.assertFalse(self.page.crop.pixmap().isNull())

    def test_drawn_box_selection_follows_the_detection_table(self):
        self.page.draw_box(.6, .6, .8, .8)   # frame 1 already has one flagged detection
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.selected, 1)
        crop_of_box = self.page.crop.pixmap().toImage()
        self.page.select_row(0, 0)
        self.assertEqual(self.page.selected, 0)
        self.assertNotEqual(self.page.crop.pixmap().toImage(), crop_of_box)
        self.page.select_annotation(0)
        self.assertEqual(self.page.selected, 1)

    def test_remove_box_keeps_the_selection_in_range(self):
        self.goto(5)
        self.page.draw_box(.1, .1, .3, .3)
        self.page.remove_box(self.page.annotations.cellWidget(0, 1))
        self.assertEqual(self.page.pending, [])
        self.assertEqual(self.page.selected, 0)
        self.page.render()

    def test_leaving_a_frame_with_a_drawn_box_prompts(self):
        self.goto(5)
        self.page.draw_box(.2, .2, .5, .5)
        with patch('pages.review_page.QMessageBox.question',
                   return_value=QMessageBox.StandardButton.No) as question:
            self.page.navigate(1)
            question.assert_called_once()
        self.assertEqual(self.page.frame_number, 5)


if __name__ == '__main__':
    unittest.main()
