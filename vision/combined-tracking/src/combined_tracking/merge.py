"""Several cameras, one picture of the field: merging their tracks.

Each camera runs its own TagTracker and BallTracker, on its own clock, against
its own field pose (calibrated to the *same* field frame). This turns their
per-camera states into one set -- what a single camera covering the whole
field would have reported -- so nothing downstream (the state doc, the sim,
strategy) knows there was more than one.

The method is a weighted sum, as in TIGERs Mannheim's vision filter
(RobotTracker.mergeRobotTrackers in their AutoReferee): every camera's estimate
is first predicted to one common time, then averaged with weight 1/sigma^2,
sigma being how much that camera's estimate is to be trusted right now:

  - how far the point is from the camera, in metres per pixel there (a tag
    further away covers fewer pixels, so each pixel of noise is more metres);
  - how far it is from that camera's image centre (lens edges are the worst
    calibrated and the most distorted);
  - how long since that camera last actually saw it (a coasting track drifts).

On top of that, each weight is feathered to almost nothing near that camera's
image border (`taper`), the way image stitching blends overlapping photos. A
camera stops seeing a tag while the tag is still about half a tag inside its
picture; by then its weight has already faded out, so losing it moves nothing.
A track a camera has only just picked up is likewise ramped in over
RAMP_IN_S. Together the hand-off between cameras is a blend, not a switch.

What this cannot do is make two cameras agree. If their calibrations put the
same tag 3 cm apart, the merged position still slides 3 cm across the overlap,
smoothly. `disagreement` reports exactly that, per tag seen by several
cameras: it is the overlap check, live.

With one camera everything is passed through untouched.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np
from ball_tracking.ball import BallFieldState
from tag_tracking.filter import SPEED_EPS, TagFieldState
from vision_core.field import CameraFieldTransform, Field

#: Pixel noise of a tag or ball centre, px. Sets the scale of sigma only; the
#: weights depend on ratios, so this mostly matters next to STALE_SPEED.
PX_NOISE = 0.5
#: sigma grows by (1 + EDGE_GAIN * r^2), r = 0 at the principal point and 1 at
#: the furthest image corner: 4x worse at the corner than the centre.
EDGE_GAIN = 3.0
#: A coasting estimate's sigma grows by this much per second unseen, m/s.
STALE_SPEED = 0.5
#: Exponent on sigma. 2 is inverse-variance weighting; TIGERs use 1.5 and note
#: that higher values "lead to more jitter" (the best camera dominates harder).
MERGE_POWER = 2.0
#: A camera's track that is this new (s) counts for proportionally less.
RAMP_IN_S = 0.3
#: sigma for a point behind a camera or otherwise unprojectable, m.
FAR_SIGMA = 10.0
#: Feathering: a camera's weight is at its floor while the object is within
#: its own half-size (+ EDGE_DEAD_PX) of the image border -- where it is about
#: to be lost anyway -- and rises smoothly to full over EDGE_FADE_PX further in.
EDGE_DEAD_PX = 4.0
EDGE_FADE_PX = 120.0
#: The feathered weight never quite reaches 0, so an object only near edges
#: still gets a defined (if edge-weighted) answer.
TAPER_FLOOR = 1e-3

#: Ball: the merged ball accepts per-camera candidates within this radius of
#: where it was (m), growing with time unseen at BALL_MAX_SPEED (m/s).
BALL_MIN_GATE = 0.15
BALL_MAX_SPEED = 6.0


@dataclass(frozen=True)
class CameraGeometry:
    """What the weighting needs to know about one camera."""

    name: str
    transform: CameraFieldTransform
    K: np.ndarray  # pinhole matrix of the (undistorted) pixels
    size: tuple[int, int]  # (width, height), px

    def project(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float] | None:
        """Field point (x, y, z) -> (u, v, depth) in this camera, or None if
        it is behind the camera."""
        p = self.transform.field_to_camera(np.array([[x, y, z]], np.float64))[0]
        if p[2] <= 1e-6:
            return None
        # Plain floats: numpy ones would leak numpy bools into the state doc
        # (via Field.contains), which json cannot encode.
        return (float(self.K[0, 0] * p[0] / p[2] + self.K[0, 2]),
                float(self.K[1, 1] * p[1] / p[2] + self.K[1, 2]), float(p[2]))

    def sigma_m(self, x: float, y: float, z: float = 0.0) -> float:
        """How uncertain this camera's reading of field point (x, y, z) is, m."""
        uvd = self.project(x, y, z)
        if uvd is None:
            return FAR_SIGMA
        u, v, depth = uvd
        cx, cy = self.K[0, 2], self.K[1, 2]
        w, h = self.size
        cx, cy = float(cx), float(cy)
        corner = math.hypot(max(cx, w - cx), max(cy, h - cy))
        r = math.hypot(u - cx, v - cy) / corner  # > 1 once outside the image
        metres_per_px = depth / float(self.K[0, 0])
        return PX_NOISE * metres_per_px * (1.0 + EDGE_GAIN * r * r)

    def border_px(self, x: float, y: float, z: float = 0.0) -> float:
        """How far inside this camera's picture field point (x, y, z) lands,
        px, to the nearest edge: negative outside it, -inf behind the camera."""
        uvd = self.project(x, y, z)
        if uvd is None:
            return -math.inf
        u, v, _ = uvd
        w, h = self.size
        return min(u, v, w - u, h - v)

    def half_size_px(self, x: float, y: float, z: float, half_size_m: float) -> float:
        """An object `half_size_m` across (half-width) at that point, in px."""
        uvd = self.project(x, y, z)
        return 0.0 if uvd is None else float(self.K[0, 0]) * half_size_m / uvd[2]

    def taper(self, x: float, y: float, z: float = 0.0, half_size_m: float = 0.0) -> float:
        """The feathering factor, TAPER_FLOOR..1: at its floor while an object
        of half-size `half_size_m` there would be cut by the image border,
        rising smoothly (smoothstep) to 1 over EDGE_FADE_PX further in."""
        dead = self.half_size_px(x, y, z, half_size_m) + EDGE_DEAD_PX
        s = (self.border_px(x, y, z) - dead) / EDGE_FADE_PX
        s = min(1.0, max(0.0, s))
        return max(TAPER_FLOOR, s * s * (3.0 - 2.0 * s))

    def sees(self, x: float, y: float, z: float = 0.0, half_size_m: float = 0.0) -> bool:
        """Whether an object of half-size `half_size_m` at (x, y, z) lies
        wholly inside this camera's picture."""
        return self.border_px(x, y, z) > self.half_size_px(x, y, z, half_size_m)


