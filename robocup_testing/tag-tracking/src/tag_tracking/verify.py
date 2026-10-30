"""Check a calibration against tape-measured ground truth, in millimetres.

    uv run verify-calibration                                   # guided: choose on screen
    uv run verify-calibration --no-setup --camera 2 --tag-size 0.127   # flags only
    uv run verify-calibration --synthetic                       # no hardware: should PASS
    uv run verify-calibration --synthetic --synthetic-error scale   # ...and catch a bad one

Every number calibrate-camera and calibrate-field print -- reprojection error,
frame spread -- says whether a step fits its *own* data. None of them says
whether a position on the real table comes out right: a mistyped --field, a
calibration tag set a few cm off its corner, a lens calibrated only mid-frame,
or a camera bumped since, all leave them looking fine. This does: one tag is
placed on 9 points measured with a tape measure (corners, edge midpoints,
centre), and each is read back through exactly the pipeline track-combined
uses -- undistort, detect, ray onto the field plane -- and compared.

Live, the window walks through the points one at a time, like
`calibrate-field --sequential`: put the tag's centre on the mark with its edges
square to the field's edges -- any side up; nothing on a printed tag marks its
top -- let go, and it averages FRAMES_PER_POINT still frames. The heading check
is then how far off square it reads. (--heading D demands one exact rotation:
D degrees counter-clockwise from upright as printed, top edge towards +Y.)
'q'/Enter
confirms, 'r' redoes the point, 's' skips it, Esc aborts.

The guided run (the default) asks for the camera, tag size and where the
edge marks go on screen, shows the 9 marks on a map before starting, and ends
on the verdict with the error plot. Words from calib_guide, drawing from
vision_core.ui.

Results go to calib/verification/ (shared by the team, like the rest of
calib/): report.md to read or show, latest.json and plot.png beside it, and
history.jsonl with one summary line per run.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pupil_apriltags as pa
from vision_core.field import (
    CameraFieldTransform,
    Field,
    ReferenceTagFieldTransform,
    SyntheticFieldTransform,
    field_pose_path,
    resolve_field,
)
from vision_core import ui
from vision_core.intrinsics import intrinsics_path, undistort_maps
from vision_core.options import initial_values
from vision_core.paths import calib_dir
from vision_core.planview import draw_field, project

from . import calib_guide as guide
from .pose import auto_tag_decimate, tag_field_pose

FRAMES_PER_POINT = 30
#: A sample this far (px) from the running mean means the tag is still moving.
STILL_PX = 3.0
#: A tag reading within this of the previous point's is still sitting there.
MIN_POINT_SEP_M = 0.05

#: (pass below, warn below) -- anything at or above the second is a FAIL.
#: Position and heading decide the verdict; the rest are health checks.
THRESHOLDS = {
    "pos_rms_mm": (10.0, 20.0),
    "pos_max_mm": (20.0, 40.0),
    "heading_max_deg": (2.0, 5.0),
    "off_plane_median_mm": (10.0, 30.0),
    "jitter_mm": (2.0, 5.0),
}
#: Detection rate is the other way round: higher is better.
DETECTION_PASS, DETECTION_WARN = 100.0, 90.0
VERDICT_KEYS = ("pos_rms_mm", "pos_max_mm", "heading_max_deg")
GRADES = ("PASS", "WARN", "FAIL")


def out_dir() -> Path:
    return calib_dir() / "verification"


# --------------------------------------------------------------------------
# The points, and measuring one of them.
# --------------------------------------------------------------------------


def nine_points(field: Field, inset: float = 0.0) -> list[tuple[float, float]]:
    """Corners, edge midpoints and centre, in the coordinates the tracker
    reports (so a custom origin shifts them too). `inset` pulls the edge rows
    and columns in, for tags that cannot sit centred on the field's edge."""
    xs = (field.x0 + inset, field.x0 + field.width / 2, field.x0 + field.width - inset)
    ys = (field.y0 + inset, field.y0 + field.height / 2, field.y0 + field.height - inset)
    return [(x, y) for y in ys for x in xs]


def parse_points(spec: list[str]) -> list[tuple[float, float]]:
    """['0,0', '1.5,0.72', ...] -> [(0, 0), (1.5, 0.72), ...]."""
    out = []
    for item in spec:
        x_s, y_s = item.split(",")
        out.append((float(x_s), float(y_s)))
    return out


@dataclass
class PointResult:
    x: float  # where the tag was placed, field metres
    y: float
    reported_x: float  # what the tracker pipeline read, averaged
    reported_y: float
    err_x_mm: float
    err_y_mm: float
    err_mm: float
    heading_err_deg: float
    off_plane_mm: float
    jitter_mm: float  # std of the per-frame position while held still
    detection_pct: float
    n_samples: int
    pixel: tuple[float, float]  # where it sat in the (undistorted) image
    skipped: bool = False


def _skipped(target: tuple[float, float]) -> PointResult:
    nan = math.nan
    return PointResult(target[0], target[1], nan, nan, nan, nan, nan, nan, nan, nan, nan,
                       0, (nan, nan), skipped=True)


def _jsonable(v):
    """NaN is not JSON: skipped points' numbers go out as null."""
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


