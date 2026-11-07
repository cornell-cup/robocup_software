"""Re-solving a PhotonVision lens calibration from its own snapshots.

Used by the web app's Field calibration page (the lens check in its
PhotonVision panel): review() says how PhotonVision's fit compares with a
fresh one and where the board reached; install() puts the fresh one in.

PhotonVision keeps every calibration's snapshots -- the board corners it found
in each, and where they sit on the board -- in photonvision_config/photon.sqlite.
On the macOS build its solve can come out far worse than the snapshots allow:
7.1 px average error on one camera whose same 72 snapshots solve to 0.8 px with
OpenCV's calibrateCamera, with wild distortion terms (k2 1.1, k3 -3.3) that
throw off every position near the picture's edges. This re-solves them.

The model is the robust one: k1 and k2 only, no k3 and no tangential terms,
square pixels. On snapshots that do not reach the corners of the picture --
the usual case for a camera on the ceiling -- the extra terms fit the middle a
hair better and the edges far worse; on these snapshots they bought 0.01 px.

install() writes the new calibration over the old one for that resolution, in
PhotonVision's database, after copying it to photon.sqlite.bak. PhotonVision
must be stopped: it keeps its configuration in memory and saves it over the
file. The trackers and the web app pick it up on their next connect.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import cv2
import numpy as np

FLAGS = cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_ASPECT_RATIO
#: A re-solve is worth installing when it is at least this much better.
WORTH_IT = 0.8
#: Corners found in a ninth of the picture below which the board "missed" it.
REACHED = 20
REGIONS = [["top-left", "top", "top-right"], ["left", "middle", "right"],
           ["bottom-left", "bottom", "bottom-right"]]


def _rows(db: Path):
    con = sqlite3.connect(db)
    try:
        return con.execute("select unique_name, config_json from cameras").fetchall()
    finally:
        con.close()


def find(db: Path, camera: str | None, res: tuple[int, int] | None):
    """(unique_name, config, calibration index) for the camera's calibration."""
    found = []
    for uid, text in _rows(db):
        cfg = json.loads(text)
        names = {cfg.get("nickname"), cfg.get("uniqueName"), uid,
                 (cfg.get("matchedCameraInfo") or {}).get("name")}
        if camera and camera not in names:
            continue
        for i, c in enumerate(cfg.get("calibrations") or []):
            size = (int(c["resolution"]["width"]), int(c["resolution"]["height"]))
            if res is None or size == res:
                found.append((uid, cfg, i, cfg.get("nickname") or uid, size))
    if not found:
        raise LookupError("no lens calibration in PhotonVision"
                          + (f" for '{camera}'" if camera else "")
                          + (f" at {res[0]}x{res[1]}" if res else ""))
    if len(found) > 1:
        raise LookupError("several lens calibrations match: "
                          + ", ".join(f"{n} {s[0]}x{s[1]}" for *_, n, s in found))
    return found[0]


def solve(cal: dict) -> tuple[dict, float, float]:
    """The calibration re-solved: a new copy in PhotonVision's format, its
    average error, and PhotonVision's own (px)."""
    obs = cal["observations"]
    objs = [np.array([[p["x"], p["y"], p["z"]] for p in o["locationInObjectSpace"]], np.float32)
            for o in obs]
    imgs = [np.array([[p["x"], p["y"]] for p in o["locationInImageSpace"]], np.float32)
            for o in obs]
    old = np.concatenate([[[p["x"], p["y"]] for p in o["reprojectionErrors"]] for o in obs])
    old_rms = float(np.sqrt((old ** 2).sum(1).mean()))
    w, h = int(cal["resolution"]["width"]), int(cal["resolution"]["height"])
    f0 = float(cal["cameraIntrinsics"]["data"][0])
    K0 = np.array([[f0, 0, w / 2], [0, f0, h / 2], [0, 0, 1]], np.float64)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        objs, imgs, (w, h), K0, np.zeros(5), flags=FLAGS | cv2.CALIB_USE_INTRINSIC_GUESS)
    new = json.loads(json.dumps(cal))
    new["cameraIntrinsics"]["data"] = [float(v) for v in K.ravel()]
    n = len(cal["distCoeffs"]["data"])
    d = np.zeros(max(n, 5))
    d[:5] = dist.ravel()[:5]
    new["distCoeffs"]["data"] = [float(v) for v in d[:max(n, 5)]]
    new["distCoeffs"]["cols"] = max(n, 5)
    new["lensmodel"] = "LENSMODEL_OPENCV"
    new["calobjectWarp"] = []
    for o, ob, im, r, t in zip(new["observations"], objs, imgs, rvecs, tvecs):
        proj, _ = cv2.projectPoints(ob, r, t, K, dist)
        err = proj.reshape(-1, 2) - im
        o["reprojectionErrors"] = [{"x": float(x), "y": float(y)} for x, y in err]
        R, _ = cv2.Rodrigues(r)
        q = _quaternion(R)
        o["optimisedCameraToObject"] = {
            "translation": {"x": float(t[0, 0]), "y": float(t[1, 0]), "z": float(t[2, 0])},
            "rotation": {"quaternion": {"W": q[0], "X": q[1], "Y": q[2], "Z": q[3]}},
        }
    return new, float(rms), old_rms


