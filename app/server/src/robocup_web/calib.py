"""Field calibration from the browser: calibrate-field's live steps, driven by
buttons instead of an OpenCV window.

The steps and the maths are calibrate_field's own -- connect to PhotonVision
(photon.py), collect frames that see every reference tag, calibrate_field()
them (or, one tag at a time, CornerSampler and solve_sequential(), as
--sequential does), place the origin (pixel_to_field, with the same
snapping), save field_pose.json -- only the window is new. The accuracy
check after it is verify-calibration's CheckSession, fed the same detections:
one tag on 9 tape-measured spots, each read back in mm. Overlays are not drawn into the
picture: their pixel coordinates go to the browser, which draws them over the
camera stream, so they stay sharp and clickable.

Several cameras over one field (`split` > 1, as calibrate-field --split):
each is calibrated here in turn, against the 4 reference tags around its own
section (`part`), and saved as field_pose_<camera>.json; a new one starts
from the origin the others use, since they must share it. The overlap check
then connects to the other cameras too (same NetworkTables connection) and
shows, live, how far apart each camera puts a tag they can all see -- the
number that says the calibrations agree.

PhotonVision is talked to from one worker thread only, except that the
overlap check's extra cameras are opened from the API thread (it waits for
their lens calibration); everything the API reads goes through the lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
from tag_tracking.calibrate_field import (
    FRAMES_PER_CORNER,
    MAX_LIVE_FRAMES,
    MIN_FRAMES,
    SNAP_PX,
    CornerSampler,
    FieldCalibrationResult,
    SequentialCalibrationResult,
    calibrate_field,
    camera_pose_files,
    default_field_layout,
    shared_origin,
    solve_sequential,
    split_part_layout,
)
from tag_tracking.detect import for_tracking
from tag_tracking.photon import PhotonStream, PhotonTags, blank_frame, open_photon
from tag_tracking.pose import tag_field_pose
from tag_tracking.verify import (
    FRAMES_PER_POINT,
    CheckSession,
    Measurer,
    finish,
    load_saved,
    out_dir,
    placement,
    point_grade,
)
from vision_core.field import (
    Field,
    ReferenceTagFieldTransform,
    field_pose_path,
    saved_camera,
    saved_field_size,
    saved_origin,
)

from .procs import CommandError

#: Points per field edge when projecting it: enough to follow the lens's curve.
EDGE_SAMPLES = 32
#: Most cameras one field may be split between.
MAX_SPLIT = 4
#: Overlap check: a camera's tags older than this (s) no longer count, and
#: each tag's distance between cameras is the median of this many readings.
OVERLAP_FRESH_S = 0.5
OVERLAP_WINDOW = 30
#: Overlap check: an accuracy-check spot this close (px) to the image border
#: is left out of that camera's check -- a tag there is not fully in view.
CHECK_BORDER_PX = 40


class FieldCalibration:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tags: PhotonTags | None = None
        self._stream: PhotonStream | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = "disconnected"  # connecting | connected | error
        self.error: str | None = None
        #: PhotonVision's cameras, when it has several and the page must ask which.
        self.cameras: list[str] | None = None
        self._dets: list = []
        self._dets_t = 0.0
        self._rate = 0.0
        # capture / result
        self._capturing = False
        self._views: list[list] = []
        self._setup: dict[str, Any] | None = None
        self._corners: CornerSampler | None = None  # one tag at a time
        self._corner_status = CornerSampler.NO_TAG
        self._result: FieldCalibrationResult | SequentialCalibrationResult | None = None
        self._fit_mm: list[float | None] | None = None  # one tag: each corner's miss
        self._transform: ReferenceTagFieldTransform | None = None  # corner frame
        self._field: Field | None = None
        self._origin: tuple[float, float] | None = None
        self._result_from = ""  # "calibrated" | "saved"
        # accuracy check
        self._check: CheckSession | None = None
        self._checking = False
        self._check_done: dict[str, Any] | None = None  # summary of the last finished one
        # several cameras: how many share the field, and which section this one covers
        self._split = 1
        self._part = 1
        # overlap check: the other cameras, opened alongside this one
        self._others: list[dict[str, Any]] = []
        self._overlap: dict[str, Any] | None = None  # {"tag_size", "main": {...}, "apart": {id: deque}}

    # -- PhotonVision -------------------------------------------------------

    def connect(self, opts: dict[str, Any]) -> None:
        with self._lock:
            if self.state in ("connecting", "connected"):
                raise CommandError("already connected -- disconnect first")
            self.state, self.error, self.cameras = "connecting", None, None
        res = opts.get("res")
        args = SimpleNamespace(
            photon_camera=(opts.get("camera") or None),
            nt_server=(opts.get("nt_server") or None),
            stream=(opts.get("stream") or None),
            photon_res=(int(res[0]), int(res[1])) if res else None,
            no_stream=False,
        )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(args,), daemon=True)
        self._thread.start()

    def _run(self, args) -> None:
        try:
            tags, stream = open_photon(args, need_stream=False)
        except BaseException as e:  # open_photon exits with its message on failure
            with self._lock:
                self.state, self.error = "error", str(e) or type(e).__name__
                self.cameras = getattr(e, "names", None)
            return
        if self._stop.is_set():  # disconnected while it was still connecting
            if stream is not None:
                stream.stop()
            tags.close()
            return
        with self._lock:
            self._tags, self._stream = tags, stream
            self.state = "connected"
        last = time.monotonic()
        try:
            self._poll(tags, last)
        except Exception as e:  # e.g. photonlibpy refusing a mismatched PhotonVision
            with self._lock:
                self._tags = self._stream = None
                self._capturing = False
                self.state, self.error = "error", f"PhotonVision connection failed: {e}"
        finally:
            if stream is not None:
                stream.stop()
            tags.close()

    def _poll(self, tags: PhotonTags, last: float) -> None:
        while not self._stop.is_set():
            r = tags.poll()
            if r is None:
                time.sleep(0.005)
                continue
            now = time.monotonic()
            with self._lock:
                self._dets = r.detections
                self._dets_t = now
                self._rate = 0.9 * self._rate + 0.1 / max(now - last, 1e-3)
                if self._overlap is not None:
                    self._overlap_step(tags, r.detections, now)
                if self._checking and self._check is not None:
                    self._check.add(r.detections)
                if self._capturing and self._corners is not None:
                    self._corner_status = self._corners.add(r.detections)
                elif self._capturing and self._setup is not None:
                    need = set(self._setup["layout"])
                    if need.issubset(d.tag_id for d in r.detections):
                        self._views.append(r.detections)
                        if len(self._views) >= self._setup["max_frames"]:
                            self._capturing = False
            last = now

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=8.0)
        with self._lock:
            self._overlap, self._others = None, []
            self._tags = self._stream = None
            self._capturing = self._checking = False
            self.state, self.error = "disconnected", None
            self._dets = []

    # -- several cameras ----------------------------------------------------

    def set_split(self, opts: dict[str, Any]) -> None:
        """How many cameras share the field, and which section (1 = the -x
        end) the connected one covers."""
        try:
            split, part = int(opts.get("split") or 1), int(opts.get("part") or 1)
        except (TypeError, ValueError):
            raise CommandError("cameras and section are whole numbers") from None
        if not 1 <= split <= MAX_SPLIT:
            raise CommandError(f"1 to {MAX_SPLIT} cameras")
        if not 1 <= part <= split:
            raise CommandError(f"the section is 1 to {split}")
        with self._lock:
            if self._capturing:
                raise CommandError("stop the capture first")
            if (split, part) != (self._split, self._part):
                # A result solved for another section (or saved under another
                # camera's name) would be saved in the wrong place.
                self._result = self._transform = self._field = None
                self._fit_mm, self._origin = None, None
            self._split, self._part = split, part

    def _pose_path(self, tags: PhotonTags | None = None) -> Path | None:
        """Where this camera's calibration lives: field_pose.json for one
        camera, field_pose_<camera>.json for one of several -- None if that
        needs the camera's name and nothing is connected."""
        if self._split <= 1:
            return field_pose_path()
        tags = tags if tags is not None else self._tags
        return None if tags is None else field_pose_path(tags.name)

    def _target(self) -> Path:
        path = self._pose_path()
        if path is None:
            raise CommandError("connect to the camera first: with several cameras each one's "
                               "calibration is saved under its PhotonVision name")
        return path

    def frame_jpeg(self) -> bytes | None:
        """The newest camera picture, raw (distorted, like the tag corners),
        as JPEG -- or a blank canvas when the stream would not open."""
        with self._lock:
            tags, stream = self._tags, self._stream
        if tags is None:
            return None
        frame = stream.latest() if stream is not None else blank_frame(tags.size)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    # -- capture and solve --------------------------------------------------

    def start_capture(self, opts: dict[str, Any]) -> None:
        tag_size = _positive(opts, "tag_size")
        w, h = _positive(opts, "field_w"), _positive(opts, "field_h")
        field = Field(w, h)
        layout = opts.get("layout")
        if layout:
            layout = {int(k): (float(v[0]), float(v[1])) for k, v in layout.items()}
        elif self._split > 1:
            layout = split_part_layout(field, self._split, self._part)
        else:
            layout = default_field_layout(field)
        if len(layout) < ReferenceTagFieldTransform.MIN_TAGS:
            raise CommandError(f"need at least {ReferenceTagFieldTransform.MIN_TAGS} reference tags")
        min_frames = int(opts.get("min_frames") or MIN_FRAMES)
        sequential = bool(opts.get("sequential"))
        per_corner = int(opts.get("frames_per_corner") or FRAMES_PER_CORNER)
        if not 3 <= per_corner <= 300:
            raise CommandError("frames per corner must be 3-300")
        with self._lock:
            if self.state != "connected":
                raise CommandError("connect to PhotonVision first")
            self._setup = {"tag_size": tag_size, "field": (w, h), "layout": layout,
                           "min_frames": min_frames,
                           "max_frames": max(min_frames, MAX_LIVE_FRAMES),
                           "sequential": sequential,
                           "split": self._split, "part": self._part}
            self._views = []
            self._corners = CornerSampler(layout, per_corner) if sequential else None
            self._corner_status = CornerSampler.NO_TAG
            self._capturing = True

    def stop_capture(self) -> None:
        with self._lock:
            self._capturing = False

    def corner(self, action: str) -> None:
        """One tag at a time: confirm the current corner, retry it, or go
        back to the one before."""
        with self._lock:
            sampler = self._corners
            if sampler is None:
                raise CommandError("start a one-tag-at-a-time capture first")
            if action == "confirm":
                if not sampler.ready:
                    raise CommandError("this corner hasn't got enough steady frames yet")
                sampler.confirm()
                if sampler.done:
                    self._capturing = False
            elif action == "retry":
                sampler.retry()
            elif action == "back":
                sampler.back()
                self._capturing = True
            else:
                raise CommandError("action must be confirm, retry or back")

    def solve(self) -> None:
        with self._lock:
            tags, setup, views = self._tags, self._setup, list(self._views)
            sampler = self._corners
            self._capturing = False
        if tags is None or setup is None:
            raise CommandError("capture frames first")
        try:
            if sampler is not None:
                if not sampler.done:
                    raise CommandError(f"capture every corner first "
                                       f"({sampler.index}/{len(sampler.items)} done)")
                result = solve_sequential(dict(sampler.results), setup["layout"], tags.K,
                                          tags.dist, tag_size=setup["tag_size"])
            else:
                result = calibrate_field(views, setup["layout"], tags.K, tags.dist,
                                         min_frames=setup["min_frames"],
                                         max_frames=setup["max_frames"],
                                         tag_size=setup["tag_size"])
        except (RuntimeError, LookupError) as e:
            raise CommandError(str(e)) from None
        fit_mm = None
        if sampler is not None:
            # The fit in millimetres on the field, which pixels are not: each
            # tag's seen centre, back onto the floor, against where it was put.
            fit_mm = []
            for tid, det in sampler.results.items():
                p = result.transform.pixel_to_field(*det.center, tags.K, tags.dist)
                fit_mm.append(None if p is None else
                              round(float(np.hypot(*(p[:2] - setup["layout"][tid]))) * 1000, 1))
        w, h = tags.size
        result.transform.source += f" ({w}x{h}, {tags.source})"
        result.transform.field_size = setup["field"]
        # A custom origin is a point on the field, not on the camera: keep the
        # saved one across a re-calibration, as track does -- or, for a camera
        # not calibrated before, the one the other cameras already share.
        target = self._pose_path(tags)
        origin = saved_origin(target) if target is not None else None
        if origin is None and self._split > 1:
            origin, _ = shared_origin(target)
        with self._lock:
            self._result = result
            self._fit_mm = fit_mm
            self._transform = result.transform
            self._field = Field(*setup["field"])
            self._origin = origin
            self._result_from = "calibrated"

    def load_saved(self) -> None:
        """The saved pose, to move its origin without calibrating again."""
        path = self._target()
        if not path.exists():
            raise CommandError(f"nothing saved yet at {path}")
        size = saved_field_size(path)
        if size is None:
            raise CommandError(f"{path} has no field size -- calibrate again")
        with self._lock:
            self._result = None
            self._transform = ReferenceTagFieldTransform.load(path, use_origin=False)
            self._field = Field(*size)
            self._origin = saved_origin(path)
            self._result_from = "saved"

    # -- origin -------------------------------------------------------------

    def set_origin(self, opts: dict[str, Any]) -> None:
        with self._lock:
            transform, field, tags = self._transform, self._field, self._tags
        if transform is None or field is None:
            raise CommandError("calibrate (or load the saved calibration) first")
        how = opts.get("mode")
        if how == "corner":
            origin = None
        elif how == "center":
            origin = (field.width / 2, field.height / 2)
        elif how == "field":
            origin = (round(_num(opts, "x"), 3), round(_num(opts, "y"), 3))
        elif how == "pixel":
            if tags is None:
                raise CommandError("connect to PhotonVision to click on the picture")
            u, v = _num(opts, "u"), _num(opts, "v")
            snaps = _snap_points(field)
            px = _project(transform, np.array([[x, y, 0.0] for x, y in snaps]), tags)
            dists = np.linalg.norm(px - (u, v), axis=1)
            if dists.min() < SNAP_PX:
                origin = snaps[int(dists.argmin())]
            else:
                p = transform.pixel_to_field(u, v, tags.K, tags.dist)
                if p is None:
                    raise CommandError("that point is not on the field plane")
                origin = (round(float(p[0]), 3), round(float(p[1]), 3))
        else:
            raise CommandError("mode must be corner, center, field or pixel")
        with self._lock:
            self._origin = None if origin is None else (float(origin[0]), float(origin[1]))

    def save(self) -> Path:
        with self._lock:
            transform, field, origin = self._transform, self._field, self._origin
        if transform is None or field is None:
            raise CommandError("nothing to save -- calibrate first")
        out = transform.with_origin(origin)
        out.field_size = (field.width, field.height)
        path = self._target()
        if self._split > 1 and self._tags is not None:
            out.camera = self._tags.name  # what makes it one camera's of several
        path.parent.mkdir(parents=True, exist_ok=True)
        out.save(path)
        return path

    # -- accuracy check -----------------------------------------------------

    def check_start(self, opts: dict[str, Any]) -> None:
        """Start checking the SAVED calibration against tape-measured spots."""
        tag_size = _positive(opts, "tag_size")
        inset = float(opts.get("inset") or 0.0)
        if not 0.0 <= inset < 1.0:
            raise CommandError("the edge inset must be 0 to 1 m")
        tag_id = opts.get("tag_id")
        tag_id = None if tag_id in (None, "") else int(tag_id)
        if self.state != "connected" or self._tags is None:
            raise CommandError("connect to PhotonVision first")
        try:
            transform, field, targets = load_saved(inset=inset, pose_path=self._target())
        except FileNotFoundError:
            raise CommandError("save a field calibration first -- the check tests the saved "
                               "one") from None
        with self._lock:
            tags = self._tags
            if self.state != "connected" or tags is None:
                raise CommandError("connect to PhotonVision first")
            measurer = Measurer(tags.K, tags.dist, transform, field, tag_size)
            if self._split > 1:
                # One of several cameras: only the spots this one can see.
                w, h = tags.size
                px = measurer.to_pixels(targets)
                b = CHECK_BORDER_PX
                targets = [t for t, (u, v) in zip(targets, px)
                           if b <= u <= w - b and b <= v <= h - b]
                if not targets:
                    raise CommandError("none of the 9 spots is in this camera's view")
            self._check = CheckSession(measurer, targets, FRAMES_PER_POINT, None, tag_id)
            self._capturing = False
            self._checking = True
            self._check_done = None

    def check_action(self, action: str) -> None:
        """confirm (keep this spot, next), redo, skip, back (the previous spot
        again) or stop."""
        with self._lock:
            session = self._check
            if session is None:
                raise CommandError("start the check first")
            if action == "confirm":
                if not session.ready:
                    raise CommandError("this spot hasn't got enough still frames yet")
                session.confirm()
            elif action == "redo":
                session.redo()
            elif action == "skip":
                session.skip()
            elif action == "back":
                session.back()
                self._checking = True
            elif action == "stop":
                self._checking = False
                self._check = None
                return
            else:
                raise CommandError("action must be confirm, redo, skip, back or stop")
            if not session.done:
                return
            self._checking = False
            tags = self._tags
        if all(r.skipped for r in session.results):
            with self._lock:
                self._check = None
            raise CommandError("every spot was skipped -- nothing to report")
        m = session.measurer
        summary, report = finish(
            session.results, m.field, m.K, m.transform, out_dir(), tag_size=m.tag_size,
            image_size=tags.size if tags else (0, 0),
            lens_source=f"{tags.size[0]}x{tags.size[1]}, {tags.source}" if tags else "")
        with self._lock:
            self._check_done = {**summary, "report": str(report),
                                "checked_at": time.time()}

    def check_plot(self) -> Path | None:
        p = out_dir() / "plot.png"
        return p if p.exists() else None

    def _check_status(self, tags: PhotonTags | None) -> dict[str, Any] | None:
        session, done = self._check, self._check_done
        if session is None and done is None:
            return None
        out: dict[str, Any] = {"active": self._checking, "summary": done}
        if session is not None:
            m = session.measurer
            px = m.to_pixels(session.targets) if tags is not None else None
            out.update({
                "index": session.index,
                "total": len(session.targets),
                "targets": [[round(x, 4), round(y, 4)] for x, y in session.targets],
                "targets_px": None if px is None else px.round(1).tolist(),
                "count": session.count,
                "frames": session.frames,
                "ready": session.ready,
                "message": session.message(),
                "live_mm": None if session.live_mm is None else round(session.live_mm, 1),
                "placement": placement(session.heading),
                "field": [m.field.x0, m.field.y0, m.field.width, m.field.height],
                "results": [{
                    "x": r.x, "y": r.y, "skipped": r.skipped,
                    "err_mm": None if r.skipped else round(r.err_mm, 1),
                    "heading_err_deg": None if r.skipped else round(r.heading_err_deg, 2),
                    "grade": point_grade(r),
                } for r in session.results],
            })
        return out

    # -- overlap check (several cameras) ------------------------------------

    def overlap_start(self, opts: dict[str, Any]) -> None:
        """Watch every calibrated camera at once: for each tag two of them
        see, how far apart their saved calibrations put it."""
        tag_size = _positive(opts, "tag_size")
        tag_height = float(opts.get("tag_height") or 0.0)
        if not 0.0 <= tag_height < 2.0:
            raise CommandError("tag height is 0 (on the floor) to 2 m")
        if self._split <= 1:
            raise CommandError("the overlap check is for a field split between cameras")
        with self._lock:
            tags = self._tags
            if self.state != "connected" or tags is None:
                raise CommandError("connect to PhotonVision first")
            if self._capturing or self._checking:
                raise CommandError("finish the capture or the accuracy check first")
        main_path = field_pose_path(tags.name)
        if not main_path.exists():
            raise CommandError(f"save this camera's calibration first ({main_path.name})")
        other_files = [p for p in camera_pose_files() if p.resolve() != main_path.resolve()]
        if not other_files:
            raise CommandError("calibrate another camera first: connect to it, calibrate its "
                               "section and save, then come back to this one")
        main = _overlap_camera(tags.name, tags, main_path)
        others = []
        for p in other_files:
            name = saved_camera(p) or p.stem
            try:
                # The calibration session owns the NetworkTables connection.
                cam = PhotonTags(name, tags.server, None, timeout_s=5.0, start_nt=False)
            except SystemExit as e:
                raise CommandError(f"camera '{name}' ({p.name}) is not answering: {e}") from None
            others.append(_overlap_camera(name, cam, p))
        for o in others:
            if not np.allclose(o["field_size"], main["field_size"], atol=1e-3):
                raise CommandError(f"{o['path'].name} is for a {o['field_size'][0]:g} x "
                                   f"{o['field_size'][1]:g} m field, {main['path'].name} for "
                                   f"{main['field_size'][0]:g} x {main['field_size'][1]:g}: "
                                   f"calibrate both with the same field size")
            oa, ob = main["transform"].origin, o["transform"].origin
            if (oa is None) != (ob is None) or (oa is not None and not np.allclose(oa, ob, atol=1e-3)):
                raise CommandError(f"{o['path'].name} has origin {ob}, {main['path'].name} has "
                                   f"{oa}: set the same origin on both and save")
        with self._lock:
            self._others = others
            self._overlap = {"tag_size": tag_size, "tag_height": tag_height, "main": main,
                             "apart": {}, "positions": {}}

    def overlap_stop(self) -> None:
        with self._lock:
            self._overlap, self._others = None, []

    def _overlap_step(self, tags: PhotonTags, dets: list, now: float) -> None:
        """One result from the connected camera (worker thread, under the
        lock): take the other cameras' newest too, and compare."""
        ov = self._overlap
        assert ov is not None
        ov["main"]["dets"], ov["main"]["t"] = dets, now
        for o in self._others:
            r = o["tags"].poll()
            if r is not None:
                o["dets"], o["t"] = r.detections, now
        seen: dict[int, dict[str, tuple[float, float]]] = {}
        for cam in [ov["main"], *self._others]:
            if now - cam["t"] > OVERLAP_FRESH_S:
                continue
            for d in for_tracking(cam["dets"], cam["K"], cam["dist"], ov["tag_size"]):
                p = tag_field_pose(d, cam["transform"], cam["field"], K=cam["K"],
                                   tag_height=ov["tag_height"])
                seen.setdefault(p.tag_id, {})[cam["name"]] = (round(p.x, 4), round(p.y, 4))
        ov["positions"] = {tid: where for tid, where in seen.items() if len(where) > 1}
        for tid, where in ov["positions"].items():
            pts = list(where.values())
            apart = max(float(np.hypot(a[0] - b[0], a[1] - b[1]))
                        for i, a in enumerate(pts) for b in pts[i + 1:])
            ov["apart"].setdefault(tid, deque(maxlen=OVERLAP_WINDOW)).append(apart)

    def _overlap_status(self) -> dict[str, Any] | None:
        ov = self._overlap
        if ov is None:
            return None
        rows = [{"id": tid, "apart_mm": round(float(np.median(hist)) * 1000, 1), "n": len(hist),
                 "now": {k: list(v) for k, v in ov["positions"].get(tid, {}).items()} or None}
                for tid, hist in sorted(ov["apart"].items())]
        return {
            "cameras": [ov["main"]["name"]] + [o["name"] for o in self._others],
            "live": [o["name"] for o in [ov["main"], *self._others]
                     if time.monotonic() - o["t"] < OVERLAP_FRESH_S],
            "tag_size": ov["tag_size"], "tag_height": ov["tag_height"],
            "tags": rows,
            "worst_mm": max((r["apart_mm"] for r in rows), default=None),
        }

    # -- status -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            tags, stream = self._tags, self._stream
            dets = list(self._dets) if time.monotonic() - self._dets_t < 1.0 else []
            out: dict[str, Any] = {
                "state": self.state,
                "error": self.error,
                "cameras": self.cameras,
                "capturing": self._capturing,
                "captured": len(self._views),
                "setup": None if self._setup is None else {
                    **{k: v for k, v in self._setup.items() if k != "layout"},
                    "layout": {str(k): v for k, v in self._setup["layout"].items()},
                },
                "rate": round(self._rate, 1) if dets else 0.0,
            }
            sampler = self._corners
            if sampler is not None:
                out["corners"] = {
                    "index": sampler.index,
                    "total": len(sampler.items),
                    "count": sampler.count,
                    "per_corner": sampler.frames_per_corner,
                    "ready": sampler.ready,
                    "status": self._corner_status if self._capturing else None,
                    # where the confirmed corners sat in the picture, raw pixels
                    "captured": [np.asarray(d.center).round(1).tolist()
                                 for d in sampler.results.values()],
                }
            transform, field, origin = self._transform, self._field, self._origin
            result, result_from, fit_mm = self._result, self._result_from, self._fit_mm
        if tags is not None:
            out["camera"] = {
                "name": tags.name, "size": list(tags.size), "fx": round(float(tags.K[0, 0]), 1),
                "source": tags.source, "stream": stream.url if stream is not None else None,
                "host": tags.host(),
            }
        out["detections"] = [
            {"id": d.tag_id, "corners": np.asarray(d.corners).round(1).tolist(),
             "center": np.asarray(d.center).round(1).tolist()}
            for d in dets
        ]
        if transform is not None and field is not None:
            frames = isinstance(result, FieldCalibrationResult)
            one_tag = isinstance(result, SequentialCalibrationResult)
            out["result"] = {
                "from": result_from,
                "source": transform.source,
                "field": [field.width, field.height],
                "origin": None if origin is None else list(origin),
                "frames": result.n_frames if frames else None,
                "rotation_spread_deg": round(result.rotation_spread_deg, 3) if frames else None,
                "translation_spread_mm": (round(result.translation_spread_m * 1000, 2)
                                          if frames else None),
                "reproj_px": round(result.reproj_error_px, 2) if one_tag else None,
                "max_reproj_px": round(result.max_reproj_error_px, 2) if one_tag else None,
                "fit_mm": fit_mm if one_tag else None,
                "camera_height_m": round(float(transform.camera_origin_in_field()[2]), 3),
            }
            if tags is not None:
                out["overlay"] = _overlay(transform, field, origin, tags)
        with self._lock:
            check = self._check_status(tags)
            overlap = self._overlap_status()
            split, part = self._split, self._part
        if check is not None:
            out["check"] = _jsonable(check)
        out["split"], out["part"] = split, part
        target = self._pose_path(tags)
        out["saved"] = (_saved_summary(target) if target is not None
                        else {"path": "", "exists": False, "needs_camera": True})
        if split > 1:
            out["saved_cameras"] = [{"name": saved_camera(p), **_saved_summary(p)}
                                    for p in camera_pose_files()]
            fields = {tuple(c["field"]) for c in out["saved_cameras"] if c.get("field")}
            origins = {None if c.get("origin") is None else tuple(c["origin"])
                       for c in out["saved_cameras"] if c.get("exists") and "error" not in c}
            out["cameras_agree"] = {"field": len(fields) <= 1, "origin": len(origins) <= 1}
        if overlap is not None:
            out["overlap"] = _jsonable(overlap)
        return out


