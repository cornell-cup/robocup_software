"""PhotonVision as the tag detector.

PhotonVision owns the camera: it captures, detects AprilTags, and publishes
each frame's tags over NetworkTables, where PhotonTags picks them up. What
comes across is each tag's id and its four corners in raw (distorted) image
pixels, plus the lens calibration done in PhotonVision's own UI. The pose and
everything after it is still worked out here (detect.py, pose.py), so the
field maths are the ones selfcheck already proves.

The picture itself is a separate thing: PhotonVision serves its camera as an
MJPEG stream, which PhotonStream reads for the overlay and the ball tracker.
The tags never depend on it -- track runs without one (--no-stream) and draws
on a blank canvas instead.

NetworkTables needs one server. By default it runs here, the way a roboRIO
would on an FRC robot: set PhotonVision's "NetworkTables Server Address"
(Settings > Networking) to this machine's IP, or 127.0.0.1 if PhotonVision runs
on this machine too. --nt-server connects to an existing server instead.
"""

from __future__ import annotations

import argparse
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .detect import TagDetection, diagonal_centre

#: PhotonVision serves its first camera's raw input stream here; each further
#: camera gets the next two ports (input, then annotated output).
STREAM_PORT = 1181
#: Its web UI, where the camera is configured and calibrated.
UI_PORT = 5800
CONNECT_TIMEOUT_S = 20.0

#: Resolutions webcams actually deliver, for guessing which one PhotonVision is
#: running from its calibration (it does not publish the resolution itself).
COMMON_SIZES = [
    (320, 240), (640, 360), (640, 480), (800, 448), (800, 600), (960, 540),
    (1024, 576), (1024, 768), (1280, 720), (1280, 800), (1280, 960), (1280, 1024),
    (1600, 896), (1600, 900), (1600, 1200), (1920, 1080), (1920, 1200),
    (2560, 1440), (3840, 2160),
]


def add_photon_args(ap: argparse.ArgumentParser, *, stream_optional: bool = True) -> None:
    g = ap.add_argument_group("PhotonVision")
    g.add_argument("--photon-camera", default=None, metavar="NAME",
                   help="camera nickname in the PhotonVision UI (default: the only "
                        "camera PhotonVision publishes)")
    g.add_argument("--nt-server", default=None, metavar="HOST",
                   help="connect to the NetworkTables server at HOST instead of running "
                        "one here (default: run it here, and point PhotonVision's "
                        "'NetworkTables Server Address' at this machine)")
    g.add_argument("--stream", default=None, metavar="URL",
                   help=f"PhotonVision's camera stream (default "
                        f"http://<photonvision>:{STREAM_PORT}/stream.mjpg)")
    if stream_optional:
        g.add_argument("--no-stream", action="store_true",
                       help="don't open the camera stream: tags only, drawn on a "
                            "blank canvas")
    g.add_argument("--photon-res", type=int, nargs=2, metavar=("W", "H"), default=None,
                   help="the resolution PhotonVision's pipeline runs at (default: "
                        "worked out from its calibration's principal point)")


def guess_size(K: np.ndarray) -> tuple[int, int]:
    """The pipeline resolution, from where the calibration put the principal
    point -- near the image centre, though never exactly on it."""
    cx, cy = float(K[0, 2]), float(K[1, 2])
    return min(COMMON_SIZES, key=lambda s: abs(s[0] - 2 * cx) / s[0] + abs(s[1] - 2 * cy) / s[1])


def _camera_matrix(values) -> np.ndarray | None:
    """PhotonVision's 9 doubles as a 3x3 matrix, row-major; None if they are
    not a camera matrix at all (missing, or no calibration yet)."""
    if values is None or len(values) != 9:
        return None
    K = np.array(values, np.float64).reshape(3, 3)
    if abs(K[2, 0]) > 1.0 and abs(K[0, 2]) < 1.0:
        K = K.T  # column-major after all
    if K[0, 0] <= 0 or K[1, 1] <= 0 or abs(K[2, 2] - 1.0) > 1e-6:
        return None
    return K


