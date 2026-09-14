import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_review
from model_evaluation import (confirm_review_frame, evaluate, evaluate_run,
                              fingerprint, frame_fingerprint, read_confirmation)
from pages.evaluation_page import EvaluationPage
from review_io import save_rows
from PyQt6.QtWidgets import QApplication


def detection(species='Tang', x=0):
    return dict(species=species, bbox=[x, 0, x+10, 10])


def row(species='Tang', x=0, status='confirmed', source='model', number=1):
    return dict(frame_number=number, species=species, x1=x, y1=0, x2=x+10, y2=10,
                review_status=status, annotation_source=source, annotation_id=f'{number}-{x}', reviewed='yes')


def original(detections, total=1):
    return dict(fps=1, duration=total, total_frames=total,
                frames=[dict(frame_number=1, detections=detections)])


def completion(data, rows, numbers=(1,)):
    return dict(source=fingerprint(data), frames={str(n): frame_fingerprint(rows, n) for n in numbers})


class EvaluationTests(unittest.TestCase):
    def test_corrections_rejections_and_misses(self):
        data = original([detection(), detection(x=20), detection(x=40)])
        rows = [row(), row('Wrasse', x=20), row(x=40, status='rejected'), row(x=60, source='manual')]
        report = evaluate(data, rows, completion(data, rows))
        self.assertEqual(report['counts'], dict(correct=1, corrected=1, rejected=1, missed=1))
        self.assertAlmostEqual(report['classification']['precision'], 1/3)
        self.assertAlmostEqual(report['classification']['recall'], 1/3)
        self.assertAlmostEqual(report['detection']['precision'], 2/3)
        self.assertAlmostEqual(report['detection']['recall'], 2/3)
        self.assertEqual(report['species']['Tang']['fp'], 2)
        self.assertEqual(report['species']['Wrasse']['fn'], 1)

    def test_unreviewed_high_confidence_predictions_are_excluded(self):
        data = original([detection()], total=2)
        data['frames'].append(dict(frame_number=2, detections=[detection()]))
        report = evaluate(data, [], completion(data, []))
        self.assertEqual(report['counts']['correct'], 1)
        self.assertFalse(report['complete'])
        self.assertEqual(report['reviewed_frames'], 1)

    def test_empty_frames_and_zero_denominators(self):
        data = original([])
        report = evaluate(data, [], completion(data, []))
        self.assertTrue(report['complete'])
        self.assertIsNone(report['detection']['precision'])
        self.assertIsNone(report['detection']['recall'])
        rows = [row(source='manual')]
        report = evaluate(data, rows, completion(data, rows))
        self.assertIsNone(report['detection']['precision'])
        self.assertEqual(report['detection']['recall'], 0)

    def test_saved_edits_invalidate_prior_confirmation(self):
        data = original([detection()])
        rows = [row()]; saved = completion(data, rows)
        rows[0]['species'] = 'Wrasse'
        self.assertEqual(evaluate(data, rows, saved)['reviewed_frames'], 0)

    def test_pending_rows_are_never_ground_truth(self):
        data = original([detection()]); rows = [row(status='pending')]
        self.assertEqual(evaluate(data, rows, completion(data, rows))['reviewed_frames'], 0)

    def test_replaced_original_invalidates_confirmations(self):
        data = original([detection()]); saved = completion(data, [])
        data['frames'][0]['detections'][0]['species'] = 'Wrasse'
        self.assertEqual(evaluate(data, [], saved)['reviewed_frames'], 0)

    def test_unmatched_review_rejected(self):
        data = original([detection()]); rows = [row(x=20)]
        with self.assertRaisesRegex(ValueError, 'do not match'):
            evaluate(data, rows, completion(data, rows))

    def test_durable_confirmation_survives_csv_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); data = original([detection()]); rows = [row()]
            (root/'original_detections.json').write_text(json.dumps(data))
            save_rows(root/'review.csv', rows, list(rows[0]))
            confirm_review_frame(root, rows, 1)
            self.assertTrue(evaluate_run(root, root/'review.csv')['complete'])
            before = read_confirmation(root)
            with patch('model_evaluation.os.replace', side_effect=OSError('locked')):
                with self.assertRaises(OSError): confirm_review_frame(root, rows, 1)
            self.assertEqual(read_confirmation(root), before)

    def test_legacy_review_requires_full_frame_confirmation(self):
        data = original([detection()]); rows = [row()]
        self.assertEqual(evaluate(data, rows, {})['reviewed_frames'], 0)

    def test_legacy_saved_frame_is_provisional(self):
        data = original([detection()]); rows = [row()]
        report = evaluate(data, rows, {}, legacy_frames=[1])
        self.assertEqual(report['classification']['precision'], 1)
        self.assertEqual(report['legacy_frames'], 1)
        self.assertFalse(report['complete'])

    def test_stale_confirmation_cannot_fall_back_to_legacy_image(self):
        data = original([detection()]); rows = [row()]
        saved = completion(data, rows)
        rows[0]['species'] = 'Wrasse'
        report = evaluate(data, rows, saved, legacy_frames=[1])
        self.assertEqual(report['reviewed_frames'], 0)

    def test_ui_explains_scores_and_switches_mode(self):
        app = QApplication.instance() or QApplication([])
        data = original([detection()]); rows = [row('Wrasse')]
        dialog = EvaluationPage(evaluate(data, rows, completion(data, rows)))
        self.assertEqual(dialog.cards['precision'][0].text(), '0.00')
        self.assertIn('wrong species', dialog.cards['recall'][1].text())
        dialog.mode.setCurrentIndex(1)
        self.assertEqual(dialog.cards['precision'][0].text(), '1.00')
        self.assertEqual(dialog.cards['recall'][1].text(), '0% of fish were not detected.')
        dialog.close()