def _overlap_camera(name: str, tags: PhotonTags, path: Path) -> dict[str, Any]:
    """One camera of the overlap check: its saved pose, in the coordinates the
    trackers report (origin applied), and its newest detections."""
    transform = ReferenceTagFieldTransform.load(path, use_origin=True)
    size = saved_field_size(path)
    if size is None:
        raise CommandError(f"{path.name} has no field size -- calibrate that camera again")
    return {"name": name, "tags": tags, "path": path, "transform": transform,
            "field": Field(*size).relative_to(transform.origin), "field_size": size,
            "K": tags.K, "dist": tags.dist, "dets": [], "t": 0.0}


# -- geometry for the overlay ------------------------------------------------


def _snap_points(field: Field) -> list[tuple[float, float]]:
    w, h = field.width, field.height
    return [(x, y) for x in (0.0, w / 2, w) for y in (0.0, h / 2, h)]


def _project(transform: ReferenceTagFieldTransform, pts: np.ndarray, tags: PhotonTags) -> np.ndarray:
    """Field points (corner coords) -> raw image pixels, through the lens."""
    px, _ = cv2.projectPoints(np.asarray(pts, np.float64), *transform.rvec_tvec(),
                              tags.K, tags.dist)
    return px.reshape(-1, 2)


def _polyline(transform, tags, a, b) -> list[list[float]]:
    t = np.linspace(0.0, 1.0, EDGE_SAMPLES)[:, None]
    pts = np.hstack([(1 - t) * np.array(a) + t * np.array(b), np.zeros((EDGE_SAMPLES, 1))])
    return _project(transform, pts, tags).round(1).tolist()


