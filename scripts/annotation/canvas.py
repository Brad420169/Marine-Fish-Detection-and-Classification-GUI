"""Widgets drawn on the annotation page: the zoomable frame and the species picker."""
import cv2
from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QComboBox, QLabel, QRubberBand


class SpeciesComboBox(QComboBox):
    """Editable species picker: Tab accepts the inline completion, the wheel never changes it."""

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
        return super().eventFilter(obj, event)

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
        self.on_exclude = None
        self.on_sam = None
        self.sam_active = False
        self.excluding = False
        self.on_zoom = None
        self.on_interact = None
        self.on_resize_start = None
        self.on_drag_start = None
        self.on_drag_move = None
        self.on_drag_end = None
        self.dragging_box = False
        self.flagged = False
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

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.flagged and self.ready():
            pix = self.pixmap()
            painter = QPainter(self)
            painter.setPen(QPen(QColor('#ff2020'), 5))
            painter.drawRect(QRect((self.width()-pix.width())//2+2,
                                   (self.height()-pix.height())//2+2,
                                   max(1,pix.width()-5),max(1,pix.height()-5)))

    def mousePressEvent(self, event):
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton) or not self.ready():
            return
        if self.on_interact: self.on_interact()
        point = event.position().toPoint()
        ax, ay = self.anchor(point)
        if not (0 <= ax <= 1 and 0 <= ay <= 1):
            return
        self.setFocus()
        resizing = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.sam_active and not resizing:
            return
        self.origin = point
        self.excluding = event.button() == Qt.MouseButton.RightButton
        if self.excluding:
            self.dragging_box = False
            self.band.setGeometry(QRect(point, point)); self.band.show()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.dragging_box = bool(self.on_resize_start and self.on_resize_start(*self.fraction(point)))
            if not self.dragging_box:
                self.origin = None
                return
        else:
            self.dragging_box = bool(not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and self.on_drag_start
                                     and self.on_drag_start(*self.fraction(point)))
        if not self.dragging_box:
            self.band.setGeometry(QRect(point, point)); self.band.show()

    def mouseDoubleClickEvent(self, event):
        if (event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
                or not self.ready() or not self.on_sam
                or event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return
        point = event.position().toPoint()
        ax, ay = self.anchor(point)
        if not (0 <= ax <= 1 and 0 <= ay <= 1):
            return
        # The first click may have selected a box; finish that gesture before SAM.
        self.origin = None
        self.band.hide()
        if self.dragging_box and self.on_drag_end:
            self.on_drag_end()
        self.dragging_box = False
        self.excluding = False
        self.setFocus()
        if self.on_interact:
            self.on_interact()
        self.on_sam(*self.fraction(point), event.button() == Qt.MouseButton.LeftButton,
                    bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        event.accept()

    def mouseMoveEvent(self, event):
        if self.origin is None:
            return
        if self.dragging_box:
            if self.on_drag_move:
                self.on_drag_move(*self.fraction(event.position().toPoint()))
        else:
            self.band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        expected = Qt.MouseButton.RightButton if self.excluding else Qt.MouseButton.LeftButton
        if self.origin is None or event.button() != expected:
            return
        start, end = self.origin, event.position().toPoint()
        self.origin = None; self.band.hide()
        if not self.ready():
            return
        if self.dragging_box:
            if self.on_drag_end: self.on_drag_end()
            self.dragging_box = False
            return
        if self.excluding:
            self.excluding = False
            if abs(end.x()-start.x()) >= self.DRAG and abs(end.y()-start.y()) >= self.DRAG and self.on_exclude:
                (x1,y1), (x2,y2) = self.fraction(start), self.fraction(end)
                self.on_exclude(min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))
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