def _quaternion(R: np.ndarray) -> list[float]:
    w = np.sqrt(max(0.0, 1.0 + R[0, 0] + R[1, 1] + R[2, 2])) / 2.0
    x = np.copysign(np.sqrt(max(0.0, 1.0 + R[0, 0] - R[1, 1] - R[2, 2])) / 2.0, R[2, 1] - R[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1.0 - R[0, 0] + R[1, 1] - R[2, 2])) / 2.0, R[0, 2] - R[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1.0 - R[0, 0] - R[1, 1] + R[2, 2])) / 2.0, R[1, 0] - R[0, 1])
    return [float(w), float(x), float(y), float(z)]


def coverage(cal: dict) -> tuple[list[list[int]], list[str]]:
    """Board corners found in each ninth of the picture (top row first), and
    the ninths the board missed -- the usual reason a calibration is weak at
    the edges, where it is only extrapolated."""
    w, h = cal["resolution"]["width"], cal["resolution"]["height"]
    grid = np.zeros((3, 3), int)
    for o in cal["observations"]:
        for p in o["locationInImageSpace"]:
            grid[min(int(p["y"] / h * 3), 2), min(int(p["x"] / w * 3), 2)] += 1
    missed = [REGIONS[r][c] for r in range(3) for c in range(3) if grid[r, c] < REACHED]
    return grid.tolist(), missed


def _summary(cal: dict, rms: float) -> dict:
    K, d = cal["cameraIntrinsics"]["data"], cal["distCoeffs"]["data"]
    return {"rms_px": round(rms, 2), "fx": round(K[0], 1), "fy": round(K[4], 1),
            "cx": round(K[2], 1), "cy": round(K[5], 1),
            "dist": [round(float(v), 3) for v in d[:5]]}


def review(db: Path, camera: str, res: tuple[int, int]) -> dict:
    """PhotonVision's calibration of `camera` at `res` next to a re-solve of
    its snapshots, and where the board reached, for the page."""
    _uid, cfg, i, name, size = find(db, camera, res)
    cal = cfg["calibrations"][i]
    new, rms, old_rms = solve(cal)
    grid, missed = coverage(cal)
    return {"camera": name, "size": list(size), "snapshots": len(cal["observations"]),
            "photonvision": _summary(cal, old_rms), "resolved": _summary(new, rms),
            "better": rms < old_rms * WORTH_IT, "coverage": grid, "missed": missed}


def install(db: Path, camera: str, res: tuple[int, int]) -> dict:
    """Replace PhotonVision's calibration of `camera` at `res` with the re-solve,
    keeping the old database as photon.sqlite.bak. PhotonVision must not be
    running. Returns the new calibration's summary."""
    uid, cfg, i, _name, _size = find(db, camera, res)
    new, rms, _old = solve(cfg["calibrations"][i])
    shutil.copy2(db, db.with_name(db.name + ".bak"))
    cfg["calibrations"][i] = new
    con = sqlite3.connect(db)
    try:
        con.execute("update cameras set config_json = ? where unique_name = ?",
                    (json.dumps(cfg), uid))
        con.commit()
    finally:
        con.close()
    return _summary(new, rms)
