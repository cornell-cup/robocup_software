"""Ball skills: one action each, written against strategy/api.py.

    go_to_ball(me, world)            drive to the ball, facing it
    kick_toward(me, world, target)   line up behind the ball on the line to target, send it there
    intercept(me, world)             meet a rolling ball where it can first be reached, play it on
    pass_to(me, world, mate)         send the ball to a teammate, where it's going to be
    dribble_to(me, world, target)    take the ball with the dribbler on and carry it to target

Each is a slot (core/slots.py): a team can replace any of them, and every
robot that calls it, built-in roles included, then uses its version. In a
slot, call one as a method of the robot: `me.kick_toward(world.their_goal)`.
The built-in attacker is made of these (tactics/attacker.py).

The pushing mechanics (stand behind the ball on the line to a target, then
drive through it) are tuned on 9 x 6 and scaled by FIELD_SCALE, like the
robot, ball and kick zone (sim/config.py): distances shrink with the field,
speeds don't (the physics doesn't either).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from strategy.core.command import Command
from strategy.skills.navigate import behind_ball, heading_while_driving
from strategy.world.analysis import ball_intercept
from strategy.world.geometry import attack_angle, opp_goal, wrap_angle
from strategy.world.simconfig import ARRIVAL_THRESH
from strategy.world.state import (
    FIELD_SCALE,
    GOAL_MOUTH_H,
    GOAL_Y_MAX,
    GOAL_Y_MIN,
    RobotState,
)

if TYPE_CHECKING:
    from strategy.api import Me, World

# Offset, in meters, placed on our side of the ball so body pushes forward.
# Must keep the ball inside the kick zone (ROBOT_RADIUS … ROBOT_RADIUS+
# KICK_ZONE_DEPTH ≈ 0.143 … 0.263 on 9 x 6) once the robot arrives,
# otherwise the kick never fires. 0.25 lands the ball cleanly inside that band.
APPROACH_OFFSET = 0.25 * FIELD_SCALE
# Per-team y bias on the approach point so the two attackers don't converge
# head-on at the same point and pin the ball. Must stay below
# KICK_ZONE_HALF_WIDTH (0.10) plus ARRIVAL_THRESH (~0.04) so the ball is still
# inside the kick zone once the robot stops at the approach point.
APPROACH_LATERAL = 0.05 * FIELD_SCALE
# Within this distance of the approach point, the robot rotates to the push
# direction. Farther away, it faces the motion direction for natural driving.
ALIGN_DIST = 0.6 * FIELD_SCALE
# Lined up to push: behind the ball, at most this much farther back than the
# approach point (the controller stops anywhere within ARRIVAL_THRESH of it)...
LINEUP_SLACK = ARRIVAL_THRESH + 0.02 * FIELD_SCALE
# ...at most this far off the line from the ball to the target (the kick
# zone's half-width: the ball then meets the robot's front)...
LINEUP_WIDTH = 0.10 * FIELD_SCALE
# ...and facing along that line to within this.
LINEUP_ANGLE = math.radians(25)
# Pushing: the target this far past the ball, so the robot keeps driving into
# it instead of arriving and stopping.
PUSH_THROUGH = 0.3 * FIELD_SCALE
# Where to push the ball at their goal: a point this far behind their goal
# line, level with the ball but at least AIM_POST_MARGIN inside the posts.
# Aiming at the goal line's centre instead, a ball next to a post was pushed
# along the goal line, from a spot inside the end wall the robot could never reach.
AIM_DEPTH = GOAL_MOUTH_H / 2
AIM_POST_MARGIN = GOAL_MOUTH_H / 4
# A ball rolling faster than this (m/s) is met where the robot can first
# reach it, not chased where it is now.
MEET_BALL_SPEED = 0.3
# A pass aims where the teammate will be this long from now.
PASS_LEAD_S = 0.5
# Carrying the ball with the dribbler: no faster than this, m/s, or it's lost.
DRIBBLE_SPEED = 0.6


# -- the skills -------------------------------------------------------------------------


def go_to_ball(me: Me, world: World) -> Command:
    """The built-in go_to_ball: drive to the ball, facing it."""
    return me.go_to(world.ball.pos, face=world.ball.pos)


def kick_toward(me: Me, world: World, target: Any) -> Command:
    """The built-in kick_toward: line up behind the ball on the line to
    `target`, then drive through it so it rolls that way (a push: the
    kicker stays off)."""
    return push_through(me.state, world.ball.pos, _xy(target), world.color)


def intercept(me: Me, world: World) -> Command:
    """The built-in intercept: meet a rolling ball where this robot can first
    reach it (a slow ball: where it is), and play it on toward their goal."""
    ball = world.ball
    point = ball.pos
    if ball.speed > MEET_BALL_SPEED:
        meet = ball_intercept(me.state, ball)
        if meet.t > 0.0:
            point = meet.point
    return push_through(me.state, point, aim_point(point, world.color), world.color)


def pass_to(me: Me, world: World, mate: Me) -> Command:
    """The built-in pass_to: line up behind the ball on the line to where
    `mate` will be, and fire the kicker once lined up."""
    target = mate.pos + mate.vel * PASS_LEAD_S
    command = push_through(me.state, world.ball.pos, target, world.color)
    to = target - world.ball.pos
    n = float(np.linalg.norm(to))
    if n > 1e-6 and lined_up(me.state, world.ball.pos, to / n, math.atan2(to[1], to[0])):
        command.kick = True
    return command


def dribble_to(me: Me, world: World, target: Any) -> Command:
    """The built-in dribble_to: take the ball with the dribbler on, then carry
    it to `target`, slowly enough to keep it."""
    target = _xy(target)
    if me.has_ball:
        return me.go_to(target, face=target, dribble=True, max_speed=DRIBBLE_SPEED)
    command = push_through(me.state, world.ball.pos, target, world.color)
    command.dribble = True
    return command


# -- the mechanics they share -------------------------------------------------------


def push_through(robot: RobotState, ball_pos: np.ndarray, target: np.ndarray, team_color: str) -> Command:
    """Stand behind the ball on the line to `target`, then drive through it.
    Lined up, the robot drives on past the ball (stopping at the approach
    point would leave it parked short, the ball never moving); while it
    pushes it stays lined up, and if the ball gets away it lines up again.
    Away from the line it faces where it drives, turning to the push near it."""
    to_target = target - ball_pos
    n = float(np.linalg.norm(to_target))
    if n < 1e-6:  # the ball is already there (at their goal: it's in)
        return Command(float(ball_pos[0]), float(ball_pos[1]), attack_angle(team_color))
    direction = to_target / n
    push_angle = math.atan2(direction[1], direction[0])
    if lined_up(robot, ball_pos, direction, push_angle):
        pt = ball_pos + direction * PUSH_THROUGH
        return Command(float(pt[0]), float(pt[1]), push_angle)
    pt = approach_point(ball_pos, target, team_color)
    ta = heading_while_driving(robot.pos, pt, push_angle, ALIGN_DIST)
    return Command(float(pt[0]), float(pt[1]), ta)


def aim_point(ball_pos: np.ndarray, team_color: str) -> np.ndarray:
    """Where to push the ball at their goal: behind their goal line, level
    with the ball, kept inside the posts."""
    goal = opp_goal(team_color)
    into_goal = math.cos(attack_angle(team_color))  # +1 blue (+x), -1 red
    y = min(max(float(ball_pos[1]), GOAL_Y_MIN + AIM_POST_MARGIN), GOAL_Y_MAX - AIM_POST_MARGIN)
    return np.array([float(goal[0]) + into_goal * AIM_DEPTH, y])


def lined_up(robot: RobotState, ball_pos: np.ndarray, direction: np.ndarray, push_angle: float) -> bool:
    """The robot is behind the ball on the push line, close, and facing along it."""
    rel = ball_pos - robot.pos
    along = float(rel @ direction)  # how far behind the ball, along the push
    across = abs(float(direction[0] * rel[1] - direction[1] * rel[0]))  # off the line
    return (
        0.0 < along <= APPROACH_OFFSET + LINEUP_SLACK
        and across <= LINEUP_WIDTH
        and abs(wrap_angle(robot.angle - push_angle)) <= LINEUP_ANGLE
    )


def approach_point(ball_pos: np.ndarray, target: np.ndarray, team_color: str) -> np.ndarray:
    """Behind the ball on the line to `target`, nudged sideways by team."""
    to_target = target - ball_pos
    n = np.linalg.norm(to_target)
    if n < 1e-6:
        return ball_pos
    # Blue from -y, red from +y so the two attackers don't pin head-on at the
    # ball when they meet. Stays inside KICK_ZONE_HALF_WIDTH so the kick still
    # fires once the robot settles.
    lateral_y = -APPROACH_LATERAL if team_color == "blue" else APPROACH_LATERAL
    return behind_ball(ball_pos, to_target / n, APPROACH_OFFSET) + np.array([0.0, lateral_y])


def _xy(p: Any) -> np.ndarray:
    return np.asarray(getattr(p, "pos", p), dtype=float)[:2]