class Measurer:
    """The tracker's own per-frame pipeline: undistort, detect, ray onto the
    field. Kept in one place so verify measures what track-combined reports."""

    def __init__(self, K: np.ndarray, dist: np.ndarray | None, size: tuple[int, int],
                 transform: CameraFieldTransform, field: Field, tag_size: float,
                 tag_height: float = 0.0) -> None:
        w, h = size
        self.K, self.transform, self.field = K, transform, field
        self.tag_size, self.tag_height = tag_size, tag_height
        self.maps = undistort_maps(K, np.zeros(5) if dist is None else dist, w, h)
        self.detector = pa.Detector(families="tag36h11", nthreads=os.cpu_count() or 4,
                                    quad_decimate=auto_tag_decimate(w),
                                    decode_sharpening=0.25)

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        return frame if self.maps is None else cv2.remap(frame, *self.maps, cv2.INTER_LINEAR)

    def measure(self, frame: np.ndarray):
        """(undistorted frame, [(detection, TagFieldPose), ...])."""
        frame = self.undistort(frame)
        grey = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        K = self.K
        dets = self.detector.detect(grey, estimate_tag_pose=True,
                                    camera_params=(K[0, 0], K[1, 1], K[0, 2], K[1, 2]),
                                    tag_size=self.tag_size)
        return frame, [(d, tag_field_pose(d, self.transform, self.field, K=K,
                                          tag_height=self.tag_height)) for d in dets]


def _pick(found, tag_id: int | None):
    """The one tag being moved around, or None if that is ambiguous."""
    if tag_id is not None:
        found = [f for f in found if f[0].tag_id == tag_id]
    return found[0] if len(found) == 1 else None


def placement(heading_deg: float | None) -> str:
    """How to lay the tag down, in words. None: any side up, square to the
    field. Otherwise heading 0 is the tag upright as printed: top edge towards
    +Y, right side towards +X (tag_field_pose measures its right side)."""
    if heading_deg is None:
        return "edges square to the field, any side up"
    if abs(_wrap_deg(heading_deg)) < 1e-9:
        return "upright: top edge towards +Y, right side towards +X"
    return f"turned {heading_deg:+g} deg counter-clockwise from upright (top edge to +Y)"


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


class PointSampler:
    """Collects still samples of one tag at one point; same stillness rule as
    calibrate-field --sequential, so a tag still being carried never counts."""

    def __init__(self, target: tuple[float, float], heading_deg: float | None, n: int,
                 previous: tuple[float, float] | None) -> None:
        self.target, self.heading, self.n, self.previous = target, heading_deg, n, previous
        self.reset()

    def reset(self) -> None:
        self.px: list[np.ndarray] = []
        self.poses: list = []
        self.frames = 0  # frames since this point's sampling started
        self.hits = 0

    @property
    def ready(self) -> bool:
        return len(self.poses) >= self.n

    def add(self, picked) -> str | None:
        """Feed one frame's pick (or None). Returns a hint, or None if fine."""
        if self.poses:
            self.frames += 1
        if picked is None:
            return None
        det, pose = picked
        if self.previous is not None and math.hypot(
                pose.x - self.previous[0], pose.y - self.previous[1]) < MIN_POINT_SEP_M:
            return "still at the previous point -- move the tag"
        c = np.asarray(det.center, np.float64)
        if self.px and np.linalg.norm(c - np.mean(self.px, axis=0)) > STILL_PX:
            self.reset()
            return "tag moving -- set it down and let go"
        if not self.poses:
            self.frames = 1
        if not self.ready:
            self.px.append(c)
            self.poses.append(pose)
            self.hits += 1
        return None

    def result(self) -> PointResult:
        xs = np.array([p.x for p in self.poses])
        ys = np.array([p.y for p in self.poses])
        rx, ry = float(xs.mean()), float(ys.mean())
        ex, ey = (rx - self.target[0]) * 1000.0, (ry - self.target[1]) * 1000.0
        # Circular mean, so headings either side of +-180 average properly.
        th = np.radians([p.theta_deg for p in self.poses])
        if self.heading is None:
            # Any side up: the heading is only known up to a quarter turn, so
            # average 4*theta and come back -- the result is directly how far
            # off square the tag reads, in (-45, 45].
            heading_err = math.degrees(math.atan2(np.sin(4 * th).mean(),
                                                  np.cos(4 * th).mean())) / 4.0
        else:
            mean_th = math.degrees(math.atan2(np.sin(th).mean(), np.cos(th).mean()))
            heading_err = _wrap_deg(mean_th - self.heading)
        jitter = float(np.sqrt(xs.var() + ys.var()) * 1000.0)
        return PointResult(
            x=self.target[0], y=self.target[1], reported_x=rx, reported_y=ry,
            err_x_mm=ex, err_y_mm=ey, err_mm=float(math.hypot(ex, ey)),
            heading_err_deg=heading_err,
            off_plane_mm=float(np.median([p.off_plane_m for p in self.poses]) * 1000.0),
            jitter_mm=jitter,
            detection_pct=100.0 * self.hits / max(self.frames, 1),
            n_samples=len(self.poses),
            pixel=tuple(float(v) for v in np.mean(self.px, axis=0)),
        )


# --------------------------------------------------------------------------
# Summary, verdict, and what the error pattern points at.
# --------------------------------------------------------------------------


