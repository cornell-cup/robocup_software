"""A stand-in PhotonVision, for running the PhotonVision path with no hardware.

    uv run photon-sim                        # then, in another terminal:
    uv run track --synthetic-field --mode floor

--synthetic-field because a saved calib/field_pose.json is your real camera's,
not this one's; the made-up floor field's defaults (1.2 x 0.8 m, 1.5 m up,
straight down) are this camera's ground truth. calibrate-field works against
it too, but pass --out somewhere else or it replaces the real field pose.

Renders the synthetic field (synthetic.py: four reference tags at the corners
and one tag driving around), bends it through a lens with real distortion,
finds the tags with PhotonVision's own detector, and publishes them the way
PhotonVision does: same NetworkTables topics, same message format, same lens
calibration topics, and the picture as an MJPEG stream on the same port. What
it does not imitate is PhotonVision's UI, its camera settings and its 3D pose
(the trackers here solve their own, see detect.py).

Like the real thing, it is a NetworkTables client: start the tracker first,
or start this first and it connects once the tracker's server is up.

Several cameras, side by side along the field's width, each straight down
over its own section with some overlap -- the setup the multi-camera tracker
is for:

    uv run photon-sim --cameras left right --field 2.6 1.2 --save-poses runs/sim
    uv run track-combined --photon-camera left --photon-camera right \\
        --field-pose runs/sim/field_pose_left.json \\
        --field-pose runs/sim/field_pose_right.json --ball-profile test

Each camera streams on its own port pair (1181, 1183, ...), as PhotonVision
does; the field has split_field_layout()'s reference tags (corners plus a pair
on each seam), so calibrate-field --split works against it too. --save-poses
writes each camera's true pose, to track without calibrating first.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
from vision_core.field import Field, ReferenceTagFieldTransform, SyntheticFieldTransform
from vision_core.intrinsics import from_fov
from vision_core.mjpeg import MjpegServer

from .calibrate_field import default_field_layout, split_field_layout
from .detect import LocalTagDetector, TagDetection, diagonal_centre
from .photon import STREAM_PORT
from .synthetic import SyntheticFieldCamera, orbit_trajectory


def distortion_maps(K: np.ndarray, dist: np.ndarray, width: int, height: int):
    """Remap tables that bend a pinhole image through the lens `dist`: for
    each pixel of the distorted picture, where it comes from in the pinhole one."""
    u, v = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    pts = np.stack([u.ravel(), v.ravel()], axis=1).reshape(-1, 1, 2)
    src = cv2.undistortPoints(pts, K, dist, P=K).reshape(height, width, 2)
    return src[..., 0].astype(np.float32), src[..., 1].astype(np.float32)


def learn_corner_order(K: np.ndarray, shape: tuple[int, int]) -> list[int]:
    """Which of tag_corners_field()'s corners the detector reports first,
    second, ...: found once by rendering one tag and detecting it, so --fast
    hands pose.py corners in exactly the detector's order (its heading
    depends on it), whatever that order is."""
    from .synthetic import _warp_tag, tag_corners_field

    h, w = shape
    field = Field(1.0, 1.0)
    truth = SyntheticFieldTransform.floor(field, 1.0, 90.0)
    frame = np.full(shape, 210, np.uint8)
    rvec, tvec = truth.rvec_tvec()
    pose = (0.5, 0.5, 23.0, 0.15)
    _warp_tag(frame, 3, *pose[:3], pose[3], rvec, tvec, K)
    dets = LocalTagDetector().detect(frame)
    if len(dets) != 1:
        raise RuntimeError("--fast: could not detect the reference tag to learn the corner order")
    proj, _ = cv2.projectPoints(tag_corners_field(*pose), rvec, tvec, K, np.zeros(5))
    proj = proj.reshape(4, 2)
    return [int(np.argmin(np.linalg.norm(proj - c, axis=1))) for c in dets[0].corners]


class ProjectedTags:
    """--fast: what the detector would find, without a picture: each tag's
    corners projected through the camera (lens distortion included, as the
    raw pixels PhotonVision sends), with `noise_px` of jitter, kept only if
    the whole tag is in frame. Exact, and fast enough for 30 fps per camera."""

    def __init__(self, truth, K: np.ndarray, dist: np.ndarray, size: tuple[int, int],
                 tag_size: float, order: list[int], noise_px: float, margin_px: float = 2.0):
        self.rvec, self.tvec = truth.rvec_tvec()
        self.K, self.dist, self.size = K, dist, size
        self.tag_size, self.order, self.noise_px = tag_size, order, noise_px
        self.margin_px = margin_px
        self._rng = np.random.default_rng()

    def corners(self, x: float, y: float, theta: float) -> np.ndarray | None:
        from .synthetic import tag_corners_field

        obj = tag_corners_field(x, y, theta, self.tag_size)
        cam = obj @ cv2.Rodrigues(self.rvec)[0].T + self.tvec.reshape(3)
        if (cam[:, 2] <= 0).any():
            return None
        px, _ = cv2.projectPoints(obj, self.rvec, self.tvec, self.K, self.dist)
        px = px.reshape(4, 2)[self.order]
        w, h = self.size
        m = self.margin_px
        if not ((px[:, 0] >= m) & (px[:, 0] < w - m) & (px[:, 1] >= m) & (px[:, 1] < h - m)).all():
            return None
        return px

    def detect(self, tags: list[tuple[int, float, float, float]]) -> list[TagDetection]:
        out = []
        for tid, x, y, th in tags:
            px = self.corners(x, y, th)
            if px is None:
                continue
            px = px + self._rng.normal(0.0, self.noise_px, px.shape)
            out.append(TagDetection(tag_id=tid, center=diagonal_centre(px), corners=px))
        return out


class FakePhotonVision:
    """Publishes tag detections as PhotonVision camera `name` would."""

    def __init__(self, inst, name: str, K: np.ndarray, dist: np.ndarray) -> None:
        import photonlibpy.generated  # noqa: F401 -- registers the message serde
        from photonlibpy.networktables.NTTopicSet import NTTopicSet

        self._ts = NTTopicSet(inst.getTable("photonvision").getSubTable(name))
        self._ts.updateEntries()
        self._K = [float(v) for v in np.asarray(K, np.float64).ravel()]  # row-major
        d = np.zeros(8)
        d[:min(8, len(dist))] = np.asarray(dist, np.float64)[:8]
        self._dist = [float(v) for v in d]
        self._seq = 0

    def publish(self, dets: list[TagDetection], capture_us: int) -> None:
        from photonlibpy.targeting.photonPipelineResult import (
            PhotonPipelineMetadata,
            PhotonPipelineResult,
        )
        from photonlibpy.targeting.photonTrackedTarget import PhotonTrackedTarget
        from photonlibpy.targeting.TargetCorner import TargetCorner

        targets = []
        for d in dets:
            box = cv2.boxPoints(cv2.minAreaRect(d.corners.astype(np.float32)))
            targets.append(PhotonTrackedTarget(
                fiducialId=d.tag_id,
                # As PhotonVision does: the detector's corners, unreordered.
                detectedCorners=[TargetCorner(float(x), float(y)) for x, y in d.corners],
                minAreaRectCorners=[TargetCorner(float(x), float(y)) for x, y in box],
            ))
        self._seq += 1
        publish_us = int(time.monotonic() * 1e6)
        result = PhotonPipelineResult(
            metadata=PhotonPipelineMetadata(
                captureTimestampMicros=capture_us, publishTimestampMicros=publish_us,
                sequenceID=self._seq, timeSinceLastPong=0),
            targets=targets,
        )
        self._ts.rawBytesEntry.set(PhotonPipelineResult.photonStruct.pack(result).getData())
        self._ts.hasTargetEntry.set(bool(targets))
        self._ts.latencyMillisEntry.set((publish_us - capture_us) / 1e3)
        self._ts.heartbeatPublisher.set(self._seq)
        self._ts.cameraIntrinsicsPublisher.set(self._K)
        self._ts.cameraDistortionPublisher.set(self._dist)


def split_height(field: Field, n: int, overlap: float, K: np.ndarray,
                 size: tuple[int, int], margin: float) -> float:
    """How high n straight-down cameras, side by side along the field's width,
    must hang for each to see its section plus `overlap` and the field's full
    height, `margin` beyond every edge."""
    w, h = size
    half_w = (field.width / n + overlap) / 2.0 + margin
    half_h = field.height / 2.0 + margin
    return max(half_w * K[0, 0] / (w / 2.0), half_h * K[1, 1] / (h / 2.0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="overhead", help="camera nickname to publish as")
    ap.add_argument("--cameras", nargs="+", default=None, metavar="NAME",
                    help="several cameras instead, side by side along the field's width, "
                         "in this order from the -X end; each streams on the next port "
                         "pair (1181, 1183, ...)")
    ap.add_argument("--overlap", type=float, default=0.3,
                    help="--cameras: metres each neighbouring pair of views overlaps by, at "
                         "least (default 0.3)")
    ap.add_argument("--save-poses", default=None, metavar="DIR",
                    help="write each camera's true pose to DIR/field_pose_<name>.json, to "
                         "pass to the tracker's --field-pose")
    ap.add_argument("--fast", action="store_true",
                    help="don't render and detect: project each tag's corners straight "
                         "through the camera (exact, plus --noise-px), which keeps up 30 fps "
                         "on several cameras. The stream is a still picture of the field "
                         "with the moving tag drawn as a square")
    ap.add_argument("--noise-px", type=float, default=0.3,
                    help="--fast: corner jitter, px (default 0.3)")
    ap.add_argument("--nt-server", default="127.0.0.1",
                    help="NetworkTables server to connect to, as PhotonVision would")
    ap.add_argument("--tag-size", type=float, default=0.080)
    ap.add_argument("--field", type=float, nargs=2, metavar=("W", "H"), default=(1.2, 0.8))
    ap.add_argument("--cam-height", type=float, default=None,
                    help="metres up (default 1.5; with --cameras, as high as the views "
                         "need to be for the overlap)")
    ap.add_argument("--pitch", type=float, default=90.0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--hfov", type=float, default=60.0)
    ap.add_argument("--k1", type=float, default=-0.12,
                    help="radial lens distortion to render with and publish, so the "
                         "undistortion path runs too (0 for a pinhole lens)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--stream-port", type=int, default=STREAM_PORT)
    ap.add_argument("--stream-scale", type=float, default=1.0,
                    help="send the stream at this fraction of the pipeline resolution, "
                         "as PhotonVision's stream resolution setting does")
    args = ap.parse_args()

    import ntcore

    field = Field(*args.field)
    w, h = args.width, args.height
    K = from_fov(w, h, args.hfov)
    dist = np.array([args.k1, 0.0, 0.0, 0.0, 0.0])
    names = args.cameras or [args.name]
    n = len(names)
    if len(set(names)) != n:
        raise SystemExit("--cameras: every camera needs its own name")
    if n == 1:
        layout = default_field_layout(field)
        height = 1.5 if args.cam_height is None else args.cam_height
        truths = [SyntheticFieldTransform.floor(field, height, args.pitch)]
    else:
        if abs(args.pitch - 90.0) > 1e-6:
            raise SystemExit("--cameras: the cameras look straight down; drop --pitch")
        layout = split_field_layout(field, n)
        height = args.cam_height or split_height(field, n, args.overlap, K, (w, h),
                                                 margin=args.tag_size)
        truths = [SyntheticFieldTransform.floor(
            field, height, 90.0, centre=(field.width * (k + 0.5) / n, field.height / 2.0))
            for k in range(n)]
    trajectory = orbit_trajectory(field)
    cams = [SyntheticFieldCamera(layout, truth, tag_size=args.tag_size, shape=(h, w), K=K,
                                 trajectory=trajectory) for truth in truths]
    # One clock, the wall clock: the moving tag is in one place for every
    # camera, and anyone can tell where it truly was at any instant --
    # orbit_trajectory(field)(time.time()) -- to check a tracker against.
    t0 = time.perf_counter() - time.time()
    for cam in cams:
        cam._t0 = t0
    maps = distortion_maps(K, dist, w, h) if args.k1 else None
    detector = LocalTagDetector()
    projectors, stills = [], []
    if args.fast:
        order = learn_corner_order(K, (h, w))
        projectors = [ProjectedTags(truth, K, dist, (w, h), args.tag_size, order, args.noise_px)
                      for truth in truths]
        for cam in cams:
            moving, cam.trajectory, cam.noise_std = cam.trajectory, None, 0.0
            grey = cam.read()  # the field and its reference tags, once
            if maps is not None:
                grey = cv2.remap(grey, *maps, cv2.INTER_LINEAR, borderValue=210)
            stills.append(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR))
            cam.trajectory = moving
        statics = [(tid, x, y, 0.0) for tid, (x, y) in layout.items()]

    if args.save_poses:
        out_dir = Path(args.save_poses)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, truth in zip(names, truths):
            pose = ReferenceTagFieldTransform(truth.R, truth.t, f"photon-sim truth, {truth.source}")
            pose.field_size = (field.width, field.height)
            pose.camera = name
            path = out_dir / f"field_pose_{name}.json"
            pose.save(path)
            print(f"saved camera '{name}' true pose to {path}")

    inst = ntcore.NetworkTableInstance.create()
    inst.setServer(args.nt_server)
    inst.startClient4("photon-sim")
    photons = [FakePhotonVision(inst, name, K, dist) for name in names]
    streams = [MjpegServer(args.stream_port + 2 * k) for k in range(n)]
    stream_size = (max(1, round(w * args.stream_scale)), max(1, round(h * args.stream_scale)))

    print(f"stand-in PhotonVision, {n} camera(s), {w}x{h}, k1 {args.k1:g}, "
          f"NetworkTables client of {args.nt_server}")
    print(f"reference tags: {', '.join(f'{t} ({x:g}, {y:g})' for t, (x, y) in layout.items())}")
    for k, (name, truth) in enumerate(zip(names, truths)):
        print(f"camera '{name}': {truth.source}; stream "
              f"http://localhost:{args.stream_port + 2 * k}/stream.mjpg "
              f"({stream_size[0]}x{stream_size[1]})")
    print("Ctrl+C to stop\n")

    period = 1.0 / args.fps
    next_t = time.monotonic()
    was_connected = False
    try:
        while True:
            for k, (cam, photon, stream) in enumerate(zip(cams, photons, streams)):
                capture_us = int(time.monotonic() * 1e6)
                if args.fast:
                    moving = trajectory(time.perf_counter() - t0)
                    photon.publish(projectors[k].detect(statics + [moving]), capture_us)
                    bgr = stills[k].copy()
                    px = projectors[k].corners(*moving[1:])
                    if px is not None:
                        cv2.fillConvexPoly(bgr, px.astype(np.int32), (40, 40, 40), cv2.LINE_AA)
                    if stream_size != (w, h):
                        bgr = cv2.resize(bgr, stream_size, interpolation=cv2.INTER_AREA)
                    stream.publish(bgr)
                    continue
                grey = cam.read()
                if maps is not None:
                    grey = cv2.remap(grey, *maps, cv2.INTER_LINEAR, borderValue=210)
                photon.publish(detector.detect(grey), capture_us)
                bgr = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
                if stream_size != (w, h):
                    bgr = cv2.resize(bgr, stream_size, interpolation=cv2.INTER_AREA)
                stream.publish(bgr)

            if inst.isConnected() != was_connected:
                was_connected = inst.isConnected()
                print("connected to the NetworkTables server" if was_connected
                      else "waiting for a NetworkTables server ...")
            next_t += period
            # Never try to catch up: a slow frame just makes the next one late.
            next_t = max(next_t, time.monotonic())
            time.sleep(max(0.0, next_t - time.monotonic()))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        for stream in streams:
            stream.stop()
        inst.stopClient()


if __name__ == "__main__":
    main()
