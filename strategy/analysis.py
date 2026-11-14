"""World analysis, computed once per frame and shared by plays and tactics.

Everything here is measured in time, not distance (strategy/README.md,
Design, principle 3): how long a robot needs to reach a point, when each robot
could first get to the moving ball, which team gets there first, and which
opponents threaten our goal. Time already accounts for field size and robot
speed, so the same thresholds work on 9 x 6 and on the real table.

The motion model is the sim's drive model (sim/config.py, pymunk_world.py):
  - three omniwheels at WHEEL_ANGLES, each pushing up to MOTOR_MAX_FORCE.
    Driving in a direction at angle theta to the robot's heading, with no
    turning, the wheels can push with at most
        F(theta) = MOTOR_MAX_FORCE * 1.5 / max_i |sin(theta - wheel_i)|,
    between 1.5x (sideways, 30 deg off) and sqrt(3)x (straight ahead, 60 deg
    off) one motor's force. config.ROBOT_MAX_ACCEL's 2x also spins the robot.
  - velocity damped by LINEAR_DAMP per second, so with full push A = F / mass
    the speed levels off at vmax = A / LINEAR_DAMP (~1.03 m/s straight ahead,
    ~0.89 m/s sideways), and from speed v0 along the way the robot covers
        x(t) = vmax t + (v0 - vmax) (1 - e^(-c t)) / c,   c = LINEAR_DAMP.
Checked against the physics in strategy/tests. It's the time to get there
flat out, not to stop there, and ignores turning and sideways velocity.
Constants marked TODO(hardware) in config.py feed it, so it follows them once
they are measured on the real robot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from strategy.geometry import our_goal
from strategy.prediction import ball_rest_position, predict_ball_position
from strategy.state import BallState, GameState, RobotState

from config import (  # noqa: E402  (sim/config.py, on sys.path via strategy.state)
    BALL_DAMP,
    BALL_RADIUS,
    LINEAR_DAMP,
    MOTOR_MAX_FORCE,
    ROBOT_MASS,
    ROBOT_RADIUS,
    WHEEL_ANGLES,
)

_WHEELS = np.asarray(WHEEL_ANGLES, dtype=float)
# Centre-to-centre distance at which a robot touches the ball.
TOUCH_DIST = ROBOT_RADIUS + BALL_RADIUS
# How far ahead to look for a ball intercept, and in what steps, seconds.
INTERCEPT_HORIZON = 4.0
INTERCEPT_STEP = 1.0 / 30.0


def drive_accel(local_angle: np.ndarray | float) -> np.ndarray | float:
    """Top acceleration (m/s^2) driving at `local_angle` to the robot's heading."""
    s = np.abs(np.sin(np.asarray(local_angle)[..., None] - _WHEELS)).max(axis=-1)
    return MOTOR_MAX_FORCE / ROBOT_MASS * 1.5 / s


def distance_covered(t: np.ndarray | float, accel: np.ndarray | float,
                     v0: np.ndarray | float) -> np.ndarray | float:
    """How far a robot gets in time t, flat out with acceleration `accel`,
    starting at speed v0 along the way (negative: moving away)."""
    c = LINEAR_DAMP
    vmax = np.asarray(accel) / c
    return vmax * t + (np.asarray(v0) - vmax) * (1.0 - np.exp(-c * np.asarray(t))) / c


def time_to_reach(robot: RobotState, point: np.ndarray, reach: float = 0.0) -> float:
    """Seconds for `robot` to get within `reach` of `point`, flat out."""
    d = point - robot.pos
    dist = float(np.hypot(d[0], d[1])) - reach
    if dist <= 0.0:
        return 0.0
    direction = d / (dist + reach)
    accel = float(drive_accel(math.atan2(direction[1], direction[0]) - robot.angle))
    v0 = float(direction @ robot.vel)
    # distance_covered has a single minimum (when v0 < 0) and then grows
    # without bound, so the first crossing is bracketed by doubling.
    hi = 0.25
    while distance_covered(hi, accel, v0) < dist:
        hi *= 2.0
    lo = 0.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if distance_covered(mid, accel, v0) >= dist:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass
class Intercept:
    robot_id: int
    t: float  # seconds until the robot can touch the ball
    point: np.ndarray  # where the ball is then


