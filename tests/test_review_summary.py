import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_review  # Configure the application import path and offscreen Qt.
from review_summary import merge_detections, summarize, refresh_results
from review_io import save_rows
from pipeline import SUMMARY_FIELDS


def detection(species, track, x):
    return dict(species=species,track_id=track,confidence=.5,bbox=[x,0,x+10,10])


def review(frame, x, species, status):
    return dict(frame_number=frame,x1=x,y1=0,x2=x+10,y2=10,species=species,review_status=status)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.original=dict(fps=2,duration=2,video='fish.mp4',source_video='',frames=[
            dict(frame_number=1,detections=[detection('A',1,0),detection('A',2,20)]),
            dict(frame_number=2,detections=[detection('A',1,0)]),
            dict(frame_number=4,detections=[detection('B',3,0)])])
        self.reviews=[review(1,0,'B','confirmed'),review(1,20,'A','rejected'),review(4,0,'C','pending')]

    def test_relabel_reject_and_pending_statistics(self):
        frames=merge_detections(self.original,self.reviews)
        rows={r['species']:r for r in summarize(self.original,frames)}
        self.assertEqual(set(rows),{'A','B'})
        self.assertEqual(rows['B']['total_detections'],2)
        self.assertEqual(rows['B']['unique_tracks'],2)
        self.assertEqual(rows['B']['max_n'],1)
        self.assertEqual(rows['B']['max_n_frame'],1)
        self.assertEqual(rows['B']['visible_seconds'],1)
        self.assertEqual(rows['B']['observation_span_seconds'],2)
        self.assertEqual(rows['A']['max_n_frame'],2)
        self.assertEqual(self.original['frames'][0]['detections'][0]['species'],'A')

    def test_manual_merge_and_statistics(self):
        original=dict(fps=2,duration=2,video='fish.mp4',frames=[dict(frame_number=1,detections=[detection('Tang',1,0)])])
        manual=dict(frame_number=1,x1=30,y1=10,x2=50,y2=40,species='Tang',review_status='confirmed',annotation_source='manual',annotation_id='new-fish')
        merged=merge_detections(original,[manual])
        row=summarize(original,merged)[0]
        self.assertEqual(row['total_detections'],2)
        self.assertEqual(row['max_n'],2)
        self.assertEqual(row['unique_tracks'],1)
        self.assertEqual(row['mean_confidence'],.5)
        self.assertEqual(merge_detections(original,[manual]),merged)
        manual['species']='New species'
        row=next(r for r in summarize(original,merge_detections(original,[manual])) if r['species']=='New species')
        self.assertEqual(row['mean_confidence'],'')
        self.assertEqual(row['unique_tracks'],0)
        manual['review_status']='rejected'
        self.assertEqual(len(merge_detections(original,[manual])[0]['detections']),1)

    def test_unknown_review_fails(self):
        with self.assertRaises(ValueError):merge_detections(self.original,[review(99,0,'A','confirmed')])

    def test_all_rejected_has_empty_summary(self):
        original=dict(self.original,frames=[self.original['frames'][1]])
        self.assertEqual(summarize(original,merge_detections(original,[review(2,0,'A','rejected')])),[])

    def test_repeat_refresh_preserves_original_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'original_detections.json').write_text(json.dumps(self.original))
            save_rows(root/'track_summary.csv',summarize(self.original,self.original['frames']),SUMMARY_FIELDS)
            original_csv=(root/'track_summary.csv').read_bytes()
            save_rows(root/'review.csv',self.reviews,list(self.reviews[0]))
            refresh_results(root,root/'review.csv')
            first=(root/'track_summary.csv').read_bytes()
            refresh_results(root,root/'review.csv')
            self.assertEqual(first,(root/'track_summary.csv').read_bytes())
            self.assertEqual(original_csv,(root/'track_summary_original.csv').read_bytes())
            with (root/'reviewed_detections.csv').open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),3)

    def test_legacy_refused_without_modifying_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);summary=root/'track_summary.csv';summary.write_text('original')
            with self.assertRaisesRegex(ValueError,'older run'):refresh_results(root,root/'review.csv')
            self.assertEqual(summary.read_text(),'original')