def _overlay(transform, field: Field, origin, tags) -> dict[str, Any]:
    """Everything the browser draws over the picture once the pose is known,
    in raw image pixels: the field outline, its centre lines, the snap points
    and where (0, 0) and the axes are."""
    w, h = field.width, field.height
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    outline = [_polyline(transform, tags, corners[i], corners[(i + 1) % 4]) for i in range(4)]
    mid = [_polyline(transform, tags, (w / 2, 0), (w / 2, h)),
           _polyline(transform, tags, (0, h / 2), (w, h / 2))]
    snaps = _project(transform, np.array([[x, y, 0.0] for x, y in _snap_points(field)]), tags)
    o = (0.0, 0.0) if origin is None else origin
    axis = 0.15 * min(w, h)
    axes = _project(transform, np.array([[o[0], o[1], 0.0], [o[0] + axis, o[1], 0.0],
                                         [o[0], o[1] + axis, 0.0]]), tags)
    return {
        "outline": outline,
        "centre_lines": mid,
        "snaps": snaps.round(1).tolist(),
        "origin": axes[0].round(1).tolist(),
        "x_axis": axes[1].round(1).tolist(),
        "y_axis": axes[2].round(1).tolist(),
    }


def _saved_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    try:
        t = ReferenceTagFieldTransform.load(path, use_origin=False)
        size = saved_field_size(path)
        return {"path": str(path), "exists": True, "source": t.source,
                "field": list(size) if size else None,
                "origin": (lambda o: None if o is None else list(o))(saved_origin(path)),
                "modified": path.stat().st_mtime}
    except (OSError, ValueError, KeyError) as e:
        return {"path": str(path), "exists": True, "error": f"unreadable: {e}"}


def _jsonable(v: Any) -> Any:
    """NaN and numpy numbers out, for the JSON the page reads."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def _num(opts: dict[str, Any], key: str) -> float:
    try:
        v = float(opts[key])
    except (KeyError, TypeError, ValueError):
        raise CommandError(f"{key} must be a number") from None
    if not np.isfinite(v):
        raise CommandError(f"{key} must be finite")
    return v


def _positive(opts: dict[str, Any], key: str) -> float:
    v = _num(opts, key)
    if v <= 0:
        raise CommandError(f"{key} must be positive")
    return v