def ball_intercept(robot: RobotState, ball: BallState,
                   horizon: float = INTERCEPT_HORIZON, step: float = INTERCEPT_STEP) -> Intercept:
    """The earliest time `robot` can touch the ball, with the ball slowing
    down as in the sim. A ball out of reach for the whole horizon is met where
    it comes to rest."""
    if float(np.hypot(*(ball.pos - robot.pos))) <= TOUCH_DIST:
        return Intercept(robot.id, 0.0, ball.pos.copy())
    if ball.speed < 1e-3:
        return Intercept(robot.id, time_to_reach(robot, ball.pos, TOUCH_DIST), ball.pos.copy())

    ts = np.arange(1, int(round(horizon / step)) + 1) * step
    travel = (1.0 - np.exp(-BALL_DAMP * ts)) / BALL_DAMP if BALL_DAMP > 0 else ts
    points = ball.pos[None, :] + ball.vel[None, :] * travel[:, None]
    d = points - robot.pos[None, :]
    dist = np.hypot(d[:, 0], d[:, 1])
    direction = d / np.maximum(dist, 1e-9)[:, None]
    accel = drive_accel(np.arctan2(direction[:, 1], direction[:, 0]) - robot.angle)
    v0 = direction @ robot.vel
    ok = distance_covered(ts, accel, v0) >= dist - TOUCH_DIST
    hits = np.flatnonzero(ok)
    if hits.size:
        i = int(hits[0])
        return Intercept(robot.id, float(ts[i]), points[i].copy())

    rest = ball_rest_position(ball)
    t_ball = horizon  # the ball has nearly stopped by the horizon
    return Intercept(robot.id, max(t_ball, time_to_reach(robot, rest, TOUCH_DIST)), rest)


@dataclass
class TeamView:
    """One team's picture of the frame."""

    color: str
    first_to_ball: Intercept | None  # our quickest robot to the ball
    their_first_to_ball: Intercept | None  # their quickest
    # Their time to the ball minus ours, seconds: > 0 we get there first.
    race_margin: float
    # CMDragons' threats [3]: the first-level threat is the most immediate way
    # a goal could be scored on us: the opponent who gets the ball first, if
    # they win the race, else the ball. Second-level threats are the other
    # opponents, nearest our goal first.
    first_threat: np.ndarray
    first_threat_robot: int | None
    second_threats: list[tuple[int, np.ndarray]] = field(default_factory=list)


@dataclass
class Analysis:
    intercepts: dict[int, Intercept]  # every available robot
    blue: TeamView
    red: TeamView

    def team(self, color: str) -> TeamView:
        return self.blue if color == "blue" else self.red


def analyze(gamestate: GameState, unavailable: set[int] | frozenset[int] = frozenset()) -> Analysis:
    """Analysis of one frame. `unavailable` robots (human-driven, lost to the
    camera) are left out of the races and threats."""
    ball = gamestate.ball
    intercepts = {
        r.id: ball_intercept(r, ball)
        for r in gamestate.blue + gamestate.red
        if r.id not in unavailable
    }

    def view(color: str, ours: list[RobotState], theirs: list[RobotState]) -> TeamView:
        mine = [intercepts[r.id] for r in ours if r.id in intercepts]
        other = [intercepts[r.id] for r in theirs if r.id in intercepts]
        first = min(mine, key=lambda i: i.t, default=None)
        their_first = min(other, key=lambda i: i.t, default=None)
        t_ours = first.t if first else math.inf
        t_theirs = their_first.t if their_first else math.inf
        margin = t_theirs - t_ours if math.isfinite(t_ours) or math.isfinite(t_theirs) else 0.0

        if their_first is not None and t_theirs < t_ours:
            threat, threat_robot = their_first.point, their_first.robot_id
        else:
            threat, threat_robot = ball.pos.copy(), None
        goal = our_goal(color)
        second = sorted(
            ((r.id, r.pos.copy()) for r in theirs if r.id in intercepts and r.id != threat_robot),
            key=lambda item: float(np.hypot(*(item[1] - goal))),
        )
        return TeamView(color, first, their_first, margin, threat, threat_robot, second)

    return Analysis(
        intercepts,
        view("blue", gamestate.blue, gamestate.red),
        view("red", gamestate.red, gamestate.blue),
    )
