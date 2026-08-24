"""
pipeline.py
-----------
Core detection and tracking logic, decoupled from argparse so it can be
called from the GUI worker thread or directly from the CLI.
"""
from __future__ import annotations

import csv
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2

from detectors import load_detector


# Constants

SUMMARY_FIELDS = [
    "dataset",
    "video",
    "video_duration_seconds",
    "species",
    "max_n",
    "max_n_timestamp",
    "max_n_frame",
    "first_seen",
    "last_seen",
    "visible_seconds",
    "observation_span_seconds",
    "unique_tracks",
    "total_detections",
    "mean_confidence",
]

FLAGGED_FIELDS = [
    "frame_number",
    "timestamp",
    "species",
    "confidence",
    "track_id",
    "x1",
    "y1",
    "x2",
    "y2",
    "notes",
]


# Run config — passed in by the GUI instead of argparse

@dataclass
class RunConfig:
    video_path:   Path
    weights_path: Path
    output_dir:   Path
    confidence:   float = 0.25
    review_confidence: float = 0.50
    iou:          float = 0.80
    device:       str | None = None


# Utilities
def format_timestamp(seconds: float) -> str:
    """Convert seconds to MM:SS or HH:MM:SS."""
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def read_video_properties(video_path: Path) -> tuple[float, int, int, float, int]:
    """Return (fps, width, height, duration_seconds, frame_count)."""
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video: {video_path}")

        fps         = cap.get(cv2.CAP_PROP_FPS)
        width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0:
            raise RuntimeError(f"Could not read FPS from: {video_path}")
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid video dimensions: {video_path}")

        duration = frame_count / fps
        return fps, width, height, duration, frame_count
    finally:
        cap.release()


def add_frame_label(frame: Any, frame_number: int, timestamp: str) -> None:
    """Overlay frame number and timestamp on an annotated frame."""
    label = f"Frame {frame_number}  |  {timestamp}"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
    cv2.rectangle(frame, (0, 0), (tw + 16, th + 16), (0, 0, 0), -1)
    cv2.putText(
        frame, label, (8, th + 8),
        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA,
    )


def safe_filename(text: str) -> str:
    """Return a filesystem-safe version of a species name."""
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in str(text).strip()
    )
    return cleaned.strip("_") or "species"