@dataclass(frozen=True)
class CameraTags:
    """One camera's tag tracks, and the wall-clock instant they are for."""

    camera: CameraGeometry
    states: list[TagFieldState]
    t: float | None  # time.time() of the result they came from; None before any


@dataclass(frozen=True)
class CameraBall:
    """One camera's ball, and the wall-clock instant it is for."""

    camera: CameraGeometry
    state: BallFieldState | None
    t: float | None


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _weight(sigma: float) -> float:
    return float(max(sigma, 1e-6)) ** -MERGE_POWER


def common_time(times: list[float | None]) -> float | None:
    """The instant to merge at: the newest of the cameras' own."""
    known = [t for t in times if t is not None]
    return max(known) if known else None


class TagMerger:
    """Per-camera tag tracks in, one track per tag id out.

    Stateful only for the ramp-in: it remembers when each camera first had
    each tag, so a freshly acquired track blends in instead of jumping in.
    """

    def __init__(self, field: Field, tag_height: float = 0.0, tag_size: float = 0.08) -> None:
        self.field = field
        self.tag_height = float(tag_height)
        #: half the tag's diagonal: how far its corners reach from its centre,
        #: whichever way it is turned -- the margin it needs to be detected
        self.tag_reach = float(tag_size) / math.sqrt(2.0)
        self._first_seen: dict[tuple[str, int], float] = {}
        #: tag id -> metres between the cameras' readings, for every tag that
        #: more than one camera sees right now. The last merge's.
        self.disagreement: dict[int, float] = {}

    def reset(self) -> None:
        self._first_seen.clear()
        self.disagreement = {}

    def merge(self, cams: list[CameraTags]) -> tuple[list[TagFieldState], float | None]:
        """(merged states sorted by id, the time.time() they are for)."""
        if len(cams) == 1:
            self.disagreement = {}
            return cams[0].states, cams[0].t
        t_out = common_time([c.t for c in cams])
        live: set[tuple[str, int]] = set()
        by_id: dict[int, list[tuple[float, TagFieldState, CameraGeometry]]] = {}
        for c in cams:
            if c.t is None:
                continue
            dt = max(0.0, (t_out or c.t) - c.t)
            for s in c.states:
                key = (c.camera.name, s.tag_id)
                live.add(key)
                first = self._first_seen.setdefault(key, c.t)
                s = _predict_tag(s, dt)
                z = self.tag_height
                sigma = math.hypot(c.camera.sigma_m(s.x, s.y, z), STALE_SPEED * s.age)
                ramp = min(1.0, max(0.05, (c.t - first) / RAMP_IN_S)) if RAMP_IN_S > 0 else 1.0
                taper = c.camera.taper(s.x, s.y, z, self.tag_reach)
                by_id.setdefault(s.tag_id, []).append(
                    (_weight(sigma) * ramp * taper, s, c.camera))
        for key in [k for k in self._first_seen if k not in live]:
            del self._first_seen[key]

        self.disagreement = {}
        out: list[TagFieldState] = []
        for tid in sorted(by_id):
            entries = by_id[tid]
            seen = [s for _w, s, _c in entries if s.visible]
            if len(seen) > 1:
                self.disagreement[tid] = max(
                    math.hypot(a.x - b.x, a.y - b.y)
                    for i, a in enumerate(seen) for b in seen[i + 1:])
            out.append(_blend_tags(entries, self.field))
        return out, t_out


