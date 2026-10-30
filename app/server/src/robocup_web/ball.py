"""Ball colour profiles from the browser: calibrate-ball's steps, driven by
the page instead of an OpenCV window.

The picture is PhotonVision's camera stream -- the one track-combined finds
the ball in -- so a profile is measured off exactly the colours the tracker
will see (PhotonVision's exposure, white balance and JPEG included), not off a
webcam opened some other way. The maths is ball_color's own: the sampled
patch becomes BallColor.hist via histogram_from_samples, and the preview is
BallColor.probability / mask.

PhotonStream reads on its own thread; the profile is shared with the page's
requests, so everything that touches it holds the lock and renders from a copy.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ball_tracking.ball_color import (
    H_BINS,
    H_RANGE,
    S_BINS,
    BallColor,
    color_file,
    histogram_from_samples,
    load_all,
    save_profile,
    scene_hue_histogram,
)
from tag_tracking.photon import STREAM_PORT, PhotonStream

from .procs import CommandError

#: The mask covering this share of the frame (percent) looks like a ball: less
#: is a ball barely found, more is the profile catching the room too.
GOOD_MASK_PCT = (0.02, 5.0)
VIEWS = ("camera", "overlay", "mask")


class BallCalibration:
    def __init__(self, colors_path: Path | None = None) -> None:
        self._path = colors_path  # None: vision/calib/ball_color.json
        self._lock = threading.Lock()
        self._stream: PhotonStream | None = None
        self.state = "disconnected"  # connecting | connected | error
        self.error: str | None = None
        self.profile = BallColor(name="")
        self._samples: list[np.ndarray] = []
        self._sampled = False  # profile.hist is a real measurement
        self.stats: dict[str, Any] = {}

    # -- the picture ----------------------------------------------------------

    def connect(self, url: str | None, host_hint: str | None = None) -> None:
        url = (url or "").strip() or f"http://{host_hint or '127.0.0.1'}:{STREAM_PORT}/stream.mjpg"
        with self._lock:
            if self.state in ("connecting", "connected"):
                raise CommandError("already connected -- disconnect first")
            self.state, self.error = "connecting", None
        try:
            stream = PhotonStream(url, None)
        except OSError as e:
            with self._lock:
                self.state, self.error = "error", (
                    f"{e}. Is PhotonVision running with its camera enabled? The address "
                    f"is in PhotonVision's camera view; paste it into Stream URL.")
            return
        with self._lock:
            self._stream = stream
            self.state = "connected"

    def disconnect(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            self.state, self.error = "disconnected", None
        if stream is not None:
            stream.stop()

    def _frame(self) -> np.ndarray | None:
        with self._lock:
            stream = self._stream
        if stream is None:
            return None
        if stream.failed:
            with self._lock:
                self.state, self.error = "error", "PhotonVision's stream stopped"
            return None
        return stream.latest()

    def render(self, view: str) -> bytes | None:
        """The newest picture as JPEG: the camera, the camera with the mask
        outlined on it, or the likelihood heatmap (calibrate-ball's right
        half). Also refreshes the mask stats the page shows."""
        frame = self._frame()
        if frame is None:
            return None
        with self._lock:
            profile = replace(self.profile, hist=self.profile.hist.copy())
            sampled = self._sampled
        if not sampled or view == "camera":
            out = frame
        else:
            prob = profile.probability(frame)
            mask = profile.mask(frame, prob)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if view == "mask":
                out = cv2.applyColorMap(profile.likelihood(frame), cv2.COLORMAP_INFERNO)
            else:
                # Dim everything the profile rejects, so what it accepts stands out.
                out = (frame * 0.35).astype(np.uint8)
                out[mask > 0] = frame[mask > 0]
            cv2.drawContours(out, contours, -1, (90, 255, 90), 2)
            pct = 100.0 * cv2.countNonZero(mask) / mask.size
            with self._lock:
                self.stats = {"mask_pct": round(pct, 3), "blobs": len(contours),
                              "looks_right": GOOD_MASK_PCT[0] < pct < GOOD_MASK_PCT[1]}
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    # -- the profile ----------------------------------------------------------

    def new(self, opts: dict[str, Any]) -> None:
        name = _name(opts)
        with self._lock:
            self.profile = BallColor(name=name, radius_m=_radius(opts, 0.020),
                                     note=str(opts.get("note") or ""))
            self._samples, self._sampled = [], False
            self.stats = {}

    def load(self, name: str) -> None:
        profiles = load_all(self._path)
        if name not in profiles:
            raise CommandError(f"no profile {name!r}; have {', '.join(sorted(profiles)) or 'none'}")
        with self._lock:
            self.profile = profiles[name]
            # Its histogram is a measurement, but the pixels behind it are not
            # kept: a new drag starts the sample afresh.
            self._samples, self._sampled = [], True
            self.stats = {}

    def sample(self, opts: dict[str, Any]) -> None:
        """A dragged box (x0, y0, x1, y1 in stream pixels) becomes the sample,
        or with `add` joins it -- calibrate-ball's drag and 'a'."""
        frame = self._frame()
        if frame is None:
            raise CommandError("connect to the camera first")
        try:
            x0, y0, x1, y1 = (int(round(float(v))) for v in opts["box"])
        except (KeyError, TypeError, ValueError):
            raise CommandError("box must be [x0, y0, x1, y1]") from None
        h, w = frame.shape[:2]
        x0, x1 = sorted((max(0, min(x0, w)), max(0, min(x1, w))))
        y0, y1 = sorted((max(0, min(y0, h)), max(0, min(y1, h))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise CommandError("drag a bigger box over the ball")
        patch = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[y0:y1, x0:x1].reshape(-1, 3)
        with self._lock:
            samples = (self._samples + [patch]) if opts.get("add") else [patch]
            self._resample(samples)

    def _resample(self, samples: list[np.ndarray]) -> None:
        """Rebuild the histogram from `samples` (lock held)."""
        try:
            hist = histogram_from_samples(np.vstack(samples), self.profile.s_min)
        except ValueError as e:
            raise CommandError(str(e).replace("--s-min", "the saturation floor")) from None
        self.profile.hist = hist
        self._samples, self._sampled = samples, True

    def clear_sample(self) -> None:
        with self._lock:
            self.profile.hist = np.zeros((H_BINS, S_BINS), np.float32)
            self._samples, self._sampled = [], False
            self.stats = {}

    def tune(self, opts: dict[str, Any]) -> None:
        with self._lock:
            p = self.profile
            if "threshold" in opts:
                p.threshold = int(np.clip(_num(opts, "threshold"), 1, 254))
            if "h_spread" in opts:
                p.h_spread = float(np.clip(_num(opts, "h_spread"), 0.0, H_BINS / 2))
            if "s_spread" in opts:
                p.s_spread = float(np.clip(_num(opts, "s_spread"), 0.0, float(S_BINS)))
            if "radius_mm" in opts:
                p.radius_m = _radius(opts, p.radius_m)
            if "note" in opts:
                p.note = str(opts["note"] or "")
            if "name" in opts:
                p.name = _name(opts)
            if "s_min" in opts:
                old = p.s_min
                p.s_min = int(np.clip(_num(opts, "s_min"), 0, 254))
                if self._samples:  # the floor decides which sampled pixels count
                    try:
                        self._resample(self._samples)
                    except CommandError:
                        p.s_min = old
                        raise

    def save(self) -> Path:
        with self._lock:
            if not self._sampled:
                raise CommandError("nothing sampled yet -- drag a box over the ball first")
            if not self.profile.name:
                raise CommandError("give the profile a name")
            profile = replace(self.profile, hist=self.profile.hist.copy(),
                              created=date.today().isoformat())
        return save_profile(profile, self._path)

    # -- reading ----------------------------------------------------------------

    def scene_hues(self) -> dict[str, Any]:
        """Which hues the room already has: a ball hue is only good where it
        has no competition (calibrate-ball's 'h')."""
        frame = self._frame()
        if frame is None:
            raise CommandError("connect to the camera first")
        counts = scene_hue_histogram(frame)
        width = H_RANGE / H_BINS
        with self._lock:
            peak = self.profile.hue_peak() if self._sampled else None
        return {"bins": [{"h0": round(i * width, 1), "share": round(float(c), 4)}
                         for i, c in enumerate(counts)],
                "ball_hue": peak}

    def status(self) -> dict[str, Any]:
        with self._lock:
            p = self.profile
            stream = self._stream
            out = {
                "state": self.state,
                "error": self.error,
                "stream": None if stream is None else {"url": stream.url, "size": list(stream.size)},
                "profile": {
                    "name": p.name, "radius_mm": round(p.radius_m * 1000, 1), "note": p.note,
                    "threshold": p.threshold, "s_min": p.s_min,
                    "h_spread": p.h_spread, "s_spread": p.s_spread,
                    "sampled": self._sampled,
                    "sample_px": int(sum(len(s) for s in self._samples)),
                    "hue": p.hue_peak() if self._sampled else None,
                },
                "stats": dict(self.stats) if self._sampled else {},
            }
        out["saved"] = profiles_summary(self._path)
        return out


def profiles_summary(path: Path | None = None) -> dict[str, Any]:
    path = path or color_file()
    try:
        profiles = load_all(path)
    except (OSError, ValueError, KeyError) as e:
        return {"path": str(path), "profiles": [], "error": f"unreadable: {e}"}
    return {"path": str(path), "profiles": [
        {"name": p.name, "radius_mm": round(p.radius_m * 1000, 1), "hue": p.hue_peak(),
         "threshold": p.threshold, "s_min": p.s_min, "created": p.created, "note": p.note}
        for p in sorted(profiles.values(), key=lambda p: p.name)
    ]}


def _num(opts: dict[str, Any], key: str) -> float:
    try:
        v = float(opts[key])
    except (KeyError, TypeError, ValueError):
        raise CommandError(f"{key} must be a number") from None
    if not np.isfinite(v):
        raise CommandError(f"{key} must be finite")
    return v


def _radius(opts: dict[str, Any], default_m: float) -> float:
    if opts.get("radius_mm") in (None, ""):
        return default_m
    r = _num(opts, "radius_mm")
    if not 1.0 <= r <= 500.0:
        raise CommandError("ball radius must be 1-500 mm")
    return r / 1000.0


def _name(opts: dict[str, Any]) -> str:
    name = str(opts.get("name") or "").strip()
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise CommandError("profile name: letters, digits, - and _ only")
    return name
