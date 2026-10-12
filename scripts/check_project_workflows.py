"""Focused headless integration check: pixi run python scripts/check_project_workflows.py.

Uses a tiny synthetic video and a stub training process, never downloads weights.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/marine-mpl')
import json
import shutil
from pathlib import Path
import tempfile
from unittest.mock import patch
import time
from types import SimpleNamespace

import cv2
import numpy as np
from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

import project_manager
from annotation_session import create_session
from dataset_validation import inspect_dataset
from pages.main_window import MainWindow
from review_io import load_review_rows, save_rows
from yolo_export import reviewed_frames, export_yolo_dataset


def main():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        project_manager.PROJECTS_DIR = folder / 'projects'
        project = project_manager.create_project('Workflow check', str(folder / 'outputs'))
        # Legacy project metadata stays readable.
        metadata = json.loads(project.meta_path.read_text()); metadata.pop('items')
        project.meta_path.write_text(json.dumps(metadata))
        project = project_manager.Project.load(project.path)
        assert project.items == []
        video = folder / 'reef.avi'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 5, (160, 120))
        for number in range(12):
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            cv2.rectangle(frame, (20, 20), (60, 60), (0, 200, 200), -1)
            writer.write(frame)
        writer.release()
        window = MainWindow(project); window.show(); app.processEvents()
        assert window.pages.currentWidget() is window.overview_page
        for key in ('detection', 'review', 'train', 'results'):
            assert window.shell.buttons[key].isEnabled()
            window._navigate_shell(key); app.processEvents()
        assert window.pages.currentWidget() is window.empty_results
        root = create_session(project, video, interval=2)
        window._open_annotation(root); app.processEvents()
        editor = window.review_page
        assert editor.manual_session and editor.raw is not None
        editor.names = ['Fish']
        editor.add_pending_box([20, 20, 60, 60], 'Fish')
        editor.confirm_frame()  # first frame with a fish
        assert editor.frame_number == 3
        editor.confirm_frame()  # explicitly empty frame
        assert editor.frame_number == 5
        editor.add_pending_box([20, 20, 60, 60], 'Fish')
        editor.confirm_frame()
        assert reviewed_frames(root)[2] == [1, 3, 5]
        editor.add_pending_box([20, 20, 60, 60], 'Fish')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
            window._navigate_shell('train')
        assert window.pages.currentWidget() is editor  # unsaved drafts protected
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            window._navigate_shell('overview')
        assert window.pages.currentWidget() is window.overview_page
        assert not (root / 'track_summary.csv').exists()  # manual work isn't a detection run
        window._open_annotation(root)
        assert editor.frame_number == 7  # resumes at first unfinished sampled frame
        saved_rows = (root / 'low_confidence_review.csv').read_bytes()
        editor.add_pending_box([20, 20, 60, 60], 'Fish')
        editor.apply_suggestions(SimpleNamespace(xyxy=[[20, 20, 60, 60], [90, 20, 120, 50]], species=['Fish', 'Fish']))
        assert len(editor.pending) == 2  # duplicate suggestion skipped; human draft preserved
        assert (root / 'low_confidence_review.csv').read_bytes() == saved_rows
        editor.show_frame()  # suggestions are drafts until confirmed
        # Completing a different detection must not replace this editor's selected result.
        prior_outputs = dict(window.results_page.outputs)
        window._pending_model_name = 'background-model'
        window._pending_output_dir = folder / 'background-run'
        window._pending_output_dir.mkdir()
        window._on_finished({'summary_csv': window._pending_output_dir / 'track_summary.csv'})
        assert window.pages.currentWidget() is editor
        assert window.results_page.outputs == prior_outputs
        project.runs.clear(); project.save()
        exported, counts = export_yolo_dataset(root, folder / 'dataset')
        assert counts == (1, 1, 1)
        assert (exported.parent / 'labels/val/frame_000003.txt').read_text() == ''
        assert not list(exported.parent.rglob('*000007*'))
        info = inspect_dataset(exported)
        assert info['counts'] == {'train': 1, 'val': 1, 'test': 1}
        assert info['names'] == ['Fish']
        window._navigate_shell('train')
        window._use_dataset(str(exported))
        assert not project.runs and window.training_page._inspect_dataset()
        # Invalid labels and YAML hooks are rejected before launching a trainer.
        label = exported.parent / 'labels/train/frame_000001.txt'
        original_label = label.read_text(); label.write_text('3 0.5 0.5 0.2 0.2\n')
        try:
            inspect_dataset(exported)
            raise AssertionError('invalid class accepted')
        except ValueError: pass
        label.write_text(original_label)
        original_yaml = exported.read_text(); exported.write_text(original_yaml + '\ndownload: echo unsafe\n')
        try:
            inspect_dataset(exported)
            raise AssertionError('download hook accepted')
        except ValueError: pass
        exported.write_text(original_yaml)
        # Exercise actual QProcess completion/registration with a tiny stand-in trainer.
        best = folder / 'runs/check/weights/best.pt'; best.parent.mkdir(parents=True); best.write_bytes(b'test')
        stub = folder / 'trainer.py'; stub.write_text(f"print('BEST_WEIGHTS={best.as_posix()}')\n")
        train = window.training_page
        real_start = train.process.start
        with patch.object(train.process, 'start', side_effect=lambda executable, args: real_start(executable, [str(stub)])):
            train._start()
        deadline = time.monotonic() + 10
        while train.process.state() != QProcess.ProcessState.NotRunning and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        app.processEvents()
        assert train.best_weights == best and train.evaluate.isEnabled()
        assert train.reuse_buttons[0].isEnabled()
        assert any(item['kind'] == 'model' for item in project.items)
        window._use_model(str(best), 'detection')
        assert window.weights_row.get_path() == str(best)
        window._use_model(str(best), 'review')
        assert editor.assist_weights == str(best)
        reloaded = project_manager.Project.load(project.path)
        assert {item['kind'] for item in reloaded.items} == {'annotation', 'dataset', 'model'}
        # Existing detection records still open in the shared editor and refresh results.
        legacy = folder / 'existing-run'; shutil.copytree(root, legacy)
        source = json.loads((legacy / 'original_detections.json').read_text())
        source.pop('kind')
        (legacy / 'original_detections.json').write_text(json.dumps(source))
        from pipeline import SUMMARY_FIELDS
        save_rows(legacy / 'track_summary.csv', [], SUMMARY_FIELDS)
        record = project.record_run('existing-model', legacy)
        window._open_run(record)
        assert window.pages.currentWidget() is editor and not editor.manual_session
        window._navigate_shell('results')
        deadline = time.monotonic() + 10
        while editor.worker and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        app.processEvents()
        assert not editor.worker and window.pages.currentWidget() is window.results_page
        assert (legacy / 'reviewed_detections.csv').is_file()
        # Switching projects clears the selected run, model results and dataset.
        other = project_manager.create_project('Other project', str(folder / 'other'))
        window.open_project(other)
        assert not window.results_page.outputs and window.training_page.best_weights is None
        assert not window.training_page.dataset.text()
        window.close(); window.deleteLater()
        QTimer.singleShot(0, app.quit); app.exec()
    print('Project workflows passed: independent entries, manual save/resume/export, external dataset, training handoff, persistence.')


if __name__ == '__main__':
    main()