def _predict_tag(s: TagFieldState, dt: float) -> TagFieldState:
    """`s` carried forward `dt` seconds on its own velocity and turn rate."""
    if dt <= 0.0:
        return s
    return dataclasses.replace(
        s, x=s.x + s.vx * dt, y=s.y + s.vy * dt,
        theta_deg=_wrap_deg(s.theta_deg + s.omega_deg * dt), age=s.age + dt)


def _blend_tags(entries: list[tuple[float, TagFieldState, CameraGeometry]],
                field: Field) -> TagFieldState:
    if len(entries) == 1:
        s = entries[0][1]
        return dataclasses.replace(s, inside=field.contains(s.x, s.y))
    total = sum(w for w, _s, _c in entries)
    best = max(entries, key=lambda e: e[0])[1]
    x = float(sum(w * s.x for w, s, _c in entries) / total)
    y = float(sum(w * s.y for w, s, _c in entries) / total)
    vx = float(sum(w * s.vx for w, s, _c in entries) / total)
    vy = float(sum(w * s.vy for w, s, _c in entries) / total)
    omega = sum(w * s.omega_deg for w, s, _c in entries) / total
    # Headings wrap, so average them as offsets from one of them (as TIGERs
    # do): 179 and -179 then average to 180, not 0.
    ref = best.theta_deg
    theta = _wrap_deg(ref + sum(w * _wrap_deg(s.theta_deg - ref) for w, s, _c in entries) / total)
    speed = math.hypot(vx, vy)
    return TagFieldState(
        tag_id=best.tag_id, x=x, y=y, theta_deg=theta, vx=vx, vy=vy, speed=speed,
        direction_deg=math.degrees(math.atan2(vy, vx)) if speed > SPEED_EPS else 0.0,
        omega_deg=omega, inside=field.contains(x, y),
        visible=any(s.visible for _w, s, _c in entries),
        age=min(s.age for _w, s, _c in entries),
        off_plane_m=best.off_plane_m,
    )


