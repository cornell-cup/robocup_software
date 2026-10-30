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
"""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
from vision_core.field import Field, SyntheticFieldTransform
from vision_core.intrinsics import from_fov
from vision_core.mjpeg import MjpegServer

from .calibrate_field import default_field_layout
from .detect import LocalTagDetector, TagDetection
from .photon import STREAM_PORT
from .synthetic import SyntheticFieldCamera, orbit_trajectory


def distortion_maps(K: np.ndarray, dist: np.ndarray, width: int, height: int):
    """Remap tables that bend a pinhole image through the lens `dist`: for
    each pixel of the distorted picture, where it comes from in the pinhole one."""
    u, v = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    pts = np.stack([u.ravel(), v.ravel()], axis=1).reshape(-1, 1, 2)
    src = cv2.undistortPoints(pts, K, dist, P=K).reshape(height, width, 2)
    return src[..., 0].astype(np.float32), src[..., 1].astype(np.float32)


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default="overhead", help="camera nickname to publish as")
    ap.add_argument("--nt-server", default="127.0.0.1",
                    help="NetworkTables server to connect to, as PhotonVision would")
    ap.add_argument("--tag-size", type=float, default=0.080)
    ap.add_argument("--field", type=float, nargs=2, metavar=("W", "H"), default=(1.2, 0.8))
    ap.add_argument("--cam-height", type=float, default=1.5)
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
    truth = SyntheticFieldTransform.floor(field, args.cam_height, args.pitch)
    cam = SyntheticFieldCamera(default_field_layout(field), truth, tag_size=args.tag_size,
                               shape=(h, w), K=K, trajectory=orbit_trajectory(field))
    maps = distortion_maps(K, dist, w, h) if args.k1 else None
    detector = LocalTagDetector()

    inst = ntcore.NetworkTableInstance.create()
    inst.setServer(args.nt_server)
    inst.startClient4("photon-sim")
    photon = FakePhotonVision(inst, args.name, K, dist)
    stream = MjpegServer(args.stream_port)
    stream_size = (max(1, round(w * args.stream_scale)), max(1, round(h * args.stream_scale)))

    print(f"stand-in PhotonVision camera '{args.name}', {w}x{h}, k1 {args.k1:g}, "
          f"NetworkTables client of {args.nt_server}")
    print(f"ground truth: {truth.source}")
    print(f"stream: http://localhost:{args.stream_port}/stream.mjpg "
          f"({stream_size[0]}x{stream_size[1]})")
    print("Ctrl+C to stop\n")

    period = 1.0 / args.fps
    next_t = time.monotonic()
    was_connected = False
    try:
        while True:
            capture_us = int(time.monotonic() * 1e6)
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
            time.sleep(max(0.0, next_t - time.monotonic()))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stream.stop()
        inst.stopClient()


if __name__ == "__main__":
    main()
