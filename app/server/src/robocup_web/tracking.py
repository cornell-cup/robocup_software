"""Live tracking from the browser: track-combined, started with the page's
settings instead of a terminal command.

The tracker runs headless and publishes on ZMQ (TRACKER_ZMQ_PORT), which the
simulator subscribes to when it mirrors the camera, and which TrackingFeed
here reads too, for the page's live readout. --mjpeg makes it serve what its
window would have shown (camera, overlays, plan view) for the page to display.
With "record", every frame also goes to vision/runs/<time>.jsonl, which the
Simulator page can replay later.

The tracker runs PhotonVision's NetworkTables server, as it does from a
terminal; the field calibration page needs that same server, so the two take
turns (app.py disconnects calibration before starting the tracker).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import zmq
from ball_tracking.ball_color import load_all

from .procs import CommandError, ManagedProcess
from .sim import REPO, TRACKER_ZMQ_PORT

VISION_DIR = REPO / "vision"
RUNS_DIR = VISION_DIR / "runs"
#: track-combined --mjpeg: its drawn view, for the Tracking page.
VIDEO_PORT = 5802
CALIB_DIR = VISION_DIR / "calib"
#: No message from the tracker for this long and the feed counts as stopped.
FEED_SILENT_S = 1.0
#: track-combined's keys that make sense from the page (q is Stop).
CONTROL_KEYS = ("g", "t", "m", "w", "r")
#: Numeric options: (key, track-combined flag, lowest, highest).
NUMBER_FLAGS = (
    ("heading_offset", "--heading-offset", -360, 360),
    ("tag_height", "--tag-height", 0, 2),
    ("tag_coast", "--tag-coast", 0, 10),
    ("tag_sigma_a", "--tag-sigma-a", 0.01, 500),
    ("tag_sigma_alpha", "--tag-sigma-alpha", 0.01, 10000),
    ("ball_min_conc", "--ball-min-conc", 0, 255),
    ("ball_sigma_a", "--ball-sigma-a", 0.01, 500),
    ("ball_sigma_a_air", "--ball-sigma-a-air", 0.01, 500),
    ("ball_rest_mm", "--ball-rest-mm", 0, 100),
    ("ball_sigma_px", "--ball-sigma-px", 0.1, 50),
    ("cam_height", "--cam-height", 0.1, 20),
    ("pitch", "--pitch", 1, 90),
    ("distance", "--distance", 0.1, 20),
    ("yaw", "--yaw", -180, 180),
    ("pitch_wall", "--pitch-wall", -89, 89),
    ("display_width", "--display-width", 320, 3840),
)


class TrackerProcess(ManagedProcess):
    """track-combined --no-window, with the page's settings."""

    def __init__(self) -> None:
        super().__init__("tracker")
        self.options: dict[str, Any] = {}

    def start(self, opts: dict[str, Any]) -> None:
        tag_size = _num(opts, "tag_size")
        if not 0.005 <= tag_size <= 1.0:
            raise CommandError("tag size is in metres: the black square's edge, e.g. 0.177")
        profile = str(opts.get("ball_profile") or "")
        profiles = load_all()
        if profile not in profiles:
            raise CommandError(
                "pick a ball profile" if not profile else
                f"no ball profile {profile!r}; have {', '.join(sorted(profiles)) or 'none -- make one on the Ball profile page'}")
        cmd = [sys.executable, "-m", "combined_tracking.track_combined", "--no-window",
               "--control", "--zmq-pub", f"tcp://*:{TRACKER_ZMQ_PORT}", "--mjpeg", str(VIDEO_PORT),
               "--tag-size", f"{tag_size:g}", "--ball-profile", profile]
        for key, flag, lo, hi in NUMBER_FLAGS:
            if opts.get(key) not in (None, ""):
                v = _num(opts, key)
                if not lo <= v <= hi:
                    raise CommandError(f"{key.replace('_', ' ')} must be {lo}-{hi}")
                cmd += [flag, f"{v:g}"]
        if opts.get("ball_radius_mm") not in (None, ""):
            r = _num(opts, "ball_radius_mm")
            if not 1 <= r <= 500:
                raise CommandError("ball radius must be 1-500 mm")
            cmd += ["--ball-radius", f"{r / 1000:g}"]
        for key, flag in (("ball_airborne", "--ball-airborne"), ("corner_origin", "--corner-origin"),
                          ("synthetic_field", "--synthetic-field"), ("print_poses", "--print-poses"),
                          ("print_states", "--print-states")):
            if opts.get(key):
                cmd.append(flag)
        if opts.get("field"):
            try:
                fw, fh = (float(x) for x in opts["field"])
            except (TypeError, ValueError):
                raise CommandError("field size: [width, height] in metres") from None
            if not (0.1 <= fw <= 50 and 0.1 <= fh <= 50):
                raise CommandError("field size is in metres, 0.1-50")
            cmd += ["--field", f"{fw:g}", f"{fh:g}"]
        if opts.get("mode"):
            if opts["mode"] not in ("floor", "wall"):
                raise CommandError("made-up field mode is floor or wall")
            cmd += ["--mode", opts["mode"]]
        if opts.get("field_pose"):
            # Only calibration files: the page can be reached from other machines.
            pose = (CALIB_DIR / Path(str(opts["field_pose"])).name)
            if pose.suffix != ".json" or not pose.is_file():
                raise CommandError(f"no field pose {pose.name!r} in vision/calib")
            cmd += ["--field-pose", str(pose)]
        if opts.get("json_out"):
            name = Path(str(opts["json_out"])).name
            if not name.endswith(".json"):
                raise CommandError("the live-state file is a .json name, written into vision/runs/")
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            cmd += ["--json-out", str(RUNS_DIR / name)]
        if opts.get("serve_http") not in (None, ""):
            port = int(_num(opts, "serve_http"))
            if not 1024 <= port <= 65535 or port in (TRACKER_ZMQ_PORT, VIDEO_PORT, 5800, 5801, 1181):
                raise CommandError(f"HTTP port {port} is out of range or already used by the app")
            cmd += ["--serve-http", str(port)]
        for key, flag in (("photon_camera", "--photon-camera"), ("nt_server", "--nt-server"),
                          ("stream", "--stream")):
            v = str(opts.get(key) or "").strip()
            if v:
                cmd += [flag, v]
        if opts.get("photon_res"):
            try:
                w, h = (int(x) for x in opts["photon_res"])
            except (TypeError, ValueError):
                raise CommandError("PhotonVision resolution: [width, height]") from None
            cmd += ["--photon-res", str(w), str(h)]
        recording = None
        if opts.get("record"):
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            recording = RUNS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
            cmd += ["--json-log", str(recording)]
        self.launch(cmd, VISION_DIR, stdin=True)
        self.options = {"tag_size": tag_size, "ball_profile": profile,
                        "recording": str(recording) if recording else None,
                        "synthetic_field": bool(opts.get("synthetic_field"))
                        or not (CALIB_DIR / "field_pose.json").is_file()}

    def control(self, cmd: dict[str, Any]) -> None:
        """The window's keys, for a tracker with no window: {"key": "g"} (g
        grid, t trails, m mask, w wall/floor, r reset) or {"ball_min_conc": N}."""
        if "ball_min_conc" in cmd:
            v = _num(cmd, "ball_min_conc")
            if not 0 <= v <= 255:
                raise CommandError("ball min. concentration must be 0-255")
            self.send_line(f"conc {v:g}")
            return
        key = str(cmd.get("key", ""))
        if key not in CONTROL_KEYS:
            raise CommandError(f"key must be one of {', '.join(CONTROL_KEYS)}")
        self.send_line(key)

    def status(self) -> dict[str, Any]:
        return {**self.process_status(), "options": self.options}