class BallMerger:
    """Per-camera balls in, one ball out.

    A ball has no id, so first decide which per-camera balls are the same,
    real one (as TIGERs' BallTrackerMerger does): those within a search
    radius of where the merged ball last was, the radius growing with the
    time since it was last seen. Nothing in it and the ball is re-found from
    scratch, around whichever camera trusts its own ball most.
    """

    def __init__(self, field: Field) -> None:
        self.field = field
        self._last: BallFieldState | None = None
        self._last_seen_t: float | None = None

    def reset(self) -> None:
        self._last, self._last_seen_t = None, None

    def merge(self, cams: list[CameraBall]) -> tuple[BallFieldState | None, float | None]:
        if len(cams) == 1:
            return cams[0].state, cams[0].t
        t_out = common_time([c.t for c in cams if c.state is not None])
        cands: list[tuple[float, BallFieldState]] = []
        for c in cams:
            s = c.state
            if s is None or c.t is None or s.lost:
                continue
            s = _predict_ball(s, max(0.0, (t_out or c.t) - c.t))
            sigma = math.hypot(c.camera.sigma_m(s.x, s.y, s.z), STALE_SPEED * s.age)
            cands.append((_weight(sigma) * c.camera.taper(s.x, s.y, s.z, s.z), s))
        if not cands:
            # Hold the last answer the way a single tracker holds a lost ball,
            # if any camera still reports one at all.
            held = [c.state for c in cams if c.state is not None]
            return (min(held, key=lambda s: s.age) if held else None), t_out

        anchor = None
        if self._last is not None and self._last_seen_t is not None and t_out is not None:
            radius = max(BALL_MIN_GATE, BALL_MAX_SPEED * max(0.0, t_out - self._last_seen_t))
            near = [(w, s) for w, s in cands
                    if math.hypot(s.x - self._last.x, s.y - self._last.y) <= radius]
            if near:
                anchor, cands = self._last, near
        if anchor is None:
            best = max(cands, key=lambda e: e[0])[1]
            cands = [(w, s) for w, s in cands
                     if math.hypot(s.x - best.x, s.y - best.y) <= BALL_MIN_GATE]

        merged = _blend_balls(cands)
        self._last = merged
        if merged.visible and t_out is not None:
            self._last_seen_t = t_out
        return merged, t_out


def _predict_ball(s: BallFieldState, dt: float) -> BallFieldState:
    if dt <= 0.0 or s.resting:
        return s
    return dataclasses.replace(s, x=s.x + s.vx * dt, y=s.y + s.vy * dt, age=s.age + dt)


def _blend_balls(cands: list[tuple[float, BallFieldState]]) -> BallFieldState:
    if len(cands) == 1:
        return cands[0][1]
    total = sum(w for w, _s in cands)
    best = max(cands, key=lambda e: e[0])[1]
    x = float(sum(w * s.x for w, s in cands) / total)
    y = float(sum(w * s.y for w, s in cands) / total)
    vx = float(sum(w * s.vx for w, s in cands) / total)
    vy = float(sum(w * s.vy for w, s in cands) / total)
    speed = math.hypot(vx, vy)
    resting = all(s.resting for _w, s in cands)
    if resting:
        vx = vy = speed = 0.0
    return dataclasses.replace(
        best, x=x, y=y, vx=vx, vy=vy, speed=speed,
        direction_deg=math.degrees(math.atan2(vy, vx)) if speed > 0.05 else best.direction_deg,
        visible=any(s.visible for _w, s in cands),
        age=min(s.age for _w, s in cands), resting=resting)
