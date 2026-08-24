"""
detectors.py
------------
Thin adapter layer so pipeline.py can run either a YOLO (.pt) or an
RF-DETR (.pth) model through one interface: load once, then call
``infer(frame_bgr, conf, iou)`` per frame and get back a normalised
``DetectionFrame`` (species names, confidences, boxes, track ids, and
an annotated BGR frame ready to write to video).

Model type is auto-detected from the weights file extension:
    .pt   -> Ultralytics YOLO (tracking is built in: BoT-SORT)
    .pth  -> RF-DETR (no built-in tracker, so we attach ByteTrack
             from `supervision` ourselves)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class DetectionFrame:
    """Normalised per-frame result, identical shape regardless of detector."""
    annotated: np.ndarray
    species: list[str] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    xyxy: list[list[float]] = field(default_factory=list)
    track_ids: list[int | None] = field(default_factory=list)


class BaseDetector:
    kind: str = "base"

    def infer(self, frame_bgr: np.ndarray, conf: float, iou: float) -> DetectionFrame:
        raise NotImplementedError


# YOLO (Ultralytics) — .pt
class YOLODetector(BaseDetector):
    kind = "yolo"

    def __init__(self, weights_path: Path, device: str | None, tracker: str = "botsort.yaml") -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(weights_path))
        self.device = device
        self.tracker = tracker
        self._started = False

    def infer(self, frame_bgr: np.ndarray, conf: float, iou: float) -> DetectionFrame:
        results = self.model.track(
            frame_bgr,
            persist=True,
            tracker=self.tracker,
            conf=conf,
            iou=iou,
            device=self.device,
            verbose=False,
        )
        result = results[0]
        annotated = result.plot()

        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return DetectionFrame(annotated=annotated)

        class_ids = boxes.cls.int().cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        xyxy = boxes.xyxy.cpu().tolist()
        species = [result.names[c] for c in class_ids]

        if boxes.is_track and boxes.id is not None:
            track_ids: list[int | None] = boxes.id.int().cpu().tolist()
        else:
            track_ids = [None] * len(class_ids)

        return DetectionFrame(
            annotated=annotated,
            species=species,
            confidences=confidences,
            xyxy=xyxy,
            track_ids=track_ids,
        )


# RF-DETR — .pth
class RFDETRDetector(BaseDetector):
    kind = "rfdetr"

    def __init__(self, weights_path: Path, device: str | None) -> None:
        import torch
        import supervision as sv
        from rfdetr import RFDETR

        # Always resolve an explicit device rather than leaving it unset —
        # checkpoints trained on a GPU machine (e.g. an HPC) can carry a
        # "cuda" device setting in their saved config, which would otherwise
        # override local auto-detection and crash on a CPU-only machine.
        resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        # trust_checkpoint=True is required for checkpoints saved by the
        # rfdetr training stack, which bundle extra Python objects (optimizer
        # state, config classes) that PyTorch's default safe loader refuses.
        # Only safe to set for checkpoints you trained/trust yourself.
        self.model = RFDETR.from_checkpoint(
            str(weights_path), trust_checkpoint=True, device=resolved_device
        )

        # RF-DETR checkpoints trained with recent versions of the rfdetr
        # training stack embed class names directly; older/legacy
        # checkpoints may not. Fall back to placeholder names rather than
        # crashing, and let the caller know via `class_names_missing`.
        names = list(self.model.class_names or [])
        self.class_names_missing = not names
        self._fallback_names = names

        self.tracker = sv.ByteTrack()
        self.box_annotator = sv.BoxAnnotator()
        self.label_annotator = sv.LabelAnnotator()
        self._sv = sv

    def infer(self, frame_bgr: np.ndarray, conf: float, iou: float) -> DetectionFrame:
        # RF-DETR is NMS-free by design, so `iou` has no equivalent here —
        # only the confidence threshold is used.
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        detections = self.model.predict(
            frame_rgb, threshold=conf, include_source_image=False
        )
        detections = self.tracker.update_with_detections(detections)

        annotated = frame_bgr.copy()

        if len(detections) == 0:
            return DetectionFrame(annotated=annotated)

        class_names = detections.data.get("class_name")
        if class_names is None:
            # Extremely old checkpoint with no embedded names at all.
            species = [f"class_{cid}" for cid in detections.class_id]
        else:
            species = [str(n) for n in class_names]

        confidences = [float(c) for c in detections.confidence]
        xyxy = [[float(v) for v in box] for box in detections.xyxy]

        if detections.tracker_id is not None:
            track_ids: list[int | None] = [int(t) for t in detections.tracker_id]
        else:
            track_ids = [None] * len(species)

        labels = [f"{s} {c:.2f}" for s, c in zip(species, confidences)]
        annotated = self.box_annotator.annotate(annotated, detections)
        annotated = self.label_annotator.annotate(annotated, detections, labels=labels)

        return DetectionFrame(
            annotated=annotated,
            species=species,
            confidences=confidences,
            xyxy=xyxy,
            track_ids=track_ids,
        )


# Factory
def load_detector(weights_path: Path, device: str | None) -> BaseDetector:
    suffix = weights_path.suffix.lower()
    if suffix == ".pt":
        return YOLODetector(weights_path, device=device)
    if suffix == ".pth":
        return RFDETRDetector(weights_path, device=device)
    raise ValueError(
        f"Unrecognised weights file type '{suffix}' for {weights_path.name} "
        "(expected .pt for YOLO or .pth for RF-DETR)."
    )