def _grade(key: str, value: float) -> str:
    ok, warn = THRESHOLDS[key]
    return GRADES[0] if value < ok else GRADES[1] if value < warn else GRADES[2]


def _grade_detection(pct: float) -> str:
    return GRADES[0] if pct >= DETECTION_PASS else GRADES[1] if pct >= DETECTION_WARN \
        else GRADES[2]


def diagnose(points: list[PointResult], field: Field, K: np.ndarray) -> list[str]:
    """Plain-language hints from the shape of the errors. Only says something
    when the pattern is both clear and big enough to matter."""
    pts = [p for p in points if not p.skipped]
    if len(pts) < 4:
        return []
    e = np.array([[p.err_x_mm, p.err_y_mm] for p in pts])
    mag = np.hypot(e[:, 0], e[:, 1])
    rms = float(np.sqrt(np.mean(mag ** 2)))
    hints = []
    if rms < 5.0:
        return hints

    # Fit reported = slope * true + offset per axis. A stretch shows in the
    # slope; what is left in the offset is a genuine shift. Fitting both at
    # once keeps a stretch (which also moves the average point) from being
    # reported as a shift too.
    offset = e.mean(axis=0) / 1000.0
    scale_hints = []
    for axis, name, extent in ((0, "x", field.width), (1, "y", field.height)):
        true = np.array([(p.x, p.y)[axis] for p in pts])
        rep = np.array([(p.reported_x, p.reported_y)[axis] for p in pts])
        if np.ptp(true) < 0.5 * extent:
            continue
        slope, intercept = (float(v) for v in np.polyfit(true, rep, 1))
        if abs(slope - 1.0) * extent * 1000.0 > 10.0:
            scale_hints.append(
                f"Distances along {name} come out {100 * (slope - 1):+.1f}% "
                f"({(slope - 1) * extent * 1000:+.0f} mm across the field): the field "
                f"{'width' if axis == 0 else 'height'} given to calibrate-field --field "
                "probably does not match the real one. Re-measure and recalibrate.")
            # Offset measured at the field's own start, with the stretch removed.
            ref = field.x0 if axis == 0 else field.y0
            offset[axis] = intercept + (slope - 1.0) * ref
    offset_mm = offset * 1000.0
    if np.hypot(*offset_mm) > max(0.6 * rms, 5.0):
        hints.append(
            f"Every point is shifted the same way (about {offset_mm[0]:+.0f} mm in x, "
            f"{offset_mm[1]:+.0f} mm in y): the field position or origin is off. Redo "
            "calibrate-field, or re-pick the origin.")
    hints += scale_hints

    r_px = np.array([math.hypot(p.pixel[0] - K[0, 2], p.pixel[1] - K[1, 2]) for p in pts])
    if np.ptp(r_px) > 1e-6 and np.std(mag) > 1e-6:
        corr = float(np.corrcoef(r_px, mag)[0, 1])
        outer = mag[r_px >= np.median(r_px)].mean()
        inner = mag[r_px < np.median(r_px)].mean() if (r_px < np.median(r_px)).any() else 0
        if corr > 0.6 and outer > 2.0 * max(inner, 1.0):
            # A bad lens model squeezes or stretches towards the edges, which
            # also looks like a shift and a scale error. Fix it first; say so.
            hints = [h + " (may be a side effect of the lens problem above -- "
                     "recheck after fixing it)" for h in hints]
            hints.insert(0,
                "Errors grow towards the edges of the camera picture: the lens "
                "calibration is the weak link -- fix this first. Redo calibrate-camera "
                "with 20+ views that take the board into every corner of the frame.")

    worst = int(np.argmax(mag))
    others = np.delete(mag, worst)
    if mag[worst] > 20.0 and mag[worst] > 2.5 * max(float(np.median(others)), 1.0):
        p = pts[worst]
        hints.append(
            f"One point, ({p.x:g}, {p.y:g}), is much worse than the rest "
            f"({mag[worst]:.0f} mm): if it is a corner, the calibration tag there was "
            "probably misplaced -- or that tape mark is. Check the mark, then redo "
            "calibrate-field.")
    return hints


def summarize(points: list[PointResult], field: Field, K: np.ndarray) -> dict:
    pts = [p for p in points if not p.skipped]
    if not pts:
        raise SystemExit("no points measured -- nothing to report")
    mag = np.array([p.err_mm for p in pts])
    values = {
        "pos_rms_mm": float(np.sqrt(np.mean(mag ** 2))),
        "pos_max_mm": float(mag.max()),
        "heading_max_deg": float(max(abs(p.heading_err_deg) for p in pts)),
        "off_plane_median_mm": float(abs(np.median([p.off_plane_mm for p in pts]))),
        "jitter_mm": float(np.median([p.jitter_mm for p in pts])),
    }
    grades = {k: _grade(k, v) for k, v in values.items()}
    det_min = float(min(p.detection_pct for p in pts))
    values["detection_min_pct"] = det_min
    grades["detection_min_pct"] = _grade_detection(det_min)
    verdict = max((grades[k] for k in VERDICT_KEYS), key=GRADES.index)
    if len(pts) < len(points):
        verdict = max(verdict, "WARN", key=GRADES.index)  # skipped points: incomplete
    worst = pts[int(np.argmax(mag))]
    return {
        "verdict": verdict,
        "values": values,
        "grades": grades,
        "worst_point": [worst.x, worst.y],
        "n_points": len(pts),
        "n_skipped": len(points) - len(pts),
        "diagnosis": diagnose(points, field, K),
    }


