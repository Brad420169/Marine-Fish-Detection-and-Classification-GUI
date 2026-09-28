"""Run with QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests."""
import csv
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cv2
import numpy as np
from PyQt6.QtWidgets import QApplication, QPushButton
from pages.review_page import ReviewPage
from pipeline import FLAGGED_FIELDS
from review_io import save_rows, ReviewVideoWorker, original_video_backup


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'review_frames').mkdir()
        self.frame = np.full((120,160,3),80,dtype=np.uint8)
        self.rows = []
        for number in (1,2):
            image = f'review_frames/frame_{number:06d}.jpg'
            cv2.imwrite(str(self.root/image),self.frame)
            self.rows.append(dict(zip(FLAGGED_FIELDS,[number,'00:00','Tang',.4,'',10,10,60,60,'',image,''])))
        self.csv = self.root/'review.csv'
        save_rows(self.csv,self.rows,FLAGGED_FIELDS)
        self.page = ReviewPage(lambda:None)
        self.page.load_run(self.csv,self.root,None)
        self.addCleanup(self.page.deleteLater)

    def test_navigation_and_resume(self):
        self.page.navigate(1)
        self.assertTrue(all(r['review_status']=='pending' for r in self.page.rows))
        self.page.navigate(-1)
        self.page.confirm_frame()
        self.page.load_run(self.csv,self.root,None)
        self.assertEqual(self.page.frame_number,2)
        self.assertTrue((self.root/'reviewed_frames/frame_000001.jpg').exists())

    def buttons(self, row):
        """The tick and cross for a row of the detection table."""
        return self.page.table.cellWidget(row, 3).findChildren(QPushButton)

    def test_cross_rejects_and_tick_is_the_default(self):
        tick, cross = self.buttons(0)
        self.assertTrue(tick.isChecked())
        self.assertFalse(cross.isChecked())
        cross.click()
        self.assertTrue(cross.isChecked())
        self.assertFalse(tick.isChecked())
        self.page.confirm_frame()
        self.page.confirm_frame()          # frame 2 is confirmed by the default tick
        with self.csv.open() as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['review_status'],'rejected')
        self.assertEqual(rows[1]['review_status'],'confirmed')
        self.assertEqual(rows[0]['reviewed'],'yes')
        self.assertEqual(rows[1]['reviewed'],'yes')
        self.assertEqual(rows[0]['original_species'],'Tang')

    def test_crossed_model_row_is_kept_so_the_refresh_can_drop_it(self):
        self.buttons(0)[1].click()
        self.page.confirm_frame()
        with self.csv.open() as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)     # the row stays, marked rejected
        self.assertEqual(rows[0]['review_status'],'rejected')

    def test_tick_can_undo_a_cross_before_saving(self):
        tick, cross = self.buttons(0)
        cross.click()
        self.assertEqual(self.page.decisions[0],'rejected')
        tick.click()
        self.assertEqual(self.page.decisions[0],'confirmed')
        self.page.confirm_frame()
        with self.csv.open() as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['review_status'],'confirmed')

    def test_failed_replace_preserves_csv(self):
        original=self.csv.read_bytes()
        with patch('review_io.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                save_rows(self.csv,[],FLAGGED_FIELDS)
        self.assertEqual(self.csv.read_bytes(),original)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_video_export_preserves_original_and_length(self):
        video=self.root/'annotated.mp4'
        writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),5,(160,120))
        self.assertTrue(writer.isOpened())
        for _ in range(3):writer.write(self.frame)
        writer.release()
        original=video.read_bytes()
        self.page.confirm_frame()
        worker=ReviewVideoWorker(video,self.root,self.page.rows)
        errors=[]; worker.failed.connect(errors.append);worker.run()
        self.assertEqual(errors,[])
        self.assertEqual(original_video_backup(video).read_bytes(),original)
        self.assertNotEqual(video.read_bytes(),original)
        cap=cv2.VideoCapture(str(video))
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),3)
        cap.release()

    def test_repeated_export_preserves_backup_and_failure_preserves_video(self):
        video = self.root/'repeat.mp4'
        writer = cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),5,(160,120))
        for _ in range(3): writer.write(self.frame)
        writer.release()
        original = video.read_bytes()
        self.page.confirm_frame()
        for _ in range(2):
            errors=[]
            worker=ReviewVideoWorker(video,self.root,self.page.rows)
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors,[])
            self.assertEqual(original_video_backup(video).read_bytes(),original)
        current=video.read_bytes()
        broken=[dict(r,frame_image='missing.jpg') for r in self.page.rows]
        worker=ReviewVideoWorker(video,self.root,broken)
        errors=[];worker.failed.connect(errors.append);worker.run()
        self.assertTrue(errors)
        self.assertEqual(video.read_bytes(),current)
        self.assertEqual(original_video_backup(video).read_bytes(),original)

    def test_arrow_navigation_and_frame_jump_do_not_confirm(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.page.show()
        self.app.processEvents()
        field = self.page.table.cellWidget(0,2).lineEdit()
        field.setFocus()
        QTest.keyClick(field,Qt.Key.Key_Right)
        self.assertEqual(self.page.frame_number,2)
        QTest.keyClick(self.page.table.cellWidget(0,2).lineEdit(),Qt.Key.Key_Left)
        self.assertEqual(self.page.frame_number,1)
        with patch('pages.review_page.QInputDialog.getInt',return_value=(2,True)):
            self.page.progress.linkActivated.emit('jump')
        self.assertEqual(self.page.frame_number,2)
        self.assertTrue(all(r['review_status']=='pending' for r in self.page.rows))
        self.page.hide()

    def test_wheel_does_not_change_review_dropdown(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QWheelEvent
        combo=self.page.table.cellWidget(0,2)
        combo.setCurrentIndex(0)
        wheel=QWheelEvent(QPointF(5,5),QPointF(5,5),QPoint(),QPoint(0,-120),
                         Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase,False)
        self.app.sendEvent(combo,wheel)
        self.assertEqual(combo.currentIndex(),0)

    def test_resolved_row_hidden_until_box_clicked(self):
        self.page.confirm_frame()
        self.page.navigate(-1)
        self.assertTrue(self.page.table.isRowHidden(0))
        self.page.select_box(30/160,30/120)
        self.assertFalse(self.page.table.isRowHidden(0))
        self.assertEqual(self.page.selected,0)

    def test_click_confident_context_adds_editable_model_row(self):
        self.page.detections_by_frame={1:[dict(species='Tang',confidence=.95,
            track_id=5,bbox=[80,70,120,110])]}
        self.page.select_box(100/160,90/120)
        self.assertEqual(self.page.table.rowCount(),2)
        added=self.page.current()[-1]
        self.assertEqual(added['annotation_source'],'model')
        self.assertEqual(added['track_id'],5)
        self.assertEqual(added['original_species'],'Tang')
        self.page.table.cellWidget(1,2).setCurrentText('Tang')
        self.page.confirm_frame()
        with self.csv.open() as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(rows[-1]['confidence'],'0.95')
        self.assertEqual(rows[-1]['reviewed'],'yes')

    def test_tab_completion_then_enter_saves_pending_annotation(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.page.names=['fish']
        self.page.show()
        self.app.processEvents()
        self.page.draw_box(.6,.6,.9,.9)
        combo=self.page.annotations.cellWidget(0,0)
        editor=combo.lineEdit()
        editor.setFocus()
        QTest.keyClicks(editor,'f')
        QTest.keyClick(editor,Qt.Key.Key_Tab)
        self.assertEqual(combo.currentText(),'fish')
        self.assertTrue(editor.hasFocus())
        QTest.keyClick(editor,Qt.Key.Key_Return)
        self.assertEqual(len(self.page.pending),0)
        manual=[r for r in self.page.rows if r.get('annotation_source')=='manual']
        self.assertEqual(len(manual),1)
        self.assertEqual(manual[0]['species'],'fish')
        with self.csv.open() as stream:
            self.assertTrue(any(r['species']=='fish' for r in csv.DictReader(stream)))
        self.page.hide()

    def test_ctrl_z_removes_pending_boxes_newest_first(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        before=self.csv.read_bytes()
        self.page.show();self.app.processEvents()
        self.page.draw_box(.1,.1,.3,.3)
        first=list(self.page.pending[0])
        self.page.draw_box(.6,.6,.9,.9)
        editor=self.page.annotations.cellWidget(1,0).lineEdit()
        editor.setFocus()
        QTest.keyClick(editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.pending,[first])
        self.assertEqual(self.page.annotations.rowCount(),1)
        QTest.keyClick(self.page.image,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.pending,[])
        self.assertEqual(self.page.annotations.rowCount(),0)
        self.page.undo_annotation()
        self.assertEqual(self.csv.read_bytes(),before)
        self.assertEqual(len(self.page.rows),2)
        self.page.hide()


if __name__=='__main__':
    unittest.main()
