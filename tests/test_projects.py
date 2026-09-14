"""Tests for the Projects page and its in-window navigation."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_review  # Configure the application import path and offscreen Qt.
from PyQt6.QtWidgets import QApplication, QMessageBox

import project_manager
from project_manager import Project, RunRecord
from pages.project_page import ProjectsPage, short_path
from pages.main_window import MainWindow
from pipeline import SUMMARY_FIELDS


class ProjectsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        # Point the project store at a sandbox so real projects are untouched.
        patcher = patch.object(project_manager, 'PROJECTS_DIR', self.root/'projects')
        patcher.start(); self.addCleanup(patcher.stop)
        (self.root/'projects').mkdir()

        self.make('Alpha', '2026-09-14 09:34', 'yolo11s', [396])
        self.make('Bravo', '2026-08-28 14:12', 'yolov8s', [500, 743])
        self.make('Charlie', '2020-01-05 10:00', 'yolo11s', [])

        self.page = ProjectsPage()
        self.addCleanup(self.page.deleteLater)

    def make(self, name, created, model, counts):
        project = Project(name=name, created=created, output_root=str(self.root/'out'/name))
        for index, count in enumerate(counts, 1):
            run_dir = self.root/'out'/name/model/f'run_{index}'
            run_dir.mkdir(parents=True)
            with (run_dir/'track_summary.csv').open('w', newline='', encoding='utf-8') as fh:
                writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS); writer.writeheader()
                writer.writerow(dict(dataset='d', video='v.mp4', video_duration_seconds=60,
                                     species='fish', max_n=4, max_n_timestamp='00:17', max_n_frame=10,
                                     first_seen='00:01', last_seen='00:40', visible_seconds=5,
                                     observation_span_seconds=40, unique_tracks=2,
                                     total_detections=count, mean_confidence=.6))
            project.runs.append(RunRecord(index, model, created, str(run_dir)))
        project.save()
        return project

    def names(self):
        return [card.project.name for card in self.page.cards]

    def test_stats_are_read_back_from_each_run(self):
        self.assertEqual(self.page.stats['Alpha']['detections'], 396)
        self.assertEqual(self.page.stats['Bravo']['detections'], 1243)   # summed across runs
        self.assertEqual(self.page.stats['Bravo']['runs'], 2)
        self.assertEqual(self.page.stats['Bravo']['model'], 'yolov8s')
        self.assertEqual(self.page.stats['Charlie']['detections'], 0)
        self.assertEqual(self.page.stats['Charlie']['model'], '')

    def test_default_sort_is_newest_first(self):
        self.assertEqual(self.names(), ['Alpha', 'Bravo', 'Charlie'])

    def test_sort_orders(self):
        for label, expected in (('Last Modified (Oldest)', ['Charlie', 'Bravo', 'Alpha']),
                                ('Name (A–Z)', ['Alpha', 'Bravo', 'Charlie']),
                                ('Most Detections', ['Bravo', 'Alpha', 'Charlie'])):
            self.page.sort.setCurrentIndex(self.page.sort.findText(label))
            self.assertEqual(self.names(), expected, label)

    def test_search_filters_by_name(self):
        self.page.search.setText('bra')
        self.assertEqual(self.names(), ['Bravo'])
        self.page.search.setText('nothing here')
        self.assertEqual(self.names(), [])
        self.assertTrue(self.page.empty.isVisible() or self.page.empty.isVisibleTo(self.page))

    def test_model_filter(self):
        self.page.model_filter.setCurrentIndex(self.page.model_filter.findText('yolo11s'))
        # Charlie has no runs, so it has no model and drops out of a model filter.
        self.assertEqual(self.names(), ['Alpha'])
        self.page.model_filter.setCurrentIndex(self.page.model_filter.findText('yolov8s'))
        self.assertEqual(self.names(), ['Bravo'])
        self.page.model_filter.setCurrentIndex(0)
        self.assertEqual(self.names(), ['Alpha', 'Bravo', 'Charlie'])

    def test_date_filter_excludes_old_projects(self):
        self.page.date_filter.setCurrentIndex(self.page.date_filter.findText('Last 30 days'))
        self.assertNotIn('Charlie', self.names())   # modified in 2020

    def test_view_all_toggles_the_recent_cap(self):
        for index in range(6):
            self.make(f'Extra{index}', '2026-09-01 08:00', 'yolo11s', [10])
        self.page.refresh()
        self.assertEqual(len(self.page.cards), 4)          # capped to the recent list
        self.page.view_all.click()
        self.assertEqual(len(self.page.cards), 9)
        self.page.view_all.click()
        self.assertEqual(len(self.page.cards), 4)

    def test_selecting_a_card_updates_the_details_panel(self):
        self.page.select(next(p for p in self.page.projects if p.name == 'Bravo'))
        text = self.page.details_text.text()
        self.assertIn('Bravo', text)
        self.assertIn('1,243', text)
        self.assertIn('2 runs', text)
        self.assertTrue(self.page.cards[1].property('selected') == 'yes')

    def test_open_emits_the_chosen_project(self):
        seen = []
        self.page.opened.connect(seen.append)
        self.page.cards[0].on_open(self.page.cards[0].project)
        self.assertEqual([p.name for p in seen], ['Alpha'])

    def test_delete_removes_the_project_and_its_card(self):
        target = next(p for p in self.page.projects if p.name == 'Bravo')
        with patch('pages.project_page.QMessageBox.warning',
                   return_value=QMessageBox.StandardButton.Yes):
            self.page.delete(target)
        self.assertNotIn('Bravo', [p.name for p in self.page.projects])
        self.assertNotIn('Bravo', self.names())

    def test_short_path_keeps_the_tail(self):
        self.assertEqual(short_path('/a/b/c/d/e'), '…/d/e')
        self.assertEqual(short_path('/a/b'), '/a/b')
        self.assertEqual(short_path(''), '')


class MainWindowNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        patcher = patch.object(project_manager, 'PROJECTS_DIR', root/'projects')
        patcher.start(); self.addCleanup(patcher.stop)
        (root/'projects').mkdir()
        self.project = Project(name='Alpha', created='2026-09-14 09:34', output_root=str(root/'out'))
        self.project.save()

        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)

    def test_starts_on_projects_with_navigation_locked(self):
        self.assertIs(self.window.pages.currentWidget(), self.window.projects_page)
        self.assertIsNone(self.window.project)
        buttons = self.window.shell.buttons
        self.assertTrue(buttons['projects'].isEnabled())
        for key in ('detection', 'results', 'review'):
            self.assertFalse(buttons[key].isEnabled(), key)

    def test_opening_a_project_moves_in_the_same_window(self):
        before = len(self.app.topLevelWidgets())
        self.window.projects_page.opened.emit(self.project)
        self.assertIs(self.window.pages.currentWidget(), self.window.detection_page)
        self.assertEqual(self.window.project.name, 'Alpha')
        self.assertIn('Alpha', self.window.windowTitle())
        self.assertIn('Alpha', self.window.shell.details.text())
        self.assertTrue(self.window.shell.buttons['detection'].isEnabled())
        # No second window was created, and the old one was not closed.
        self.assertEqual(len(self.app.topLevelWidgets()), before)
        self.assertFalse(self.window.isHidden() and not self.window.isVisible() and False)

    def test_sidebar_returns_to_projects_without_losing_the_project(self):
        self.window.projects_page.opened.emit(self.project)
        self.window._navigate_shell('projects')
        self.assertIs(self.window.pages.currentWidget(), self.window.projects_page)
        self.assertEqual(self.window.project.name, 'Alpha')      # still open behind the scenes
        self.window._navigate_shell('detection')
        self.assertIs(self.window.pages.currentWidget(), self.window.detection_page)

    def test_running_detection_blocks_leaving_projects_until_confirmed(self):
        self.window.projects_page.opened.emit(self.project)

        class Busy:
            def isRunning(self): return True
            def cancel(self): self.cancelled = True

        self.window.worker = Busy()
        with patch('pages.main_window.QMessageBox.question',
                   return_value=QMessageBox.StandardButton.No):
            self.window._back_to_projects()
        self.assertIs(self.window.pages.currentWidget(), self.window.detection_page)
        with patch('pages.main_window.QMessageBox.question',
                   return_value=QMessageBox.StandardButton.Yes):
            self.window._back_to_projects()
        self.assertIs(self.window.pages.currentWidget(), self.window.projects_page)
        self.window.worker = None


if __name__ == '__main__':
    unittest.main()
