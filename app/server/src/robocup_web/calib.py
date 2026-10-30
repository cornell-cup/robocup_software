"""Field calibration from the browser: calibrate-field's live steps, driven by
buttons instead of an OpenCV window.

The steps and the maths are calibrate_field's own -- connect to PhotonVision
(photon.py), collect frames that see every reference tag, calibrate_field()
them (or, one tag at a time, CornerSampler and solve_sequential(), as
--sequential does), place the origin (pixel_to_field, with the same
snapping), save field_pose.json -- only the window is new. Overlays are not drawn into the
picture: their pixel coordinates go to the browser, which draws them over the
camera stream, so they stay sharp and clickable.

PhotonVision is talked to from one worker thread only; everything the API
reads goes through the lock.
"""

from __future__ import annotations

import threading
import time
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
    default_field_layout,
    solve_sequential,
)
from tag_tracking.photon import PhotonStream, PhotonTags, blank_frame, open_photon
from vision_core.field import (
    Field,
    ReferenceTagFieldTransform,
    field_pose_path,
    saved_field_size,
    saved_origin,
)

from .procs import CommandError

#: Points per field edge when projecting it: enough to follow the lens's curve.
EDGE_SAMPLES = 32


class FieldCalibration:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tags: PhotonTags | None = None
        self._stream: PhotonStream | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = "disconnected"  # connecting | connected | error
        self.error: str | None = None
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

    # -- PhotonVision -------------------------------------------------------

    def connect(self, opts: dict[str, Any]) -> None:
        with self._lock:
            if self.state in ("connecting", "connected"):
                raise CommandError("already connected -- disconnect first")
            self.state, self.error = "connecting", None
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
            self._tags = self._stream = None
            self._capturing = False
            self.state, self.error = "disconnected", None
            self._dets = []

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
        layout = ({int(k): (float(v[0]), float(v[1])) for k, v in layout.items()}
                  if layout else default_field_layout(field))
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
                           "sequential": sequential}
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
        with self._lock:
            self._result = result
            self._fit_mm = fit_mm
            self._transform = result.transform
            self._field = Field(*setup["field"])
            # A custom origin is a point on the field, not on the camera:
            # keep the saved one across a re-calibration, as track does.
            self._origin = saved_origin(field_pose_path())
            self._result_from = "calibrated"

    def load_saved(self) -> None:
        """The saved pose, to move its origin without calibrating again."""
        path = field_pose_path()
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
        path = field_pose_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        out.save(path)
        return path

    # -- status -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            tags, stream = self._tags, self._stream
            dets = list(self._dets) if time.monotonic() - self._dets_t < 1.0 else []
            out: dict[str, Any] = {
                "state": self.state,
                "error": self.error,
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
        out["saved"] = _saved_summary(field_pose_path())
        return out


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
