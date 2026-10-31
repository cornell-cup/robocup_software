"""Check a calibration against tape-measured ground truth, in millimetres.

    uv run verify-calibration --tag-size 0.127                  # live, from PhotonVision
    uv run verify-calibration --synthetic                       # no hardware: should PASS
    uv run verify-calibration --synthetic --synthetic-error scale   # ...and catch a bad one

Or from the browser: the web app's Calibration page, "Check the accuracy"
(app/server's calib.py drives the same CheckSession with buttons).

Every number a calibration step prints -- reprojection error, frame spread --
says whether that step fits its *own* data. None of them says whether a
position on the real table comes out right: a mistyped field size, a
calibration tag set a few cm off its corner, a lens calibrated only mid-frame,
or a camera bumped since, all leave them looking fine. This does: one tag is
placed on 9 points measured with a tape measure (corners, edge midpoints,
centre), and each is read back through exactly the pipeline the trackers use
-- PhotonVision's corners, undistorted, pose-solved and rayed onto the field
plane (detect.for_tracking, pose.tag_field_pose) -- and compared.

At each point: put the tag's centre on the mark with its edges square to the
field's edges -- any side up; nothing on a printed tag marks its top -- let go,
and it averages FRAMES_PER_POINT still frames. The heading check is then how
far off square it reads. (--heading D demands one exact rotation: D degrees
counter-clockwise from upright as printed, top edge towards +Y.) In the
window: 'q'/Enter confirms, 'r' redoes the point, 's' skips it, 'b' goes back
one, Esc aborts.

Results go to calib/verification/ (shared by the team, like the rest of
calib/): report.md to read or show, latest.json and plot.png beside it, and
history.jsonl with one summary line per run.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from vision_core.camera import read_key
from vision_core.field import (
    CameraFieldTransform,
    Field,
    ReferenceTagFieldTransform,
    SyntheticFieldTransform,
    field_pose_path,
    resolve_field,
)
from vision_core.paths import calib_dir

from .detect import LocalTagDetector, TagDetection, for_tracking
from .photon import add_photon_args
from .pose import TagFieldPose, tag_field_pose

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
    """The trackers' own per-frame pipeline, on one frame's raw (distorted)
    detections: undistort the corners, solve each tag's pose, ray it onto the
    field (detect.for_tracking, pose.tag_field_pose, as track and
    track-combined call them). Kept in one place so the check measures what
    the trackers report."""

    def __init__(self, K: np.ndarray, dist: np.ndarray | None,
                 transform: CameraFieldTransform, field: Field, tag_size: float,
                 tag_height: float = 0.0) -> None:
        self.K, self.dist, self.transform, self.field = K, dist, transform, field
        self.tag_size, self.tag_height = tag_size, tag_height

    def measure(self, dets: list[TagDetection]) -> list[tuple[TagDetection, TagFieldPose]]:
        """[(undistorted detection, its field pose), ...]."""
        und = for_tracking(dets, self.K, self.dist, self.tag_size)
        return [(d, tag_field_pose(d, self.transform, self.field, K=self.K,
                                   tag_height=self.tag_height)) for d in und]

    def to_pixels(self, pts: list[tuple[float, float]]) -> np.ndarray:
        """Field points (reported coords) -> raw image pixels, through the lens:
        where to draw them over the camera picture."""
        if not pts:
            return np.zeros((0, 2))
        obj = np.array([[x, y, 0.0] for x, y in pts], np.float64)
        dist = np.zeros(5) if self.dist is None else self.dist
        px, _ = cv2.projectPoints(obj, *self.transform.rvec_tvec(), self.K, dist)
        return px.reshape(-1, 2)


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




class CheckSession:
    """One run of the check, apart from any window: run_live() drives it from
    an OpenCV window, the web app (app/server calib.py) from buttons.

    Feed it each frame's raw detections with add(); it samples the current
    point until ready, and confirm() keeps it and moves on. message() says
    what is happening, in words for whoever shows it."""

    def __init__(self, measurer: Measurer, targets: list[tuple[float, float]],
                 frames: int = FRAMES_PER_POINT, heading: float | None = None,
                 tag_id: int | None = None) -> None:
        self.measurer, self.targets = measurer, list(targets)
        self.frames, self.heading, self.tag_id = frames, heading, tag_id
        self.results: list[PointResult] = []
        self.found: list[tuple[TagDetection, TagFieldPose]] = []
        self.hint: str | None = None
        self.visible = 0
        self.live_mm: float | None = None
        self._new_sampler()

    @property
    def index(self) -> int:
        return len(self.results)

    @property
    def done(self) -> bool:
        return self.index >= len(self.targets)

    @property
    def target(self) -> tuple[float, float] | None:
        return None if self.done else self.targets[self.index]

    @property
    def count(self) -> int:
        return 0 if self.sampler is None else len(self.sampler.poses)

    @property
    def ready(self) -> bool:
        return self.sampler is not None and self.sampler.ready

    def _new_sampler(self) -> None:
        prev = next(((r.reported_x, r.reported_y) for r in reversed(self.results[-1:])
                     if not r.skipped), None)
        self.sampler = (None if self.done else
                        PointSampler(self.targets[self.index], self.heading, self.frames, prev))
        self.hint, self.live_mm = None, None

    def add(self, dets: list[TagDetection]) -> None:
        if self.sampler is None:
            return
        self.found = self.measurer.measure(dets)
        picked = _pick(self.found, self.tag_id)
        self.hint = self.sampler.add(picked)
        self.visible = (len(self.found) if self.tag_id is None
                        else sum(d.tag_id == self.tag_id for d, _ in self.found))
        t = self.sampler.target
        self.live_mm = (None if picked is None else
                        math.hypot(picked[1].x - t[0], picked[1].y - t[1]) * 1000.0)

    def message(self) -> str:
        if self.done:
            return "all points done"
        if self.hint is not None:
            return self.hint
        if self.visible > 1:
            return f"{self.visible} tags visible -- show only one, or set the tag id"
        if self.visible == 0:
            return "no tag visible"
        if self.ready:
            return "got it -- confirm and move on"
        return "hold still -- measuring"

    def confirm(self) -> PointResult:
        if not self.ready:
            raise RuntimeError("this point hasn't got enough still frames yet")
        r = self.sampler.result()
        self.results.append(r)
        self._new_sampler()
        return r

    def skip(self) -> None:
        if self.done:
            return
        self.results.append(_skipped(self.targets[self.index]))
        self._new_sampler()

    def redo(self) -> None:
        if self.sampler is not None:
            self.sampler.reset()
        self.hint = None

    def back(self) -> None:
        """Drops the last point, to measure it again."""
        if self.results:
            self.results.pop()
        self._new_sampler()


def load_saved(field_arg: list[float] | None = None, points: list[str] | None = None,
               inset: float = 0.0) -> tuple[ReferenceTagFieldTransform, Field, list]:
    """The saved field calibration (with its origin), the field in the
    coordinates the trackers report, and the points to check."""
    pose_path = field_pose_path()
    if not pose_path.exists():
        raise FileNotFoundError(f"no field calibration at {pose_path} -- calibrate the field "
                                "first")
    field = resolve_field(field_arg, pose_path)
    transform = ReferenceTagFieldTransform.load(pose_path, use_origin=True)
    if transform.origin is not None:
        field = field.relative_to(transform.origin)
    targets = parse_points(points) if points else nine_points(field, inset)
    return transform, field, targets


def finish(results: list[PointResult], field: Field, K: np.ndarray, transform,
           directory: Path | None, *, tag_size: float, tag_height: float = 0.0,
           heading: float | None = None, image_size=(0, 0),
           lens_source: str = "") -> tuple[dict, Path | None]:
    """Summarize, print, and (unless directory is None) write the report.
    Returns (summary, report path or None)."""
    summary = summarize(results, field, K)
    print_summary(results, summary)
    if directory is None:
        return summary, None
    fld = {"source": transform.source, "metrics": getattr(transform, "metrics", None)}
    meta = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "field_m": [field.width, field.height],
        "origin": list(getattr(transform, "origin", None) or (0.0, 0.0)),
        "tag_size_m": tag_size,
        "tag_height_m": tag_height,
        "heading_deg": heading,
        "image_size": list(image_size),
        "lens": {"source": lens_source, "metrics": None},
        "field": fld,
        "calibration": {"lens": lens_source, "field": fld["source"]},
    }
    report = write_report(directory, results, summary, meta, draw_plot(results, field, summary))
    print(f"\nreport: {report}\n        {directory / 'plot.png'}, {directory / 'latest.json'}, "
          f"history in {directory / 'history.jsonl'}")
    return summary, report


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
                f"{'width' if axis == 0 else 'height'} given when calibrating the "
                "field probably does not match the real one. Re-measure it and calibrate "
                "the field again.")
            # Offset measured at the field's own start, with the stretch removed.
            ref = field.x0 if axis == 0 else field.y0
            offset[axis] = intercept + (slope - 1.0) * ref
    offset_mm = offset * 1000.0
    if np.hypot(*offset_mm) > max(0.6 * rms, 5.0):
        hints.append(
            f"Every point is shifted the same way (about {offset_mm[0]:+.0f} mm in x, "
            f"{offset_mm[1]:+.0f} mm in y): the field position or origin is off. Redo "
            "the field calibration, or re-pick the origin.")
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
                "calibration is the weak link -- fix this first. Redo it in PhotonVision "
                "(Cameras > Calibration) with views that take the board into every corner "
                "of the frame.")

    worst = int(np.argmax(mag))
    others = np.delete(mag, worst)
    if mag[worst] > 20.0 and mag[worst] > 2.5 * max(float(np.median(others)), 1.0):
        p = pts[worst]
        hints.append(
            f"One point, ({p.x:g}, {p.y:g}), is much worse than the rest "
            f"({mag[worst]:.0f} mm): if it is a corner, the calibration tag there was "
            "probably misplaced -- or that tape mark is. Check the mark, then redo "
            "the field calibration.")
    return hints


def summarize(points: list[PointResult], field: Field, K: np.ndarray) -> dict:
    pts = [p for p in points if not p.skipped]
    if not pts:
        raise ValueError("no points measured -- nothing to report")
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


def point_grade(p: PointResult) -> str:
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
        col = GRADE_BGR[point_grade(p)]
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

    lens_src = meta["lens"]["source"]
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
        "| Lens (PhotonVision) | Calibrated in PhotonVision's UI | its numbers are there |",
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
    if not fld:
        lines += ["The field calibration has no quality numbers saved with it.", ""]
    lines += [
        f"Lens: `{lens_src}`  ",
        f"Field: `{fld_src}`  ",
        f"Tag size {meta['tag_size_m']} m, tag height {meta['tag_height_m']} m, "
        f"camera {meta['image_size'][0]}×{meta['image_size'][1]}.",
        "",
        "Generated by `uv run verify-calibration` (or the web app's Calibration page); "
        "raw data in `latest.json`, every run in `history.jsonl`.",
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
# Live, in an OpenCV window.
# --------------------------------------------------------------------------


def run_live(session: CheckSession, live) -> None:
    """Walk through the points with PhotonVision (calibrate_field.LiveSource).
    Esc raises SystemExit."""
    win = "verify-calibration"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    font = cv2.FONT_HERSHEY_SIMPLEX
    m = session.measurer
    try:
        while not session.done:
            frame, dets = live.read()
            session.add(dets)
            disp = frame.copy()
            i, target = session.index, session.target
            for d in dets:  # raw pixels, like the picture
                cv2.polylines(disp, [np.asarray(d.corners, np.int32)], True, (0, 165, 255), 2)
            c = tuple(int(v) for v in m.to_pixels([target])[0])
            cv2.drawMarker(disp, c, (255, 0, 255), cv2.MARKER_CROSS, 30, 2)
            cv2.circle(disp, c, 18, (255, 0, 255), 2, cv2.LINE_AA)
            done = [r for r in session.results if not r.skipped]
            for r, p in zip(done, m.to_pixels([(r.x, r.y) for r in done])):
                cv2.circle(disp, tuple(int(v) for v in p), 7, GRADE_BGR[point_grade(r)], -1,
                           cv2.LINE_AA)
            live_txt = "" if session.live_mm is None else f"   reads {session.live_mm:.0f} mm off"
            col = (0, 255, 0) if session.ready else (0, 200, 255)
            cv2.rectangle(disp, (0, 0), (disp.shape[1], 84), (0, 0, 0), -1)
            cv2.putText(disp, f"point {i + 1}/{len(session.targets)}: tag centre on "
                        f"({target[0]:g}, {target[1]:g}), {placement(session.heading)}   "
                        f"{session.count}/{session.frames}{live_txt}",
                        (10, 28), font, 0.55, col, 2, cv2.LINE_AA)
            cv2.putText(disp, f"{session.message()}   |   q/Enter confirm   r redo   s skip   "
                        "b back   Esc abort", (10, 58), font, 0.52, (220, 220, 220), 1, cv2.LINE_AA)
            cv2.imshow(win, disp)
            key = read_key()
            if key == 27:
                raise SystemExit("aborted -- nothing saved")
            if key == ord("r"):
                session.redo()
            if key == ord("b"):
                session.back()
            if key == ord("s"):
                session.skip()
                print(f"  point {i + 1}: ({target[0]:g}, {target[1]:g}) skipped")
            if key in (ord("q"), 13, 10) and session.ready:
                r = session.confirm()
                print(f"  point {i + 1}: ({target[0]:g}, {target[1]:g}) read as "
                      f"({r.reported_x:.3f}, {r.reported_y:.3f}) -> {r.err_mm:.1f} mm")
    finally:
        cv2.destroyWindow(win)


# --------------------------------------------------------------------------
# Synthetic: the same measurement and report, no hardware.
# --------------------------------------------------------------------------


def _perturbed(truth: CameraFieldTransform, field: Field, kind: str) -> CameraFieldTransform:
    """A deliberately wrong calibration, to show the check catches it."""
    if kind == "shift":  # field solved 15 mm / 10 mm off
        return CameraFieldTransform(truth.R, truth.t - truth.R @ np.array([0.015, 0.010, 0.0]),
                                    "shifted 15/10 mm")
    if kind == "scale":  # field width typed 2% too big at calibration
        from vision_core.intrinsics import from_fov

        from .synthetic import SyntheticFieldCamera
        w, h = 1280, 720
        K = from_fov(w, h)
        wrong = {0: (0.0, 0.0), 1: (field.width * 1.02, 0.0),
                 2: (field.width * 1.02, field.height), 3: (0.0, field.height)}
        true_layout = {0: (0.0, 0.0), 1: (field.width, 0.0), 2: (field.width, field.height),
                       3: (0.0, field.height)}
        cam = SyntheticFieldCamera(true_layout, truth, shape=(h, w), K=K)
        dets = LocalTagDetector().detect(cam.read())
        return ReferenceTagFieldTransform.from_detections(dets, wrong, K, None, 0.08)
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
    # The camera rises with the field (_synthetic_height); grow the tag with
    # it, or on a big field it is too small to detect.
    tag = args.tag_size * max(1.0, field.width / 1.2, field.height / 0.8)
    session = CheckSession(Measurer(K, None, calib, field, tag, 0.0), targets,
                           args.frames, args.heading)
    detector = LocalTagDetector()
    while not session.done:
        x, y = session.target
        th = args.heading if args.heading is not None else 90.0 * (session.index % 4)
        cam = SyntheticFieldCamera({}, truth, tag_size=tag, shape=(h, w), K=K,
                                   extra_tags=((9, x, y, th),), noise_std=3.0)
        for _ in range(args.frames * 3):
            if session.ready:
                break
            session.add(detector.detect(cam.read()))
        if session.ready:
            session.confirm()
        else:
            session.skip()
    return session.results, K, (w, h), calib


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag-size", type=float, default=0.080,
                    help="measured black-square size of the tag being placed, metres")
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
                    help="report folder (default vision/calib/verification; "
                         "--synthetic writes nothing unless given)")
    ap.add_argument("--synthetic", action="store_true",
                    help="run the whole check on a digital rig, no hardware needed")
    ap.add_argument("--synthetic-error", choices=("none", "shift", "scale"), default="none",
                    help="--synthetic: give it a deliberately bad calibration to catch")
    add_photon_args(ap)
    args = ap.parse_args()

    if args.synthetic:
        # The made-up rig, not the saved calibration's size: like calibrate-field
        # --synthetic. A big saved field puts the synthetic camera so far up that
        # an 80 mm tag is too small to detect.
        field = resolve_field(args.field, None)
        targets = parse_points(args.points) if args.points else nine_points(field, args.inset)
        results, K, size, transform = run_synthetic(args, field, targets)
        lens_source = "synthetic (exact)"
        directory = Path(args.out) if args.out else None
    else:
        from .calibrate_field import LiveSource

        try:
            transform, field, targets = load_saved(args.field, args.points, args.inset)
        except FileNotFoundError as e:
            raise SystemExit(str(e)) from None
        print(f"field {field.width:g} x {field.height:g} m; checking {len(targets)} points:")
        for i, (x, y) in enumerate(targets):
            print(f"  {i + 1}. ({x:g}, {y:g})")
        print(f"put the tag's centre on each mark, {placement(args.heading)}, "
              "and let go.\n")
        live = LiveSource(args)
        K, size, lens_source = live.K, live.tags.size, live.lens_note
        session = CheckSession(Measurer(K, live.dist, transform, field, args.tag_size,
                                        args.tag_height), targets, args.frames, args.heading,
                               args.tag_id)
        try:
            run_live(session, live)
        finally:
            live.close()
        results = session.results
        directory = Path(args.out) if args.out else out_dir()

    if all(r.skipped for r in results):
        raise SystemExit("no points measured -- nothing to report")
    _, report = finish(results, field, K, transform, directory, tag_size=args.tag_size,
                       tag_height=args.tag_height, heading=args.heading, image_size=size,
                       lens_source=lens_source)
    if report is None:
        print("\n(--synthetic run, nothing saved -- pass --out DIR to write the report)")


if __name__ == "__main__":
    main()
