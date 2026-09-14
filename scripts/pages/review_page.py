"""Explicit review decisions, inline annotation, crop inspection and video export."""
import json
from uuid import uuid4
from pathlib import Path

import cv2
from PyQt6.QtCore import QEvent, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QGroupBox, QApplication, QSizePolicy, QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter, QDialog, QHBoxLayout, QLabel,
    QInputDialog, QMessageBox, QPushButton, QRubberBand, QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from review_summary import RefreshWorker
from model_evaluation import confirm_review_frame, evaluate_run
from pages.evaluation_page import EvaluationPage
from paths import open_path
from pipeline import format_timestamp
from review_io import (ReviewVideoWorker, context_for, draw_review,
                       load_review_rows, load_species_names, save_rows)


class ReviewComboBox(QComboBox):
    def eventFilter(self, obj, event):
        if (obj is self.lineEdit() and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Tab
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            completion = self.completer().currentCompletion()
            if self.lineEdit().text() and completion:
                self.setEditText(completion)
                self.lineEdit().setCursorPosition(len(completion))
            # Keep focus here so Enter saves the annotation, never activates Remove.
            return True
        return super().eventFilter(obj,event)

    def wheelEvent(self, event):
        # Let the containing panel scroll without changing this selection.
        event.ignore()


class ImageLabel(QLabel):
    """A click selects a detection and a drag boxes a missed fish, both in frame
    coordinates. Ctrl+wheel zooms about the cursor so small fish can be boxed
    accurately; once zoomed, the wheel pans (with Shift for sideways)."""
    DRAG = 5
    MAX_ZOOM = 12.0
    STEP = 1.25
    PAN = 0.15

    def __init__(self, parent=None):
        super().__init__(parent)
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = None
        self.on_click = None
        self.on_draw = None
        self.on_zoom = None
        self.frame = None
        self.zoom = 1.0
        self.centre = (.5,.5)
        self.region = (0,0,1,1)

    def reset_view(self):
        self.zoom = 1.0; self.centre = (.5,.5)

    def set_frame(self, frame):
        self.frame = frame
        self.redraw()

    def clear_frame(self):
        self.frame = None; self.clear()

    def visible_region(self):
        """The part of the frame currently on screen, clamped inside it."""
        h,w = self.frame.shape[:2]
        vw,vh = w/self.zoom, h/self.zoom
        cx,cy = self.centre
        return min(max(cx*w-vw/2,0),w-vw), min(max(cy*h-vh/2,0),h-vh), vw, vh

    def redraw(self):
        if self.frame is None:
            return
        x0,y0,vw,vh = self.visible_region()
        self.region = (x0,y0,vw,vh)
        h,w = self.frame.shape[:2]
        self.centre = ((x0+vw/2)/w,(y0+vh/2)/h)
        view = self.frame[round(y0):round(y0+vh),round(x0):round(x0+vw)]
        if view.size:
            self.setPixmap(pixmap(view).scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))

    def ready(self):
        return self.frame is not None and bool(self.pixmap() and self.pixmap().width())

    def anchor(self, point):
        """Where a label point falls within the displayed pixmap, as fractions."""
        pix = self.pixmap()
        return ((point.x()-(self.width()-pix.width())/2)/pix.width(),
                (point.y()-(self.height()-pix.height())/2)/pix.height())

    def fraction(self, point):
        """Where a label point falls within the whole frame, allowing for zoom."""
        ax,ay = self.anchor(point)
        x0,y0,vw,vh = self.region
        h,w = self.frame.shape[:2]
        return (x0+ax*vw)/w,(y0+ay*vh)/h

    def wheelEvent(self, event):
        steps = event.angleDelta().y()/120 if self.ready() else 0
        if not steps:
            event.ignore();return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            zoom = min(self.MAX_ZOOM,max(1.0,self.zoom*self.STEP**steps))
            if zoom != self.zoom:
                # Keep whatever is under the cursor under the cursor.
                ax,ay = self.anchor(event.position())
                fx,fy = self.fraction(event.position())
                h,w = self.frame.shape[:2]
                self.zoom = zoom
                vw,vh = w/zoom, h/zoom
                self.centre = ((fx*w-ax*vw+vw/2)/w,(fy*h-ay*vh+vh/2)/h)
                self.redraw()
                if self.on_zoom: self.on_zoom()
            event.accept();return
        if self.zoom > 1.0:
            cx,cy = self.centre
            _,_,vw,vh = self.region
            h,w = self.frame.shape[:2]
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.centre = (cx-steps*vw*self.PAN/w,cy)
            else:
                self.centre = (cx,cy-steps*vh*self.PAN/h)
            self.redraw();event.accept();return
        event.ignore()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.ready():
            return
        self.origin = event.position().toPoint()
        self.band.setGeometry(QRect(self.origin, self.origin)); self.band.show()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self.origin is None or event.button() != Qt.MouseButton.LeftButton:
            return
        start, end = self.origin, event.position().toPoint()
        self.origin = None; self.band.hide()
        if not self.ready():
            return
        if abs(end.x()-start.x()) < self.DRAG and abs(end.y()-start.y()) < self.DRAG:
            if self.on_click: self.on_click(*self.fraction(end))
            return
        if self.on_draw:
            (x1,y1), (x2,y2) = self.fraction(start), self.fraction(end)
            self.on_draw(min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))