def extract_maxn_example_frames(
    rows: list[dict],
    maxn_snapshots: dict[str, dict[str, Any]],
    output_dir: Path,
    limit: int = 2,
) -> list[Path]:
    """
    Save up to ``limit`` distinct, review-style Max-N frames.

    The target Max-N species is highlighted with a thick black + bright-yellow
    double border. Other detections remain thin cyan so the viewer can
    immediately see which fish constitute the reported Max-N.
    """
    selected: list[dict] = []
    used_frames: set[int] = set()

    for row in rows:
        species = str(row["species"])
        snapshot = maxn_snapshots.get(species)
        if not snapshot:
            continue

        frame_number = int(snapshot["frame_number"])
        if frame_number <= 0 or frame_number in used_frames:
            continue

        selected.append(row)
        used_frames.add(frame_number)

        if len(selected) >= limit:
            break

    if not selected:
        return []

    examples_dir = output_dir / "maxn_examples"
    if examples_dir.exists():
        shutil.rmtree(examples_dir)
    examples_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []

    # BGR colours for OpenCV.
    normal_colour = (255, 210, 0)   # cyan/blue for context detections
    highlight_colour = (0, 255, 255)  # bright yellow
    black = (0, 0, 0)
    white = (255, 255, 255)

    for rank, row in enumerate(selected, start=1):
        species = str(row["species"])
        snapshot = maxn_snapshots[species]

        frame = snapshot["frame"].copy()
        detections = snapshot["detections"]
        frame_number = int(snapshot["frame_number"])
        timestamp = str(snapshot["timestamp"])
        max_n = int(row["max_n"])

        # Slightly darken the full image so the highlighted target boxes pop.
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (0, 0),
            (frame.shape[1], frame.shape[0]),
            (0, 0, 0),
            -1,
        )
        frame = cv2.addWeighted(frame, 0.90, overlay, 0.10, 0)

        for det in detections:
            x1, y1, x2, y2 = [int(round(v)) for v in det["bbox"]]
            det_species = str(det["species"])
            confidence = float(det["confidence"])

            # Clamp to frame bounds.
            x1 = max(0, min(frame.shape[1] - 1, x1))
            x2 = max(0, min(frame.shape[1] - 1, x2))
            y1 = max(0, min(frame.shape[0] - 1, y1))
            y2 = max(0, min(frame.shape[0] - 1, y2))

            if det_species == species:
                # High-contrast double border: black outer stroke + yellow inner.
                cv2.rectangle(frame, (x1, y1), (x2, y2), black, 8)
                cv2.rectangle(frame, (x1, y1), (x2, y2), highlight_colour, 4)

                label = f"MAX-N  {det_species}  {confidence:.2f}"
                font_scale = 0.62
                font_thickness = 2
                (tw, th), baseline = cv2.getTextSize(
                    label,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    font_thickness,
                )
                label_y1 = max(0, y1 - th - baseline - 10)
                label_y2 = min(frame.shape[0] - 1, y1)
                label_x2 = min(frame.shape[1] - 1, x1 + tw + 12)

                cv2.rectangle(
                    frame,
                    (x1, label_y1),
                    (label_x2, label_y2),
                    black,
                    -1,
                )
                cv2.putText(
                    frame,
                    label,
                    (x1 + 6, max(th + 2, label_y2 - baseline - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    highlight_colour,
                    font_thickness,
                    cv2.LINE_AA,
                )
            else:
                # Keep other fish visible as context, but visually subordinate.
                cv2.rectangle(
                    frame,
                    (x1, y1),
                    (x2, y2),
                    normal_colour,
                    2,
                )
                label = f"{det_species} {confidence:.2f}"
                cv2.putText(
                    frame,
                    label,
                    (x1, max(18, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    normal_colour,
                    1,
                    cv2.LINE_AA,
                )

        # Strong header banner identifying exactly what the example demonstrates.
        banner = f"MAX-N {max_n}  |  {species}  |  {timestamp}"
        font_scale = 0.82
        font_thickness = 2
        (tw, th), baseline = cv2.getTextSize(
            banner,
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            font_thickness,
        )
        banner_h = th + baseline + 18
        banner_w = min(frame.shape[1], tw + 24)

        cv2.rectangle(frame, (0, 0), (banner_w, banner_h), black, -1)
        cv2.rectangle(
            frame,
            (0, 0),
            (banner_w, banner_h),
            highlight_colour,
            3,
        )
        cv2.putText(
            frame,
            banner,
            (12, th + 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            white,
            font_thickness,
            cv2.LINE_AA,
        )

        safe_species = safe_filename(species)
        safe_time = timestamp.replace(":", "-")
        image_path = examples_dir / (
            f"{rank:02d}_{safe_species}_maxn{max_n}_{safe_time}.jpg"
        )

        if cv2.imwrite(str(image_path), frame):
            saved.append(image_path)

    return saved


# Main pipeline function
def run_pipeline(
    config: RunConfig,
    on_progress: Callable[[int, int], None] | None = None,
    on_log:      Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Path]:
    """
    Run detection and tracking on a video.

    Parameters
    ----------
    config       : RunConfig with all paths and settings.
    on_progress  : called each frame with (current_frame, total_frames).
    on_log       : called with status strings for display in the GUI log.
    is_cancelled : called each frame; return True to abort early.

    Returns
    -------
    dict with keys "video", "summary_csv", "flagged_csv" pointing to outputs.
    """

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    # Validate inputs
    for path, desc in [
        (config.video_path,   "Input video"),
        (config.weights_path, "Model weights"),
        # (config.tracker_path, "Tracker config"),
    ]:
        if not path.is_file():
            raise FileNotFoundError(f"{desc} not found: {path}")

    config.output_dir.mkdir(parents=True, exist_ok=True)

    video_stem   = config.video_path.stem
    summary_csv  = config.output_dir / "track_summary.csv"
    flagged_csv  = config.output_dir / "low_confidence_review.csv"
    output_video = config.output_dir / f"{video_stem}_annotated.mp4"

    for old_file in (summary_csv, flagged_csv, output_video):
        if old_file.exists():
            old_file.unlink()

    fps, width, height, video_duration, total_frames = read_video_properties(
        config.video_path
    )

    log(f"Video:      {config.video_path.name}")
    log(f"Duration:   {format_timestamp(video_duration)}")
    log(f"Frames:     {total_frames}  |  FPS: {fps:.2f}")
    log(f"Weights:    {config.weights_path.name}")
    log(f"Confidence: {config.confidence}  |  IoU: {config.iou}")
    log("Loading model...")

    detector = load_detector(config.weights_path, device=config.device)

    if detector.kind == "yolo":
        log("Model type: YOLO  |  Tracker: botsort (default)")
    else:
        log("Model type: RF-DETR  |  Tracker: ByteTrack")
        if getattr(detector, "class_names_missing", False):
            log(
                "WARNING: this RF-DETR checkpoint has no class names embedded — "
                "species will be labelled class_0, class_1, etc."
            )

    cap = cv2.VideoCapture(str(config.video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {config.video_path}")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(output_video), fourcc, fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not create output video: {output_video}")

    flagged_detections: list[dict] = []
    track_data: dict[int, dict[str, Any]] = {}
    species_data: dict[str, dict[str, Any]] = {}
    frame_counts: dict[str, list[tuple[int, int, float]]] = defaultdict(list)

    # Store the best raw frame + detections for each species so the Results
    # page can show a dedicated high-contrast Max-N review image.
    maxn_snapshots: dict[str, dict[str, Any]] = {}

    was_cancelled = False

    log("Running detection...")

    try:
        frame_index = 0
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break

            if is_cancelled and is_cancelled():
                log("Cancelled.")
                was_cancelled = True
                break

            frame_number      = frame_index + 1
            timestamp_seconds = frame_index / fps
            timestamp         = format_timestamp(timestamp_seconds)

            det_frame = detector.infer(frame_bgr, conf=config.confidence, iou=config.iou)

            annotated = det_frame.annotated
            add_frame_label(annotated, frame_number, timestamp)
            writer.write(annotated)

            if on_progress:
                on_progress(frame_number, total_frames)

            frame_index += 1

            species_list = det_frame.species
            if not species_list:
                continue

            confidences = det_frame.confidences
            xyxy        = det_frame.xyxy
            track_ids   = det_frame.track_ids

            species_this_frame: Counter = Counter(species_list)

            for species, count in species_this_frame.items():
                frame_counts[species].append(
                    (frame_number, count, timestamp_seconds)
                )

            # Capture a raw review snapshot whenever this frame establishes a
            # new Max-N for a species. We retain all detections for context,
            # but later only the selected target species receives the thick
            # yellow highlight.
            detections_this_frame = [
                {
                    "species": species,
                    "confidence": confidence,
                    "bbox": bbox,
                    "track_id": track_id,
                }
                for track_id, species, confidence, bbox in zip(
                    track_ids, species_list, confidences, xyxy
                )
            ]

            for species, count in species_this_frame.items():
                previous = maxn_snapshots.get(species)
                if previous is None or count > int(previous["count"]):
                    maxn_snapshots[species] = {
                        "count": count,
                        "frame_number": frame_number,
                        "timestamp": timestamp,
                        "timestamp_seconds": timestamp_seconds,
                        "frame": frame_bgr.copy(),
                        "detections": detections_this_frame,
                    }

            for track_id, species, confidence, bbox in zip(
                track_ids, species_list, confidences, xyxy
            ):
                if species not in species_data:
                    species_data[species] = {
                        "first_seconds": timestamp_seconds,
                        "last_seconds": timestamp_seconds,
                        "total_detections": 0,
                        "confidences": [],
                        "track_ids": set(),
                    }
                sd = species_data[species]
                sd["first_seconds"] = min(sd["first_seconds"], timestamp_seconds)
                sd["last_seconds"] = max(sd["last_seconds"], timestamp_seconds)
                sd["total_detections"] += 1
                sd["confidences"].append(confidence)

                if track_id is not None:
                    sd["track_ids"].add(track_id)
                    if track_id not in track_data:
                        track_data[track_id] = {
                            "species_votes": [],
                            "confidences": [],
                            "first_seconds": timestamp_seconds,
                            "last_seconds": timestamp_seconds,
                            "total_detections": 0,
                        }

                    track = track_data[track_id]
                    track["species_votes"].append(species)
                    track["last_seconds"] = timestamp_seconds
                    track["confidences"].append(confidence)
                    track["total_detections"] += 1

                if confidence < config.review_confidence:
                    x1, y1, x2, y2 = bbox
                    flagged_detections.append({
                        "frame_number": frame_number,
                        "timestamp": timestamp,
                        "species": species,
                        "confidence": round(confidence, 4),
                        "track_id": "" if track_id is None else track_id,
                        "x1": round(x1, 2),
                        "y1": round(y1, 2),
                        "x2": round(x2, 2),
                        "y2": round(y2, 2),
                        "notes": "",
                    })
    finally:
        cap.release()
        writer.release()

    if was_cancelled:
        for partial_file in (output_video, summary_csv, flagged_csv):
            try:
                partial_file.unlink(missing_ok=True)
            except OSError:
                pass
        return {}

    log("Writing CSV outputs...")

    for track in track_data.values():
        track["species"] = Counter(
            track["species_votes"]
        ).most_common(1)[0][0]

    rows = []

    for species, sd in species_data.items():
        counts = frame_counts.get(species, [])

        if counts:
            max_frame, max_n, max_n_seconds = max(counts, key=lambda x: x[1])
            max_n_timestamp = format_timestamp(max_n_seconds)
        else:
            max_frame, max_n, max_n_timestamp = 0, 0, "00:00"

        mean_conf = (
            sum(sd["confidences"]) / len(sd["confidences"])
            if sd["confidences"] else 0.0
        )

        unique_frames    = {fn: ts for fn, _, ts in counts}
        frames_present   = len(unique_frames)
        visible_seconds  = round(frames_present / fps, 1)

        if unique_frames:
            first_seconds    = min(unique_frames.values())
            last_seconds     = max(unique_frames.values())
            obs_span_seconds = round(last_seconds - first_seconds + 1 / fps, 1)
        else:
            first_seconds    = 0.0
            last_seconds     = 0.0
            obs_span_seconds = 0.0

        rows.append({
            "dataset":                  config.video_path.stem,
            "video":                    config.video_path.name,
            "video_duration_seconds":   round(video_duration, 1),
            "species":                  species,
            "max_n":                    max_n,
            "max_n_timestamp":          max_n_timestamp,
            "max_n_frame":              max_frame,
            "first_seen":               format_timestamp(first_seconds),
            "last_seen":                format_timestamp(last_seconds),
            "visible_seconds":          visible_seconds,
            "observation_span_seconds": obs_span_seconds,
            "unique_tracks":            len(sd["track_ids"]),
            "total_detections":         sd["total_detections"],
            "mean_confidence":          round(mean_conf, 4),
        })

    rows.sort(key=lambda r: (-r["max_n"], -r["visible_seconds"]))

    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(rows)

    flagged_detections.sort(key=lambda r: r["frame_number"])

    with flagged_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FLAGGED_FIELDS)
        w.writeheader()
        w.writerows(flagged_detections)

    # Save the top two distinct annotated Max-N frames for quick visual review.
    example_frames = extract_maxn_example_frames(
        rows=rows,
        maxn_snapshots=maxn_snapshots,
        output_dir=config.output_dir,
        limit=2,
    )

    if example_frames:
        log(
            "Saved Max-N example frames: "
            + ", ".join(path.name for path in example_frames)
        )

    log(f"Done. {len(rows)} species detected, "
        f"{len(flagged_detections)} low-confidence detections flagged.")

    outputs = {
        "video":       output_video,
        "summary_csv": summary_csv,
        "flagged_csv": flagged_csv,
    }

    for index, image_path in enumerate(example_frames, start=1):
        outputs[f"maxn_frame_{index}"] = image_path

    return outputs
