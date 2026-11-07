"""Live tracking of the robot's AprilTag and the ball, together, from one or more cameras.

    uv run track-combined --ball-profile test --tag-size 0.080

    # two cameras, each over its part of a bigger field (calibrated with
    # calibrate-field --split 2 --photon-camera NAME, once per camera):
    uv run track-combined --photon-camera left --photon-camera right ...

One camera, owned by PhotonVision. The tags come from PhotonVision itself
(tag_tracking.photon) into the per-tag Kalman filter from tag-tracking; the
ball is found in PhotonVision's camera stream by the color-based detector +
Kalman filter from ball-tracking. Same field, same lens calibration (the one
done in PhotonVision), same window -- one plan view with both the robot and
the ball on it, one combined readout strip.

The two run on their own clocks. The ball steps once per stream frame; the
tags step once per PhotonVision result, by PhotonVision's capture times. The
camera's exposure and white balance are PhotonVision's too: lock them in its
camera settings, or the ball's colour drifts out from under its profile.

This does not replace `uv run track` or `uv run track-ball` -- both still
work exactly as before, independently, for whichever one thing you actually
need. This is for when you need both at once. Nothing here reimplements
detection or filtering: it calls straight into TagTracker/BallTracker and the
same draw_tag_marks/draw_ball_marks each skill already uses, so improvements
to either tracker show up here too, automatically.

Keys:
    q / Esc  quit            g  grid on/off           t  trails on/off
    m        mask overlay    w  wall / floor          r  reset trails/tracks
    - / =    min ball concentration

Several cameras (--photon-camera once per camera): each runs all of the above
on its own -- its own field pose (vision/calib/field_pose_<camera>.json by
default, all in one field frame), tag filter, ball filter and ball profile, on
its own clock -- and merge.py blends their tracks into one set, as if one
camera saw the whole field. The output is the same document either way, with
the whole field's size in it, so the simulator and anything else reading it
need not know. What it adds is stats.cameras (each camera's own numbers) and
stats.camera_disagreement_m: for every tag two cameras see at once, how far
apart they put it -- the live check that their calibrations agree.

With --no-window there is no window and no keys: nothing is drawn at all, and
Ctrl+C quits. That is the mode for when the only reader is another program
(--zmq-pub, --serve-http, --json-out, --json-log). --control takes the same keys
as lines on stdin instead, for a program driving a headless tracker that it
shows through --mjpeg (the web app's Tracking page).
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import queue
import socketserver
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np
from ball_tracking.ball import REST_EXIT_M, BallFieldState, BallTracker
from ball_tracking.ball_color import color_file, load_all, load_profile
from ball_tracking.track_ball import AIR_COLOR, BALL_COLOR, draw_ball_marks
from ball_tracking.track_ball import GHOST_COLOR as BALL_GHOST_COLOR
from tag_tracking.detect import for_tracking
from tag_tracking.filter import SPEED_EPS as TAG_SPEED_EPS
from tag_tracking.filter import TagFieldState, TagTracker
from tag_tracking.photon import (
    add_photon_args,
    camera_names,
    close_photon_cameras,
    open_photon_cameras,
)
from tag_tracking.pose import TagFieldPose, tag_field_pose
from tag_tracking.track import GHOST_COLOR as TAG_GHOST_COLOR
from tag_tracking.track import VELOCITY_COLOR, draw_tag_marks
from vision_core import intrinsics as intr
from vision_core.camera import read_key
from vision_core.field import (
    CameraFieldTransform,
    Field,
    ReferenceTagFieldTransform,
    SyntheticFieldTransform,
    field_pose_path,
    resolve_field,
)
from vision_core.mjpeg import MjpegServer
from vision_core.planview import (
    OUT_COLOR,
    PlanView,
    color_for,
    draw_field,
    side_by_side,
)

from .merge import BallMerger, CameraBall, CameraGeometry, CameraTags, TagMerger

TAG_TRAIL_LEN = 90
BALL_TRAIL_LEN = 120
HEADER_H = 38  # px, the band above the camera image

#: A tag lying on the field reads off_plane_m within a centimetre or so of 0
#: (selfcheck's synthetic tags: under 1 cm). A median |off_plane_m| past this,
#: over OFF_PLANE_WINDOW sightings, is not jitter -- see OffPlaneWatch.
OFF_PLANE_WARN_M = 0.05
OFF_PLANE_WINDOW = 60

#: No PhotonVision result for this long (s) and the tag tracks are stepped on
#: as if it saw nothing, so they coast and drop instead of freezing in place.
TAG_STALE_S = 0.25
#: --mjpeg: frames a second sent to a viewer. Encoding every frame costs more
#: than drawing it, and a person watching needs far fewer.
VIDEO_FPS = 15
#: Several cameras: each one's outline on the plan view, and its name on its picture.
FOOTPRINT_COLORS = [(255, 200, 80), (80, 200, 255), (200, 120, 255), (120, 255, 160)]
#: Several cameras: a pose file's field size or origin this far (m) from the
#: first camera's is a different field frame, not rounding.
POSE_MATCH_M = 1e-3


# --------------------------------------------------------------------------
# Drawing: one readout, one plan view, both tag(s) and ball on each.
# --------------------------------------------------------------------------


def draw_combined_readout(
    frame: np.ndarray,
    tag_states: list[TagFieldState],
    ball_state: BallFieldState | None,
    field: Field,
    transform: CameraFieldTransform,
    K: np.ndarray,
    tag_size: float,
    ball_name: str,
    ball_radius_m: float,
    intr_source: str,
    fps: float,
    tag_rows: int = 1,
    head: str | None = None,
) -> np.ndarray:
    """Header (what's calibrated, fps), then one line per tag, then the ball.

    Returns `frame` with a header band above it and the readout band below, so
    no text covers the camera image. The bottom band has room for `tag_rows`
    tag lines (more if more tags are on screen); pass the most seen so far so
    the window does not change size every time a tag comes and goes. `head`
    replaces the one-camera header line (several cameras say their own).
    """
    w = frame.shape[1]
    n_tag_lines = max(len(tag_states), tag_rows, 1)
    n_ball_lines = 2 if ball_state is not None else 1
    strip_h = 16 + 22 * n_tag_lines + 22 * n_ball_lines
    frame = cv2.copyMakeBorder(frame, HEADER_H, strip_h, 0, 0,
                               cv2.BORDER_CONSTANT, value=(0, 0, 0))
    h = frame.shape[0]
    if head is None:
        head = (
            f"{transform.source}  |  tag {tag_size * 1000:.0f}mm  |  "
            f"ball '{ball_name}' r={ball_radius_m * 1000:.0f}mm  |  "
            f"fx {K[0, 0]:.0f} ({intr.hfov_of(K, w):.0f} deg HFOV, {intr_source})  |  "
            f"{fps:4.1f} fps"
        )
    cv2.putText(frame, head, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                (220, 220, 220), 1, cv2.LINE_AA)

    y = h - strip_h + 20

    if not tag_states:
        cv2.putText(frame, "no tags detected", (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (140, 140, 210), 1, cv2.LINE_AA)
        y += 22
    else:
        for s in tag_states:
            color = color_for(s.tag_id) if s.visible else TAG_GHOST_COLOR
            where = "IN " if s.inside else "OUT"
            line = (
                f"tag {s.tag_id:2d}  x {s.x:+6.3f}  y {s.y:+6.3f}  th {s.theta_deg:+7.1f}  "
                f"v {s.speed:4.2f} m/s  {where}{'' if s.visible else '  coasting'}"
            )
            cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        color if s.inside else OUT_COLOR, 1, cv2.LINE_AA)
            y += 22

    # The ball sits below every tag row the band has room for, not just the
    # ones in use, so it stays put as tags come and go.
    y = h - strip_h + 20 + 22 * n_tag_lines
    if ball_state is None:
        cv2.putText(frame, "no ball detected (check: uv run calibrate-ball, or lower "
                    "the min concentration with -)",
                    (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (140, 140, 210), 1, cv2.LINE_AA)
        return frame

    status = (
        f"LOST {ball_state.age:.1f}s (held)" if ball_state.lost
        else f"COASTING {ball_state.age * 1000:.0f}ms" if not ball_state.visible
        else "AIRBORNE" if not ball_state.grounded
        else "grounded, resting" if ball_state.resting
        else "grounded"
    )
    color = (
        BALL_GHOST_COLOR if not ball_state.visible
        else AIR_COLOR if not ball_state.grounded
        else BALL_COLOR
    )
    if not ball_state.inside(field):
        color = OUT_COLOR
    line1 = (
        f"ball  x {ball_state.x:+6.3f}  y {ball_state.y:+6.3f}  z {ball_state.z:+6.3f}  "
        f"{'IN ' if ball_state.inside(field) else 'OUT'}  {status}"
    )
    line2 = (
        f"      speed {ball_state.speed:5.3f} m/s  dir {ball_state.direction_deg:+7.1f} deg"
    )
    cv2.putText(frame, line1, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    y += 22
    cv2.putText(frame, line2, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return frame


def draw_combined_plan(
    plan: PlanView,
    tag_states: list[TagFieldState],
    tag_trails: dict[int, deque],
    ball_state: BallFieldState | None,
    ball_trail: deque,
    transform: CameraFieldTransform | list,
    show_trails: bool,
    footprints: list[tuple[str, list[tuple[float, float]]]] | None = None,
) -> np.ndarray:
    """One plan-view panel with every tag and the ball on it. `transform` may
    be one per camera; `footprints` outlines what each camera sees."""
    panel = plan.base()
    for i, (name, outline) in enumerate(footprints or []):
        pts = np.array([plan.to_px(x, y) for x, y in outline], np.int32)
        col = FOOTPRINT_COLORS[i % len(FOOTPRINT_COLORS)]
        cv2.polylines(panel, [pts], True, col, 1, cv2.LINE_AA)
        top = min(outline, key=lambda p: -p[1])
        cv2.putText(panel, name, plan.to_px(*top), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    col, 1, cv2.LINE_AA)
    for t in (transform if isinstance(transform, list) else [transform]):
        plan.draw_camera(panel, t)

    if show_trails:
        for tid, trail in tag_trails.items():
            plan.draw_trail(panel, trail, color_for(tid))
        if len(ball_trail) > 1:
            plan.draw_trail(panel, ball_trail, BALL_COLOR)

    for s in tag_states:
        color = color_for(s.tag_id) if s.visible else TAG_GHOST_COLOR
        p = plan.to_px(s.x, s.y)
        if not plan.on_panel(p):
            continue
        plan.draw_arrow(panel, s.x, s.y, s.theta_deg, 0.12, color)
        if s.speed > TAG_SPEED_EPS:
            plan.draw_arrow(panel, s.x, s.y, s.direction_deg, 0.5 * s.speed, VELOCITY_COLOR, 1)
        cv2.circle(panel, p, 7, color, -1 if s.inside else 2, cv2.LINE_AA)
        cv2.putText(panel, str(s.tag_id), (p[0] + 9, p[1] - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 1, cv2.LINE_AA)

    if ball_state is not None:
        color = (
            BALL_GHOST_COLOR if not ball_state.visible
            else AIR_COLOR if not ball_state.grounded
            else BALL_COLOR
        )
        p = plan.to_px(ball_state.x, ball_state.y)
        if plan.on_panel(p):
            if ball_state.speed > 0.05:
                plan.draw_arrow(panel, ball_state.x, ball_state.y, ball_state.direction_deg,
                                min(0.20 * ball_state.speed, 0.45), color, 2)
            r = max(plan.metres(0.02), 5)
            cv2.circle(panel, p, r, color, -1 if ball_state.visible else 2, cv2.LINE_AA)
            if not ball_state.grounded:
                cv2.circle(panel, p, r + 5, AIR_COLOR, 1, cv2.LINE_AA)
    return panel


# --------------------------------------------------------------------------
# Live state export: the current frame's detections, for another process
# (a separate repo, a simulator, anything) to read.
# --------------------------------------------------------------------------


def tag_state_to_dict(s: TagFieldState, max_coast_s: float) -> dict:
    """`lost` is here so a reader can treat tags and the ball alike, but
    TagTracker drops a tag the moment it would become lost -- so a tag that is
    in the list at all always has lost=False. A lost tag is a missing tag."""
    return {
        "id": s.tag_id,
        "x": s.x,
        "y": s.y,
        "theta_deg": s.theta_deg,
        "vx": s.vx,
        "vy": s.vy,
        "speed": s.speed,
        "direction_deg": s.direction_deg,
        "omega_deg": s.omega_deg,
        "inside": s.inside,
        "visible": s.visible,
        "off_plane_m": s.off_plane_m,
        "age": s.age,
        "lost": s.age > max_coast_s,
    }


def ball_state_to_dict(s: BallFieldState, field: Field) -> dict:
    return {
        "x": s.x,
        "y": s.y,
        "z": s.z,
        "vx": s.vx,
        "vy": s.vy,
        "speed": s.speed,
        "direction_deg": s.direction_deg,
        "grounded": s.grounded,
        "visible": s.visible,
        "inside": s.inside(field),
        "age": s.age,
        "lost": s.lost,
        "resting": s.resting,
    }


def build_state_doc(
    field: Field,
    tag_states: list[TagFieldState],
    ball_state: BallFieldState | None,
    *,
    seq: int,
    t_capture: float,
    t_tags: float | None,
    tag_max_coast_s: float,
    stats: dict,
    run_id: str,
    frame: int,
) -> dict:
    """The current frame's detections as one plain dict -- the single source
    of truth every publisher (--json-out, --serve-http, --json-log,
    --zmq-pub) sends, so a reader gets the identical shape whichever
    transport it uses.

    Field coordinates throughout: metres, field frame, same convention as
    every printed readout in this project (see the root README).

    `timestamp` predates t_capture/t_publish and is kept for readers that
    already use it; it is the same instant as t_publish, not the capture.

    `run_id` is fixed for one launch and `frame` counts up by one per camera
    frame, so a reader can split an appended --json-log into runs and tell a
    stalled loop (timestamp jumps, frame +1) from missing lines (frame skips).

    `t_capture` is when this camera frame arrived, which the ball comes from.
    The tags come from PhotonVision's latest result instead, measured on its
    own schedule: `t_tags` is when that was captured, on the same clock. Null
    until PhotonVision's first result.
    """
    t_publish = time.time()
    return {
        "seq": seq,
        "run_id": run_id,
        "frame": frame,
        "timestamp": t_publish,
        "t_capture": t_capture,
        "t_tags": t_tags,
        "t_publish": t_publish,
        "field": {"width": field.width, "height": field.height,
                  "x0": field.x0, "y0": field.y0},
        "tags": [tag_state_to_dict(s, tag_max_coast_s) for s in tag_states],
        "ball": None if ball_state is None else ball_state_to_dict(ball_state, field),
        "stats": {**stats, "latency_ms": (t_publish - t_capture) * 1000.0},
    }


def write_json_state(path: Path, doc: dict) -> None:
    """Overwrite `path` with `doc`, as JSON, atomically.

    Written to a sibling `.tmp` file, then moved over the real path with
    `os.replace()` -- an atomic rename on both Windows and POSIX, so a reader
    on another process, polling this file on its own schedule unsynchronised
    with this one, can never open it mid-write and see truncated or
    half-updated JSON. Plain "open path, write, close" does not have that
    guarantee: a reader can land exactly between the open (which truncates
    the old file) and the write finishing.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    os.replace(tmp, path)


def lan_ip() -> str:
    """Best guess at this machine's address on the local network -- same
    trick serve_tag.py uses, so a reader on a *different* machine knows what
    to connect to, not just localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class LiveStateServer:
    """Serves the latest build_state_doc() as JSON over plain HTTP GET, so a
    reader anywhere on the network -- a different process, a different
    machine, a browser-based simulator -- can poll it without touching this
    machine's filesystem. Runs in a background thread; call publish() once
    per frame from the main loop, same spirit as write_json_state but kept in
    memory instead of on disk.
    """

    def __init__(self, port: int) -> None:
        self._lock = threading.Lock()
        self._doc: dict | None = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in ("/", "/state"):
                    self.send_error(404)
                    return
                with outer._lock:
                    doc = outer._doc
                body = json.dumps(doc if doc is not None else {}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                # No CORS proxy needed for a browser-based reader (e.g. a web
                # simulator) to fetch() this directly.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *fmt_args) -> None:  # noqa: N802
                pass  # quiet; startup already printed where to look

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._httpd = socketserver.ThreadingTCPServer(("0.0.0.0", port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def publish(self, doc: dict) -> None:
        with self._lock:
            self._doc = doc

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def import_zmq():
    """pyzmq, or a clear exit saying how to get it. It is an optional extra,
    not a dependency: nothing but --zmq-pub needs it."""
    try:
        import zmq
    except ImportError:
        raise SystemExit(
            "--zmq-pub needs pyzmq. Install it with\n"
            "  uv sync   (at the monorepo root, robocup_software)\n"
            "or run once with it:  uv run --with pyzmq track-combined ..."
        ) from None
    return zmq


class ZmqStatePublisher:
    """Pushes every build_state_doc() out on a ZMQ PUB socket, as one
    single-part message of UTF-8 JSON -- no topic frame, so a subscriber
    subscribes to "" and can set CONFLATE to only ever hold the newest.

    The push counterpart of LiveStateServer: a reader polling HTTP gets the
    latest doc whenever it asks and may skip frames or see one twice; a
    subscriber here is handed each frame once, as soon as it is published.
    PUB never blocks the tracker: a subscriber that falls behind has frames
    dropped for it at the high-water mark, which `seq` gaps then show.
    """

    def __init__(self, endpoint: str) -> None:
        zmq = import_zmq()
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.PUB)
        # A few frames of slack per subscriber, not the default 1000: stale
        # frames queued for a slow reader are worse than frames it never got.
        self._sock.setsockopt(zmq.SNDHWM, 4)
        self._sock.setsockopt(zmq.LINGER, 0)  # never hang on quit
        try:
            self._sock.bind(endpoint)
        except zmq.ZMQError as e:
            self.stop()
            raise SystemExit(f"--zmq-pub {endpoint}: {e}") from None

    def publish(self, doc: dict) -> None:
        self._sock.send_string(json.dumps(doc))

    def stop(self) -> None:
        self._sock.close()
        self._ctx.term()


# --------------------------------------------------------------------------
# Capture: read the camera on its own thread, keep only the newest frame.
# --------------------------------------------------------------------------


class OffPlaneWatch:
    """Says so, once per tag, when a tag keeps reading well off the field plane.

    A tag lying on the field reads off_plane_m within about a centimetre of
    zero. A steady offset of tens of centimetres is not jitter and not a tag
    held in the air: it means the field plane or the tag's range is wrong.
    Positive is towards the camera -- a tag lifted off the field reads
    positive. Negative means the tag appears *further* away than the plane
    it is supposed to be lying on, which no real tag can be, so that is the
    plane (or --tag-size, or fx) being wrong, not the tag.
    """

    def __init__(self, synthetic: bool) -> None:
        self.synthetic = synthetic
        self._seen: dict[int, deque] = defaultdict(lambda: deque(maxlen=OFF_PLANE_WINDOW))
        self._warned: set[int] = set()

    def add(self, poses: list[TagFieldPose]) -> None:
        for p in poses:
            hist = self._seen[p.tag_id]
            hist.append(p.off_plane_m)
            if p.tag_id in self._warned or len(hist) < OFF_PLANE_WINDOW:
                continue
            med_abs = float(np.median(np.abs(hist)))
            if med_abs <= OFF_PLANE_WARN_M:
                continue
            self._warned.add(p.tag_id)
            med = float(np.median(hist))
            print(f"warning: tag {p.tag_id} reads {med:+.2f} m off the field plane "
                  f"(median of its last {OFF_PLANE_WINDOW} sightings; on the field "
                  f"it should be within ~1 cm)")
            if self.synthetic:
                print("  the field pose is the made-up one (--cam-height/--pitch "
                      "guesses), so tag x/y and ball x/y are on different scales; "
                      "run `uv run calibrate-field`")
            else:
                print("  check --tag-size against the printed tag's black square, and "
                      "that the tag lies flat on the field")


# --------------------------------------------------------------------------


def build_transform(args, field: Field, mode: str) -> CameraFieldTransform:
    """Same shape as both trackers' own -- swapping in a measured field pose
    means changing this function and nothing else."""
    if mode == "floor":
        return SyntheticFieldTransform.floor(field, args.cam_height, args.pitch)
    return SyntheticFieldTransform.wall(field, args.distance, args.yaw, args.pitch_wall)


class StdinKeys:
    """--control: the window's keys, one per line on stdin -- g, t, m, w, r,
    q, - or = -- plus "conc N" to set the minimum ball concentration outright.
    Read on a thread of its own, so a quiet stdin never stalls a frame."""

    #: next()'s key for "conc N".
    SET_CONC = 0x100

    def __init__(self) -> None:
        self._lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        threading.Thread(target=self._read, daemon=True, name="stdin-keys").start()

    def _read(self) -> None:
        for line in sys.stdin:
            if line.strip():
                self._lines.put(line.strip())

    def next(self) -> tuple[int, float | None]:
        """(key code, value) for the oldest waiting line; (-1, None) if none."""
        try:
            line = self._lines.get_nowait()
        except queue.Empty:
            return -1, None
        if len(line) == 1:
            return ord(line), None
        word, _, value = line.partition(" ")
        if word == "conc":
            try:
                return self.SET_CONC, float(value)
            except ValueError:
                pass
        print(f"--control: ignoring {line!r}")
        return -1, None


def _new_ball_tracker(args, color, transform, K, field) -> BallTracker:
    """A BallTracker for the *already undistorted* frame this tool hands it.

    dist is None on purpose, and deliberately not a parameter: by the time the
    ball tracker sees the frame it has been whole-frame undistorted for the tag
    detector, so undistorting the ball centre again would correct a distortion
    that is no longer there. Making it an argument only invites someone to pass
    the real coefficients through.
    """
    return BallTracker(
        color, transform, K, None, sigma_px=args.ball_sigma_px,
        sigma_a=args.ball_sigma_a, sigma_a_air=args.ball_sigma_a_air, field=field,
        min_concentration=args.ball_min_conc, ground_only=not args.ball_airborne,
        rest_exit_m=args.ball_rest_mm / 1000.0,
    )


def camera_footprint(transform: CameraFieldTransform, K: np.ndarray, size: tuple[int, int],
                     z: float = 0.0, steps: int = 8) -> list[tuple[float, float]]:
    """What one camera sees of the plane `z` metres above the field: its image
    border traced onto that plane, in field (x, y). Points whose ray misses the
    plane are left out."""
    w, h = size
    border = ([(w * i / steps, 0.0) for i in range(steps)]
              + [(float(w), h * i / steps) for i in range(steps)]
              + [(w * (steps - i) / steps, float(h)) for i in range(steps)]
              + [(0.0, h * (steps - i) / steps) for i in range(steps)])
    cam = transform.camera_origin_in_field()
    Kinv = np.linalg.inv(K)
    out = []
    for u, v in border:
        ray = transform.direction_to_field(Kinv @ np.array([u, v, 1.0]))[0]
        if abs(ray[2]) < 1e-9:
            continue
        s = (z - cam[2]) / ray[2]
        if s > 0:
            p = cam + s * ray
            out.append((float(p[0]), float(p[1])))
    return out


class CameraPipeline:
    """One camera's whole tracking chain: its PhotonVision tags and stream,
    its field pose, its own tag and ball filters, on its own clock. With one
    camera this is exactly what track-combined always ran; with several, one
    of these per camera, and merge.py combines what they report."""

    def __init__(self, args, tags, stream, transform: CameraFieldTransform, color,
                 field: Field, synthetic: bool) -> None:
        self.tags, self.stream = tags, stream
        self.name: str = tags.name
        self.color = color
        self.size: tuple[int, int] = tags.size
        self.K, self.dist, self.intr_source = tags.K, tags.dist, tags.source
        # Whole-frame undistortion: the ball detector searches the entire
        # image, so it (and the overlay drawn on top of it) need to share one
        # undistorted pinhole space with the tag corners -- see
        # vision_core.intrinsics.undistort_maps.
        self.undistort_maps = intr.undistort_maps(self.K, self.dist, *self.size)
        self.tag_tracker = TagTracker(field, sigma_a=args.tag_sigma_a,
                                      sigma_alpha_deg=args.tag_sigma_alpha,
                                      max_coast_s=args.tag_coast)
        self.off_plane = OffPlaneWatch(synthetic=synthetic)
        self.set_transform(args, transform, field)

        self.last_t = time.perf_counter()
        self.fps = 0.0
        self.frame: np.ndarray | None = None  # newest, undistorted, not drawn on
        self.t_capture: float | None = None  # time.time() of that frame
        self.tag_last_t: float | None = None  # PhotonVision's clock: last result's capture
        self.tag_last_perf = self.last_t  # ours, when that result was taken in
        self.t_tags: float | None = None  # time.time() instant tag_states are for
        self.dets: list = []
        self.tag_poses: list[TagFieldPose] = []
        self.tag_states: list[TagFieldState] = []
        self.tag_latency_ms = 0.0
        self.tag_skipped = 0
        self.ball_state: BallFieldState | None = None
        self.tag_ms = 0.0
        self.ball_ms = 0.0

    def set_transform(self, args, transform: CameraFieldTransform, field: Field) -> None:
        """A new field pose (the w key's made-up field): the ball filter is
        built around it, so it starts over too."""
        self.transform = transform
        self.geometry = CameraGeometry(self.name, transform, self.K, self.size)
        self.ball_tracker = _new_ball_tracker(args, self.color, transform, self.K, field)

    def reset(self, args, field: Field) -> None:
        self.tag_tracker.reset()
        # BallTracker has no reset() -- rebuilding it is the reset. Leaving this
        # out makes `r` clear the tag tracks while silently keeping the ball
        # track, which may be exactly the thing you pressed `r` to get rid of.
        self.ball_tracker = _new_ball_tracker(args, self.color, self.transform, self.K, field)

    def step(self, got: tuple[np.ndarray, float, float], args, field: Field,
             seeds: dict[int, TagFieldState] | None = None) -> None:
        """One stream frame: the ball steps on it, and the tags on whatever
        PhotonVision result arrived since. `seeds`, the merged tracks of
        several cameras, start this camera's new tracks moving (see
        TagTracker.update)."""
        frame, self.t_capture, t_perf = got
        # dt between *captures*, not between loop iterations: when the stream
        # drops a frame, the ball filter is told the real gap.
        dt = t_perf - self.last_t
        self.last_t = t_perf
        if dt > 0:  # the first frame can predate the loop's clock
            self.fps = 0.9 * self.fps + 0.1 / dt
        if self.undistort_maps is not None:
            frame = cv2.remap(frame, *self.undistort_maps, cv2.INTER_LINEAR)
        self.frame = frame

        t0 = time.perf_counter()
        result = self.tags.poll()
        if result is not None:
            # Tags step by PhotonVision's capture times, not this loop's:
            # results arrive on their own schedule, not with the frames.
            tag_dt = 0.0 if self.tag_last_t is None else max(0.0, result.t - self.tag_last_t)
            self.tag_last_t, self.tag_last_perf = result.t, t0
            self.t_tags = result.t_wall
            self.tag_latency_ms = result.latency_ms
            self.tag_skipped += result.skipped
            self.dets = for_tracking(result.detections, self.K, self.dist, args.tag_size)
            self.tag_poses = [
                tag_field_pose(d, self.transform, field, args.heading_offset, K=self.K,
                               tag_height=args.tag_height)
                for d in self.dets
            ]
            self.tag_states = self.tag_tracker.update(self.tag_poses, tag_dt, seeds)
            self.off_plane.add(self.tag_poses)
        elif self.tag_last_t is not None and t0 - self.tag_last_perf > TAG_STALE_S:
            # PhotonVision went quiet. Step on as if it saw nothing, so the
            # tracks coast and drop; both clocks move by the gap, which keeps
            # the next result's dt right.
            gap = t0 - self.tag_last_perf
            self.tag_last_t += gap
            self.tag_last_perf = t0
            if self.t_tags is not None:
                self.t_tags += gap
            self.dets, self.tag_poses = [], []
            self.tag_states = self.tag_tracker.update([], gap)
        t1 = time.perf_counter()
        self.ball_state = self.ball_tracker.update(frame, dt)
        t2 = time.perf_counter()
        self.tag_ms, self.ball_ms = (t1 - t0) * 1000.0, (t2 - t1) * 1000.0

    def draw(self, field: Field, show_grid: bool, show_mask: bool,
             label: tuple[int, int, int] | None = None) -> np.ndarray:
        """This camera's picture with its own overlays: the field as it sees
        it, its tags, its ball. `label` puts its name in the corner."""
        assert self.frame is not None
        frame = self.frame.copy() if label is not None else self.frame
        draw_field(frame, field, self.transform, self.K, show_grid)
        for d, pose in zip(self.dets, self.tag_poses):
            draw_tag_marks(frame, d, pose, color_for(pose.tag_id))
        draw_ball_marks(frame, self.ball_tracker, self.ball_state, show_mask)
        if label is not None:
            text = f"{self.name}  {self.fps:4.1f} fps"
            cv2.rectangle(frame, (0, 0), (16 + 11 * len(text), 30), (0, 0, 0), -1)
            cv2.putText(frame, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, label, 2,
                        cv2.LINE_AA)
        return frame

    def stats(self) -> dict:
        return {
            "fps": self.fps,
            "photon_latency_ms": self.tag_latency_ms,
            "photon_skipped": self.tag_skipped,
            "capture_dropped": self.stream.dropped,
            "tags_seen": [s.tag_id for s in self.tag_states if s.visible],
            "ball_visible": bool(self.ball_state is not None and self.ball_state.visible),
        }


def load_camera_poses(paths: list[Path], names: list[str], use_origin: bool,
                      field_arg) -> tuple[list[ReferenceTagFieldTransform], Field]:
    """Every camera's measured pose, and the one field they all share.

    They must all be in the same field frame -- same field size, same origin
    -- or one camera's (1, 0) is another's (1.3, 0) and merging them is
    meaningless; that is refused here, not discovered later as jumping robots.
    """
    missing = [(n, p) for n, p in zip(names, paths) if not p.exists()]
    if missing:
        raise SystemExit(
            "no field pose for camera(s) " + ", ".join(f"'{n}' ({p})" for n, p in missing)
            + f"\ncalibrate each one: uv run calibrate-field --split {len(names)} "
              f"--photon-camera NAME --part K ... (or pass --field-pose once per camera)")
    poses = [ReferenceTagFieldTransform.load(p, use_origin=use_origin) for p in paths]
    for n, p, t in zip(names[1:], paths[1:], poses[1:]):
        a, b = poses[0], t
        if (a.field_size is not None and b.field_size is not None
                and not np.allclose(a.field_size, b.field_size, atol=POSE_MATCH_M)):
            raise SystemExit(f"camera '{n}' ({p.name}) was calibrated for a "
                             f"{b.field_size[0]:g} x {b.field_size[1]:g} m field, camera "
                             f"'{names[0]}' ({paths[0].name}) for {a.field_size[0]:g} x "
                             f"{a.field_size[1]:g}: recalibrate them with the same --field")
        oa, ob = a.origin, b.origin
        if (oa is None) != (ob is None) or (oa is not None and not np.allclose(
                oa, ob, atol=POSE_MATCH_M)):
            raise SystemExit(f"camera '{n}' ({p.name}) has origin {ob}, camera "
                             f"'{names[0]}' ({paths[0].name}) has {oa}: they must share one. "
                             f"Re-pick it with calibrate-field --pick-origin --origin X Y "
                             f"--out <file> for each (or pass --corner-origin)")
    field = resolve_field(field_arg, paths[0])
    return poses, field


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Keys: q quit, g grid, t trails, m mask, w wall/floor, r reset, - = min ball conc "
               "(with --no-window: none, Ctrl+C quits)",
    )
    # -- tag-specific --------------------------------------------------
    ap.add_argument("--tag-size", type=float, default=0.080,
                    help="black-square width in metres (default 0.080)")
    ap.add_argument("--heading-offset", type=float, default=0.0,
                    help="degrees added to tag theta, if not mounted upright")
    ap.add_argument("--tag-height", type=float, default=0.0,
                    help="metres the tag sits above the field (0 = lying on it; a robot's "
                         "shell height for a tag on top of a robot)")
    ap.add_argument("--tag-sigma-a", type=float, default=8.0,
                    help="tag filter process noise, m/s^2 (raise for a fast robot)")
    ap.add_argument("--tag-sigma-alpha", type=float, default=180.0,
                    help="tag filter process noise for turning, deg/s^2")
    ap.add_argument("--tag-coast", type=float, default=0.5, metavar="SECONDS",
                    help="keep reporting a tag on its predicted motion (visible: false) "
                         "for this long after it was last seen, before dropping it "
                         "from the output (default 0.5). Raise it so a JSON reader "
                         "sees fewer tags vanish; the cost is a stale estimate if "
                         "the robot really did leave, and slower re-acquire after "
                         "it is picked up and moved")
    # -- ball-specific ---------------------------------------------------
    ap.add_argument("--ball-profile", default=None, action="append",
                    help="named color profile from calib/ball_color.json "
                         "(default 'test'; make one with 'uv run calibrate-ball'). With "
                         "several cameras, once for all of them or once per camera: "
                         "cameras of different makes see the ball's colour differently")
    ap.add_argument("--ball-radius", type=float, default=None,
                    help="override the profile's ball RADIUS, in metres")
    ap.add_argument("--ball-sigma-a", type=float, default=3.0,
                    help="ball filter process noise while grounded, m/s^2")
    ap.add_argument("--ball-sigma-a-air", type=float, default=10.0,
                    help="ball filter process noise while airborne, m/s^2")
    ap.add_argument("--ball-sigma-px", type=float, default=3.0,
                    help="assumed pixel noise on the ball centre")
    ap.add_argument("--ball-rest-mm", type=float, default=REST_EXIT_M * 1000,
                    help="a ball that stops is held still, its position the average "
                         "of what is seen, until it moves this far (mm, default "
                         f"{REST_EXIT_M * 1000:g}); 0 follows every measurement")
    ap.add_argument("--ball-min-conc", type=float, default=80.0,
                    help="mean color likelihood (0-255) a blob needs to count as "
                         "the ball; below it no ball is published. Each blob's "
                         "value is drawn on the video; tune live with - / =")
    ap.add_argument("--ball-airborne", action="store_true",
                    help="let the ball be flagged airborne from its apparent size. "
                         "Off by default: with the camera ~3 m up the ball is ~7 px "
                         "across, a pixel or two of color bleed reads as a metre of "
                         "height, and a ball on the table was called airborne 92%% "
                         "of the time -- its x/y then slid along the view ray")
    # -- shared: field / camera -----------------------------------------
    ap.add_argument("--field", type=float, nargs=2, metavar=("W", "H"),
                    default=None,
                    help="field size in metres (default: the size saved with the "
                         "calibration, else 1.2 0.8)")
    ap.add_argument("--mode", choices=["wall", "floor"], default="floor",
                    help="floor is the physically meaningful one for a robot+ball rig; "
                         "wall is a hand-held test harness (default floor)")
    ap.add_argument("--distance", type=float, default=1.8,
                    help="wall mode: metres from camera to the field plane")
    ap.add_argument("--yaw", type=float, default=0.0,
                    help="wall mode: rotate the field about vertical, degrees")
    ap.add_argument("--pitch-wall", type=float, default=0.0,
                    help="wall mode: tip the field top-away, degrees")
    ap.add_argument("--cam-height", type=float, default=1.5,
                    help="floor mode: camera height above the field, metres")
    ap.add_argument("--pitch", type=float, default=90.0,
                    help="floor mode: camera downward tilt from horizontal, degrees "
                         "(90 = straight down, directly over the field centre)")
    ap.add_argument("--field-pose", type=str, default=None, action="append",
                    help="measured field pose to load (default vision/calib/field_pose.json, "
                         "written by calibrate-field). With several cameras, once per "
                         "camera, in --photon-camera order (default "
                         "vision/calib/field_pose_<camera>.json)")
    ap.add_argument("--corner-origin", action="store_true",
                    help="ignore any custom origin saved by calibrate-field and report "
                         "positions from the first field corner, as before")
    ap.add_argument("--synthetic-field", action="store_true",
                    help="use the made-up field from --cam-height/--pitch even if a "
                         "measured one is saved")
    ap.add_argument("--list-profiles", action="store_true")
    ap.add_argument("--display-width", type=int, default=1600,
                    help="scale the window to this width in pixels; "
                         "enlarges as well as shrinks")
    ap.add_argument("--print-poses", action="store_true",
                    help="also stream tag (id, x, y, theta, vx, vy, speed, visible) "
                         "to stdout, one tab-separated line per tag per frame")
    ap.add_argument("--print-states", action="store_true",
                    help="also stream ball (x, y, z, vx, vy, grounded, visible) "
                         "to stdout, one tab-separated line per frame")
    ap.add_argument("--json-out", type=str, default=None,
                    help="write the current frame's tag and ball detections to this "
                         "PATH as JSON, overwritten every frame, for another process "
                         "(a simulator, another repo) to read. Off by default. The "
                         "write is atomic, so a reader polling the file on its own "
                         "schedule never sees a half-written one")
    ap.add_argument("--serve-http", type=int, default=None, metavar="PORT",
                    help="also (or instead) serve the same detections as JSON over "
                         "plain HTTP GET at this port, for a reader anywhere on the "
                         "network -- a different machine, a browser-based simulator -- "
                         "instead of (or as well as) --json-out's local file. Off by "
                         "default")
    ap.add_argument("--json-log", type=str, default=None,
                    help="also (or instead) APPEND every frame's detections to this "
                         "PATH, one JSON object per line (JSON Lines), instead of "
                         "--json-out's 'always just the latest frame'. For a full "
                         "history rather than current state. Off by default")
    ap.add_argument("--zmq-pub", type=str, default=None, metavar="ENDPOINT",
                    help="also (or instead) PUSH every frame's detections as JSON on a "
                         "ZMQ PUB socket bound to this ENDPOINT, e.g. tcp://*:5556, so a "
                         "subscriber gets each frame as it happens instead of polling. "
                         "Needs the optional pyzmq extra. Off by default")
    ap.add_argument("--no-window", action="store_true",
                    help="headless: no window, nothing drawn, no keys; Ctrl+C quits. "
                         "For when the only reader is another program")
    ap.add_argument("--mjpeg", type=int, default=None, metavar="PORT",
                    help="also serve what the window shows as an MJPEG stream at "
                         "http://<host>:PORT/stream.mjpg (the web app's Tracking page "
                         "uses this). Works with --no-window: it still draws, just "
                         "not on screen. Off by default")
    ap.add_argument("--control", action="store_true",
                    help="take the keys as lines on stdin too (g, t, m, w, r, q, - or =, "
                         "or 'conc N'), for a program driving a --no-window tracker it "
                         "shows through --mjpeg. Keys change only what is drawn, so "
                         "without a window or --mjpeg only r, q and the ball "
                         "concentration do anything")
    add_photon_args(ap, stream_optional=False, multi=True)
    args = ap.parse_args()
    if args.zmq_pub:
        import_zmq()  # fail now, not after PhotonVision has been connected to
    headless = args.no_window
    json_out = Path(args.json_out) if args.json_out else None
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
    json_log_path = Path(args.json_log) if args.json_log else None
    if json_log_path:
        json_log_path.parent.mkdir(parents=True, exist_ok=True)

    if args.list_profiles:
        profiles = load_all()
        print(f"{color_file()}: {', '.join(sorted(profiles)) or '(none yet)'}")
        return

    names = camera_names(args)
    n_cams = len(names)
    multi = n_cams > 1
    profile_names = args.ball_profile or ["test"]
    if len(profile_names) == 1:
        profile_names = profile_names * n_cams
    elif len(profile_names) != n_cams:
        raise SystemExit(f"--ball-profile was given {len(profile_names)} times for {n_cams} "
                         f"cameras: once for all of them, or once per --photon-camera")
    colors = []
    for pname in profile_names:
        color = load_profile(pname)
        if args.ball_radius is not None:
            color.radius_m = float(args.ball_radius)
        colors.append(color)
    color = colors[0]  # for the one-camera readout

    mode = args.mode
    if not multi:
        pose_path = Path(args.field_pose[-1]) if args.field_pose else field_pose_path()
        field = resolve_field(args.field, None if args.synthetic_field else pose_path)
        using_calibrated = pose_path.exists() and not args.synthetic_field
        if using_calibrated:
            transforms = [ReferenceTagFieldTransform.load(
                pose_path, use_origin=not args.corner_origin)]
            print(f"loaded calibrated field pose from {pose_path}")
        else:
            transforms = [build_transform(args, field, mode)]
    else:
        if args.synthetic_field:
            raise SystemExit("several cameras need their measured field poses: the made-up "
                             "field only places one camera (drop --synthetic-field)")
        if args.field_pose and len(args.field_pose) != n_cams:
            raise SystemExit(f"--field-pose was given {len(args.field_pose)} times for "
                             f"{n_cams} cameras: once per --photon-camera, or not at all")
        pose_paths = ([Path(p) for p in args.field_pose] if args.field_pose
                      else [field_pose_path(n) for n in names])
        transforms, field = load_camera_poses(pose_paths, names, not args.corner_origin,
                                              args.field)
        using_calibrated = True
        for n, p in zip(names, pose_paths):
            print(f"camera '{n}': field pose from {p}")
    origin = getattr(transforms[0], "origin", None)
    if origin is not None:
        field = field.relative_to(origin)
        print(f"custom origin: (0, 0) is ({origin[0]:g}, {origin[1]:g}) m "
              f"from the first corner (--corner-origin to ignore)")
        # On screen too, so a custom zero is never a surprise.
        for t in transforms:
            t.source += f", origin ({origin[0]:g}, {origin[1]:g})"

    # Each stream reads on its own thread from here on, keeping only the newest
    # frame, so a frame is never older than the one PhotonVision sent last.
    opened = open_photon_cameras(args, need_stream=True)
    http_server = None
    zmq_pub = None
    json_log_file = None
    video = None

    try:
        cams = [CameraPipeline(args, tags, stream, t, c, field, not using_calibrated)
                for (tags, stream), t, c in zip(opened, transforms, colors)]
        tag_merger = TagMerger(field, tag_height=args.tag_height, tag_size=args.tag_size)
        ball_merger = BallMerger(field)
        if multi:
            footprints = [(c.name, camera_footprint(c.transform, c.K, c.size, args.tag_height))
                          for c in cams]
            tile_h = max(c.size[1] for c in cams)
        else:
            footprints = None
            tile_h = cams[0].size[1]

        plan = PlanView(field, height=tile_h, mode=mode)
        readout_rows = 1  # most tag lines shown so far; the bottom band only grows
        tag_trails: dict[int, deque] = defaultdict(lambda: deque(maxlen=TAG_TRAIL_LEN))
        ball_trail: deque = deque(maxlen=BALL_TRAIL_LEN)

        show_grid, show_trails, show_mask = True, True, True
        seq = 0
        ball_dropouts = 0
        run_id = time.strftime("%Y%m%d-%H%M%S")
        frame_no = 0

        print(f"\nfield {field.width:g} x {field.height:g} m")
        for c in cams:
            print(f"camera '{c.name}': {c.transform.source}; intrinsics: {c.intr_source}"
                  + (f"; ball '{c.color.name}'" if multi else ""))
        print(f"tag size {args.tag_size * 1000:.1f} mm, "
              f"ball '{color.name}' r={color.radius_m * 1000:.0f} mm")
        if multi:
            for (n, outline), c in zip(footprints, cams):
                if outline:
                    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
                    print(f"camera '{n}' sees x {min(xs):+.2f}..{max(xs):+.2f}, "
                          f"y {min(ys):+.2f}..{max(ys):+.2f} m at tag height")
        if json_out is not None:
            print(f"writing live state to {json_out} every frame")
        if json_log_path is not None:
            json_log_file = json_log_path.open("a")
            print(f"appending one JSON line per frame to {json_log_path}")
        http_server = LiveStateServer(args.serve_http) if args.serve_http else None
        if http_server is not None:
            print(f"serving live state at http://localhost:{args.serve_http}/state "
                  f"(or http://{lan_ip()}:{args.serve_http}/state from another machine)")
        zmq_pub = ZmqStatePublisher(args.zmq_pub) if args.zmq_pub else None
        if zmq_pub is not None:
            print(f"publishing live state on ZMQ PUB {args.zmq_pub} every frame")
        publishing = any(p is not None for p in (json_out, http_server, json_log_file, zmq_pub))
        video = MjpegServer(args.mjpeg) if args.mjpeg else None
        if video is not None:
            print(f"serving the tracking view at http://localhost:{args.mjpeg}/stream.mjpg")
        last_video = 0.0
        control = StdinKeys() if args.control else None

        win = "combined tracking"
        if headless:
            print("running headless -- Ctrl+C to quit\n")
        else:
            print("window open -- q or Esc to quit\n")
            cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

        warned_disagree: set[int] = set()
        tag_states: list[TagFieldState] = []  # the last merge's
        try:
            while True:
                if not multi:
                    # One camera: block on its stream, exactly as always.
                    got = cams[0].stream.read()
                    if got is None:
                        print("PhotonVision's stream stopped sending frames")
                        break
                    cams[0].step(got, args, field)
                else:
                    # Several: take whichever have a new frame, never waiting
                    # on one while another has something.
                    stepped = False
                    seeds = {s.tag_id: s for s in tag_states}
                    for c in cams:
                        got = c.stream.try_read()
                        if got is not None:
                            c.step(got, args, field, seeds)
                            stepped = True
                    failed = [c.name for c in cams if c.stream.failed]
                    if failed:
                        print(f"PhotonVision's stream stopped sending frames for camera(s) "
                              f"{', '.join(failed)}")
                        break
                    if not stepped:
                        time.sleep(0.002)
                        continue
                    if any(c.frame is None for c in cams):
                        continue  # not every camera has a picture yet
                frame_no += 1
                t_perf = time.perf_counter()

                tag_states, t_tags = tag_merger.merge(
                    [CameraTags(c.geometry, c.tag_states, c.t_tags) for c in cams])
                ball_state, _t_ball = ball_merger.merge(
                    [CameraBall(c.geometry, c.ball_state, c.t_capture) for c in cams])
                t_capture = max(c.t_capture for c in cams if c.t_capture is not None)
                for tid, d in tag_merger.disagreement.items():
                    if d > 0.02 and tid not in warned_disagree:
                        warned_disagree.add(tid)
                        print(f"warning: the cameras put tag {tid} {d * 100:.1f} cm apart where "
                              f"they overlap (want under ~1 cm): their field poses disagree "
                              f"-- recalibrate, and check --tag-height")

                # None is the tracker idling (nothing reached --ball-min-conc),
                # which is a frame without the ball as much as a coasting one is.
                if ball_state is not None and ball_state.visible:
                    ball_trail.append((ball_state.x, ball_state.y))
                else:
                    ball_dropouts += 1
                for s in tag_states:
                    if s.visible:
                        tag_trails[s.tag_id].append((s.x, s.y))

                # Same fields each skill's own --print-* emits, prefixed with
                # which kind of row it is. Dropping grounded/visible would leave
                # a reader unable to tell a coasting or airborne estimate from a
                # measured one, which is the one thing it most needs to know.
                if args.print_poses:
                    for s in tag_states:
                        print(f"tag\t{s.tag_id}\t{s.x:.4f}\t{s.y:.4f}\t{s.theta_deg:.2f}\t"
                              f"{s.vx:+.3f}\t{s.vy:+.3f}\t{s.speed:.3f}\t{int(s.visible)}",
                              flush=True)
                if args.print_states and ball_state is not None:
                    print(f"ball\t{ball_state.x:.4f}\t{ball_state.y:.4f}\t"
                          f"{ball_state.z:.4f}\t"
                          f"{ball_state.vx:+.4f}\t{ball_state.vy:+.4f}\t"
                          f"{int(ball_state.grounded)}\t{int(ball_state.visible)}",
                          flush=True)
                if publishing:
                    stats = {
                        # The slowest camera's rate: the merged picture is no
                        # fresher than its stalest part.
                        "fps": min(c.fps for c in cams),
                        "tag_ms": sum(c.tag_ms for c in cams),
                        "ball_ms": sum(c.ball_ms for c in cams),
                        "capture_dropped": sum(c.stream.dropped for c in cams),
                        "photon_latency_ms": max(c.tag_latency_ms for c in cams),
                        "photon_skipped": sum(c.tag_skipped for c in cams),
                        "ball_dropouts": ball_dropouts,
                        # what the keys have set, for a --control driver to show
                        "view": {"grid": show_grid, "trails": show_trails, "mask": show_mask,
                                 "mode": mode, "ball_min_conc": args.ball_min_conc},
                    }
                    if multi:
                        stats["cameras"] = {c.name: c.stats() for c in cams}
                        stats["camera_disagreement_m"] = {
                            str(tid): d for tid, d in tag_merger.disagreement.items()}
                    doc = build_state_doc(field, tag_states, ball_state, seq=seq,
                                          run_id=run_id, frame=frame_no,
                                          t_capture=t_capture, t_tags=t_tags,
                                          tag_max_coast_s=args.tag_coast,
                                          stats=stats)
                    if json_out is not None:
                        write_json_state(json_out, doc)
                    if http_server is not None:
                        http_server.publish(doc)
                    if zmq_pub is not None:
                        zmq_pub.publish(doc)
                    if json_log_file is not None:
                        # Flushed every line, not just buffered: a reader
                        # tailing the file (tail -f, or its own poll loop) sees
                        # each frame as soon as it lands, not whenever the OS
                        # decides to flush a full buffer.
                        json_log_file.write(json.dumps(doc) + "\n")
                        json_log_file.flush()
                seq += 1

                # Drawing only after publishing: none of it feeds the doc, and
                # every millisecond spent on overlays first is latency_ms.
                if headless and video is None and control is None:
                    continue
                readout_rows = max(readout_rows, len(tag_states))
                if not multi:
                    c = cams[0]
                    shown = draw_combined_readout(
                        c.draw(field, show_grid, show_mask), tag_states, ball_state, field,
                        c.transform, c.K, args.tag_size, color.name, color.radius_m,
                        c.intr_source, c.fps, tag_rows=readout_rows)
                else:
                    shown = draw_combined_readout(
                        tile_cameras(cams, field, show_grid, show_mask, tile_h), tag_states,
                        ball_state, field, cams[0].transform, cams[0].K, args.tag_size,
                        color.name, color.radius_m, "", 0.0, tag_rows=readout_rows,
                        head=multi_header(cams, tag_merger.disagreement, args.tag_size))
                panel = draw_combined_plan(plan, tag_states, tag_trails, ball_state,
                                           ball_trail, [c.transform for c in cams],
                                           show_trails, footprints)
                composite = side_by_side(shown, panel)
                if composite.shape[1] != args.display_width:
                    k = args.display_width / composite.shape[1]
                    # INTER_AREA is the right filter for shrinking and a poor
                    # one for enlarging, where it degenerates to
                    # nearest-neighbour.
                    composite = cv2.resize(
                        composite, None, fx=k, fy=k,
                        interpolation=cv2.INTER_AREA if k < 1.0 else cv2.INTER_LINEAR)
                # JPEG-encoding every frame would cost more than the drawing;
                # a viewer needs far fewer.
                if video is not None and t_perf - last_video >= 1.0 / VIDEO_FPS:
                    video.publish(composite)
                    last_video = t_perf
                key = -1
                if not headless:
                    cv2.imshow(win, composite)
                    key = read_key()
                value = None
                if key == -1 and control is not None:
                    key, value = control.next()
                if key in (ord("q"), 27):
                    break
                elif key == ord("g"):
                    show_grid = not show_grid
                elif key == ord("t"):
                    show_trails = not show_trails
                elif key == ord("m"):
                    show_mask = not show_mask
                elif key == ord("r"):
                    tag_trails.clear()
                    ball_trail.clear()
                    for c in cams:
                        c.reset(args, field)
                    tag_merger.reset()
                    ball_merger.reset()
                elif key == ord("w"):
                    if using_calibrated:
                        print(f"field is calibrated ({cams[0].transform.source}); "
                              "pass --synthetic-field to demo the made-up field instead")
                    else:
                        mode = "floor" if mode == "wall" else "wall"
                        cams[0].set_transform(args, build_transform(args, field, mode), field)
                        plan = PlanView(field, height=tile_h, mode=mode)
                        tag_trails.clear()
                        ball_trail.clear()
                        cams[0].tag_tracker.reset()
                        cams[0].off_plane = OffPlaneWatch(synthetic=True)
                        print(f"transform: {cams[0].transform.source}")
                elif key in (ord("-"), ord("=")) or (key == StdinKeys.SET_CONC
                                                     and value is not None):
                    if key == StdinKeys.SET_CONC:
                        args.ball_min_conc = float(np.clip(value, 0.0, 255.0))
                    else:
                        step = -5.0 if key == ord("-") else 5.0
                        args.ball_min_conc = float(np.clip(args.ball_min_conc + step, 0.0, 255.0))
                    for c in cams:
                        c.ball_tracker.detector.min_concentration = args.ball_min_conc
                    print(f"min ball concentration {args.ball_min_conc:.0f}")
        except KeyboardInterrupt:
            # The only way out with --no-window, and fine with a window too.
            print("\ninterrupted -- shutting down")

    finally:
        for tags, stream in opened:
            if stream is not None:
                print(f"camera '{tags.name}': stream thread dropped {stream.dropped} frame(s) "
                      "the tracker was too busy to take")
        close_photon_cameras(opened)
        if http_server is not None:
            http_server.stop()
        if zmq_pub is not None:
            zmq_pub.stop()
        if json_log_file is not None:
            json_log_file.close()
        if video is not None:
            video.stop()
        if not headless:
            cv2.destroyAllWindows()


def tile_cameras(cams: list[CameraPipeline], field: Field, show_grid: bool, show_mask: bool,
                 height: int) -> np.ndarray:
    """Every camera's picture, with its own overlays, side by side at one
    height, in --photon-camera order."""
    tiles = []
    for i, c in enumerate(cams):
        img = c.draw(field, show_grid, show_mask, label=FOOTPRINT_COLORS[i % len(FOOTPRINT_COLORS)])
        if img.shape[0] != height:
            k = height / img.shape[0]
            img = cv2.resize(img, (max(1, round(img.shape[1] * k)), height),
                             interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
        tiles.append(img)
    return np.hstack(tiles)


def multi_header(cams: list[CameraPipeline], disagreement: dict[int, float],
                 tag_size: float) -> str:
    """The several-camera header: each camera's rate, and how far apart the
    cameras put the tags they share -- the number that says the calibrations
    agree (want it under ~1 cm)."""
    rates = "  ".join(f"{c.name} {c.fps:4.1f} fps" for c in cams)
    if disagreement:
        worst = max(disagreement.items(), key=lambda kv: kv[1])
        seam = f"overlap: cams agree to {worst[1] * 100:.1f} cm (tag {worst[0]})"
    else:
        seam = "overlap: no tag in two cameras right now"
    return f"{len(cams)} cameras  |  {rates}  |  tag {tag_size * 1000:.0f}mm  |  {seam}"


if __name__ == "__main__":
    main()
