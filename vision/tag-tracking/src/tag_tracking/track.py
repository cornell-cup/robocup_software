"""Live AprilTag tracking against a virtual field.

    uv run track --tag-size 0.080        # tags from PhotonVision (photon.py)
    uv run track --synthetic-camera      # no camera: watch it work on a fully simulated field
    uv run track --calibrate-live        # calibrate from reference tags, then track
    uv run photon-sim                    # no hardware, but the real PhotonVision path:
                                         # run this, then `track` in another terminal

Shows the camera feed with the virtual field drawn on it, a top-down plan view,
and each tag's position, heading and velocity in field coordinates. Velocity
comes from a per-tag Kalman filter (filter.py); the plan view draws the raw
per-frame reading as a small hollow ring and the filtered track as the solid
dot, with a white arrow showing where it will be in half a second, so the two
can be compared live.

The tags come from PhotonVision, which owns the camera and its lens
calibration; the picture behind the overlay is its camera stream, or a blank
canvas with --no-stream. The filter steps by PhotonVision's capture times, so
fps on screen is the rate its results arrive at.

The field is not real until calibrate-field has measured it: it is whatever
vision_core/field.py says it is -- and with --synthetic-camera, neither is the
camera feed; a fake, moving tag on a fake field is calibrated and tracked live,
with no hardware at all, through this exact same window. --calibrate-live is
the real-camera counterpart: point the camera at real reference tags (e.g. 4
printed AprilTags on a paper field) and it solves the field pose live, in this
same window, before tracking begins -- no separate calibrate-field run or saved
calib/field_pose.json required first.

Keys:
    q / Esc  quit           g  grid on/off          t  trails on/off
    w        wall / floor   r  reset trails and tracks
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np
from vision_core import intrinsics as intr
from vision_core.camera import read_key
from vision_core.field import (
    CameraFieldTransform,
    Field,
    ReferenceTagFieldTransform,
    SyntheticFieldTransform,
    resolve_field,
)
from vision_core.planview import OUT_COLOR, PlanView, color_for, draw_field, side_by_side

from .calibrate_field import (
    FRAMES_PER_CORNER,
    MIN_FRAMES as CALIB_MIN_FRAMES,
    LiveSource,
    calibrate_field,
    capture_reference_views,
    capture_sequential_corners,
    default_field_layout,
    field_pose_path,
    saved_origin,
    solve_sequential,
)
from .detect import LocalTagDetector, for_tracking
from .filter import SPEED_EPS, TagFieldState, TagTracker
from .photon import add_photon_args
from .pose import TagFieldPose, tag_field_pose

TRAIL_LEN = 90
GHOST_COLOR = (120, 120, 120)  # coasting: not actually seen this frame
VELOCITY_COLOR = (255, 255, 255)
HEADER_H = 38  # px, the band above the camera image
#: No PhotonVision result for this long and the tracks coast without it, so
#: the window stays live while its settings are being changed.
PHOTON_QUIET_S = 0.25


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------


def draw_tag_marks(frame: np.ndarray, det, pose: TagFieldPose, color) -> None:
    """Outline the detected tag in the video and label it."""
    quad = np.asarray(det.corners, np.int32)
    cv2.polylines(frame, [quad], True, color, 2, cv2.LINE_AA)
    c = np.asarray(det.center, int)
    cv2.circle(frame, tuple(c), 4, color, -1, cv2.LINE_AA)
    label = f"id {pose.tag_id}  {pose.x:+.2f}, {pose.y:+.2f} m"
    cv2.putText(frame, label, (c[0] + 10, c[1] - 10), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, color, 2, cv2.LINE_AA)


def draw_plan(
    plan: PlanView,
    states: list[TagFieldState],
    raw: list[TagFieldPose],
    trails: dict[int, deque],
    transform: CameraFieldTransform,
    show_trails: bool,
) -> np.ndarray:
    """Plan view: raw reading as a hollow ring, filtered track as the solid dot
    with a heading arrow and a velocity arrow."""
    panel = plan.base()
    plan.draw_camera(panel, transform)

    if show_trails:
        for tid, trail in trails.items():
            plan.draw_trail(panel, trail, color_for(tid))

    for pose in raw:
        p = plan.to_px(pose.x, pose.y)
        if plan.on_panel(p):
            cv2.circle(panel, p, 4, color_for(pose.tag_id), 1, cv2.LINE_AA)

    for s in states:
        color = color_for(s.tag_id) if s.visible else GHOST_COLOR
        p = plan.to_px(s.x, s.y)
        if not plan.on_panel(p):
            continue  # off the panel entirely
        # Heading arrow, 12 cm long, in field coords.
        plan.draw_arrow(panel, s.x, s.y, s.theta_deg, 0.12, color)
        if s.speed > SPEED_EPS:
            # Velocity arrow: where the filter says the tag will be in 0.5 s.
            plan.draw_arrow(panel, s.x, s.y, s.direction_deg, 0.5 * s.speed,
                            VELOCITY_COLOR, 1)
        cv2.circle(panel, p, 7, color, -1 if s.inside else 2, cv2.LINE_AA)
        cv2.putText(panel, str(s.tag_id), (p[0] + 9, p[1] - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 1, cv2.LINE_AA)
    return panel


def draw_readout(
    frame: np.ndarray,
    states: list[TagFieldState],
    transform: CameraFieldTransform,
    K: np.ndarray,
    tag_size: float,
    intr_source: str,
    fps: float,
    rows: int = 1,
) -> np.ndarray:
    """The text block: what the transform is, and one line per tag.

    Returns `frame` with a header band above it and the readout band below, so
    no text covers the camera image. The bottom band has room for `rows` tag
    lines (more if more tags are on screen); pass the most seen so far so the
    window does not change size every time a tag comes and goes.
    """
    w = frame.shape[1]
    n = max(len(states), rows, 1)
    strip_h = 20 + 24 * n
    frame = cv2.copyMakeBorder(frame, HEADER_H, strip_h, 0, 0,
                               cv2.BORDER_CONSTANT, value=(0, 0, 0))
    h = frame.shape[0]
    head = (
        f"{transform.source}  |  tag {tag_size * 1000:.0f} mm  |  "
        f"fx {K[0, 0]:.0f} ({intr.hfov_of(K, w):.0f} deg HFOV, {intr_source})"
        f"  |  {fps:4.1f} fps"
    )
    cv2.putText(frame, head, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (220, 220, 220), 1, cv2.LINE_AA)

    y = h - strip_h + 28
    if not states:
        cv2.putText(frame, "no tags detected", (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (140, 140, 210), 1, cv2.LINE_AA)
        return frame

    for s in states:
        color = color_for(s.tag_id) if s.visible else GHOST_COLOR
        where = "IN " if s.inside else "OUT"
        line = (
            f"id {s.tag_id:2d}   x {s.x:+6.3f}   y {s.y:+6.3f}   th {s.theta_deg:+7.1f}   "
            f"v {s.speed:4.2f} m/s   turn {s.omega_deg:+5.0f} d/s   "
            f"off-plane {s.off_plane_m:+6.3f}   {where}{'' if s.visible else '  coasting'}"
        )
        cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56,
                    color if s.inside else OUT_COLOR, 1, cv2.LINE_AA)
        y += 24
    return frame


# --------------------------------------------------------------------------


def build_transform(args, field: Field, mode: str) -> CameraFieldTransform:
    """The one call that decides where the field is.

    Swapping in ReferenceTagFieldTransform for the real RoboCup field means
    changing this function and nothing else.
    """
    if mode == "floor":
        return SyntheticFieldTransform.floor(field, args.cam_height, args.pitch)
    return SyntheticFieldTransform.wall(field, args.distance, args.yaw, args.pitch_wall)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Keys: q quit, g grid, t trails, w wall/floor, r reset",
    )
    ap.add_argument("--tag-size", type=float, default=0.080,
                    help="black-square width in metres (default 0.080)")
    ap.add_argument("--field", type=float, nargs=2, metavar=("W", "H"),
                    default=None,
                    help="field size in metres (default: the size saved with the "
                         "calibration, else 1.2 0.8)")
    ap.add_argument("--mode", choices=["wall", "floor"], default=None,
                    help="virtual field standing up facing you, or lying flat "
                         "(default wall; --synthetic-camera defaults to floor instead, "
                         "since a fronto-parallel tag is the worst case for the "
                         "detector's per-tag pose solver)")
    ap.add_argument("--distance", type=float, default=1.8,
                    help="wall mode: metres from camera to the field plane. The "
                         "default fits a 1.2x0.8 m field in a 60-degree view; "
                         "closer crops the field, further shrinks it")
    ap.add_argument("--yaw", type=float, default=0.0,
                    help="wall mode: rotate the field about vertical, degrees")
    ap.add_argument("--pitch-wall", type=float, default=0.0,
                    help="wall mode: tip the field top-away, degrees")
    ap.add_argument("--cam-height", type=float, default=1.5,
                    help="floor mode: camera height above the field, metres. The "
                         "default frames a 1.2x0.8 m field in a 60-degree view "
                         "with room to spare; below about 1.3 m the field no "
                         "longer fits")
    ap.add_argument("--pitch", type=float, default=90.0,
                    help="floor mode: camera downward tilt from horizontal, "
                         "degrees. 90 is straight down, directly over the field "
                         "centre, which is the rig this is built for. Lower it "
                         "only if your camera really is off to one side")
    ap.add_argument("--width", type=int, default=1280,
                    help="--synthetic-camera: image width")
    ap.add_argument("--height", type=int, default=720,
                    help="--synthetic-camera: image height")
    ap.add_argument("--heading-offset", type=float, default=0.0,
                    help="degrees added to theta, if the tag is not mounted upright")
    ap.add_argument("--display-width", type=int, default=1600,
                    help="scale the window to this width in pixels; "
                         "enlarges as well as shrinks")
    ap.add_argument("--print-poses", action="store_true",
                    help="also stream (id, x, y, theta, vx, vy, speed) to stdout, "
                         "one line per tracked tag per frame")
    ap.add_argument("--sigma-a", type=float, default=1.0,
                    help="filter process noise: how hard a robot may accelerate, "
                         "m/s^2. Raise it if the track lags behind a fast robot")
    ap.add_argument("--sigma-alpha", type=float, default=180.0,
                    help="filter process noise for turning, deg/s^2")
    ap.add_argument("--field-pose", type=str, default=None,
                    help="field pose file to load, or to save to with --calibrate-live "
                         "(default vision/calib/field_pose.json -- see calibrate-field)")
    ap.add_argument("--synthetic-field", action="store_true",
                    help="use the made-up field even if a calibrated one is saved")
    ap.add_argument("--corner-origin", action="store_true",
                    help="ignore any custom origin saved by calibrate-field and report "
                         "positions from the first field corner, as before")
    ap.add_argument("--synthetic-camera", action="store_true",
                    help="no PhotonVision: track an animated tag on a simulated field instead, "
                         "calibrating live from simulated reference tags")
    ap.add_argument("--calibrate-live", action="store_true",
                    help="calibrate the field pose live from reference tags "
                         "(e.g. 4 printed AprilTags on a paper field) before tracking, "
                         "instead of loading a previously saved calib/field_pose.json")
    ap.add_argument("--sequential", action="store_true",
                    help="--calibrate-live: one tag moved to each corner in turn "
                         "(camera fixed), e.g. a robot carrying a single AprilTag "
                         "driven to each corner, instead of all 4 tags visible at once")
    ap.add_argument("--frames-per-corner", type=int, default=FRAMES_PER_CORNER,
                    help=f"--sequential: samples to average at each corner "
                         f"(default {FRAMES_PER_CORNER})")
    add_photon_args(ap)
    args = ap.parse_args()

    pose_path = Path(args.field_pose) if args.field_pose else field_pose_path()
    field = resolve_field(
        args.field, None if (args.synthetic_camera or args.synthetic_field) else pose_path)
    mode = args.mode or ("floor" if args.synthetic_camera else "wall")
    live = None
    sim_cam = None
    calibrated_note = ""

    if args.synthetic_camera:
        from .synthetic import SyntheticFieldCamera, orbit_trajectory

        print("SIMULATED CAMERA -- no PhotonVision. The field, the camera, and the")
        print("tracked tag are all synthetic; everything else on screen -- overlay,")
        print("plan view, readout -- is exactly what a real run would show.\n")

        h, w = args.height, args.width
        K = intr.from_fov(w, h)
        dist, intr_source = np.zeros(5), "synthetic camera (ground truth K)"
        layout = default_field_layout(field)
        ground_truth = build_transform(args, field, mode)
        sim_cam = SyntheticFieldCamera(
            layout, ground_truth, tag_size=args.tag_size, shape=(h, w), K=K,
            trajectory=orbit_trajectory(field),
        )

        print(f"calibrating field pose from {len(layout)} simulated reference tags...")
        local = LocalTagDetector()
        calib_views = [local.detect(sim_cam.read()) for _ in range(20)]
        result = calibrate_field(calib_views, layout, K, min_frames=10,
                                 tag_size=args.tag_size)
        print(f"  {result.transform.source}")
        print(f"  agreement across frames: rotation spread "
              f"{result.rotation_spread_deg:.3f} deg, translation spread "
              f"{result.translation_spread_m * 1000:.2f} mm\n")
        transform = result.transform
        using_calibrated = True
        calibrated_note = "solved live from the simulated reference tags"
        if args.calibrate_live:
            print("note: --calibrate-live is ignored with --synthetic-camera "
                  "(the field pose is already solved live, from simulated tags)\n")
    else:
        live = LiveSource(args)
        w, h = live.tags.size
        K, dist, intr_source = live.K, live.dist, live.tags.source

        if args.calibrate_live:
            layout = default_field_layout(field)

            if args.sequential:
                print(f"calibrating live: move ONE tag to each of {len(layout)} corners of "
                      f"a {field.width:g} x {field.height:g} m field in turn, camera fixed")
                print("(e.g. a single AprilTag mounted on a robot, driven to each corner).")
                print("hold steady at each corner; 'q' confirms and moves to the next "
                      "('r' retries the current one), Esc aborts.\n")

                averaged = capture_sequential_corners(
                    live.read, layout, args.frames_per_corner,
                    window_name="calibrate-field (one tag, live)",
                )
                result = solve_sequential(averaged, layout, K, dist, tag_size=args.tag_size)
                print(f"\n{result.transform.source}")
                print(f"reprojection error: {result.reproj_error_px:.3f} px rms, "
                      f"{result.max_reproj_error_px:.3f} px max")
            else:
                print(f"calibrating live: point the camera at reference tags {sorted(layout)} "
                      f"at the corners of a {field.width:g} x {field.height:g} m field")
                print("(e.g. printed AprilTags taped to the corners of a paper rectangle).")
                print(f"hold steady; capturing until {CALIB_MIN_FRAMES}+ good frames, "
                      "'q' to finish, Esc to abort.\n")

                calib_views = capture_reference_views(
                    live.read, layout, min_frames=CALIB_MIN_FRAMES,
                    window_name="calibrate-field (live)",
                )
                result = calibrate_field(calib_views, layout, K, dist, min_frames=CALIB_MIN_FRAMES,
                                         tag_size=args.tag_size)
                print(f"\n{result.transform.source}")
                print(f"agreement across frames: rotation spread "
                      f"{result.rotation_spread_deg:.3f} deg, translation spread "
                      f"{result.translation_spread_m * 1000:.2f} mm")
            transform = result.transform
            using_calibrated = True
            calibrated_note = "solved live this session"

            out_path = Path(args.field_pose) if args.field_pose else field_pose_path()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # A custom origin is a point on the field, not on the camera, so it
            # survives re-solving the pose: carry it over into the new file.
            transform = transform.with_origin(saved_origin(out_path))
            transform.field_size = (field.width, field.height)
            transform.save(out_path)
            print(f"saved {out_path}\n")
            if args.corner_origin:
                transform = transform.with_origin(None)
        else:
            out_path = Path(args.field_pose) if args.field_pose else field_pose_path()
            using_calibrated = out_path.exists() and not args.synthetic_field
            if using_calibrated:
                transform = ReferenceTagFieldTransform.load(
                    out_path, use_origin=not args.corner_origin)
                calibrated_note = f"loaded from {out_path}"
                print(f"loaded calibrated field pose from {out_path}")
            else:
                transform = build_transform(args, field, mode)

    if getattr(transform, "origin", None) is not None:
        field = field.relative_to(transform.origin)
        print(f"custom origin: (0, 0) is ({transform.origin[0]:g}, {transform.origin[1]:g}) m "
              f"from the first corner (--corner-origin to ignore)")
        # On screen too, so a custom zero is never a surprise.
        transform.source += f", origin ({transform.origin[0]:g}, {transform.origin[1]:g})"

    local = LocalTagDetector() if sim_cam is not None else None

    show_grid, show_trails = True, True
    trails: dict[int, deque] = defaultdict(lambda: deque(maxlen=TRAIL_LEN))
    tracker = TagTracker(field, sigma_a=args.sigma_a, sigma_alpha_deg=args.sigma_alpha)
    plan = PlanView(field, height=h, mode=mode)
    readout_rows = 1  # most tag lines shown so far; the bottom band only grows
    fps, last_t = 0.0, None
    last_perf, quiet = time.perf_counter(), False

    print(f"\nfield {field.width:g} x {field.height:g} m, {transform.source}")
    print(f"tag size {args.tag_size * 1000:.1f} mm, intrinsics: {intr_source}")
    print("window open -- q or Esc to quit\n")

    win = "virtual field tracking"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

    # Undistort the whole picture, not just the tag corners: the overlay is
    # drawn on `frame`, so both must live in the same (pinhole) pixel space or
    # the outlines drift off the tags towards the edges. Maps are built once;
    # cv2.undistort rebuilds them every call (~8x slower). A blank canvas has
    # nothing to undistort.
    has_picture = live is None or live.stream is not None
    undistort_maps = intr.undistort_maps(K, dist, w, h) if has_picture else None

    while True:
        if sim_cam is not None:
            grey = sim_cam.read()
            frame = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
            raw = local.detect(grey)
            now = time.perf_counter()
            dt = 0.0 if last_t is None else now - last_t
            last_t = now
        else:
            result = live.tags.wait(PHOTON_QUIET_S)
            frame = live.frame()
            now = time.perf_counter()
            if result is not None:
                if quiet:
                    print("PhotonVision is back")
                    quiet = False
                # PhotonVision's capture time, not ours: the filter scales every
                # velocity by dt, and results reach us neither on time nor steadily.
                raw = result.detections
                dt = 0.0 if last_t is None else max(0.0, result.t - last_t)
                last_t = result.t
            else:
                if not quiet:
                    print("no results from PhotonVision -- tracks coasting until it is back")
                    quiet = True
                # Step on as if it saw nothing. Its clock moves by our gap too,
                # which keeps the next result's dt right.
                raw = []
                dt = now - last_perf
                if last_t is not None:
                    last_t += dt
            last_perf = now
        if dt > 0:
            fps = 0.9 * fps + 0.1 / dt

        if undistort_maps is not None:
            frame = cv2.remap(frame, *undistort_maps, cv2.INTER_LINEAR)
        dets = for_tracking(raw, K, dist, args.tag_size)

        poses = [
            tag_field_pose(d, transform, field, args.heading_offset, K=K) for d in dets
        ]
        states = tracker.update(poses, dt)

        draw_field(frame, field, transform, K, show_grid)
        for d, pose in zip(dets, poses):
            draw_tag_marks(frame, d, pose, color_for(pose.tag_id))
        for s in states:
            if s.visible:
                trails[s.tag_id].append((s.x, s.y))
        readout_rows = max(readout_rows, len(states))
        shown = draw_readout(frame, states, transform, K, args.tag_size, intr_source, fps,
                             rows=readout_rows)

        if args.print_poses:
            for s in states:
                print(f"{s.tag_id}\t{s.x:.4f}\t{s.y:.4f}\t{s.theta_deg:.2f}\t"
                      f"{s.vx:+.3f}\t{s.vy:+.3f}\t{s.speed:.3f}", flush=True)

        panel = draw_plan(plan, states, poses, trails, transform, show_trails)
        composite = side_by_side(shown, panel)
        if composite.shape[1] != args.display_width:
            k = args.display_width / composite.shape[1]
            # INTER_AREA is the right filter for shrinking and a poor one
            # for enlarging, where it degenerates to nearest-neighbour.
            composite = cv2.resize(
                composite, None, fx=k, fy=k,
                interpolation=cv2.INTER_AREA if k < 1.0 else cv2.INTER_LINEAR)
        cv2.imshow(win, composite)

        key = read_key()
        if key in (ord("q"), 27):
            break
        elif key == ord("g"):
            show_grid = not show_grid
        elif key == ord("t"):
            show_trails = not show_trails
        elif key == ord("r"):
            trails.clear()
            tracker.reset()
        elif key == ord("w"):
            if using_calibrated:
                extra = ("restart without --synthetic-camera for a movable wall/floor demo"
                          if sim_cam is not None else
                          "pass --synthetic-field to demo the made-up field instead")
                print(f"field is calibrated ({calibrated_note}); {extra}")
            else:
                mode = "floor" if mode == "wall" else "wall"
                transform = build_transform(args, field, mode)
                plan = PlanView(field, height=h, mode=mode)
                trails.clear()
                tracker.reset()  # positions jump with the transform; old velocities are junk
                print(f"transform: {transform.source}")

    if live is not None:
        live.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