@dataclass
class PhotonResult:
    """One PhotonVision frame's tags. `t` is its capture time in PhotonVision's
    clock, seconds: only differences between results mean anything.

    `t_wall` is the same instant on this machine's time.time() clock: when the
    result arrived, less the capture-to-publish time PhotonVision reports. The
    network hop in between is not counted (well under a millisecond on one
    machine, a few over Ethernet)."""

    t: float
    t_wall: float
    latency_ms: float
    detections: list[TagDetection]
    #: Results that arrived since the last poll and were passed over for this one.
    skipped: int = 0


class PhotonTags:
    """Tags from one PhotonVision camera, over NetworkTables."""

    def __init__(
        self,
        camera: str | None = None,
        server: str | None = None,
        res: tuple[int, int] | None = None,
        timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        import ntcore

        self._inst = ntcore.NetworkTableInstance.getDefault()
        self._nt_now = ntcore._now  # microseconds, the clock NT stamps arrivals with
        self.server = server
        if server is None:
            # Nothing here is persistent; keep ntcore's persistence file out of
            # whatever directory the tracker was started from.
            self._inst.startServer(str(Path(tempfile.gettempdir()) / "robocup-networktables.json"))
            print("NetworkTables server running here -- PhotonVision connects to it")
        else:
            self._inst.setServer(server)
            self._inst.startClient4("robocup-vision")
            print(f"NetworkTables: connecting to {server}")
        try:
            self._connect(camera, timeout_s)
        except BaseException:
            self.close()  # a caller that carries on must not inherit the server
            raise
        self.size: tuple[int, int] = (int(res[0]), int(res[1])) if res else guess_size(self.K)
        self.source = f"PhotonVision '{self.name}' calibration"
        print(f"PhotonVision camera '{self.name}': fx {self.K[0, 0]:.1f}, "
              f"{self.size[0]}x{self.size[1]}"
              + ("" if res else " (guessed from the calibration; --photon-res to set)"))
        self._warned_not_tags = False

    # -- connecting -------------------------------------------------------

    def _connect(self, camera: str | None, timeout_s: float) -> None:
        import ntcore
        from photonlibpy.photonCamera import PhotonCamera

        # A client only hears about topics it subscribes to; this makes every
        # PhotonVision camera's topics visible, for _find_camera().
        self._topics = ntcore.MultiSubscriber(
            self._inst, ["/photonvision/"], ntcore.PubSubOptions(topicsOnly=True))
        self.name = camera or self._find_camera(timeout_s)
        self._cam = PhotonCamera(self.name)
        table = self._inst.getTable("photonvision").getSubTable(self.name)
        self._intrinsics = table.getDoubleArrayTopic("cameraIntrinsics").subscribe([])
        self._distortion = table.getDoubleArrayTopic("cameraDistortion").subscribe([])
        self.K, self.dist = self._wait_for_intrinsics(timeout_s)

    def _camera_names(self) -> list[str]:
        names = set()
        for topic in self._inst.getTopics("/photonvision/"):
            parts = topic.getName().strip("/").split("/")
            if len(parts) == 3 and parts[2] == "rawBytes":
                names.add(parts[1])
        return sorted(names)

    def _find_camera(self, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        print("waiting for PhotonVision on NetworkTables ...")
        while time.monotonic() < deadline:
            names = self._camera_names()
            if len(names) == 1:
                return names[0]
            if len(names) > 1:
                raise SystemExit(f"PhotonVision publishes several cameras ({', '.join(names)}); "
                                 f"pick one with --photon-camera")
            time.sleep(0.1)
        if self.server is None:
            how = ("in its UI (http://<photonvision>:%d) open Settings > Networking and set "
                   "the NetworkTables server address to this machine's IP -- 127.0.0.1 if "
                   "PhotonVision runs here" % UI_PORT)
        else:
            how = f"check that {self.server} is running a NetworkTables server PhotonVision uses"
        raise SystemExit(f"no PhotonVision camera appeared on NetworkTables within "
                         f"{timeout_s:g} s.\nIs PhotonVision running, with a camera enabled? "
                         f"Then {how}.")

    def _wait_for_intrinsics(self, timeout_s: float) -> tuple[np.ndarray, np.ndarray]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            K = _camera_matrix(self._intrinsics.get())
            if K is not None:
                dist = np.zeros(8)
                d = np.asarray(self._distortion.get(), np.float64)[:8]
                dist[:len(d)] = d
                return K, dist
            time.sleep(0.1)
        raise SystemExit(
            f"PhotonVision camera '{self.name}' publishes no lens calibration. Calibrate it "
            f"in PhotonVision's UI (Cameras > Calibration) at the resolution its AprilTag "
            f"pipeline runs at: the field pose and every tag position are solved with it.")

    def host(self) -> str | None:
        """Where PhotonVision runs: the server we connected to, or the address
        PhotonVision connected to us from."""
        if self.server is not None:
            return self.server
        conns = self._inst.getConnections()
        # Other clients (a dashboard, say) may be connected too; PhotonVision
        # identifies itself as "photonvision".
        photon = [c for c in conns if "photon" in c.remote_id.lower()]
        conns = photon or conns
        return conns[0].remote_ip if conns else None

    # -- results ----------------------------------------------------------

    def _detection(self, target) -> TagDetection | None:
        corners = target.getDetectedCorners()
        if target.getFiducialId() < 0 or len(corners) != 4:
            if not self._warned_not_tags:
                self._warned_not_tags = True
                print(f"warning: PhotonVision camera '{self.name}' is sending targets that "
                      f"are not AprilTags -- switch its pipeline to AprilTag, family 36h11")
            return None
        c = np.array([[p.x, p.y] for p in corners], np.float64)
        return TagDetection(tag_id=int(target.getFiducialId()), center=diagonal_centre(c),
                            corners=c)

    def poll(self) -> PhotonResult | None:
        """The newest result since the last call, or None if nothing new arrived.
        Older unread ones are dropped: a tracker wants the latest, and measures
        its dt between capture times, so a skipped result costs nothing but the
        measurement."""
        results = self._cam.getAllUnreadResults()
        if not results:
            return None
        r = results[-1]
        dets = [d for d in map(self._detection, r.getTargets()) if d is not None]
        # ntReceiveTimestampMicros is ntcore's local clock, not time.time()'s:
        # carry it over as an age, which both clocks agree on.
        received_s_ago = max(0.0, (self._nt_now() - r.ntReceiveTimestampMicros) / 1e6)
        latency_ms = r.getLatencyMillis()
        return PhotonResult(t=r.metadata.captureTimestampMicros / 1e6,
                            t_wall=time.time() - received_s_ago - latency_ms / 1e3,
                            latency_ms=latency_ms, detections=dets,
                            skipped=len(results) - 1)

    def wait(self, timeout_s: float = 2.0) -> PhotonResult | None:
        """poll(), but waiting up to `timeout_s` for a result to arrive."""
        deadline = time.monotonic() + timeout_s
        while True:
            r = self.poll()
            if r is not None or time.monotonic() >= deadline:
                return r
            time.sleep(0.002)

    def close(self) -> None:
        if self.server is None:
            self._inst.stopServer()
        else:
            self._inst.stopClient()


class PhotonStream:
    """PhotonVision's camera stream, read on a background thread so a frame is
    never older than the newest one sent, and scaled to the pipeline resolution
    so it lines up with the tag corners and K.

    The stream is the raw camera picture (distorted, like the corners), not
    the annotated output -- the ball tracker wants the real colours.
    """

    def __init__(self, url: str, size: tuple[int, int] | None, timeout_s: float = 5.0) -> None:
        """`size` None keeps the stream's own resolution: for uses that need
        the picture but not the pipeline's pixel space (ball colour sampling)."""
        self.url = url
        self._timeout_s = timeout_s
        ms = int(timeout_s * 1000)
        self._cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, ms])
        if not self._cap.isOpened():
            raise OSError(f"could not open PhotonVision's stream at {url}")
        ok, first = self._cap.read()
        if not ok:
            self._cap.release()
            raise OSError(f"PhotonVision's stream at {url} opened but sent no frame")
        native = (first.shape[1], first.shape[0])
        self.size = native if size is None else (int(size[0]), int(size[1]))
        if native != self.size:
            print(f"stream is {first.shape[1]}x{first.shape[0]}, pipeline "
                  f"{self.size[0]}x{self.size[1]}: scaling frames to match")
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = self._fit(first)
        self._last: np.ndarray = self._frame
        self._t_capture, self._t_perf = time.time(), time.perf_counter()
        self._failed = False
        self._stopping = False
        #: Frames overwritten before anyone took them. Cumulative.
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        if (frame.shape[1], frame.shape[0]) == self.size:
            return frame
        return cv2.resize(frame, self.size, interpolation=cv2.INTER_LINEAR)

    def _run(self) -> None:
        while not self._stopping:
            ok, frame = self._cap.read()
            t_capture, t_perf = time.time(), time.perf_counter()
            with self._cond:
                if not ok:
                    self._failed = True
                    self._cond.notify_all()
                    return
                if self._frame is not None:
                    self.dropped += 1
                self._frame = self._last = self._fit(frame)
                self._t_capture, self._t_perf = t_capture, t_perf
                self._cond.notify_all()

    def read(self) -> tuple[np.ndarray, float, float] | None:
        """The newest frame not yet handed out, as (frame, t_capture, t_perf).
        Waits for one if need be. None once the stream stops."""
        with self._cond:
            while self._frame is None and not self._failed:
                # Short waits: on Windows a blocked wait ignores Ctrl+C.
                self._cond.wait(0.1)
            if self._frame is None:
                return None
            frame, self._frame = self._frame, None
            return frame, self._t_capture, self._t_perf

    def latest(self) -> np.ndarray:
        """The newest frame, whether or not it was handed out before."""
        with self._cond:
            return self._last

    @property
    def failed(self) -> bool:
        return self._failed

    def stop(self) -> None:
        self._stopping = True
        # The thread may be inside read(), which can block for the whole read
        # timeout; releasing the capture under it crashes FFmpeg. If it is
        # still stuck after that, leave the capture to process exit.
        self._thread.join(timeout=self._timeout_s + 1.0)
        if not self._thread.is_alive():
            self._cap.release()


