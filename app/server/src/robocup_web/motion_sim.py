"""The simulator's robot, put through the same motion tests as a real one.

SimRobot dresses sim/pymunk_world.py's physics as what a motion-test runner
talks to (fleet, feed, clock, sleep -- like robot/laptop/fake_robot.py), so
measure() can run the tests on it and the Robot test page can set the real
robot's numbers beside the sim's, measured the same way.

A command reaches it as a real robot's would: forward, left and turn (each
-1..1) are what robot_node sends a real robot, turned back into the body
velocity robot_node computed (`_body_command` undone) and into wheel speeds by
robot_node's own `_inverse_kinematics`. The camera is perfect: every physics
tick, no noise, no lag.

Measured at the sim's own field size (9 x 6 m unless ROBOCUP_FIELD is set for
this server), which sets the simulated robot's size, and so how it turns.
"""

from __future__ import annotations

import math
import sys
import threading
from typing import Any

from .robots import _laptop
from .sim import REPO, SIM_DIR, sim_config

NAME = "SIM"
TAG_ID = 7
#: The same timeout as the fleet's: a robot with no new command stops.
COMMAND_TIMEOUT_S = 0.25

_lock = threading.Lock()
_measured: dict[str, Any] | None = None


class SimRobot:
    def __init__(self) -> None:
        cfg = sim_config()
        for path in (str(SIM_DIR), str(REPO)):
            if path not in sys.path:
                sys.path.insert(0, path)
        import pymunk_world
        import robot_node

        self._cfg = cfg
        self._ik = robot_node._inverse_kinematics
        self.world = pymunk_world.PymunkWorld()
        w, h = cfg.FIELD_W, cfg.FIELD_H
        self.field = {"width": w, "height": h, "x0": 0.0, "y0": 0.0}
        # Robot 0 alone in the middle, facing +x; the others and the ball
        # parked along the edges, out of every test's way.
        body = self.world.robots[0]
        body.position, body.angle = (w / 2, h / 2), 0.0
        for i, other in enumerate(self.world.robots[1:], 1):
            other.position = (w * i / len(self.world.robots), 0.2)
        self.world.reset_ball((0.3, h - 0.3))

        self.name = NAME
        self.fleet = self
        self.feed = self
        self._t = 0.0
        self._ahead = 0.0  # sleep time not yet stepped (ticks are DT long)
        self._command = (0.0, 0.0, 0.0)
        self._last_drive: float | None = None
        self._n = 0
        self._doc = self._capture()

    # -- clock --------------------------------------------------------------------

    def clock(self) -> float:
        return self._t

    def sleep(self, dt: float) -> None:
        self._ahead += dt
        while self._ahead >= self._cfg.DT / 2:
            self._step()
            self._ahead -= self._cfg.DT

    def _step(self) -> None:
        fresh = self._last_drive is not None and self._t - self._last_drive <= COMMAND_TIMEOUT_S
        forward, left, turn = self._command if fresh else (0.0, 0.0, 0.0)
        # robot_node gives a real robot _body_command(vx, vy, w): forward = vx,
        # left = vy, turn = WHEEL_DISTANCE / FIELD_SCALE * w. Undo that.
        w = turn * self._cfg.FIELD_SCALE / self._cfg.WHEEL_DISTANCE
        self.world.step({0: self._ik(forward, left, w)})
        self._t += self._cfg.DT
        self._doc = self._capture()

    def _capture(self) -> dict[str, Any]:
        body = self.world.robots[0]
        self._n += 1
        return {"t_tags": self._t, "t_capture": self._t, "t_publish": self._t, "field": self.field,
                "tags": [{"id": TAG_ID, "x": body.position.x, "y": body.position.y,
                          "theta_deg": math.degrees(body.angle), "visible": True}]}

    # -- as the fleet -----------------------------------------------------------------

    def drive(self, name: str, axes: Any, buttons: Any) -> None:
        # body_axes gives (-left, -forward, -turn, 0): undo it.
        self._command = (-axes[1], -axes[0], -axes[2])
        self._last_drive = self._t

    def stop(self, name: str) -> None:
        self._last_drive = None

    def status(self) -> list[dict[str, Any]]:
        return [{"name": NAME, "state": "connected", "connected": True, "error": None,
                 "moving": False, "command_age_s": None}]

    # -- as the camera feed -------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {"up": True, "age_s": 0.0, "n": self._n, "doc": self._doc}


def measure() -> dict[str, Any] | None:
    """The simulated robot's hardware() numbers, at full command. Measured
    once and kept: the sim's config doesn't change while the server runs.
    None if the sim's physics can't be loaded."""
    global _measured
    with _lock:
        if _measured is not None:
            return _measured
        laptop = _laptop()
        import laptop.motion_profile as profile
        import laptop.motion_report as report
        import laptop.motion_runner as runner_module

        try:
            robot = SimRobot()
        except Exception as e:  # no pymunk, a broken config: compare with nothing
            print(f"motion tests: the sim's robot can't be measured: {e}")
            return None
        robot.sleep(0.2)
        names = ("forward", "backward", "strafe_left", "strafe_right", "spin_ccw", "spin_cw")
        runner = runner_module.MotionRunner(
            robot, robot, NAME, [profile.TESTS_BY_NAME[n] for n in names], [report.FULL],
            clock=robot.clock, sleep=robot.sleep)
        records = [r.to_dict() for r in runner.run()]
        _measured = report.hardware(records)
        return _measured


def config_values() -> dict[str, float]:
    """The sim/config.py values the comparison's suggestions start from."""
    cfg = sim_config()
    return {k: float(getattr(cfg, k)) for k in
            ("MOTOR_MAX_FORCE", "LINEAR_DAMP", "ANGULAR_DAMP", "ROBOT_MAX_SPEED", "ROBOT_MAX_ACCEL")}
