#!/usr/bin/env python3
"""
Robot Node — iterative control for each robot.

Subscribes to world state    (VISION_PORT  ZMQ SUB).
Subscribes to manual targets (MANUAL_PORT  ZMQ SUB).
Pushes wheel commands to simulation (COMMAND_PORT ZMQ PUSH).
Publishes the same commands per body axis for real robots (REAL_PORT ZMQ PUB).

Implements the three controllers from robot.gd:
  2005_INVERSION    — PD Dynamic Inversion (robot.gd calculate_dynamic_inversion)
  MPC               — Greedy MPC rollout   (robot.gd calculate_mpc_rollout)
  2005_TIME_OPTIMAL — Bang-bang controller  (robot.gd calculate_time_optimal_2005)
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import zmq

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
    0, _HERE
)  # keep local python/config.py ahead of any similarly named module

from config import (  # noqa: E402
    ARRIVAL_THRESH,
    COMMAND_PORT,
    DT,
    FIELD_SCALE,
    KD,
    KP,
    LINEAR_DAMP,
    MANUAL_PORT,
    MOTOR_MAX_FORCE,
    MPC_DT,
    MPC_HORIZON,
    NUM_ROBOTS,
    REAL_PORT,
    ROBOT_MASS,
    STRATEGY_PORT,
    VISION_PORT,
    WHEEL_ANGLES,
    WHEEL_DISTANCE,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _rot2d(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def _wrap_angle(a: float) -> float:
    """Wrap to (-pi, pi] so the rotation error never takes the long way around."""
    return ((a + math.pi) % (2.0 * math.pi)) - math.pi


def _inverse_kinematics(vx: float, vy: float, w: float) -> list[float]:
    """
    Local body-frame velocity (vx, vy, ω) → normalised wheel speeds.
    Matches robot.gd inverse kinematics block.
    """
    speeds = [
        -math.sin(alpha) * vx + math.cos(alpha) * vy + WHEEL_DISTANCE * w
        for alpha in WHEEL_ANGLES
    ]
    max_s = max(abs(s) for s in speeds)
    if max_s > 1.0:
        speeds = [s / max_s for s in speeds]
    return speeds


def _body_command(vx: float, vy: float, w: float) -> tuple[float, float, float]:
    """
    The same command as _inverse_kinematics, scaled the same way, but per body
    axis instead of per wheel: (forward, left, turn), each -1..1, turn
    counter-clockwise. For a real robot, whose firmware mixes its own wheels
    (robot/firmware/mixing.py).

    Turn uses the 9 x 6 wheel spacing: a sim robot shrinks with a small field
    (FIELD_SCALE), a real one does not, and the shrunk spacing would leave it
    almost no turn (0.12 for full Q / E on a 1.9 m table).
    """
    speeds = [
        -math.sin(alpha) * vx + math.cos(alpha) * vy + WHEEL_DISTANCE * w
        for alpha in WHEEL_ANGLES
    ]
    max_s = max(abs(s) for s in speeds)
    scale = 1.0 / max_s if max_s > 1.0 else 1.0

    def clip(v: float) -> float:
        return max(-1.0, min(1.0, v * scale))

    return clip(vx), clip(vy), clip(WHEEL_DISTANCE / FIELD_SCALE * w)


# ── controllers ───────────────────────────────────────────────────────────────


def _ctrl_dynamic_inversion(
    pos: np.ndarray,
    target: np.ndarray,
    velocity: np.ndarray,
    angle: float,
    target_angle: float = 0.0,
) -> tuple[float, float, float]:
    """robot.gd calculate_dynamic_inversion(). Imposed error dynamics: ë + C·ė + K·e = 0."""
    error = target - pos
    desired_accel = error * KP - velocity * KD
    local_accel = _rot2d(desired_accel, -angle)
    desired_omega = _wrap_angle(target_angle - angle) * 10.0
    return local_accel[0], local_accel[1], desired_omega


def _ctrl_mpc_rollout(
    pos: np.ndarray,
    target: np.ndarray,
    velocity: np.ndarray,
    angle: float,
    target_angle: float = 0.0,
) -> tuple[float, float, float]:
    """robot.gd calculate_mpc_rollout(). Greedy MPC over 8 candidate inputs."""
    candidates = np.array(
        [
            [1.0, 0.0],
            [-1.0, 0.0],
            [0.0, 1.0],
            [0.0, -1.0],
            [0.7, 0.7],
            [-0.7, 0.7],
            [0.7, -0.7],
            [-0.7, -0.7],
        ]
    )
    best_input = np.zeros(2)
    lowest_cost = math.inf

    for inp in candidates:
        sim_pos = pos.copy()
        sim_vel = velocity.copy()
        for _ in range(MPC_HORIZON):
            global_force = _rot2d(inp, angle) * MOTOR_MAX_FORCE * 2.0
            sim_accel = (global_force / ROBOT_MASS) - sim_vel * LINEAR_DAMP
            sim_vel += sim_accel * MPC_DT
            sim_pos += sim_vel * MPC_DT
        cost = float(np.sum((sim_pos - target) ** 2))
        if cost < lowest_cost:
            lowest_cost = cost
            best_input = inp

    desired_omega = _wrap_angle(target_angle - angle) * 10.0
    return best_input[0], best_input[1], desired_omega


def _ctrl_time_optimal(
    pos: np.ndarray,
    target: np.ndarray,
    velocity: np.ndarray,
    angle: float,
    target_angle: float = 0.0,
) -> tuple[float, float, float]:
    """robot.gd calculate_time_optimal_2005(). Bang-bang until braking distance."""
    to_target = target - pos
    dist = float(np.linalg.norm(to_target))
    if dist < 1e-6:
        return 0.0, 0.0, 0.0

    direction = to_target / dist
    a_max = (MOTOR_MAX_FORCE * 1.5) / ROBOT_MASS
    current_speed = float(np.dot(velocity, direction))
    stopping_dist = (current_speed**2) / (2.0 * a_max) if a_max > 0 else 0.0

    if dist > stopping_dist:
        desired_velocity = direction * 500.0  # high → saturation
    else:
        desired_velocity = direction * math.sqrt(max(0.0, 2.0 * a_max * dist))

    local_v = _rot2d(desired_velocity, -angle)
    desired_omega = _wrap_angle(target_angle - angle) * 5.0
    return local_v[0], local_v[1], desired_omega


_CONTROLLERS = {
    "2005_INVERSION": _ctrl_dynamic_inversion,
    "MPC": _ctrl_mpc_rollout,
    "2005_TIME_OPTIMAL": _ctrl_time_optimal,
}

# Maximum manual angular velocity (rad/s); linear speed is normalised by IK.
MANUAL_MAX_OMEGA = 5.0


# ── per-robot state ───────────────────────────────────────────────────────────


class RobotController:
    def __init__(self, robot_id: int, mode: str = "2005_INVERSION"):
        self.id: int = robot_id
        self.mode: str = mode
        self.target: list[float] | None = None
        # Explicit facing target from strategy. None -> fall back to atan2(target-pos).
        self.target_angle: float | None = None
        self.path_length: float = 0.0
        self.total_time: float = 0.0
        self._last_pos: np.ndarray | None = None
        # MANUAL mode: (vx, vy, omega) received directly from operator, in the
        # world frame, or the robot's own (x forward, y left) if direct_body
        self.direct_vel: tuple[float, float, float] | None = None
        self.direct_body: bool = False

    def set_target(
        self,
        x: float,
        y: float,
        mode: str | None = None,
        target_angle: float | None = None,
    ) -> None:
        self.target = [x, y]
        self.target_angle = target_angle
        self.path_length = 0.0
        self.total_time = 0.0
        self._last_pos = None
        self.direct_vel = None
        if mode:
            self.mode = mode

    def set_direct_vel(self, vx: float, vy: float, w: float, body: bool = False) -> None:
        """Switch to MANUAL mode and set a velocity command: world frame, or
        with body the robot's own (vx forward, vy left)."""
        self.direct_vel = (vx, vy, w)
        self.direct_body = body
        self.mode = "MANUAL"
        self.target = None

    def compute_wheels(self, rstate: dict[str, float]) -> list[float]:
        """Given robot state dict, return normalised wheel speeds [w0,w1,w2]."""
        return _inverse_kinematics(*self.compute_command(rstate))

    def compute_command(self, rstate: dict[str, float]) -> tuple[float, float, float]:
        """Given robot state dict, return the body-frame command (vx, vy, ω)
        that _inverse_kinematics turns into wheel speeds."""
        pos = np.array([rstate["x"], rstate["y"]])
        vel = np.array([rstate["vx"], rstate["vy"]])
        angle = rstate["angle"]

        # ── MANUAL: direct velocity pass-through, no iteration needed ──────────
        if self.mode == "MANUAL":
            if self.direct_vel is None:
                return 0.0, 0.0, 0.0
            vx, vy, w = self.direct_vel
            if self.direct_body:
                return vx, vy, w
            # Rotate world-frame velocity into the robot's body frame
            local = _rot2d(np.array([vx, vy]), -angle)
            return local[0], local[1], w

        if self._last_pos is not None:
            self.path_length += float(np.linalg.norm(pos - self._last_pos))
        self._last_pos = pos.copy()
        self.total_time += DT

        if self.target is None:
            return 0.0, 0.0, 0.0

        tgt = np.array(self.target)
        dist = float(np.linalg.norm(pos - tgt))

        if dist < ARRIVAL_THRESH:
            avg = self.path_length / self.total_time if self.total_time > 0 else 0.0
            self.target = None
            return 0.0, 0.0, 0.0

        if self.target_angle is not None:
            ta = self.target_angle
        else:
            # No explicit facing from strategy — aim the body at the motion target.
            dx, dy = tgt[0] - pos[0], tgt[1] - pos[1]
            ta = math.atan2(dy, dx) if math.hypot(dx, dy) > 0.05 else angle

        ctrl = _CONTROLLERS[self.mode]
        return ctrl(pos, tgt, vel, angle, ta)