def stream_url(args, tags: PhotonTags) -> str | None:
    if args.stream:
        return args.stream
    host = tags.host()
    return f"http://{host}:{STREAM_PORT}/stream.mjpg" if host else None


def open_photon(args, *, need_stream: bool) -> tuple[PhotonTags, PhotonStream | None]:
    """PhotonTags plus its stream, from add_photon_args()' flags. Without
    `need_stream`, a stream that will not open is a warning, not an error."""
    tags = PhotonTags(args.photon_camera, args.nt_server, args.photon_res)
    if getattr(args, "no_stream", False):
        return tags, None
    url = stream_url(args, tags)
    try:
        if url is None:
            raise OSError("cannot tell where PhotonVision runs -- pass --stream URL")
        stream = PhotonStream(url, tags.size)
        print(f"camera stream: {url}")
        return tags, stream
    except OSError as e:
        if need_stream:
            raise SystemExit(f"{e}\n(the ball tracker needs the picture; check the URL "
                             f"in PhotonVision's camera view, and pass --stream)") from None
        print(f"warning: {e} -- tags only, drawn on a blank canvas")
        return tags, None


def blank_frame(size: tuple[int, int]) -> np.ndarray:
    """The canvas the overlay goes on when there is no camera picture."""
    return np.full((size[1], size[0], 3), 40, np.uint8)
