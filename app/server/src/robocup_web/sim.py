"""The simulator, seen from the browser.

SimBridge stands in for viz_node: it subscribes to the world state and the
strategy stream, and publishes the same manual-port messages viz_node does, so
robot_node and strategy_node cannot tell the two apart. Run one or the other:
both bind the manual port.

SimProcess is the Start/Stop button: it runs run_pipeline.py --no-viz. A sim
started any other way (run_pipeline.py --no-viz in a terminal) works the same,
since the bridge only listens on the ports.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import zmq

from .procs import CommandError, ManagedProcess

REPO = Path(__file__).resolve().parents[4]
SIM_DIR = REPO / "sim"
#: Where the Tracking page's tracker publishes, and the sim subscribes for it.
TRACKER_ZMQ_PORT = 5556
TRACKER_ZMQ = f"tcp://localhost:{TRACKER_ZMQ_PORT}"

#: Controller names robot_node knows, as the web app labels them.
MODES = {"pd": "2005_INVERSION", "time": "2005_TIME_OPTIMAL", "mpc": "MPC"}
#: Full-stick rotation for direct driving, as viz_node's MANUAL_MAX_OMEGA.
MAX_OMEGA = 5.0
#: World-state pushes to the browser per second; the sim publishes at 60.
PUSH_HZ = 30
#: No world state for this long and the sim counts as stopped.
SIM_SILENT_S = 1.5
#: A robot being driven gets a stop command if its driver goes quiet this long
#: (a closed tab, a lost connection), so it never keeps the last speed.
DRIVE_TIMEOUT_S = 0.4


def sim_config():
    """sim/config.py, for the port numbers. Not a package, so it
    is imported the way the sim nodes import it."""
    if str(SIM_DIR) not in sys.path:
        sys.path.insert(0, str(SIM_DIR))
    import config

    return config


class SimBridge:
    def __init__(self) -> None:
        cfg = sim_config()
        self._ports = (cfg.VISION_PORT, cfg.STRATEGY_PORT, cfg.MANUAL_PORT)
        self._ctx = zmq.Context.instance()
        self._lock = threading.Lock()
        self._world: dict[str, Any] | None = None
        self._world_seq = 0
        self._world_t = 0.0
        self._attackers: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual: zmq.Socket | None = None
        self.manual_error: str | None = None
        self._last_bind_try = 0.0
        # What this app last told the sim. The world state does not echo
        # these back, so a sim that restarts resets them (see _on_sim_up).
        self.controls = {"strategy": False, "dribble": False, "rl_kick": False}
        self._driving: dict[int, float] = {}  # robot -> last drive command time
        self._hellos: list[float] = []
        self._was_up = False
        self.clients: set[asyncio.Queue[str]] = set()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._bind_manual()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._manual is not None:
            self._manual.close(linger=0)

    def _bind_manual(self) -> None:
        self._last_bind_try = time.monotonic()
        sock = self._ctx.socket(zmq.PUB)
        try:
            sock.bind(f"tcp://127.0.0.1:{self._ports[2]}")
        except zmq.ZMQError as e:
            sock.close(linger=0)
            self.manual_error = (f"cannot open the manual-command port {self._ports[2]} ({e}). "
                                 f"Is the pygame viewer (viz_node) running? Close it, or start "
                                 f"the sim with --no-viz.")
            return
        self._manual = sock
        self.manual_error = None

    # -- world state in (its own thread: plain blocking zmq) -----------------

    def _listen(self) -> None:
        vision = self._ctx.socket(zmq.SUB)
        vision.connect(f"tcp://localhost:{self._ports[0]}")
        vision.setsockopt_string(zmq.SUBSCRIBE, "")
        strategy = self._ctx.socket(zmq.SUB)
        strategy.connect(f"tcp://localhost:{self._ports[1]}")
        strategy.setsockopt_string(zmq.SUBSCRIBE, "")
        poller = zmq.Poller()
        poller.register(vision, zmq.POLLIN)
        poller.register(strategy, zmq.POLLIN)
        try:
            while not self._stop.is_set():
                events = dict(poller.poll(200))
                world = attackers = None
                # Drain both: only the newest message of each matters.
                while vision in events:
                    try:
                        world = vision.recv_string(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                while strategy in events:
                    try:
                        attackers = json.loads(strategy.recv_string(zmq.NOBLOCK)).get("attackers")
                    except zmq.Again:
                        break
                with self._lock:
                    if world is not None:
                        self._world = json.loads(world)
                        self._world_seq += 1
                        self._world_t = time.monotonic()
                    if attackers is not None:
                        self._attackers = [str(a) for a in attackers]
        finally:
            vision.close(linger=0)
            strategy.close(linger=0)

    @property
    def sim_up(self) -> bool:
        return self._world is not None and time.monotonic() - self._world_t < SIM_SILENT_S

    # -- to the browser -----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            world = self._world
            attackers = list(self._attackers)
        return {
            "type": "world",
            "up": self.sim_up,
            "world": world,
            "attackers": attackers,
            "controls": dict(self.controls),
            "manual_error": self.manual_error,
        }

    async def pump(self) -> None:
        """Pushes the newest world state to every connected browser, PUSH_HZ
        times a second, and runs the housekeeping that needs a clock: hellos
        to a freshly started sim, and stopping robots whose driver went quiet."""
        last_seq = -1
        while True:
            await asyncio.sleep(1.0 / PUSH_HZ)
            up = self.sim_up
            if up and not self._was_up:
                self._on_sim_up()
            self._was_up = up
            self._housekeeping()
            if self._world_seq == last_seq and up:
                continue
            last_seq = self._world_seq
            msg = json.dumps(self.snapshot())
            for q in list(self.clients):
                if q.full():
                    q.get_nowait()  # a slow browser gets the newest, not a backlog
                q.put_nowait(msg)

    def _on_sim_up(self) -> None:
        # A (re)started sim has the defaults again; so does this app's view of
        # it. strategy_node needs to hear which robots are human-driven (none,
        # from a browser) -- several times, since its subscription to this
        # port may not be up yet (ZMQ's slow-joiner window).
        self.controls = {"strategy": False, "dribble": False, "rl_kick": False}
        now = time.monotonic()
        self._hellos = [now + 0.5, now + 1.5, now + 3.0]

    def _housekeeping(self) -> None:
        now = time.monotonic()
        if self._manual is None:
            if now - self._last_bind_try > 2.0:
                self._bind_manual()
            return
        if self._hellos and now >= self._hellos[0]:
            self._hellos.pop(0)
            self._publish_humans()
        stopped = False
        for robot, t in list(self._driving.items()):
            if now - t > DRIVE_TIMEOUT_S:
                self._publish({"direct": {str(robot): {"vx": 0.0, "vy": 0.0, "w": 0.0}}})
                del self._driving[robot]
                stopped = True
        if stopped:
            self._publish_humans()

    # -- commands from the browser or a script --------------------------------

    def _publish(self, msg: dict[str, Any]) -> None:
        if self._manual is None:
            raise CommandError(self.manual_error or "manual-command port not open")
        self._manual.send_string(json.dumps(msg))

    def _publish_humans(self) -> None:
        """Tell strategy_node which robots someone is driving with keys, so it
        leaves them alone rather than overriding the keys with its targets."""
        self._publish({"human_blue_ids": sorted(self._driving)})

    def _robot(self, cmd: dict[str, Any]) -> int:
        n = self._num_robots()
        try:
            rid = int(cmd.get("robot", 0))
        except (TypeError, ValueError):
            raise CommandError("robot must be a number") from None
        if not 0 <= rid < n:
            raise CommandError(f"robot must be 0-{n - 1}")
        return rid

    def _num_robots(self) -> int:
        world = self._world or {}
        return int((world.get("field") or {}).get("num_robots", 6))

    def command(self, cmd: dict[str, Any]) -> None:
        """One command, in the app's own vocabulary; see the README for the
        list. Raises CommandError for anything malformed."""
        kind = cmd.get("type")
        if kind == "target":
            rid = self._robot(cmd)
            mode = MODES.get(str(cmd.get("mode", "pd")))
            if mode is None:
                raise CommandError(f"mode must be one of {', '.join(MODES)}")
            x, y = _finite(cmd, "x"), _finite(cmd, "y")
            self._publish({"targets": {str(rid): {"x": x, "y": y, "mode": mode}}})
            if self._driving.pop(rid, None) is not None:
                self._publish_humans()
        elif kind == "drive":
            # Field directions (vx right, vy up the field), or the robot's own
            # (forward, left: W drives it the way it faces).
            rid = self._robot(cmd)
            body = "forward" in cmd or "left" in cmd
            vx = max(-1.0, min(1.0, _finite(cmd, "forward" if body else "vx")))
            vy = max(-1.0, min(1.0, _finite(cmd, "left" if body else "vy")))
            w = max(-MAX_OMEGA, min(MAX_OMEGA, _finite(cmd, "w")))
            new = rid not in self._driving
            self._driving[rid] = time.monotonic()
            direct: dict[str, Any] = {"vx": vx, "vy": vy, "w": w}
            if body:
                direct["frame"] = "robot"
            self._publish({"direct": {str(rid): direct}})
            if new:
                self._publish_humans()
        elif kind == "kick":
            self._publish({"kick": {str(self._robot(cmd)): True}})
        elif kind == "strategy":
            on = bool(cmd.get("enabled"))
            self._publish({"strategy_enabled": on, "human_blue_ids": sorted(self._driving)})
            self.controls["strategy"] = on
        elif kind == "dribble":
            on = bool(cmd.get("enabled"))
            self._publish({"dribble_attacker": on})
            self.controls["dribble"] = on
        elif kind == "rl_kick":
            on = bool(cmd.get("enabled"))
            field = (self._world or {}).get("field") or {}
            if on and not field.get("is_default", True):
                raise CommandError("the RL kick policy was trained on a 9 x 6 field")
            self._publish({"rl_kick_enabled": on})
            self.controls["rl_kick"] = on
        elif kind == "ball_stuck_reset":
            # The sim only takes a toggle; it reports its state, so compare.
            game = (self._world or {}).get("game") or {}
            if bool(cmd.get("enabled")) != bool(game.get("ball_stuck_enabled")):
                self._publish({"type": "ball_stuck_toggle"})
        else:
            raise CommandError(f"unknown command type {kind!r}")

    def stop_driving(self, robots: set[int]) -> None:
        stopped = False
        for rid in robots:
            if self._driving.pop(rid, None) is not None and self._manual is not None:
                self._publish({"direct": {str(rid): {"vx": 0.0, "vy": 0.0, "w": 0.0}}})
                stopped = True
        if stopped:
            self._publish_humans()


def _finite(cmd: dict[str, Any], key: str) -> float:
    try:
        v = float(cmd[key])
    except (KeyError, TypeError, ValueError):
        raise CommandError(f"{key} must be a number") from None
    if not math.isfinite(v):
        raise CommandError(f"{key} must be finite")
    return v


class SimProcess(ManagedProcess):
    """run_pipeline.py --no-viz, started and stopped from the browser."""

    def __init__(self) -> None:
        super().__init__("simulator")
        self.options: dict[str, Any] = {}

    def start(self, opts: dict[str, Any]) -> None:
        """`opts`: strategy (bool), color, field [w, h] or field_from_calibration,
        and the camera mirror: vision ("tracker" for the Tracking page's live
        output, a recording's path, or any --vision source), ignore_tags,
        tag_map, flip_x, flip_y, replay_speed."""
        color = "red" if opts.get("color") == "red" else "blue"
        cmd = [sys.executable, str(SIM_DIR / "run_pipeline.py"), "--no-viz", "--color", color]
        if not opts.get("strategy", True):
            cmd.append("--no-strategy")
        field: list[float] | str | None = None
        if opts.get("field_from_calibration"):
            pose = field_pose_file()
            if not pose.exists():
                raise CommandError("no saved field calibration yet -- calibrate the field first")
            field = "calibrated"
            cmd += ["--field", str(pose)]
        elif opts.get("field"):
            try:
                field = [float(opts["field"][0]), float(opts["field"][1])]
            except (TypeError, ValueError, IndexError):
                raise CommandError("field must be [width, height] in metres") from None
            if not (field[0] > 0 and field[1] > 0):
                raise CommandError("field size must be positive")
            cmd += ["--field", f"{field[0]:g}", f"{field[1]:g}"]
        vision = str(opts.get("vision") or "").strip()
        if vision == "tracker":
            vision = TRACKER_ZMQ
        if vision:
            cmd += ["--vision", vision]
            ignore = str(opts.get("ignore_tags") or "").replace(" ", "")
            if ignore:
                if not re.fullmatch(r"\d+(,\d+)*", ignore):
                    raise CommandError("tags to ignore: ids separated by commas, e.g. 0,1,2,3")
                cmd += ["--ignore-tags", ignore]
            tag_map = str(opts.get("tag_map") or "").replace(" ", "")
            if tag_map:
                if not re.fullmatch(r"\d+:\d+(,\d+:\d+)*", tag_map):
                    raise CommandError("tag map: tag:robot pairs, e.g. 9:0,12:1")
                cmd += ["--tag-map", tag_map]
            if opts.get("flip_x"):
                cmd.append("--flip-x")
            if opts.get("flip_y"):
                cmd.append("--flip-y")
            if opts.get("replay_speed") not in (None, "", 1, 1.0):
                speed = float(opts["replay_speed"])
                if not 0.05 <= speed <= 20:
                    raise CommandError("replay speed must be 0.05-20")
                cmd += ["--replay-speed", f"{speed:g}"]
        self.launch(cmd, SIM_DIR)
        self.options = {"strategy": bool(opts.get("strategy", True)), "color": color,
                        "field": field, "vision": vision or None}

    def status(self) -> dict[str, Any]:
        return {**self.process_status(), "options": self.options}


def field_pose_file() -> Path:
    """vision/calib/field_pose.json, where calibration saves the field."""
    from vision_core.field import field_pose_path

    return field_pose_path()
