"""AprilTag detections, whichever detector they came from.

Live, the detector is PhotonVision's (photon.py): it owns the camera and sends
each tag's id and four corners over NetworkTables. Frames PhotonVision never
sees -- selfcheck's renders, the synthetic camera -- go through LocalTagDetector
here instead, which is WPILib's AprilTagDetector: the same detector
PhotonVision runs, so both paths hand out corners in the same order.

Either way a detection starts as pixels only. The 3D pose is solved here, from
the corners, by with_pose(): PhotonVision's own pose would need its 3D mode and
its axis conventions, and is solved on the distorted corners; this one is
solved in the same undistorted pinhole space the rest of the tracker works in,
in the convention pose.py was built against.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

TAG_FAMILY = "tag36h11"


@dataclass(frozen=True)
class TagDetection:
    """One tag in one image. `corners` keep the detector's order, which
    pose.py's heading and the field calibration both depend on."""

    tag_id: int
    center: np.ndarray  # (2,) px
    corners: np.ndarray  # (4, 2) px
    #: Detector confidence, higher is better. NaN from PhotonVision, which
    #: filters on it itself (the pipeline's decision-margin cutoff) and does
    #: not send it on.
    decision_margin: float = float("nan")
    #: Tag -> camera rotation and the tag centre in camera coords (metres).
    #: None until with_pose().
    pose_R: np.ndarray | None = None
    pose_t: np.ndarray | None = None


def diagonal_centre(corners: np.ndarray) -> np.ndarray:
    """Where the tag's diagonals cross: the image of its centre under any
    perspective, which is how the detector defines it too."""
    c = np.asarray(corners, np.float64).reshape(4, 2)
    p, r = c[0], c[2] - c[0]
    q, s = c[1], c[3] - c[1]
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-12:
        return c.mean(axis=0)
    u = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / denom
    return p + u * r


def tag_object_points(tag_size: float) -> np.ndarray:
    """The tag's corners in its own frame, in the detector's corner order.

    The frame is the one pupil_apriltags reported pose_R in, which is what
    pose.TAG_VISUAL_RIGHT was established against; selfcheck fails loudly if
    the two ever stop agreeing.
    """
    h = tag_size / 2.0
    return np.array([[-h, h, 0.0], [h, h, 0.0], [h, -h, 0.0], [-h, -h, 0.0]])


def with_pose(det: TagDetection, K: np.ndarray, tag_size: float) -> TagDetection:
    """Solve the tag's pose from its corners. `det` must be in the undistorted
    pinhole space of K (see undistort()). Corners no pose fits (a degenerate
    quad) come back without one: pose_R/pose_t stay None."""
    try:
        ok, rvec, tvec = cv2.solvePnP(
            tag_object_points(tag_size), np.asarray(det.corners, np.float64), K, None,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
    except cv2.error:
        return det
    if not ok:
        return det
    R, _ = cv2.Rodrigues(rvec)
    return replace(det, pose_R=R, pose_t=tvec.reshape(3, 1))


def undistort(dets: list[TagDetection], K: np.ndarray, dist: np.ndarray | None) -> list[TagDetection]:
    """Raw pixels -> the undistorted pinhole space of K: the same space a frame
    remapped with intrinsics.undistort_maps() is in, so overlays still line up."""
    if dist is None or not np.any(dist) or not dets:
        return list(dets)
    pts = np.concatenate([np.vstack([d.corners, d.center]) for d in dets]).astype(np.float64)
    und = cv2.undistortPoints(pts.reshape(-1, 1, 2), K, dist, P=K).reshape(-1, 5, 2)
    return [replace(d, corners=u[:4], center=u[4]) for d, u in zip(dets, und)]


def for_tracking(
    dets: list[TagDetection], K: np.ndarray, dist: np.ndarray | None, tag_size: float
) -> list[TagDetection]:
    """Raw detections -> undistorted and pose-solved, ready for tag_field_pose().
    A tag no pose fits is dropped: tag_field_pose() cannot place it."""
    solved = (with_pose(d, K, tag_size) for d in undistort(dets, K, dist))
    return [d for d in solved if d.pose_t is not None]


class LocalTagDetector:
    """WPILib's AprilTagDetector on a greyscale frame -- PhotonVision's
    detector, run in-process, for the frames that never reach PhotonVision."""

    def __init__(self, quad_decimate: float = 1.0, threads: int = 2) -> None:
        import robotpy_apriltag as ra

        self._det = ra.AprilTagDetector()
        self._det.addFamily(TAG_FAMILY)
        cfg = self._det.getConfig()
        cfg.quadDecimate = float(quad_decimate)
        cfg.numThreads = int(threads)
        self._det.setConfig(cfg)
        self._buf = (0.0,) * 8

    def detect(self, grey: np.ndarray) -> list[TagDetection]:
        if grey.ndim != 2:
            grey = cv2.cvtColor(grey, cv2.COLOR_BGR2GRAY)
        out = []
        for d in self._det.detect(np.ascontiguousarray(grey)):
            c = d.getCenter()
            out.append(TagDetection(
                tag_id=int(d.getId()),
                center=np.array([c.x, c.y], np.float64),
                corners=np.array(d.getCorners(self._buf), np.float64).reshape(4, 2),
                decision_margin=float(d.getDecisionMargin()),
            ))
        return out
