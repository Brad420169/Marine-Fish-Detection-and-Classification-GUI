"""Run with QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests."""
import json
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
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QPushButton, QComboBox, QLineEdit
from pages.review_page import ReviewPage
from review_io import REVIEW_COLUMNS, load_review_rows, save_rows, original_box
from review_tracks import create_track, advance_tracks, stop_track
from review_summary import merge_detections
from model_evaluation import evaluate, fingerprint, frame_fingerprint

APP = QApplication.instance() or QApplication([])
FIELDS = list(dict.fromkeys(['frame_number','timestamp','species','confidence','track_id',
    'x1','y1','x2','y2','reviewed','notes','frame_image'] + REVIEW_COLUMNS))

def manual(number=1):
    return dict.fromkeys(FIELDS,'') | dict(frame_number=number, species='Fish', x1=10,y1=10,x2=30,y2=30,
        annotation_source='manual',annotation_id='manual-1',review_status='confirmed',reviewed='yes')

class TracksTest(unittest.TestCase):
    def test_step_tracking_is_pending_bounded_and_idempotent(self):
        rows, identity = create_track([manual()], 'manual-1')
        self.assertEqual(len(rows),1)
        rows = advance_tracks(rows,1,3)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['review_status'],'pending')
        self.assertNotEqual(rows[0]['annotation_id'],rows[1]['annotation_id'])
        self.assertEqual(advance_tracks(rows,1,3),rows)
        self.assertEqual(advance_tracks(rows,2,3),rows)
        rows[1].update(review_status='confirmed',x1=20,x2=40)
        rows = advance_tracks(rows,2,3)
        self.assertEqual(rows[2]['x1'],20)
        rows[2]['review_status']='confirmed'
        self.assertEqual(advance_tracks(rows,3,3),rows)
        stopped = stop_track(rows,identity,2)
        self.assertEqual([r['review_status'] for r in stopped],['confirmed','rejected','rejected'])
        self.assertEqual(advance_tracks(stopped,1,3),stopped)

    def test_moved_model_merges_by_original_identity(self):
        row=manual();row.update(annotation_source='model',original_bbox='[10,10,30,30]',x1=20,x2=40)
        original={'fps':1,'duration':1,'frames':[{'frame_number':1,'detections':[
            dict(species='Fish',confidence=.7,track_id=2,bbox=[10,10,30,30])]}]}
        merged=merge_detections(original,[row])
        self.assertEqual(len(merged[0]['detections']),1)
        self.assertEqual(merged[0]['detections'][0]['bbox'],[20,10,40,30])
        self.assertEqual(merged[0]['detections'][0]['track_id'],2)
        row['review_status']='rejected'
        self.assertEqual(merge_detections(original,[row])[0]['detections'],[])

    def test_evaluation_matches_moved_model(self):
        row=manual();row.update(annotation_source='model',original_bbox='[10,10,30,30]',x1=20,x2=40)
        original={'fps':1,'duration':1,'class_names':['Fish'],'frames':[{'frame_number':1,'detections':[
            dict(species='Fish',confidence=.7,track_id=2,bbox=[10,10,30,30])]}]}
        completion={'source':fingerprint(original),'frames':{'1':frame_fingerprint([row],1)}}
        report=evaluate(original,[row],completion)
        self.assertEqual(report['counts']['correct'],1)
        self.assertEqual(report['verified_frames'],1)

    def test_legacy_csv_roundtrip_and_invalid_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'review.csv';row=manual();row['annotation_source']='model'
            legacy=[f for f in FIELDS if f not in ('original_bbox','manual_track_id','track_keyframe')]
            save_rows(path,[{k:row[k] for k in legacy}],legacy)
            rows,fields=load_review_rows(path)
            self.assertEqual(original_box(rows[0]),[10,10,30,30])
            save_rows(path,rows,fields)
            self.assertEqual(len(load_review_rows(path)[0]),1)
        with self.assertRaises(ValueError): original_box(dict(row,original_bbox='[0,0,NaN,20]'))

class PageTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.video=self.root/'source.avi'
        writer=cv2.VideoWriter(str(self.video),cv2.VideoWriter_fourcc(*'MJPG'),10,(160,120))
        assert writer.isOpened()
        for i in range(6): writer.write(np.full((120,160,3),30+i*20,dtype=np.uint8))
        writer.release()
        self.original=dict(fps=10,duration=.6,total_frames=6,source_video=str(self.video),frames=[
            dict(frame_number=i,detections=[dict(species='Fish',confidence=.4,track_id=1,bbox=[10,10,30,30])])
            for i in range(1,7)])
        (self.root/'original_detections.json').write_text(json.dumps(self.original))
        row=manual(2);row.update(annotation_source='model',annotation_id='',reviewed='',review_status='pending',original_species='Fish')
        self.path=self.root/'low_confidence_review.csv';save_rows(self.path,[row],FIELDS)
        self.page=ReviewPage(lambda:None);self.page.resize(1200,900);self.page.show()
        self.page.load_run(self.path,self.root,self.video);APP.processEvents()

    def tearDown(self):
        self.page.shutdown();self.page.close();sip.delete(self.page);APP.processEvents();self.temp.cleanup()

    def test_copy_paste_annotation_across_frames_and_undo(self):
        self.page.frame_number = 2; self.page.show_frame()
        self.page.selected = 0
        self.page.box_edits[0] = [10, 10, 40, 30]
        self.page.image.setFocus()
        QTest.keyClick(self.page.image, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.copied_annotation, ('Fish', 30, 20))
        self.page.box_edits.clear()
        self.page.navigate(1)
        self.assertEqual(self.page.frame_number, 3)
        image = self.page.image
        centre = image.mapToGlobal(image.rect().center())
        with patch('pages.review_page.QCursor.pos', return_value=centre):
            QTest.keyClick(image, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        box = self.page.pending[0]
        self.assertAlmostEqual(box[2]-box[0], 30)
        self.assertAlmostEqual(box[3]-box[1], 20)
        self.assertAlmostEqual((box[0]+box[2])/2, 80, delta=1)
        self.assertEqual(self.page.annotations.cellWidget(0,0).currentText(), 'Fish')
        QTest.keyClick(image, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.pending, [])
        # A zoomed, panned viewport still maps the cursor to source pixels.
        image.zoom = 2; image.centre = (.65, .6); image.redraw()
        point = image.rect().center()
        expected = image.fraction(point)
        with patch('pages.review_page.QCursor.pos', return_value=image.mapToGlobal(point)):
            self.page.paste_annotation()
        box = self.page.pending[0]
        self.assertAlmostEqual((box[0]+box[2])/2, expected[0]*160, places=1)
        self.page.commit_box(0)
        saved = [r for r in self.page.rows if r['frame_number'] == 3 and r.get('annotation_source') == 'manual']
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0]['annotation_id'])
        self.assertEqual(saved[0]['species'], 'Fish')
        self.assertFalse(saved[0].get('manual_track_id'))

    def test_paste_bounds_and_text_field_copy(self):
        self.page.copied_annotation = ('Fish', 30, 20)
        image = self.page.image
        pix = image.pixmap()
        point = QPoint((image.width()-pix.width())//2+1, (image.height()-pix.height())//2+1)
        with patch('pages.review_page.QCursor.pos', return_value=image.mapToGlobal(point)):
            self.page.paste_annotation()
        self.assertEqual(self.page.pending[0], [0, 0, 30, 20])
        with patch('pages.review_page.QCursor.pos', return_value=image.mapToGlobal(QPoint(-10,-10))):
            self.page.paste_annotation()
        self.assertEqual(len(self.page.pending), 1)
        editor = self.page.annotations.cellWidget(0,0).lineEdit()
        editor.setFocus(); editor.selectAll()
        QTest.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(APP.clipboard().text(), 'Fish')
        APP.clipboard().setText('Other')
        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.text(), 'Other')
        self.assertEqual(len(self.page.pending), 1)

    def test_playback_every_frame_red_border_and_stop(self):
        self.assertFalse(self.page.review_only.isVisible())
        self.assertEqual(self.page.frame_number,1)
        self.page.toggle_playback();visited=[]
        for _ in range(5):
            self.page.playback.stop();self.page.play_next_frame();visited.append(self.page.frame_number)
            if self.page.frame_number==2:
                self.assertTrue(self.page.image.flagged)
                image=self.page.image.grab().toImage()
                reds=sum(image.pixelColor(x, self.page.image.height()//2).red()>230
                         and image.pixelColor(x, self.page.image.height()//2).green()<80
                         for x in range(image.width()))
                self.assertGreater(reds,0)
        self.assertEqual(visited,[2,3,4,5,6]);self.assertFalse(self.page.playing)
        self.assertAlmostEqual(float(self.page.raw.mean()),130,delta=3)
        self.assertFalse((self.root/'review_completion.json').exists())
        self.page.toggle_playback();self.assertEqual(self.page.frame_number,1)
        self.page.hide();self.assertFalse(self.page.playback.isActive())

    def test_move_save_reload_model_and_no_duplicate(self):
        self.page.navigate(1)
        self.assertTrue(self.page.begin_box_drag(20/160,20/120))
        self.page.move_box_drag(40/160,30/120);self.page.end_box_drag()
        self.assertEqual(original_box(self.page.drafts()[0]),[10,10,30,30])
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):
            self.page.navigate(1)
        self.assertEqual(self.page.frame_number,2)
        self.page.confirm_frame()
        rows,_=load_review_rows(self.path)
        self.assertEqual([float(rows[0][k]) for k in ('x1','y1','x2','y2')],[30,20,50,40])
        merged=merge_detections(self.original,rows)
        self.assertEqual(merged[1]['detections'][0]['bbox'],[30,20,50,40])
        self.assertEqual(len(merged[1]['detections']),1)

    def test_save_failure_keeps_geometry_draft(self):
        self.page.navigate(1)
        self.page.begin_box_drag(20/160,20/120);self.page.move_box_drag(40/160,30/120)
        before=self.path.read_bytes()
        with patch('pages.review_page.save_rows',side_effect=OSError('Disk full')), patch.object(QMessageBox,'critical'):
            self.page.confirm_frame()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.page.frame_number,2)
        self.assertEqual(self.page.box_edits[0],[30,20,50,40])

    def test_timer_advances_without_manual_ticks(self):
        self.page.toggle_playback()
        for _ in range(30):
            QTest.qWait(50)
            if not self.page.playing:break
        self.assertEqual(self.page.frame_number,6)
        self.assertFalse(self.page.playback.isActive())

    def test_mouse_drag_and_clamp_pending_box(self):
        self.page.draw_box(.2,.2,.4,.4)
        self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        image=self.page.image;APP.processEvents()
        pix=image.pixmap();left=(image.width()-pix.width())/2;top=(image.height()-pix.height())/2
        start=QPoint(round(left+.3*pix.width()),round(top+.3*pix.height()))
        end=QPoint(round(left+.5*pix.width()),round(top+.5*pix.height()))
        QTest.mousePress(image,Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(image,end)
        QTest.mouseRelease(image,Qt.MouseButton.LeftButton,pos=end)
        box=self.page.pending[0]
        self.assertAlmostEqual(box[0],64,delta=1);self.assertAlmostEqual(box[1],48,delta=1)
        self.assertTrue(self.page.begin_box_drag(.5,.5))
        self.page.move_box_drag(2,2);self.page.end_box_drag()
        self.assertEqual(self.page.pending[0][2:],[160,120])

    def test_track_propagates_only_on_confirm_and_survives_reload(self):
        self.page.draw_box(.4,.4,.6,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.commit_box(0,track=True)
        self.page.navigate(1);self.page.navigate(-1)
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),1)
        self.page.confirm_frame()
        self.assertEqual(self.page.frame_number,2)
        self.page.box_edits[1]=[70,48,102,72]
        self.page.confirm_frame()
        rows,_=load_review_rows(self.path)
        tracked=[r for r in rows if r.get('manual_track_id')]
        self.assertEqual(len(tracked),3)
        self.assertEqual(float(tracked[-1]['x1']),70)
        self.assertEqual(tracked[-1]['review_status'],'pending')
        completion=json.loads((self.root/'review_completion.json').read_text())
        self.assertNotIn('3',completion['frames'])
        self.page.load_run(self.path,self.root,self.video)
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),3)

    def test_track_confirmation_failure_does_not_propagate(self):
        self.page.draw_box(.4,.4,.6,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.commit_box(0,track=True)
        before=self.path.read_bytes()
        with patch('pages.review_page.save_rows',side_effect=OSError('Disk full')), patch.object(QMessageBox,'critical'):
            self.page.confirm_frame()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.page.frame_number,1)
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),1)

    def test_delete_model_via_keyboard_and_arrows_do_not_confirm(self):
        self.page.navigate(1)
        self.page.select_box(20/160,20/120)
        self.assertIs(APP.focusWidget(),self.page.image)
        QTest.keyClick(self.page.image,Qt.Key.Key_Delete)
        self.assertTrue(self.page.table.isRowHidden(0))
        rows,_=load_review_rows(self.path)
        self.assertEqual(rows[0]['review_status'],'rejected')
        self.assertEqual(merge_detections(self.original,rows)[1]['detections'],[])
        QTest.keyClick(self.page.image,Qt.Key.Key_Right)
        self.assertEqual(self.page.frame_number,3)
        QTest.keyClick(self.page.image,Qt.Key.Key_Left)
        self.assertEqual(self.page.frame_number,2)
        self.assertTrue(self.page.table.isRowHidden(0))
        self.assertFalse((self.root/'review_completion.json').exists())

    def test_delete_manual_pending_and_saved(self):
        self.page.draw_box(.4,.4,.6,.6)
        self.assertTrue(self.page.track_prompt.isVisible())
        self.page.image.setFocus();QTest.keyClick(self.page.image,Qt.Key.Key_Delete)
        self.assertEqual(self.page.pending,[])
        self.page.draw_box(.4,.4,.6,.6)
        self.page.annotations.cellWidget(0,0).setCurrentText('Fish');self.page.commit_box(0)
        self.page.select_box(.5,.5);QTest.keyClick(self.page.image,Qt.Key.Key_Delete)
        self.assertTrue(self.page.table.isRowHidden(0))
        self.page.load_run(self.path,self.root,self.video)
        self.assertTrue(self.page.table.isRowHidden(0))
        rows,_=load_review_rows(self.path)
        self.assertFalse(any(d.get('annotation_source')=='manual'
                             for f in merge_detections(self.original,rows) for d in f['detections']))

    def test_delete_track_stops_and_preserves_other_drafts(self):
        self.page.draw_box(.4,.4,.6,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.commit_box(0,track=True)
        self.page.confirm_frame()
        self.page.box_edits[0]=[11,11,31,31]
        self.page.select_box(.5,.5);self.page.delete_selected_box()
        self.assertEqual(self.page.box_edits[0],[11,11,31,31])
        rows,_=load_review_rows(self.path)
        tracked=sorted((r for r in rows if r.get('manual_track_id')),key=lambda r:r['frame_number'])
        self.assertEqual([r['review_status'] for r in tracked],['confirmed','rejected'])
        self.page.confirm_frame()
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),2)

    def test_delete_failure_keeps_box_and_typing_delete_edits_text(self):
        self.page.navigate(1);self.page.select_box(20/160,20/120)
        with patch('pages.review_page.save_rows',side_effect=OSError('Disk full')), patch.object(QMessageBox,'critical'):
            QTest.keyClick(self.page.image,Qt.Key.Key_Delete)
        self.assertFalse(self.page.table.isRowHidden(0))
        editor=self.page.table.cellWidget(0,2).lineEdit();editor.setFocus();editor.setText('Fish');editor.setCursorPosition(0)
        QTest.keyClick(editor,Qt.Key.Key_Delete)
        self.assertEqual(editor.text(),'ish')
        self.assertFalse(self.page.table.isRowHidden(0))

    def test_undo_deleted_model_after_navigation_restores_saved_row(self):
        self.page.navigate(1);self.page.select_box(20/160,20/120)
        before=self.path.read_bytes()
        self.page.delete_selected_box();self.page.navigate(1)
        QTest.keyClick(self.page.image,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.frame_number,2)
        self.assertFalse(self.page.table.isRowHidden(0))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.page.deletion_undo)
        self.assertFalse((self.root/'review_completion.json').exists())

    def test_undo_saved_manual_and_pending_removal(self):
        self.page.draw_box(.4,.4,.6,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.commit_box(0)
        before=self.path.read_bytes()
        self.page.select_box(.5,.5);self.page.delete_selected_box();self.page.undo_deletion()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.page.table.isRowHidden(0))
        self.page.draw_box(.65,.4,.85,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Another fish')
        self.page.remove_box(self.page.annotations.cellWidget(0,1))
        self.page.image.setFocus()
        QTest.keyClick(self.page.image,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.pending,[[104,48,136,72]])
        self.assertEqual(self.page.annotations.cellWidget(0,0).currentText(),'Another fish')
        self.assertIsNone(APP.activeModalWidget())
        self.assertEqual(self.path.read_bytes(),before)

    def test_undo_track_restores_continuation_and_unrelated_drafts(self):
        self.page.draw_box(.4,.4,.6,.6);self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.commit_box(0,track=True);self.page.confirm_frame()
        before=self.path.read_bytes()
        self.page.box_edits[0]=[11,11,31,31]
        self.page.select_box(.5,.5);self.page.delete_selected_box();self.page.undo_deletion()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.page.box_edits[0],[11,11,31,31])
        self.assertFalse(self.page.table.isRowHidden(1))
        self.page.confirm_frame()
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),3)

    def test_multiple_deletions_undo_in_reverse_order_and_failure_retries(self):
        self.page.navigate(1);self.page.select_box(20/160,20/120);self.page.delete_selected_box()
        self.page.navigate(1);self.page.select_box(20/160,20/120);self.page.delete_selected_box()
        before=self.path.read_bytes()
        with patch('pages.review_page.save_rows',side_effect=OSError('Disk full')), patch.object(QMessageBox,'critical'):
            self.page.undo_deletion()
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(len(self.page.deletion_undo),2)
        self.page.undo_deletion();self.assertEqual(self.page.frame_number,3)
        self.page.undo_deletion();self.assertEqual(self.page.frame_number,2)
        self.assertFalse(self.page.deletion_undo)
        rows,_=load_review_rows(self.path)
        self.assertTrue(all(r['review_status']!='rejected' for r in rows))

    def test_undo_preserves_deleted_box_draft_and_clears_on_new_save(self):
        self.page.navigate(1);self.page.select_box(20/160,20/120)
        self.page.box_edits[0]=[20,20,40,40]
        self.page.table.cellWidget(0,2).setCurrentText('Edited species')
        self.page.delete_selected_box();self.page.undo_deletion()
        self.assertEqual(self.page.box_edits[0],[20,20,40,40])
        self.assertEqual(self.page.table.cellWidget(0,2).currentText(),'Edited species')
        self.page.delete_selected_box();self.page.confirm_frame()
        self.assertFalse(self.page.deletion_undo)

    def test_inline_tracking_defaults_off_and_saves_per_box(self):
        self.page.draw_box(.4,.4,.6,.6)
        self.assertIsNone(APP.activeModalWidget())
        self.assertTrue(self.page.track_prompt.isVisible())
        self.assertTrue(self.page.track_no.isChecked())
        self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.track_yes.click()
        self.page.delete_selected_box();self.page.undo_deletion()
        self.assertTrue(self.page.track_yes.isChecked())
        self.page.draw_box(.7,.4,.9,.6)
        self.page.annotations.cellWidget(1,0).setCurrentText('Fish')
        self.assertTrue(self.page.track_no.isChecked())
        self.page.track_yes.click();self.page.track_no.click()
        self.page.confirm_frame()
        rows,_=load_review_rows(self.path)
        tracked=[r for r in rows if r.get('manual_track_id')]
        self.assertEqual(len(tracked),2)
        self.assertEqual(tracked[-1]['frame_number'],2)
        self.assertEqual(tracked[-1]['review_status'],'pending')
        self.assertEqual(len([r for r in rows if r.get('annotation_source')=='manual']),3)
        self.assertFalse(self.page.track_prompt.isVisible())

    def test_inline_tick_enter_saves_without_advancing(self):
        self.page.draw_box(.4,.4,.6,.6)
        self.page.annotations.cellWidget(0,0).setCurrentText('Fish')
        self.page.track_yes.click()
        self.page.commit_box(0)
        self.assertEqual(self.page.frame_number,1)
        self.assertEqual(len([r for r in self.page.rows if r.get('manual_track_id')]),1)
        self.assertFalse(self.page.pending)

    def test_ctrl_drag_resizes_model_and_preserves_identity(self):
        self.page.navigate(1)
        image=self.page.image
        def point(x,y):
            pix=image.pixmap();x0,y0,vw,vh=image.region
            return QPoint(round((image.width()-pix.width())/2+(x-x0)*pix.width()/vw),
                          round((image.height()-pix.height())/2+(y-y0)*pix.height()/vh))
        start,end=point(10,20),point(15,20)
        QTest.mousePress(image,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,pos=start)
        QTest.mouseMove(image,end)
        QTest.mouseRelease(image,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,pos=end)
        self.assertAlmostEqual(self.page.box_edits[0][0],15,delta=1)
        self.assertEqual(self.page.box_edits[0][1:],[10,30,30])
        self.page.confirm_frame()
        rows,_=load_review_rows(self.path)
        self.assertEqual(original_box(rows[0]),[10,10,30,30])
        self.assertAlmostEqual(float(rows[0]['x1']),15,delta=1)

    def test_resize_top_corner_clamps_and_works_zoomed(self):
        self.page.draw_box(.4,.4,.6,.6)
        self.page.image.zoom=2;self.page.image.redraw()
        self.assertTrue(self.page.begin_box_resize(.5,.4))
        self.page.move_box_drag(.5,.45);self.page.end_box_drag()
        self.assertEqual(self.page.pending[0],[64,54,96,72])
        self.assertTrue(self.page.begin_box_resize(.6,.6))
        self.page.move_box_drag(2,2);self.page.end_box_drag()
        self.assertEqual(self.page.pending[0],[64,54,160,120])
        self.assertTrue(self.page.begin_box_resize(.4,.45))
        self.page.move_box_drag(2,2);self.page.end_box_drag()
        self.assertEqual(self.page.pending[0],[158,118,160,120])

    def test_play_refuses_to_discard_edits_and_no_source(self):
        self.page.draw_box(.4,.4,.6,.6)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):self.page.toggle_playback()
        self.assertFalse(self.page.playing);self.assertEqual(len(self.page.pending),1)
        self.page.shutdown();self.page.update_controls()
        self.assertFalse(self.page.play_button.isEnabled())

if __name__=='__main__':unittest.main()