# ── main loop ─────────────────────────────────────────────────────────────────


def main() -> None:
    robots = [RobotController(i) for i in range(NUM_ROBOTS)]

    ctx = zmq.Context()

    vision_sub = ctx.socket(zmq.SUB)
    _ = vision_sub.connect(f"tcp://localhost:{VISION_PORT}")
    vision_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    vision_sub.setsockopt(zmq.RCVTIMEO, 100)  # wait up to 100 ms

    manual_sub = ctx.socket(zmq.SUB)
    _ = manual_sub.connect(f"tcp://localhost:{MANUAL_PORT}")
    manual_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    manual_sub.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking

    strategy_sub = ctx.socket(zmq.SUB)
    _ = strategy_sub.connect(f"tcp://localhost:{STRATEGY_PORT}")
    strategy_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    strategy_sub.setsockopt(zmq.RCVTIMEO, 0)

    cmd_push = ctx.socket(zmq.PUSH)
    _ = cmd_push.connect(f"tcp://localhost:{COMMAND_PORT}")

    # Every robot's command again, per body axis, for real robots that follow
    # a sim robot (robot/laptop/sim_follow.py, the web app's Robots page).
    real_pub = ctx.socket(zmq.PUB)
    _ = real_pub.bind(f"tcp://*:{REAL_PORT}")

    strategy_enabled = False

    print(
        f"[RobotNode] vision←:{VISION_PORT}  manual←:{MANUAL_PORT}  cmds→:{COMMAND_PORT}  strategy←:{STRATEGY_PORT}  real→:{REAL_PORT}"
    )

    while True:
        # Drain strategy targets first (non-blocking), only if enabled
        if strategy_enabled:
            _drain_targets(strategy_sub, robots, zmq)
        else:
            # Still drain the socket so messages don't pile up
            while True:
                try:
                    _ = strategy_sub.recv_string()
                except zmq.Again:
                    break

        # Drain manual targets / direct-velocity commands second so mouse
        # clicks and WASD/gamepad override strategy
        while True:
            try:
                msg = manual_sub.recv_string()
                data = json.loads(msg)

                # Strategy toggle from viz_node
                if "strategy_enabled" in data:
                    strategy_enabled = data["strategy_enabled"]
                    state_str = "ON" if strategy_enabled else "OFF"
                    print(f"[RobotNode] Strategy → {state_str}")

                for rid_str, info in data.get("targets", {}).items():
                    i = int(rid_str)
                    if 0 <= i < NUM_ROBOTS:
                        robots[i].set_target(
                            info["x"],
                            info["y"],
                            info.get("mode", "2005_INVERSION"),
                            info.get("angle"),
                        )

                for rid_str, vel in data.get("direct", {}).items():
                    i = int(rid_str)
                    if 0 <= i < NUM_ROBOTS:
                        robots[i].set_direct_vel(
                            vel["vx"], vel["vy"], vel["w"], vel.get("frame") == "robot"
                        )

                if data.get("type") == "ball_stuck_toggle":
                    _ = cmd_push.send_string(json.dumps({"type": "ball_stuck_toggle"}))

                for rid_str in data.get("kick", {}):
                    i = int(rid_str)
                    if 0 <= i < NUM_ROBOTS:
                        _ = cmd_push.send_string(
                            json.dumps({"type": "kick", "robot_id": i})
                        )

                # Per-robot dribble from human controllers. Strategy_node sends
                # dribble for AI-owned robots only, so these two paths never
                # fight over the same robot id.
                for rid_str, active in data.get("dribble", {}).items():
                    i = int(rid_str)
                    if 0 <= i < NUM_ROBOTS:
                        _ = cmd_push.send_string(
                            json.dumps({"type": "dribble", "robot_id": i, "active": bool(active)})
                        )

            except zmq.Again:
                break

        # Block until next vision frame
        try:
            state = json.loads(vision_sub.recv_string())
        except zmq.Again:
            continue

        real: dict[str, dict[str, object]] = {}
        for robot in robots:
            rid_str = str(robot.id)
            if rid_str not in state.get("robots", {}):
                continue
            rstate = state["robots"][rid_str]
            command = robot.compute_command(rstate)
            _ = cmd_push.send_string(
                json.dumps(
                    {
                        "robot_id": robot.id,
                        "wheel_speeds": _inverse_kinematics(*command),
                    }
                )
            )
            forward, left, turn = _body_command(*command)
            real[rid_str] = {
                "forward": round(forward, 3),
                "left": round(left, 3),
                "turn": round(turn, 3),
                "dribble": bool(rstate.get("dribbling")),
                # The camera sees this robot right now. A real robot only
                # follows its command then: otherwise it would drive blind.
                "tracked": rstate.get("source") == "vision" and not rstate.get("stale", True),
            }
        _ = real_pub.send_string(json.dumps({"t": state.get("t"), "robots": real}))


# drain all pending target messages from a SUB socket.
def _drain_targets(sub_socket, robots, zmq_module):
    while True:
        try:
            msg = sub_socket.recv_string()
            targets = json.loads(msg).get("targets", {})
            for rid_str, info in targets.items():
                i = int(rid_str)
                if 0 <= i < NUM_ROBOTS:
                    robots[i].set_target(
                        info["x"],
                        info["y"],
                        info.get("mode", "2005_INVERSION"),
                        info.get("angle"),
                    )
        except zmq_module.Again:
            break


if __name__ == "__main__":
    main()