# --------------------------------------------------------------------------
# The evidence: JSON, history line, plot, and a readable report.
# --------------------------------------------------------------------------

GRADE_BGR = {"PASS": (90, 200, 90), "WARN": (60, 180, 240), "FAIL": (80, 80, 240)}


def _point_grade(p: PointResult) -> str:
    return "SKIP" if p.skipped else _grade("pos_max_mm", p.err_mm)


def draw_plot(points: list[PointResult], field: Field, summary: dict) -> np.ndarray:
    """Top-down field with an arrow per point: where it read vs where it is,
    magnified so millimetre errors are visible -- by 10x, or less if that
    would run the longest arrow off the picture."""
    W, H, pad, top = 1100, 0, 150, 110
    scale = (W - 2 * pad) / field.width
    worst_m = max((p.err_mm for p in points if not p.skipped), default=0.0) / 1000.0
    arrow_scale = 10.0
    for k in (10.0, 5.0, 2.0, 1.0):
        arrow_scale = k
        if worst_m * scale * k <= pad - 20:
            break
    H = int(field.height * scale) + top + pad
    img = np.full((H, W, 3), 255, np.uint8)

    def px(x, y):
        return (int(round(pad + (x - field.x0) * scale)),
                int(round(top + (field.y0 + field.height - y) * scale)))

    cv2.rectangle(img, px(field.x0, field.y0), px(field.x0 + field.width,
                  field.y0 + field.height), (60, 60, 60), 2)
    font = cv2.FONT_HERSHEY_SIMPLEX
    v = summary["values"]
    cv2.putText(img, f"Calibration check: {summary['verdict']}", (pad, 34), font, 0.9,
                GRADE_BGR[summary["verdict"]], 2, cv2.LINE_AA)
    cv2.putText(img, f"RMS {v['pos_rms_mm']:.1f} mm   worst {v['pos_max_mm']:.1f} mm   "
                f"heading worst {v['heading_max_deg']:.1f} deg   "
                f"(arrows x{arrow_scale:g})", (pad, 64), font, 0.55, (60, 60, 60), 1,
                cv2.LINE_AA)
    for p in points:
        c = px(p.x, p.y)
        if p.skipped:
            cv2.drawMarker(img, c, (160, 160, 160), cv2.MARKER_TILTED_CROSS, 14, 2)
            continue
        col = GRADE_BGR[_point_grade(p)]
        tip = (int(round(c[0] + p.err_x_mm / 1000.0 * scale * arrow_scale)),
               int(round(c[1] - p.err_y_mm / 1000.0 * scale * arrow_scale)))
        cv2.circle(img, c, 6, (40, 40, 40), -1, cv2.LINE_AA)
        if c != tip:
            cv2.arrowedLine(img, c, tip, col, 2, cv2.LINE_AA, tipLength=0.25)
        cv2.putText(img, f"{p.err_mm:.0f} mm", (c[0] + 8, c[1] + 18), font, 0.5, col, 1,
                    cv2.LINE_AA)
    bar_m = 0.01  # a 1 cm scale bar, at arrow magnification
    x0, y0 = pad, H - 30
    cv2.line(img, (x0, y0), (x0 + int(bar_m * scale * arrow_scale), y0), (60, 60, 60), 3)
    cv2.putText(img, "1 cm error", (x0 + int(bar_m * scale * arrow_scale) + 8, y0 + 5),
                font, 0.5, (60, 60, 60), 1, cv2.LINE_AA)
    return img


def _calib_metrics(path: Path) -> tuple[str | None, dict | None]:
    if not path.exists():
        return None, None
    d = json.loads(path.read_text())
    return d.get("source"), d.get("metrics")