def pixmap(frame):
    rgb = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
    h,w,_ = rgb.shape
    return QPixmap.fromImage(QImage(rgb.data,w,h,rgb.strides[0],QImage.Format.Format_RGB888).copy())


class ReviewPage(QWidget):
    results_refreshed = pyqtSignal()
    def __init__(self,on_back,parent=None):
        super().__init__(parent)
        self.on_back = on_back
        self.path = None; self.root = None; self.video = None; self.cap = None
        self.rows = []; self.fields = []; self.names = []; self.pending = []; self.decisions = []
        self.flagged_frames = []; self.detections_by_frame = {}
        self.frame_number = 1; self.total_frames = 0; self.fps = 25.0; self.selected = 0
        self.raw = None; self.canvas = None; self.worker = None; self.loading = False
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.render)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        QApplication.instance().installEventFilter(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(16,12,16,12)
        root.setSpacing(10)
        title = QLabel('Review Detections'); title.setObjectName('pageTitle')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        hint = QLabel('Inspect detections, draw boxes around missed fish, then confirm. Ctrl + scroll to zoom; scroll to pan.')
        hint.setWordWrap(True); hint.setAlignment(Qt.AlignmentFlag.AlignCenter); root.addWidget(hint)
        self.progress = QLabel()
        self.progress.setTextFormat(Qt.TextFormat.RichText)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.progress.linkActivated.connect(self.jump_to_frame)
        self.progress.setToolTip('Click to jump to any video frame by number.')
        root.addWidget(self.progress)
        mode = QHBoxLayout(); root.addLayout(mode)
        self.review_only = QCheckBox('Review frames only'); self.review_only.setChecked(True)
        self.review_only.setToolTip('On: Previous, Skip, Confirm & Next and the arrow keys move between flagged frames.\n'
                                    'Off: they step one video frame at a time, so you can find fish the model missed entirely.')
        self.review_only.toggled.connect(lambda _checked: self.update_controls())
        mode.addStretch(); mode.addWidget(self.review_only); mode.addStretch()
        images = QHBoxLayout()
        panel = QGroupBox("Annotations"); panel.setFixedWidth(210)
        panel_layout = QVBoxLayout(panel); panel_layout.setContentsMargins(10,18,10,10)
        panel_title = QLabel('Annotations'); panel_title.setStyleSheet('font-weight: bold;')
        panel_title.hide()
        panel_hint = QLabel('<ul style="-qt-list-indent:0; margin-left:8px; margin-top:0px;">'
                            '<li>Annotate missed fish detection</li>'
                            '<li>Select species</li>'
                            '<li>Enter and Confirm &amp; Next to save</li>'
                            '<li>Remove latest annotation with CTRL+Z</li>'
                            '</ul>')
        panel_hint.setTextFormat(Qt.TextFormat.RichText)
        panel_hint.setWordWrap(True); panel_layout.addWidget(panel_hint)
        self.annotations = QTableWidget(0,2)
        self.annotations.setHorizontalHeaderLabels(['Species',''])
        self.annotations.setColumnWidth(0,115)
        self.annotations.setColumnWidth(1,38)
        self.annotations.verticalHeader().hide()
        self.annotations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.annotations.cellClicked.connect(self.select_annotation)
        self.annotations.horizontalHeader().setStretchLastSection(True)
        panel_layout.addWidget(self.annotations,1)
        images.addWidget(panel)
        self.image = ImageLabel(); self.image.setMinimumSize(360,220)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        self.image.on_click = self.select_box
        self.image.on_draw = self.draw_box
        self.image.on_zoom = self.update_controls
        self.crop = QLabel('Select a fish to preview')
        self.crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.crop.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        for label in (self.image,self.crop):
            label.setStyleSheet('background: #021b2b; color: #b1d5e9; border: 1px solid #24779b; border-radius: 7px;')
        preview = QGroupBox('Zoom / Preview'); preview.setFixedWidth(210)
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(self.crop,1)
        preview_hint = QLabel('Ctrl + scroll to zoom\nScroll to pan • Shift for sideways')
        preview_hint.setObjectName('muted'); preview_hint.setWordWrap(True)
        preview_layout.addWidget(preview_hint)
        images.addWidget(self.image,1); images.addWidget(preview)
        root.addLayout(images,1)
        self.button(preview_layout,'Full resolution',self.zoom)
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['Original AI prediction','Confidence','Reviewed species','Decision'])
        self.table.setToolTip("Resolved rows are hidden. Click a box on the image to reopen its species editor.")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setFixedHeight(140)
        self.table.cellClicked.connect(self.select_row)
        self.table.horizontalHeader().setStretchLastSection(True)
        detections_panel = QGroupBox("Detections in this frame")
        detections_layout = QVBoxLayout(detections_panel)
        detections_layout.addWidget(self.table)
        root.addWidget(detections_panel)
        self.status = QLabel(); self.status.setWordWrap(True); root.addWidget(self.status)
        nav = QHBoxLayout(); root.addLayout(nav)
        self.back = self.button(nav,'Back to Results',self.leave)
        self.previous = self.button(nav,'Previous',lambda:self.navigate(-1))
        self.skip = self.button(nav,'Skip',lambda:self.navigate(1))
        self.confirm = self.button(nav,'Confirm && Next  →',self.confirm_frame)
        self.confirm.setObjectName('confirmButton')
        actions = QHBoxLayout(); root.addLayout(actions)
        self.refresh = self.button(actions,'Refresh Results',self.refresh_results)
        self.export = self.button(actions,'Regenerate reviewed video',self.regenerate)
        self.open_video = self.button(actions,'Open video',lambda:open_path(self.reviewed_video))
        self.evaluate_button = self.button(actions,'Evaluate AI model',self.evaluate_model)
        self.evaluate_button.setToolTip('Compare original predictions with saved reviews. Confirm every fish in a frame, including misses, before evaluating.')
        self.open_video.setEnabled(False)
        # Modified shortcuts avoid interfering with species completion or text editing.
        QShortcut(QKeySequence('Ctrl+Return'),self,activated=self.confirm_frame)
        QShortcut(QKeySequence('Alt+Right'),self,activated=lambda:self.navigate(1))
        QShortcut(QKeySequence('Alt+Left'),self,activated=lambda:self.navigate(-1))

    @staticmethod
    def button(layout,text,action):
        button = QPushButton(text); button.clicked.connect(action); layout.addWidget(button); return button

    def load_run(self,flagged_csv,output_dir,video_path):
        if self.cap:
            self.cap.release(); self.cap = None
        self.path = Path(flagged_csv) if flagged_csv else None
        self.root = Path(output_dir) if output_dir else None
        self.video = Path(video_path) if video_path else None
        self.rows=[]; self.fields=[]; self.pending=[]
        self.detections_by_frame={}; self.total_frames=0; self.fps=25.0
        self.image.reset_view()
        try:
            if not self.path or not self.root:
                raise ValueError('No review CSV is available.')
            self.rows, self.fields = load_review_rows(self.path)
            names_file=self.root/'species_names.json'
            self.names=load_species_names(self.root,self.rows)
            self.status.setText('Saved decisions are loaded. Older runs may have an incomplete species list.' if not names_file.exists() else 'Ready. Changes are saved when you confirm the frame.')
        except (OSError,ValueError,KeyError) as exc:
            self.rows=[]; self.names=[]
            QMessageBox.critical(self,'Cannot load review',str(exc))
        self.refresh_flagged()
        self.open_source()
        if not self.total_frames:
            # No readable video: flagged frames are all that can be reviewed.
            self.total_frames = max(self.flagged_frames, default=0)
            self.review_only.setChecked(True)
        self.frame_number = next((n for n in self.flagged_frames
                                  if any(r['review_status'] not in ('confirmed','rejected')
                                         for r in self.rows if r['frame_number']==n)),
                                 self.flagged_frames[0] if self.flagged_frames else 1)
        self.reviewed_video=self.video
        self.open_video.setEnabled(bool(self.reviewed_video and self.reviewed_video.exists()))
        self.show_frame()

    def open_source(self):
        """Open the raw source video so any frame can be reviewed, not just flagged ones."""
        source = self.video
        original = self.root/'original_detections.json' if self.root else None
        if original and original.exists():
            try:
                data = json.loads(original.read_text(encoding='utf-8'))
            except (OSError,ValueError):
                return
            self.fps = float(data.get('fps') or self.fps)
            self.detections_by_frame = {f['frame_number']: f['detections'] for f in data.get('frames',[])}
            candidate = Path(data.get('source_video',''))
            if candidate.is_file():
                source = candidate
        if not (source and source.is_file()):
            return
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            cap.release(); return
        self.cap = cap
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not self.detections_by_frame:
            self.fps = cap.get(cv2.CAP_PROP_FPS) or self.fps

    def refresh_flagged(self):
        self.flagged_frames = sorted({r['frame_number'] for r in self.rows})

    def current(self):
        return [r for r in self.rows if r['frame_number']==self.frame_number]

    def frame_context(self):
        if self.detections_by_frame:
            return self.detections_by_frame.get(self.frame_number,[])
        return context_for(self.root,self.frame_number)

    def timestamp(self):
        rows=self.current()
        if rows and rows[0].get('timestamp'):
            return rows[0]['timestamp']
        return format_timestamp((self.frame_number-1)/self.fps) if self.fps else ''

    def next_frame_number(self,step):
        """The frame Previous/Skip/Confirm & Next lands on, per the Review frames only mode."""
        if self.review_only.isChecked():
            if step>0:
                later=[n for n in self.flagged_frames if n>self.frame_number]
                return later[0] if later else self.frame_number
            earlier=[n for n in self.flagged_frames if n<self.frame_number]
            return earlier[-1] if earlier else self.frame_number
        if not self.total_frames:
            return self.frame_number
        return max(1,min(self.total_frames,self.frame_number+step))

    def load_frame_image(self):
        rows=self.current()
        if rows and rows[0].get('frame_image'):
            saved=self.root/rows[0]['frame_image']
            if saved.is_file():
                image=cv2.imread(str(saved))
                if image is not None:
                    return image
        if self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES,self.frame_number-1)
            ok,frame=self.cap.read()
            if ok:
                return frame
        return None

    def show_frame(self):
        self.loading=True
        self.table.setRowCount(0); self.annotations.setRowCount(0)
        self.pending=[]; self.canvas=None
        rows=self.current()
        # A row is confirmed unless it is crossed out, so an untouched frame can be
        # accepted wholesale with Confirm & Next.
        self.decisions=['rejected' if row['review_status']=='rejected' else 'confirmed' for row in rows]
        for i,row in enumerate(rows):
            self.add_table_row(i,row)
            self.table.setRowHidden(i,row.get("reviewed")=="yes")
        self.table.setColumnWidth(0,230); self.table.setColumnWidth(1,90); self.table.setColumnWidth(2,230)
        self.selected=0
        self.raw=self.load_frame_image()
        self.update_controls()
        self.loading=False; self.render()

    def add_table_row(self,index,row):
        """Build one row of the detection table. Rows are only ever appended, so the
        indexes captured by the row's own controls stay correct."""
        self.table.insertRow(index)
        for col,text in enumerate(('Manual annotation' if row.get('annotation_source')=='manual' else row['original_species'],
                                   str(row['confidence']) if row['confidence'] != '' else 'N/A')):
            self.table.setCellWidget(index,col,QLabel(text))
        species=self.species_box(row['species'])
        self.table.setCellWidget(index,2,species)
        self.table.setCellWidget(index,3,self.decision_widget(index))
        species.activated.connect(lambda _, i=index: self.select_row(i,2))
        species.currentTextChanged.connect(self.render)
        self.style_decision(index)

    # The app-wide sheet gives every QPushButton a 30px minimum and wide padding, which
    # squashes a small square button, so these opt out of all of it explicitly.
    # The size has to be set here rather than with setFixedSize: applying a stylesheet
    # re-derives the widget's size constraints from the sheet and discards it.
    DECISION_STYLE = ('QPushButton{{min-width:26px;max-width:26px;min-height:20px;max-height:20px;'
                      'padding:0px;font-size:14px;font-weight:700;'
                      'border:1px solid #39718e;border-radius:4px;'
                      'background:#0b3048;color:#93b8cb;}}'
                      'QPushButton:hover{{border:1px solid {colour};color:{colour};}}'
                      'QPushButton:checked{{background:{colour};border:1px solid {colour};color:#FFFFFF;}}')

    def decision_widget(self,index):
        """Tick to confirm the row, cross to drop the detection."""
        holder=QWidget();layout=QHBoxLayout(holder)
        layout.setContentsMargins(8,4,8,4);layout.setSpacing(7)
        group=QButtonGroup(holder);group.setExclusive(True)
        for symbol,status,tip,colour in (('✓','confirmed','Confirm this species','#36b88e'),
                                         ('✕','rejected','Not a fish — drop this detection','#dd6675')):
            button=QPushButton(symbol);button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.setStyleSheet(self.DECISION_STYLE.format(colour=colour))
            button.setChecked(self.decisions[index]==status)
            # clicked, not toggled: only a real click should change the decision.
            button.clicked.connect(lambda _, i=index, s=status: self.set_decision(i,s))
            group.addButton(button);layout.addWidget(button)
        holder.caption=QLabel();layout.addWidget(holder.caption);layout.addStretch()
        return holder

    def set_decision(self,index,status):
        self.decisions[index]=status
        self.selected=index;self.table.selectRow(index)
        self.style_decision(index);self.render()

    def style_decision(self,index):
        """Strike out a crossed row so it reads as removed before it is saved."""
        rejected=self.decisions[index]=='rejected'
        for col in (0,1):
            label=self.table.cellWidget(index,col)
            font=label.font();font.setStrikeOut(rejected);label.setFont(font)
            label.setStyleSheet('color: #98A2AD;' if rejected else '')
        self.table.cellWidget(index,2).setEnabled(not rejected)
        caption=self.table.cellWidget(index,3).caption
        manual=self.current()[index].get('annotation_source')=='manual'
        caption.setText(('Annotation deleted' if manual else 'Removed — not a fish') if rejected else 'Confirmed')
        caption.setStyleSheet(f"color: {'#dd6675' if rejected else '#36b88e'};")

    def species_box(self,text=''):
        species=ReviewComboBox(); species.setEditable(True); species.addItems(self.names)
        species.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        species.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        species.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        species.completer().setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        species.lineEdit().installEventFilter(species)
        if text:species.setCurrentText(text)
        else:species.setCurrentIndex(-1)
        return species

    def update_controls(self):
        complete=sum(all(r['review_status'] in ('confirmed','rejected') for r in self.rows if r['frame_number']==n) for n in self.flagged_frames)
        flagged=' • flagged' if self.frame_number in self.flagged_frames else ''
        zoom=f' • {self.image.zoom:.1f}× zoom' if self.image.zoom>1 else ''
        self.progress.setText(
            f'<a href="jump" style="color: #46d8ff;">Frame {self.frame_number} / {self.total_frames}</a> • {self.timestamp()}{flagged}{zoom}'
            f' • {complete} of {len(self.flagged_frames)} flagged frames reviewed')
        self.previous.setEnabled(not self.worker and self.next_frame_number(-1)!=self.frame_number)
        self.skip.setEnabled(not self.worker and self.next_frame_number(1)!=self.frame_number)
        self.confirm.setEnabled(not self.worker and self.raw is not None)
        self.evaluate_button.setEnabled(bool(self.path and self.root) and not self.worker)
        self.export.setEnabled(bool(self.video and self.video.exists() and self.rows) and not self.worker)
        self.review_only.setEnabled(bool(self.cap) and not self.worker)

    def drafts(self):
        result=[]
        for i,row in enumerate(self.current()):
            copy=dict(row);copy['species']=self.table.cellWidget(i,2).currentText().strip()
            copy['review_status']=self.decisions[i];result.append(copy)
        return result

    def pending_rows(self):
        """Boxes drawn on this frame but not yet written to the review CSV."""
        result=[]
        for i,box in enumerate(self.pending):
            species=self.annotations.cellWidget(i,0).currentText().strip()
            result.append(dict(zip(('x1','y1','x2','y2'),box),species=species or f'New box {i+1}',
                               review_status='confirmed',annotation_source='manual'))
        return result

    def draw_box(self,x1,y1,x2,y2):
        if self.raw is None or self.worker:
            return
        h,w=self.raw.shape[:2]
        box=[round(max(0,min(w,x1*w)),2),round(max(0,min(h,y1*h)),2),
             round(max(0,min(w,x2*w)),2),round(max(0,min(h,y2*h)),2)]
        if box[2]-box[0]<2 or box[3]-box[1]<2:
            return
        index=len(self.pending)
        self.pending.append(box)
        self.annotations.insertRow(index)
        species=self.species_box()
        species.currentTextChanged.connect(self.render)
        # Rows shift as boxes are committed or removed, so look the row up by widget.
        species.activated.connect(lambda: self.select_annotation(self.annotation_row(species)))
        species.lineEdit().returnPressed.connect(lambda: self.commit_box(self.annotation_row(species)))
        self.annotations.setCellWidget(index,0,species)
        remove=QPushButton('×')
        remove.setToolTip('Remove this pending annotation')
        remove.setStyleSheet('padding: 0; min-width: 24px; min-height: 28px;')
        remove.clicked.connect(lambda: self.remove_box(remove))
        self.annotations.setCellWidget(index,1,remove)
        self.annotations.scrollToBottom()
        # Show the new box enlarged on the right, as a selected detection would be.
        self.selected=self.table.rowCount()+index
        self.annotations.selectRow(index)
        self.render()

    def annotation_row(self,widget):
        """Which Annotations row a control currently belongs to, or -1 if it is gone."""
        for index in range(self.annotations.rowCount()):
            if widget in (self.annotations.cellWidget(index,0),self.annotations.cellWidget(index,1)):
                return index
        return -1

    def remove_box(self,button):
        index=self.annotation_row(button)
        if index<0:return
        # Deleting the row destroys its controls, whose dying signals would otherwise
        # redraw from a half-updated table.
        self.loading=True
        self.annotations.removeRow(index)
        self.pending.pop(index)
        self.selected=min(self.selected,max(0,self.table.rowCount()+len(self.pending)-1))
        self.loading=False
        self.render()

    def undo_annotation(self):
        """Remove the newest unsaved box on this frame, without touching saved rows."""
        if self.worker or not self.pending:
            return
        self.remove_box(self.annotations.cellWidget(len(self.pending)-1,1))
        self.status.setText('Last drawn annotation removed. Ctrl+Z undoes remaining unsaved boxes.')

    def commit_box(self,index):
        """Save one drawn box immediately, so pressing Enter moves it straight into the
        reviewed-species table instead of waiting for Confirm & Next."""
        if self.worker or index<0 or self.raw is None:
            return
        species=self.annotations.cellWidget(index,0).currentText().strip()
        if not species:
            QMessageBox.warning(self,'Species required','Choose or type a species name for this box.');return
        if species not in self.names and QMessageBox.question(self,'Unknown species',
                f'"{species}" is not in the available species list. Save it anyway?') != QMessageBox.StandardButton.Yes:
            return
        try:
            image=self.ensure_frame_image()
        except OSError as exc:
            QMessageBox.critical(self,'Annotation not saved',str(exc));return
        new={key:'' for key in self.fields}
        new.update(frame_number=self.frame_number,timestamp=self.timestamp(),species=species,
                   confidence='',track_id='',original_species='',annotation_source='manual',
                   annotation_id=str(uuid4()),review_status='confirmed',reviewed='yes',
                   notes='Manually annotated',frame_image=image)
        new.update(dict(zip(('x1','y1','x2','y2'),self.pending[index])))
        rows=self.rows+[new]
        try:save_rows(self.path,rows,self.fields)
        except OSError as exc:
            QMessageBox.critical(self,'Annotation not saved',str(exc));return
        # Move the box from the Annotations panel into the table as one atomic step:
        # the two are briefly inconsistent, and dying widgets emit as they go.
        self.loading=True
        self.annotations.removeRow(index);self.pending.pop(index)
        self.rows=rows
        self.names=sorted(set(self.names)|{species})
        self.refresh_flagged()
        position=len(self.current())-1
        self.decisions.append('confirmed')
        self.add_table_row(position,new)
        self.selected=position;self.table.selectRow(position)
        self.loading=False
        self.status.setText(f'Saved "{species}" on frame {self.frame_number}. '
                            'Click Refresh Results to apply it to the summary, charts and Max-N.')
        self.update_controls();self.render()

    def all_rows(self):
        """Saved detections on this frame, then boxes drawn but not yet confirmed."""
        return self.drafts()+self.pending_rows()

    def render(self,*args):
        if self.loading:return
        if self.raw is None:
            self.image.clear_frame()
            self.image.setText('No image available.' if self.total_frames else 'No flagged detections.')
            self.crop.clear();return
        rows=self.all_rows()
        self.canvas=draw_review(self.raw,rows,self.selected,self.frame_context())
        self.image.set_frame(self.canvas)
        if 0<=self.selected<len(rows):
            row=rows[self.selected];h,w=self.raw.shape[:2]
            x1,y1,x2,y2=[round(float(row[k])) for k in ('x1','y1','x2','y2')]
            crop=self.raw[max(0,y1-25):min(h,y2+25),max(0,x1-25):min(w,x2+25)]
            if crop.size:self.crop.setPixmap(pixmap(crop).scaled(max(1,self.crop.width()),max(1,self.crop.height()),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        else:
            self.crop.clear();self.crop.setText('Selected fish')

    def select_row(self,row,col):
        self.selected=row;self.render()

    def select_annotation(self,row,_col=0):
        self.selected=self.table.rowCount()+row;self.render()

    def edit_detection(self, index):
        """Reopen a resolved row when its on-image annotation is selected."""
        self.table.setRowHidden(index,False)
        self.selected=index
        self.table.selectRow(index)
        editor=self.table.cellWidget(index,2)
        self.table.scrollTo(self.table.model().index(index,2))
        editor.setFocus()
        editor.lineEdit().selectAll()
        self.render()

    def select_box(self,x,y):
        if self.raw is None or self.worker:
            return
        h,w=self.raw.shape[:2]
        point=(x*w,y*h)
        rows=self.all_rows()
        hits=[]
        for i,row in enumerate(rows):
            if row.get('review_status')=='rejected':
                continue
            x1,y1,x2,y2=[float(row[k]) for k in ('x1','y1','x2','y2')]
            if x1<=point[0]<=x2 and y1<=point[1]<=y2:
                hits.append(((x2-x1)*(y2-y1),i))
        if hits:
            _,i=min(hits)
            if i<self.table.rowCount():
                self.edit_detection(i)
            else:
                index=i-self.table.rowCount()
                self.select_annotation(index)
                editor=self.annotations.cellWidget(index,0)
                editor.setFocus();editor.lineEdit().selectAll()
            return
        # Confident model boxes are drawn as context even when never flagged.
        # Promote a clicked box to an editable review row; retain its model identity.
        represented={tuple(round(float(r[k]),2) for k in ('x1','y1','x2','y2'))
                     for r in self.current() if r.get('annotation_source')!='manual'}
        hits=[]
        for det in self.frame_context():
            box=tuple(round(float(v),2) for v in det['bbox'])
            if box in represented:
                continue
            x1,y1,x2,y2=box
            if x1<=point[0]<=x2 and y1<=point[1]<=y2:
                hits.append(((x2-x1)*(y2-y1),det))
        if not hits:
            return
        det=min(hits,key=lambda hit:hit[0])[1]
        try:
            image=self.ensure_frame_image()
        except OSError as exc:
            QMessageBox.critical(self,'Cannot edit detection',str(exc));return
        new={key:'' for key in self.fields}
        new.update(frame_number=self.frame_number,timestamp=self.timestamp(),
                   species=det['species'],original_species=det['species'],
                   confidence=det['confidence'],track_id=det.get('track_id') if det.get('track_id') is not None else '',
                   annotation_source='model',review_status='pending',reviewed='',frame_image=image)
        new.update(dict(zip(('x1','y1','x2','y2'),[round(float(v),2) for v in det['bbox']])))
        self.loading=True
        self.rows.append(new)
        self.decisions.append('confirmed')
        index=len(self.current())-1
        self.add_table_row(index,new)
        self.loading=False
        self.edit_detection(index)

    def zoom(self):
        if self.canvas is None:return
        dialog=QDialog(self);dialog.setWindowTitle('Full-resolution review — scroll to inspect');dialog.resize(1000,700)
        layout=QVBoxLayout(dialog);scroll=QScrollArea();label=QLabel();label.setPixmap(pixmap(self.canvas));scroll.setWidget(label);layout.addWidget(scroll);dialog.exec()

    def discard_ok(self):
        changed=bool(self.pending) or any(a['species']!=b['species'] or (a['review_status']!=b['review_status'] and b['review_status']!='pending') or (b['review_status']=='pending' and a['review_status']!='confirmed') for a,b in zip(self.drafts(),self.current()))
        return not changed or QMessageBox.question(self,'Unsaved edits','Leave this frame and discard unconfirmed edits?')==QMessageBox.StandardButton.Yes

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.KeyPress and self.isVisible()
                and isinstance(obj, QWidget) and obj.window() == self.window()
                and (obj is self or self.isAncestorOf(obj))
                and event.modifiers() == Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_Z and self.pending):
            self.undo_annotation()
            return True
        # Plain arrows are review navigation, including when a table field has focus.
        # Separate dialogs and open dropdown popups keep their normal keyboard controls.
        if (event.type() == QEvent.Type.KeyPress and self.isVisible()
                and isinstance(obj, QWidget) and obj.window() == self.window()
                and (obj is self or self.isAncestorOf(obj))
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)):
            self.navigate(1 if event.key() == Qt.Key.Key_Right else -1)
            return True
        return super().eventFilter(obj, event)

    def jump_to_frame(self, _link=None):
        if self.worker or not self.total_frames:
            return
        number, accepted = QInputDialog.getInt(
            self, 'Jump to frame', f'Video frame number (1–{self.total_frames}):',
            self.frame_number, 1, self.total_frames, 1)
        if accepted and number != self.frame_number and self.discard_ok():
            self.frame_number = number
            self.show_frame()

    def navigate(self,step):
        if self.worker or not self.discard_ok():return
        self.frame_number=self.next_frame_number(step);self.show_frame()

    def advance(self):
        """Move on after a save, with nothing left to discard."""
        self.frame_number=self.next_frame_number(1);self.show_frame()

    def leave(self):
        if not self.worker and self.discard_ok():self.on_back()

    def confirm_frame(self):
        if self.worker or self.raw is None:return
        drafts=self.drafts();pending=self.pending_rows()
        if not drafts and not pending:
            if self.record_frame_completion():
                self.advance()
            return
        if any(not r['species'] for r in drafts if r['review_status']!='rejected'):
            QMessageBox.warning(self,'Species required','Choose or enter a species name, or cross the row out to drop it.');return
        if any(not self.annotations.cellWidget(i,0).currentText().strip() for i in range(len(self.pending))):
            QMessageBox.warning(self,'Species required','Choose a species for every box you have drawn, or remove it.');return
        unknown = sorted({r['species'] for r in drafts if r['review_status']=='confirmed' and r['species'] not in self.names}
                         | {r['species'] for r in pending if r['species'] not in self.names})
        if unknown and QMessageBox.question(self,'Unknown species','These names are not in the available species list. Save them anyway?\n\n'+'\n'.join(unknown)) != QMessageBox.StandardButton.Yes:
            return
        for row in drafts:row['reviewed']='yes'
        additions=[]
        if pending:
            try:
                image=self.ensure_frame_image()
            except OSError as exc:
                QMessageBox.critical(self,'Changes not saved',str(exc));return
            for row in pending:
                new={key:'' for key in self.fields}
                new.update(frame_number=self.frame_number,timestamp=self.timestamp(),species=row['species'],
                           confidence='',track_id='',original_species='',annotation_source='manual',
                           annotation_id=str(uuid4()),review_status='confirmed',reviewed='yes',
                           notes='Manually annotated',frame_image=image)
                new.update({key:row[key] for key in ('x1','y1','x2','y2')})
                additions.append(new)
        updates={id(old):new for old,new in zip(self.current(),drafts)}
        # Crossing out a manual annotation deletes it outright; a model detection keeps
        # its row so the refresh knows to drop that detection rather than re-count it.
        deleted={id(old) for old,new in zip(self.current(),drafts)
                 if new['review_status']=='rejected' and new.get('annotation_source')=='manual'}
        rows=[updates.get(id(r),r) for r in self.rows if id(r) not in deleted]+additions
        try:save_rows(self.path,rows,self.fields)
        except OSError as exc:
            QMessageBox.critical(self,'Changes not saved',str(exc));return
        # Clear the drawn boxes while the tables still match the old rows.
        self.pending=[];self.annotations.setRowCount(0)
        self.rows=rows
        self.names=sorted(set(self.names)|{r['species'] for r in pending})
        self.refresh_flagged()
        if not self.record_frame_completion():
            self.show_frame()
            return
        try:
            folder=self.root/'reviewed_frames';folder.mkdir(exist_ok=True)
            target=folder/f'frame_{self.frame_number:06d}.jpg'
            temporary=target.with_name(target.stem+'.tmp.jpg')
            if not cv2.imwrite(str(temporary),draw_review(self.raw,self.current(),context=self.frame_context())):raise OSError('Could not write reviewed image.')
            temporary.replace(target)
            self.status.setText('Saved to review CSV and reviewed_frames. Click Refresh Results to apply these decisions to the summary.')
        except OSError as exc:self.status.setText(f'CSV saved; reviewed image failed: {exc}. You can confirm again to retry.')
        self.advance()

    def record_frame_completion(self):
        try:
            confirm_review_frame(self.root, self.rows, self.frame_number)
            return True
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, 'Review completion not saved',
                                 f'Evaluation completion could not be recorded: {exc}. Any saved detection edits are retained. Confirm this frame again to retry.')
            return False

    def ensure_frame_image(self):
        """Relative path to this frame's raw image, saving it the first time a frame is annotated."""
        rows=self.current()
        existing=rows[0].get('frame_image') if rows else ''
        if existing and (self.root/existing).is_file():
            return existing
        relative=f'review_frames/frame_{self.frame_number:06d}.jpg'
        target=self.root/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() and not cv2.imwrite(str(target),self.raw):
            raise OSError('Could not write the frame image.')
        context=self.root/'review_frames'/f'frame_{self.frame_number:06d}.json'
        if not context.exists():
            context.write_text(json.dumps(self.frame_context()),encoding='utf-8')
        return relative

    def evaluate_model(self):
        if self.worker or not self.path or not self.root:
            return
        try:
            report = evaluate_run(self.root, self.path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.information(self, 'Evaluation unavailable', str(exc))
            return
        EvaluationPage(report, self).exec()

    def refresh_results(self):
        if self.worker or not self.path:
            return
        if not self.discard_ok():
            return
        self.worker = RefreshWorker(self.root, self.path, self)
        for button in (self.back,self.previous,self.skip,self.confirm,self.export,self.refresh):
            button.setEnabled(False)
        self.table.setEnabled(False)
        self.status.setText('Refreshing results from saved review decisions…')
        self.worker.succeeded.connect(self.refreshed)
        self.worker.failed.connect(lambda error: QMessageBox.critical(self,'Cannot refresh results',error))
        self.worker.finished.connect(self.export_finished)
        self.worker.start()

    def refreshed(self, species, missing_images):
        message = f'Results refreshed: {species} species. Summary CSV, reviewed detections and charts updated.'
        if missing_images:
            message += ' Some Max-N images could not be rebuilt because the raw source is unavailable.'
        self.status.setText(message)
        self.results_refreshed.emit()

    def regenerate(self):
        if self.worker or not self.video:return
        if not self.discard_ok():return
        decided=[r for r in self.rows if r['review_status'] in ('confirmed','rejected')]
        if not decided:
            QMessageBox.information(self,'Nothing saved','Confirm a frame before regenerating the video.');return
        missing_context=any(not (self.root/'review_frames'/f"frame_{r['frame_number']:06d}.json").exists() for r in decided)
        message='Replace the annotated video with saved review decisions? The current annotated output will be overwritten.'
        if missing_context:message+='\n\nThis older run lacks full detection context. Replaced frames will show only flagged detections; confident detection overlays on those frames cannot be reconstructed. Rerun detection to retain all overlays.'
        if QMessageBox.question(self,'Regenerate video',message)!=QMessageBox.StandardButton.Yes:return
        self.worker=ReviewVideoWorker(self.video,self.root,[dict(r) for r in self.rows],self)
        for button in (self.back,self.previous,self.skip,self.confirm,self.export,self.refresh):button.setEnabled(False)
        self.table.setEnabled(False)
        self.worker.progress.connect(lambda n:self.status.setText(f'Regenerating reviewed video: {n}%'))
        self.worker.succeeded.connect(self.exported)
        self.worker.failed.connect(lambda error:QMessageBox.critical(self,'Video regeneration failed',error))
        self.worker.finished.connect(self.export_finished);self.worker.start()

    def exported(self,path):
        self.reviewed_video=Path(path);self.open_video.setEnabled(True);self.status.setText(f'Reviewed video saved: {path}')

    def export_finished(self):
        worker=self.worker;self.worker=None;worker.deleteLater();self.back.setEnabled(True);self.refresh.setEnabled(True);self.table.setEnabled(True);self.show_frame()

    def shutdown(self):
        self._layout_timer.stop()
        if self.worker:
            self.worker.requestInterruption()
            self.worker.wait()
        if self.cap:
            self.cap.release();self.cap=None

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not self.loading:
            self._layout_timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_timer.start(0)
