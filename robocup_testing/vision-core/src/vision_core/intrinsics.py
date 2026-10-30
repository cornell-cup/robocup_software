"""Camera intrinsics: load, save, and a usable guess when there is no calibration.

fx turns apparent size in pixels into metric distance, so it decides whether
"2 metres" means two metres. A guessed fx is survivable while the field is
synthetic and stops being survivable once reference tags locate it -- see the
tag-tracking README.

Everything is keyed on resolution: a matrix for 1280x720 is wrong for 640x480
as it stands. A webcam that delivers the same picture at another size with the
same aspect ratio (1280x960 vs 640x480) is just scaling its sensor, so the
matrix scales with it and the distortion coefficients, which live in
normalised coordinates, carry over unchanged. A different aspect ratio
(4:3 vs 16:9) means the camera cropped, and where it cropped is unknowable from
here -- that still falls back to a guess rather than pretend.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .paths import calib_path

#: Shared across every skill -- it describes the camera, not the task.
DEFAULT_CALIB = "intrinsics.json"

#: What a generic webcam is, absent any better information.
DEFAULT_HFOV_DEG = 60.0


def intrinsics_path() -> Path:
    return calib_path(DEFAULT_CALIB)


def from_fov(width: int, height: int, hfov_deg: float = DEFAULT_HFOV_DEG) -> np.ndarray:
    """A pinhole guess from an assumed horizontal field of view.

    Square pixels, principal point at the centre, no distortion. Good enough to
    get a demo running and honest about being a guess.
    """
    fx = (width / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
    return np.array(
        [[fx, 0.0, width / 2.0], [0.0, fx, height / 2.0], [0.0, 0.0, 1.0]], np.float64
    )


def hfov_of(K: np.ndarray, width: int) -> float:
    """The horizontal field of view a camera matrix implies, in degrees."""
    return float(np.degrees(2.0 * np.arctan((width / 2.0) / K[0, 0])))


def undistort_maps(K: np.ndarray, dist: np.ndarray, width: int, height: int):
    """Precomputed remap tables for cv2.remap, or None when there is no distortion.

    Whole-frame undistortion (as opposed to undistorting one point at a time)
    is worth the setup cost whenever a detector searches the whole image, not
    just a single already-known point -- an AprilTag detector finds corners
    everywhere, so the frame it sees has to be in the same undistorted pinhole
    space the drawn overlay is, or outlines drift off the tags towards the
    frame edges. Built once per K/dist/size; cv2.undistort() rebuilds the maps
    on every call, about 8x slower.
    """
    if not np.any(dist):
        return None
    return cv2.initUndistortRectifyMap(K, dist, None, K, (width, height), cv2.CV_16SC2)


#: How far two aspect ratios may differ and still count as the same picture
#: (covers sizes like 1920x1080 vs 1280x720 rounding, not 4:3 vs 16:9).
ASPECT_TOLERANCE = 0.01


def scale_to(K: np.ndarray, from_w: int, from_h: int, to_w: int, to_h: int) -> np.ndarray:
    """A camera matrix measured at one resolution, rescaled to another.

    Valid only when both are the same picture at a different size (same aspect
    ratio). Uses OpenCV's pixel-centre convention, so the principal point maps
    exactly rather than drifting by half a pixel.
    """
    sx, sy = to_w / from_w, to_h / from_h
    K2 = np.array(K, np.float64, copy=True)
    K2[0, 0] *= sx
    K2[1, 1] *= sy
    K2[0, 2] = (K[0, 2] + 0.5) * sx - 0.5
    K2[1, 2] = (K[1, 2] + 0.5) * sy - 0.5
    return K2


def load(
    width: int, height: int, path: Path | None = None
) -> tuple[np.ndarray, np.ndarray, str]:
    """Real calibration if we have it for this picture, otherwise a guess.

    An exact resolution match is used as-is; the same aspect ratio at another
    size is rescaled (see the module docstring); anything else is a guess.
    Returns (camera_matrix, dist_coeffs, source), where `source` is a short
    human-readable note to put on screen so nobody mistakes a guess for a
    measurement.
    """
    path = path or intrinsics_path()
    if path.exists():
        d = json.loads(path.read_text())
        cw, ch = d.get("width"), d.get("height")
        K = np.array(d["camera_matrix"], np.float64)
        dist = np.array(d.get("dist_coeffs", [0.0] * 5), np.float64).ravel()
        source = d.get("source", str(path))
        if cw == width and ch == height:
            return K, dist, source
        if cw and ch and abs(cw / ch - width / height) <= ASPECT_TOLERANCE * (width / height):
            print(
                f"note: {path} is for {cw}x{ch}, camera is {width}x{height} -- "
                f"same aspect ratio, scaling it"
            )
            return scale_to(K, cw, ch, width, height), dist, f"{source}, scaled from {cw}x{ch}"
        print(
            f"note: {path} is for {cw}x{ch}, camera is {width}x{height} -- different "
            f"aspect ratio (the camera crops), ignoring it; re-run calibrate-camera "
            f"at {width}x{height}"
        )
    return (
        from_fov(width, height, DEFAULT_HFOV_DEG),
        np.zeros(5, np.float64),
        f"assumed {DEFAULT_HFOV_DEG:g} deg HFOV",
    )


def save(
    K: np.ndarray,
    width: int,
    height: int,
    source: str,
    dist: np.ndarray | None = None,
    path: Path | None = None,
    metrics: dict | None = None,
) -> Path:
    """Write a camera matrix to calib/, creating the directory if needed.

    `metrics` (calibrate-camera's quality numbers: reprojection error, views,
    frame coverage, when) is stored under "metrics" so the file says how good
    it is."""
    path = path or intrinsics_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    dist = np.zeros(5, np.float64) if dist is None else np.asarray(dist, np.float64)
    doc = {
        "width": int(width),
        "height": int(height),
        "camera_matrix": np.asarray(K, np.float64).tolist(),
        "dist_coeffs": dist.ravel().tolist(),
        "source": source,
    }
    if metrics is not None:
        doc["metrics"] = metrics
    path.write_text(json.dumps(doc, indent=2))
    return path