def _fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def write_report(directory: Path, points: list[PointResult], summary: dict, meta: dict,
                 plot: np.ndarray) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    doc = {**meta, "summary": summary, "thresholds": THRESHOLDS,
           "points": [asdict(p) for p in points]}
    (directory / "latest.json").write_text(json.dumps(_jsonable(doc), indent=2))
    with (directory / "history.jsonl").open("a") as f:
        f.write(json.dumps({"checked_at": meta["checked_at"], "verdict": summary["verdict"],
                            **{k: round(v, 2) for k, v in summary["values"].items()},
                            "calibration": meta["calibration"]}) + "\n")
    cv2.imwrite(str(directory / "plot.png"), plot)

    lens_src, lens = meta["lens"]["source"], meta["lens"]["metrics"] or {}
    fld_src, fld = meta["field"]["source"], meta["field"]["metrics"] or {}
    v, g = summary["values"], summary["grades"]
    lines = [
        "# Calibration report",
        "",
        f"**Verdict: {summary['verdict']}** — checked {meta['checked_at']} against "
        f"{summary['n_points']} tape-measured points"
        + (f" ({summary['n_skipped']} skipped)" if summary["n_skipped"] else "") + ".",
        "",
        "![error plot](plot.png)",
        "",
        "## Overall accuracy (the whole pipeline, against ground truth)",
        "",
        "| Metric | Value | Pass / Warn below | Result |",
        "| --- | --- | --- | --- |",
        f"| Position error, RMS | {v['pos_rms_mm']:.1f} mm | 10 / 20 mm | {g['pos_rms_mm']} |",
        f"| Position error, worst point | {v['pos_max_mm']:.1f} mm | 20 / 40 mm | "
        f"{g['pos_max_mm']} |",
        f"| Heading error, worst point | {v['heading_max_deg']:.1f}° | 2 / 5° | "
        f"{g['heading_max_deg']} |",
        f"| Off-plane, median | {v['off_plane_median_mm']:.1f} mm | 10 / 30 mm | "
        f"{g['off_plane_median_mm']} |",
        f"| Jitter (still tag) | {v['jitter_mm']:.2f} mm | 2 / 5 mm | {g['jitter_mm']} |",
        f"| Detection rate, worst point | {v['detection_min_pct']:.0f}% | 100 / 90% | "
        f"{g['detection_min_pct']} |",
        "",
        "Position and heading decide the verdict; the other rows are health checks "
        "(off-plane reflects --tag-size and focal length, not x/y).",
        "",
    ]
    if summary["diagnosis"]:
        lines += ["## What the error pattern suggests", ""]
        lines += [f"- {h}" for h in summary["diagnosis"]] + [""]
    lines += [
        "## Per point",
        "",
        "| Placed at (m) | Read as (m) | Error | dx, dy (mm) | Heading | Off-plane | "
        "Jitter | Detected |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in points:
        if p.skipped:
            lines.append(f"| {p.x:.3f}, {p.y:.3f} | skipped | | | | | | |")
            continue
        lines.append(
            f"| {p.x:.3f}, {p.y:.3f} | {p.reported_x:.3f}, {p.reported_y:.3f} | "
            f"{p.err_mm:.1f} mm | {p.err_x_mm:+.1f}, {p.err_y_mm:+.1f} | "
            f"{p.heading_err_deg:+.1f}° | {p.off_plane_mm:+.0f} mm | {p.jitter_mm:.2f} mm | "
            f"{p.detection_pct:.0f}% |")
    lines += [
        "",
        "## The calibration that was checked",
        "",
        "Each step's own fit. Good numbers here are necessary but not sufficient — "
        "the table above is what shows positions are right.",
        "",
        "| Step | Metric | Value |",
        "| --- | --- | --- |",
        f"| Lens (`calibrate-camera`) | RMS reprojection error | "
        f"{_fmt(lens.get('rms_reproj_px'), 3)} px |",
        f"| | Views | {_fmt(lens.get('n_views'))} |",
        f"| | Frame coverage | {_fmt(lens.get('coverage_pct'), 0)}%, "
        f"{_fmt(lens.get('corner_cells_reached'))}/4 corners |",
        f"| | Calibrated | {_fmt(lens.get('calibrated_at'))} |",
        f"| Field (`calibrate-field`) | Mode | {_fmt(fld.get('mode'))} |",
    ]
    if fld.get("mode") == "four_tags":
        lines += [f"| | Rotation / translation spread | "
                  f"{_fmt(fld.get('rotation_spread_deg'), 3)}° / "
                  f"{_fmt(fld.get('translation_spread_mm'), 2)} mm |"]
    else:
        lines += [f"| | Corner reprojection error, RMS / max | "
                  f"{_fmt(fld.get('reproj_rms_px'), 3)} / "
                  f"{_fmt(fld.get('reproj_max_px'), 3)} px |"]
    lines += [
        f"| | Field size | {meta['field_m'][0]:g} × {meta['field_m'][1]:g} m |",
        f"| | Calibrated | {_fmt(fld.get('calibrated_at'))} |",
        "",
    ]
    if not lens or not fld:
        lines += ["Steps showing — were calibrated before quality numbers were saved; "
                  "re-run them to record their metrics.", ""]
    lines += [
        f"Lens: `{lens_src}`  ",
        f"Field: `{fld_src}`  ",
        f"Tag size {meta['tag_size_m']} m, tag height {meta['tag_height_m']} m, "
        f"camera {meta['image_size'][0]}×{meta['image_size'][1]}.",
        "",
        "Generated by `uv run verify-calibration`; raw data in `latest.json`, every run "
        "in `history.jsonl`.",
        "",
    ]
    report = directory / "report.md"
    report.write_text("\n".join(lines))
    return report


def print_summary(points: list[PointResult], summary: dict) -> None:
    print("\n  placed at (m)      read as (m)        error     heading   off-plane  jitter")
    for p in points:
        if p.skipped:
            print(f"  {p.x:6.3f}, {p.y:6.3f}    skipped")
            continue
        print(f"  {p.x:6.3f}, {p.y:6.3f}    {p.reported_x:6.3f}, {p.reported_y:6.3f}    "
              f"{p.err_mm:5.1f} mm  {p.heading_err_deg:+5.1f}°    {p.off_plane_mm:+5.0f} mm"
              f"   {p.jitter_mm:4.2f} mm")
    v = summary["values"]
    print(f"\n  RMS {v['pos_rms_mm']:.1f} mm   worst {v['pos_max_mm']:.1f} mm at "
          f"({summary['worst_point'][0]:g}, {summary['worst_point'][1]:g})   "
          f"heading worst {v['heading_max_deg']:.1f}°   ->  {summary['verdict']}")
    for h in summary["diagnosis"]:
        print(f"  * {h}")


# --------------------------------------------------------------------------
# Live capture.
# --------------------------------------------------------------------------


def points_map(field: Field, targets: list[tuple[float, float]], results: list[PointResult],
               size: tuple[int, int] = (320, 210), coords: bool = False) -> np.ndarray:
    """The marks on a small field map: measured ones coloured by their grade,
    the next one pulsing."""
    states = []
    for i in range(len(targets)):
        if i < len(results):
            p = _point_grade(results[i])
            states.append("skip" if p == "SKIP" else {"PASS": "ok", "WARN": "warn"}.get(p, "bad"))
        else:
            states.append("current" if i == len(results) else "todo")
    return ui.field_map((field.x0, field.y0, field.width, field.height), targets,
                        [str(i + 1) for i in range(len(targets))], states, size, coords)


def run_live(args, measurer: Measurer, read_bgr: Callable[[], np.ndarray],
             targets: list[tuple[float, float]],
             window: ui.Window | None = None) -> list[PointResult]:
    """Walk through the targets on the live picture. Esc raises ui.Cancelled."""
    own = window is None
    window = window or ui.Window("verify-calibration")
    results: list[PointResult] = []
    try:
        for i, target in enumerate(targets):
            prev = ((results[-1].reported_x, results[-1].reported_y)
                    if results and not results[-1].skipped else None)
            sampler = PointSampler(target, args.heading, args.frames, prev)
            while True:
                frame, found = measurer.measure(read_bgr())
                picked = _pick(found, args.tag_id)
                hint = sampler.add(picked)
                disp = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame,
                                                                         cv2.COLOR_GRAY2BGR)
                draw_field(disp, measurer.field, measurer.transform, measurer.K, False)
                tpx, ok = project(np.array([[*target, 0.0]]), measurer.transform, measurer.K)
                if ok.all():
                    c = tuple(int(v) for v in tpx[0])
                    cv2.drawMarker(disp, c, (255, 0, 255), cv2.MARKER_CROSS, 30, 2)
                    cv2.circle(disp, c, 18, (255, 0, 255), 2, cv2.LINE_AA)
                for r in results:
                    if r.skipped:
                        continue
                    rp, rok = project(np.array([[r.x, r.y, 0.0]]), measurer.transform,
                                      measurer.K)
                    if rok.all():
                        cv2.circle(disp, tuple(int(v) for v in rp[0]), 7,
                                   GRADE_BGR[_point_grade(r)], -1, cv2.LINE_AA)
                for d, _pose in found:
                    cv2.polylines(disp, [np.asarray(d.corners, np.int32)], True,
                                  (0, 165, 255), 2)
                live = None
                if picked is not None:
                    _d, pose = picked
                    live = math.hypot(pose.x - target[0], pose.y - target[1]) * 1000.0
                visible = len(found) if args.tag_id is None else int(picked is not None)
                info = guide.verify_point_text(i, len(targets), target, placement(args.heading),
                                               len(sampler.poses), args.frames, hint, visible,
                                               live)
                window.show(ui.stack(disp, ui.instruction_panel(
                    disp.shape[1], info, points_map(measurer.field, targets, results))))
                key = window.key()
                if key == ui.ESC:
                    print("aborted -- nothing saved")
                    raise ui.Cancelled()
                if key == ord("r"):
                    sampler.reset()
                if key == ord("s"):
                    results.append(_skipped(target))
                    print(f"  point {i + 1}: ({target[0]:g}, {target[1]:g}) skipped")
                    break
                if key in (ord("q"), ui.ENTER, ui.SPACE) and sampler.ready:
                    r = sampler.result()
                    results.append(r)
                    print(f"  point {i + 1}: ({target[0]:g}, {target[1]:g}) read as "
                          f"({r.reported_x:.3f}, {r.reported_y:.3f}) -> {r.err_mm:.1f} mm")
                    break
    finally:
        if own:
            window.close()
    return results