class TrackingFeed:
    """The tracker's ZMQ output, newest message only, for the page. Listens
    whether or not this app started the tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._doc: dict[str, Any] | None = None
        self._t = 0.0
        self._n = 0  # messages so far, so a reader can tell a new one
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._listen, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _listen(self) -> None:
        sock = zmq.Context.instance().socket(zmq.SUB)
        sock.connect(f"tcp://localhost:{TRACKER_ZMQ_PORT}")
        sock.setsockopt_string(zmq.SUBSCRIBE, "")
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        try:
            while not self._stop.is_set():
                if not dict(poller.poll(200)):
                    continue
                doc = None
                while True:  # only the newest matters
                    try:
                        doc = sock.recv_string(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                if doc is not None:
                    with self._lock:
                        self._doc = json.loads(doc)
                        self._t = time.monotonic()
                        self._n += 1
        finally:
            sock.close(linger=0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            age = time.monotonic() - self._t if self._doc is not None else None
            return {"up": age is not None and age < FEED_SILENT_S, "age_s": age, "n": self._n,
                    "doc": self._doc}


def field_poses() -> list[str]:
    """Field calibrations the tracker may load (vision/calib/*.json that hold one)."""
    out = []
    for p in sorted(CALIB_DIR.glob("*.json")):
        try:
            if "R" in json.loads(p.read_text(encoding="utf-8")):
                out.append(p.name)
        except (OSError, ValueError):
            pass
    return out


def recordings() -> list[dict[str, Any]]:
    """Sessions the tracker recorded (vision/runs/*.jsonl), newest first, plus
    the sample recording committed with the repo."""
    files = sorted(RUNS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if RUNS_DIR.exists() else []
    sample = VISION_DIR / "test.jsonl"
    if sample.exists():
        files.append(sample)
    return [{"name": p.name if p.parent == RUNS_DIR else f"{p.name} (sample)", "path": str(p),
             "size_kb": round(p.stat().st_size / 1024), "modified": p.stat().st_mtime}
            for p in files]


def check_vision_source(src: str) -> str:
    """The sim's --vision source, if it is one the page may pass: the live
    tracker, a ZMQ or HTTP address, or a recording that exists."""
    if src in ("", "tracker") or re.match(r"^(tcp|ipc|zmq|https?)://", src):
        return src
    path = Path(src)
    if path.suffix == ".jsonl" and path.is_file():
        return str(path.resolve())
    raise CommandError(f"vision source must be live tracking, a tcp:// or http:// address, "
                       f"or a recorded .jsonl file; {src!r} is none of those")


def _num(opts: dict[str, Any], key: str) -> float:
    try:
        v = float(opts[key])
    except (KeyError, TypeError, ValueError):
        raise CommandError(f"{key.replace('_', ' ')} must be a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise CommandError(f"{key.replace('_', ' ')} must be finite")
    return v
