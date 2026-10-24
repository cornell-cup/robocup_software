"""AprilTag detections -> field coordinates.

This is the tag-shaped half of what used to be field.py. It lives in the skill,
not in vision-core, because it is about reading AprilTags: the detector's frame
conventions, the tag's heading, the record a tag tracker emits. The geometry it
stands on -- where the field is at all -- is vision-core's problem.

Downstream of the swap point: nothing here may care which CameraFieldTransform
it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from vision_core.field import CameraFieldTransform, Field

# --------------------------------------------------------------------------
# Output contract. Downstream code reads these fields; keep them stable.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TagFieldPose:
    """Where one tag is, in field coordinates."""

    tag_id: int
    x: float  # metres from the origin corner, along field +X
    y: float  # metres from the origin corner, along field +Y
    theta_deg: float  # rotation about the field normal, -180..180
    off_plane_m: float  # signed distance off the field plane (along field +Z)
    inside: bool  # within the rectangle in both x and y
    decision_margin: float  # detector confidence, higher is better

    def as_tuple(self) -> tuple[int, float, float, float]:
        """The frozen public format: (id, x, y, theta_deg)."""
        return (self.tag_id, self.x, self.y, self.theta_deg)


#: Which axis of the detector's tag frame points to the tag's visual right --
#: the direction that runs left-to-right across an upright tag as you look at it.
#:
#: pupil_apriltags reports pose_R with the tag's +X axis pointing to the tag's
#: visual LEFT, +Y up and +Z into the tag (away from the viewer). That is a 180
#: degree rotation about the tag normal from the convention you might expect, so
#: visual-right is -X. Established empirically; selfcheck.py fails loudly if it
#: ever changes.
TAG_VISUAL_RIGHT = np.array([-1.0, 0.0, 0.0])


#: Frame width from which tag outlines are found on a half-size image.
DECIMATE_FROM_WIDTH = 1200


def auto_tag_decimate(frame_width: int) -> float:
    """pupil_apriltags quad_decimate for a frame this wide. Halving a 1280-wide
    frame is ~4x faster with the same accuracy (corners are refined at full
    resolution either way). Below that detection is already a few ms, and a
    far tag can be under 20 px, where halving loses it -- an 80 mm tag on a
    3 m field at 800x600 was missed in a third of frames at decimate 2, none at 1.
    """
    return 2.0 if frame_width >= DECIMATE_FROM_WIDTH else 1.0


def _corner_right_on_field(detection, transform: CameraFieldTransform, K: np.ndarray):
    """The tag's visual-right direction on the field plane, from its corners.

    Edge midpoint of corners 0+3 minus that of corners 1+2: the same direction
    as pose_R @ TAG_VISUAL_RIGHT, established the same way (empirically, against
    synthetic tags at known headings); selfcheck.py fails loudly if it ever
    changes. None if a corner's ray misses the plane.
    """
    pts = [transform.pixel_to_field(float(u), float(v), K) for u, v in detection.corners]
    if any(p is None for p in pts):
        return None
    c = np.asarray(pts)[:, :2]
    return (c[0] + c[3]) / 2.0 - (c[1] + c[2]) / 2.0


def _ray_to_plane(u: float, v: float, transform: CameraFieldTransform, K: np.ndarray,
                  height: float):
    """Field (x, y) where pixel (u, v)'s ray meets the plane `height` metres
    above the field (along +Z), or None if it never does in front of the camera.
    Pixels are already undistorted, as for _corner_right_on_field."""
    ray = transform.direction_to_field(
        np.linalg.inv(K) @ np.array([u, v, 1.0]))[0]
    cam = transform.camera_origin_in_field()
    if abs(ray[2]) < 1e-9:
        return None
    s = (height - cam[2]) / ray[2]
    if s <= 0:
        return None
    p = cam + s * ray
    return float(p[0]), float(p[1])


def tag_field_pose(
    detection,
    transform: CameraFieldTransform,
    field: Field,
    heading_offset_deg: float = 0.0,
    K: np.ndarray | None = None,
    tag_height: float = 0.0,
) -> TagFieldPose:
    """Convert one pose-estimated AprilTag detection into field coordinates.

    `detection` must come from Detector.detect(..., estimate_tag_pose=True), so
    that it carries pose_R (tag->camera rotation) and pose_t (the tag centre in
    camera coords, metres).

    theta is 0 when the tag is upright, and increases counter-clockwise as seen
    from in front of the field. `heading_offset_deg` is added to it, for when the
    tag is mounted on a robot that does not face the same way as the tag's right
    edge -- a robot with the tag rotated 90 degrees on its shell wants -90 here.

    Pass `K` (the matrix the detector's pixels are in -- frames are undistorted
    before detection, so no distortion here) to take the heading from the tag's
    corners on the field plane instead of from pose_R. A small or distant tag's
    pose_R often lands on the wrong one of the two planar-pose solutions: the
    position stays right, but the tag looks tilted and the heading is off by
    tens of degrees. The corners have no such ambiguity, and projecting them onto
    the field plane keeps directions exactly even when the tag rides above it,
    since the tag lies parallel to the field.

    `K` also moves the position onto the field plane: x, y are where the ray
    through the tag's centre pixel meets the plane `tag_height` metres above
    the field (0 for a tag lying on it; the shell height for one on a robot).
    pose_t is ranged from the tag's apparent size, so a --tag-size or fx a few
    percent off slides every position towards or away from the camera -- about
    10 cm at the corners of a 3 m field for 5 mm of tag size. The ray depends
    only on the calibrated pose, so a tag set where a calibration tag stood
    reads back that corner. off_plane_m stays pose_t's: its distance from the
    plane is the tell-tale for exactly that size/fx mismatch.
    """
    centre_cam = np.asarray(detection.pose_t, dtype=np.float64).reshape(3)
    x, y, z = transform.camera_to_field(centre_cam)[0]
    if K is not None:
        on_plane = _ray_to_plane(float(detection.center[0]), float(detection.center[1]),
                                 transform, K, tag_height)
        if on_plane is not None:
            x, y = on_plane
            z -= tag_height

    # Heading: take the direction that points right across the upright tag,
    # express it in the field frame, and measure its angle about the field normal.
    right = _corner_right_on_field(detection, transform, K) if K is not None else None
    if right is not None:
        dx, dy = right
    else:
        pose_R = np.asarray(detection.pose_R, dtype=np.float64).reshape(3, 3)
        forward_cam = pose_R @ TAG_VISUAL_RIGHT
        # dx, dy are the in-plane components of the direction the tag FACES -- not a
        # velocity. Nothing here tracks motion between frames.
        dx, dy, _ = transform.direction_to_field(forward_cam)[0]
    theta = float(np.degrees(np.arctan2(dy, dx)) + heading_offset_deg)
    theta = (theta + 180.0) % 360.0 - 180.0  # wrap to (-180, 180]

    return TagFieldPose(
        tag_id=int(detection.tag_id),
        x=float(x),
        y=float(y),
        theta_deg=theta,
        off_plane_m=float(z),
        inside=field.contains(float(x), float(y)),
        decision_margin=float(detection.decision_margin),
    )