# --------------------------------------------------------------------------
# Synthetic: the same measurement and report, no hardware.
# --------------------------------------------------------------------------


def _perturbed(truth: CameraFieldTransform, field: Field, kind: str) -> CameraFieldTransform:
    """A deliberately wrong calibration, to show the check catches it."""
    if kind == "shift":  # field solved 15 mm / 10 mm off
        return CameraFieldTransform(truth.R, truth.t - truth.R @ np.array([0.015, 0.010, 0.0]),
                                    "shifted 15/10 mm")
    if kind == "scale":  # field width typed 2% too big at calibration
        from vision_core.field import ReferenceTagFieldTransform as RT
        from vision_core.intrinsics import from_fov

        from .synthetic import SyntheticFieldCamera
        w, h = 1280, 720
        K = from_fov(w, h)
        wrong = {0: (0.0, 0.0), 1: (field.width * 1.02, 0.0),
                 2: (field.width * 1.02, field.height), 3: (0.0, field.height)}
        true_layout = {0: (0.0, 0.0), 1: (field.width, 0.0), 2: (field.width, field.height),
                       3: (0.0, field.height)}
        cam = SyntheticFieldCamera(true_layout, truth, shape=(h, w), K=K)
        dets = pa.Detector(families="tag36h11").detect(cam.read())
        return RT.from_detections(dets, wrong, K, None, 0.08)
    return truth


