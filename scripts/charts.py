"""
charts.py
---------
Summary-CSV reading and matplotlib chart construction for the
Results page, plus the scroll-passthrough canvas used to embed
charts inside a scrollable Qt page.
"""
from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from PyQt6.QtWidgets import QScrollArea


# Colour palette that matches the app's blue/teal theme
_PALETTE = [
    "#0072CE", "#00A3A1", "#2E7D32", "#E87722", "#7B3F9E",
    "#C62828", "#1565C0", "#00695C", "#6A1B9A", "#AD1457",
    "#1976D2", "#00897B", "#558B2F", "#EF6C00", "#4527A0",
]


def _theme_axes(ax: plt.Axes) -> None:
    """Apply consistent light-theme styling to a matplotlib Axes."""
    ax.set_facecolor("#FAFBFC")
    ax.tick_params(colors="#52606D", labelsize=9)
    ax.xaxis.label.set_color("#52606D")
    ax.yaxis.label.set_color("#52606D")
    ax.title.set_color("#003B70")
    for spine in ax.spines.values():
        spine.set_edgecolor("#D7E0E8")


def _read_summary_csv(csv_path: Path) -> list[dict]:
    """Return rows from track_summary.csv as a list of dicts."""
    rows = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        pass
    return rows


def _timestamp_to_seconds(timestamp: str) -> float:
    """Convert MM:SS or HH:MM:SS timestamp text to seconds."""
    try:
        parts = [float(p) for p in str(timestamp).strip().split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return hours * 3600 + minutes * 60 + seconds
    except (TypeError, ValueError):
        pass
    return 0.0


def build_charts(csv_path: Path) -> Figure | None:
    """
    Build a 4x1 vertical figure of four charts from the summary CSV.
    """
    rows = _read_summary_csv(csv_path)
    if not rows:
        return None

    species      = [r["species"] for r in rows]
    max_n        = [int(r["max_n"]) for r in rows]
    max_n_time   = [r.get("max_n_timestamp", "") for r in rows]
    obs_span     = [float(r["observation_span_seconds"]) for r in rows]
    total_dets   = [int(r["total_detections"]) for r in rows]
    mean_conf    = [float(r["mean_confidence"]) for r in rows]
    vis_span     = [float(r["visible_seconds"]) for r in rows]
    first_seen   = [r.get("first_seen", "") for r in rows]
    last_seen    = [r.get("last_seen", "") for r in rows]
    video_length = max(
        [float(r.get("video_duration_seconds", 0) or 0) for r in rows] or [0]
    )

    n = len(species)
    colours = [_PALETTE[i % len(_PALETTE)] for i in range(n)]

    fig = Figure(figsize=(15, 20), facecolor="#F4F7FA")
    fig.subplots_adjust(
        hspace=0.58,
        left=0.20,
        right=0.98,
        top=0.97,
        bottom=0.055,
    )

    # ── 1. Max-N ────────────────────────────────────────────
    ax1 = fig.add_subplot(4, 1, 1)
    _theme_axes(ax1)
    bars1 = ax1.barh(species, max_n, color=colours, edgecolor="white", height=0.6)
    ax1.set_xlabel("Max fish in one frame")
    ax1.set_title("Peak Abundance (Max-N)", fontsize=11, fontweight="bold", pad=8)
    ax1.invert_yaxis()
    ax1.set_xlim(0, max(max_n) * 1.45 if max_n else 1)
    for bar, val, timestamp in zip(bars1, max_n, max_n_time):
        label = f"{val}  @ {timestamp}" if timestamp else str(val)
        ax1.text(
            bar.get_width() + max(max_n) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center",
            fontsize=8,
            color="#52606D",
        )

    # Explain the inline "@ timestamp" annotation without covering the bars.
    timestamp_legend = mpatches.Patch(
        facecolor="none",
        edgecolor="none",
        label="@ time = video timestamp when Max-N was recorded",
    )
    ax1.legend(
        handles=[timestamp_legend],
        loc="lower right",
        fontsize=7,
        frameon=False,
        handlelength=0,
        handletextpad=0,
        borderaxespad=0.6,
    )

    # ── 2. Observation span ──────────────────────────────────
    # ax2 = fig.add_subplot(2, 2, 2)
    # _theme_axes(ax2)
    # bars2 = ax2.barh(species, obs_span, color=colours, edgecolor="white", height=0.6)
    # ax2.set_xlabel("Seconds")
    # ax2.set_title("Observation Span per Species", fontsize=11, fontweight="bold", pad=8)
    # ax2.invert_yaxis()
    # ax2.set_xlim(0, max(obs_span) * 1.18 if obs_span else 1)
    # for bar, val in zip(bars2, obs_span):
    #     ax2.text(bar.get_width() + max(obs_span) * 0.02, bar.get_y() + bar.get_height() / 2,
    #              f"{val:.1f}s", va="center", fontsize=8, color="#52606D")

    # ── 3. Total detections ──────────────────────────────────
    ax3 = fig.add_subplot(4, 1, 2)
    _theme_axes(ax3)
    bars3 = ax3.barh(species, total_dets, color=colours, edgecolor="white", height=0.6)
    ax3.set_xlabel("Detection count")
    ax3.set_title("Total Detections by Species", fontsize=11, fontweight="bold", pad=8)
    ax3.invert_yaxis()
    ax3.set_xlim(0, max(total_dets) * 1.18 if total_dets else 1)
    for bar, val in zip(bars3, total_dets):
        ax3.text(bar.get_width() + max(total_dets) * 0.02, bar.get_y() + bar.get_height() / 2,
                 str(val), va="center", fontsize=8, color="#52606D")

    # Visual chart
    ax2 = fig.add_subplot(4, 1, 3)
    _theme_axes(ax2)
    bars2 = ax2.barh(species, vis_span, color=colours, edgecolor="white", height=0.6)
    ax2.set_xlabel("Seconds")
    ax2.set_title("Visible Span per Species", fontsize=11, fontweight="bold", pad=8)
    ax2.invert_yaxis()
    ax2.set_xlim(0, max(vis_span) * 1.18 if vis_span else 1)
    for bar, val in zip(bars2, vis_span):
        ax2.text(bar.get_width() + max(vis_span) * 0.02, bar.get_y() + bar.get_height() / 2,
                 f"{val:.1f}s", va="center", fontsize=8, color="#52606D")

    # ── 4. Mean detection confidence ────────────────────────
    ax4 = fig.add_subplot(4, 1, 4)
    _theme_axes(ax4)

    conf_colours = [
        "#2E7D32" if c >= 0.70 else "#E87722" if c >= 0.50 else "#C62828"
        for c in mean_conf
    ]

    y_pos = np.arange(len(species))

    # Light stems make this a lollipop/dot chart rather than another bar chart.
    for y, val, colour in zip(y_pos, mean_conf, conf_colours):
        ax4.hlines(y, 0, val, color=colour, linewidth=1.2, alpha=0.45)

    ax4.scatter(
        mean_conf,
        y_pos,
        s=55,
        c=conf_colours,
        edgecolors="white",
        linewidths=0.7,
        zorder=3,
    )

    ax4.set_yticks(y_pos)
    ax4.set_yticklabels(species)
    ax4.set_xlabel("Mean confidence score")
    ax4.set_title("Mean Detection Confidence", fontsize=11, fontweight="bold", pad=30)
    ax4.invert_yaxis()
    ax4.set_xlim(0, 1.08)

    ax4.axvline(0.5, color="#C62828", linewidth=0.8, linestyle="--", alpha=0.6)
    ax4.axvline(0.7, color="#2E7D32", linewidth=0.8, linestyle="--", alpha=0.6)

    for y, val in zip(y_pos, mean_conf):
        ax4.text(
            val + 0.015,
            y,
            f"{val:.2f}",
            va="center",
            fontsize=8,
            color="#52606D",
        )

    legend_patches = [
        mpatches.Patch(color="#2E7D32", label="High  (≥ 0.70)"),
        mpatches.Patch(color="#E87722", label="Medium (0.50-0.70)"),
        mpatches.Patch(color="#C62828", label="Low  (< 0.50)"),
    ]

    ax4.legend(
        handles=legend_patches,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=3,
        fontsize=7,
        frameon=False,
        borderaxespad=0.0,
        columnspacing=1.4,
        handletextpad=0.5,
    )

    return fig


class ScrollPassthroughCanvas(FigureCanvas):
    """FigureCanvas that forwards wheel events to the nearest QScrollArea
    so the page still scrolls when the mouse is over the chart."""

    def wheelEvent(self, event) -> None:
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.wheelEvent(event)
                return
            parent = parent.parent()
        super().wheelEvent(event)