def run_synthetic(args, field: Field, targets: list[tuple[float, float]]):
    from vision_core.intrinsics import from_fov

    from .calibrate_field import _synthetic_height
    from .synthetic import SyntheticFieldCamera

    w, h = 1280, 720
    K = from_fov(w, h)
    truth = SyntheticFieldTransform.floor(field, height=_synthetic_height(field) * 1.15,
                                          pitch_deg=60.0)
    calib = _perturbed(truth, field, args.synthetic_error)
    print(f"synthetic rig: {truth.source}; calibration under test: "
          f"{'exact' if args.synthetic_error == 'none' else args.synthetic_error}\n")
    measurer = Measurer(K, None, (w, h), calib, field, args.tag_size, 0.0)
    results = []
    for target in targets:
        cam = SyntheticFieldCamera({}, truth, tag_size=args.tag_size, shape=(h, w), K=K,
                                   extra_tags=((9, target[0], target[1], args.heading
                                                if args.heading is not None
                                                else 90.0 * (len(results) % 4)),),
                                   noise_std=3.0)
        sampler = PointSampler(target, args.heading, args.frames, None)
        for _ in range(args.frames * 3):
            if sampler.ready:
                break
            sampler.add(_pick(measurer.measure(cam.read())[1], None))
        if not sampler.ready:
            results.append(_skipped(target))
            continue
        results.append(sampler.result())
    return results, K, (w, h), calib


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-setup", action="store_true",
                    help="skip the on-screen choices and use the flags as given")
    ap.add_argument("--camera", type=int, default=None,
                    help="camera index (default: the one chosen last time, else 0)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--tag-size", type=float, default=None,
                    help="measured black-square size of the tag being placed, metres "
                         "(default: the size used last time, else 0.08)")
    ap.add_argument("--tag-height", type=float, default=0.0,
                    help="metres the tag sits above the field (0 = lying on it)")
    ap.add_argument("--tag-id", type=int, default=None,
                    help="only use this tag id (needed if other tags stay in view)")
    ap.add_argument("--heading", type=float, default=None,
                    help="demand one exact rotation, degrees counter-clockwise from "
                         "upright as printed (top edge towards +Y). Default: any side "
                         "up, edges square to the field; heading error is then how far "
                         "off square it reads")
    ap.add_argument("--points", nargs="+", metavar="X,Y", default=None,
                    help="custom points in metres instead of the 9-point grid")
    ap.add_argument("--inset", type=float, default=0.0,
                    help="pull the grid's edge points this many metres in from the edges")
    ap.add_argument("--frames", type=int, default=FRAMES_PER_POINT,
                    help=f"still frames to average per point (default {FRAMES_PER_POINT})")
    ap.add_argument("--field", type=float, nargs=2, metavar=("W", "H"), default=None,
                    help="field size, metres (default: the size saved with the calibration)")
    ap.add_argument("--out", type=str, default=None,
                    help="report folder (default robocup_testing/calib/verification; "
                         "--synthetic writes nothing unless given)")
    ap.add_argument("--synthetic", action="store_true",
                    help="run the whole check on a digital rig, no camera needed")
    ap.add_argument("--synthetic-error", choices=("none", "shift", "scale"), default="none",
                    help="--synthetic: give it a deliberately bad calibration to catch")
    return ap


def _load_calibration(args) -> tuple[ReferenceTagFieldTransform, Field, list]:
    """The saved field pose, the field in reported coords, and the points to check."""
    pose_path = field_pose_path()
    field = resolve_field(args.field, pose_path)
    transform = ReferenceTagFieldTransform.load(pose_path, use_origin=True)
    if transform.origin is not None:
        field = field.relative_to(transform.origin)
    targets = parse_points(args.points) if args.points else nine_points(field, args.inset)
    return transform, field, targets


def _measure_live(args, transform, field: Field, targets, window: ui.Window | None = None,
                  cap=None):
    """Open the camera and run the points. Returns (results, K, size, lens)."""
    from .calibrate_field import _open_live

    cap, read_bgr, K, dist, lens_note = _open_live(args, cap)
    try:
        first = read_bgr()
        size = (first.shape[1], first.shape[0])
        measurer = Measurer(K, dist, size, transform, field, args.tag_size, args.tag_height)
        results = run_live(args, measurer, read_bgr, targets, window)
    finally:
        cap.release()
        if window is None:
            cv2.destroyAllWindows()
    lens_src, lens_m = _calib_metrics(intrinsics_path())
    return results, K, size, {"source": lens_src or lens_note, "metrics": lens_m}


def _finish(args, results, field: Field, K, size, transform, lens: dict, fld: dict,
            directory: Path | None) -> tuple[dict, Path | None]:
    """Summarize, print, and (unless directory is None) write the report."""
    summary = summarize(results, field, K)
    print_summary(results, summary)
    if directory is None:
        return summary, None
    meta = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "field_m": [field.width, field.height],
        "origin": list(getattr(transform, "origin", None) or (0.0, 0.0)),
        "tag_size_m": args.tag_size,
        "tag_height_m": args.tag_height,
        "heading_deg": args.heading,
        "image_size": list(size),
        "lens": lens,
        "field": fld,
        "calibration": {"lens": lens["source"], "field": fld["source"]},
    }
    report = write_report(directory, results, summary, meta, draw_plot(results, field, summary))
    print(f"\nreport: {report}\n        {directory / 'plot.png'}, {directory / 'latest.json'}, "
          f"history in {directory / 'history.jsonl'}")
    return summary, report


def guided(window: ui.Window | None = None, args=None) -> bool:
    """The whole check on screen: choices, the marks to tape down, the 9 spots,
    the verdict. Returns True if a report was written."""
    args = args or build_parser().parse_args([])
    own = window is None
    window = window or ui.Window("verify-calibration")
    if not field_pose_path().exists():
        ui.message(window, guide.VERIFY_TITLE,
                   ["There is no field calibration to check yet.",
                    "Do step 1 (Camera lens) and step 2 (Find the field) first."], kind="warn")
        if own:
            window.close()
        return False
    camera = guide.remembered("camera", args.camera, 0)
    tag_size = guide.remembered("tag_size", args.tag_size,
                                guide.saved_field_info()["tag_size"] or 0.080)
    options = guide.verify_options(camera, tag_size, args.inset, args.tag_id)
    values = initial_values(options, {"camera": camera, "tag_size": tag_size,
                                      "inset": args.inset, "tag_id": args.tag_id})
    peek = ui.CameraPeek(args.width, args.height)
    try:
        while True:
            pressed, values = ui.form(window, guide.VERIFY_TITLE, guide.VERIFY_INTRO, options,
                                      values, buttons=(("start", "Next"),
                                                       ("back", "Quit" if own else "Back")),
                                      preview=peek.preview)
            if pressed == "back":
                return False
            args.camera, args.tag_size = int(values["camera"]), values["tag_size"]
            args.inset, args.tag_id = values["inset"], values["tag_id"]
            guide.save_choices({"camera": args.camera, "tag_size": args.tag_size})
            transform, field, targets = _load_calibration(args)
            go = ui.message(window, "Before you start: mark 9 spots",
                            guide.marks_lines(targets, args.inset),
                            image=points_map(field, targets, [], (500, 340), coords=True),
                            buttons=(("start", "They're marked -- start"), ("back", "Back")))
            if go == "back":
                continue
            try:
                results, K, size, lens = _measure_live(args, transform, field, targets, window,
                                                       peek.take(args.camera))
            except ui.Cancelled:
                continue
            except (SystemExit, RuntimeError) as e:
                ui.message(window, "The camera didn't open", [str(e.code if isinstance(
                    e, SystemExit) else e), "Pick another camera and try again."], kind="bad")
                continue
            if all(r.skipped for r in results):
                ui.message(window, "Nothing measured", ["Every spot was skipped."], kind="warn")
                continue
            directory = Path(args.out) if args.out else out_dir()
            summary, report = _finish(args, results, field, K, size, transform, lens,
                                      {"source": transform.source,
                                       "metrics": transform.metrics}, directory)
            v = guide.verify_verdict(summary)
            while True:
                choice = ui.message(window, "Accuracy check", v.lines + [
                    f"Full report: {report}"], kind=v.kind, headline=v.headline,
                    image=draw_plot(results, field, summary),
                    buttons=(("done", "Done"), ("open", "Open the full report"),
                             ("again", "Check again")))
                if choice == "open":
                    ui.open_file(report)
                    continue
                break
            if choice == "again":
                continue
            return True
    finally:
        peek.release()
        if own:
            window.close()


def main() -> None:
    args = build_parser().parse_args()
    if not args.synthetic and not args.no_setup:
        guided(args=args)
        return
    if args.tag_size is None:
        args.tag_size = 0.080 if args.synthetic else guide.remembered(
            "tag_size", None, guide.saved_field_info()["tag_size"] or 0.080)

    pose_path = field_pose_path()
    if args.synthetic:
        field = resolve_field(args.field, pose_path)
        targets = parse_points(args.points) if args.points else nine_points(field, args.inset)
        results, K, size, transform = run_synthetic(args, field, targets)
        lens = {"source": "synthetic (exact)", "metrics": None}
        fld = {"source": f"synthetic ({args.synthetic_error})", "metrics": None}
        directory = Path(args.out) if args.out else None
    else:
        if not pose_path.exists():
            raise SystemExit(f"no field calibration at {pose_path} -- run calibrate-field first")
        args.camera = guide.remembered("camera", args.camera, 0)
        transform, field, targets = _load_calibration(args)
        print(f"field {field.width:g} x {field.height:g} m; checking {len(targets)} points:")
        for i, (x, y) in enumerate(targets):
            print(f"  {i + 1}. ({x:g}, {y:g})")
        print(f"put the tag's centre on each mark, {placement(args.heading)}, "
              "and let go.\n")
        results, K, size, lens = _measure_live(args, transform, field, targets)
        fld = {"source": transform.source, "metrics": transform.metrics}
        directory = Path(args.out) if args.out else out_dir()

    _, report = _finish(args, results, field, K, size, transform, lens, fld, directory)
    if report is None:
        print("\n(--synthetic run, nothing saved -- pass --out DIR to write the report)")


if __name__ == "__main__":
    main()
