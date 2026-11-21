"""Frozen copy of the built-in team as of 2026-11-20, after strategy step 2 (v2).

v1 (baseline_v1.py) plus the attacker meeting a rolling ball where it can
first reach it (+0.58 goals a game against v1 over 80 games): tactics/
(attacker, supporter, defender, role assignment), the navigation helpers they
use, and decide()'s loop, copied into one file as plain functions on the game
state, so later changes to tactics/, skills/ or api.py never change v2.

The match runner's default opponent from v2 on (`--b v2`): strategy step 3
and later have to match or beat it. Do not edit or "fix" this file; a change
here moves the bar every later version is measured against. Shared basics it
still imports (GameState, geometry, Command, sim settings, and the analysis
layer's ball_intercept) are plain data and maths.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from strategy.core.command import Command
from strategy.world.analysis import ball_intercept
from strategy.world.geometry import (
    attack_angle,
    distance,
    opp_goal,
    our_goal,
    wrap_angle,
)
from strategy.world.simconfig import ARRIVAL_THRESH
from strategy.world.state import (
    FIELD_SCALE,
    GOAL_MOUTH_H,
    GOAL_Y_MAX,
    GOAL_Y_MIN,
    BallState,
    GameState,
    RobotState,
)

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill


def decide(
    gamestate: GameState,
    rl_skill: "RLKickSkill | None" = None,
    rl_enabled: bool = False,
    sticky_attackers: dict[str, int] | None = None,
    kicked_off: bool = True,
    human_blue_ids: set[int] | None = None,
    lost_ids: set[int] | None = None,
) -> tuple[dict[int, tuple[float, float, float]], list[int], set[int]]:
    """One frame for both teams: (targets, kicks, attackers), like core/team.py's."""
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    lost = lost_ids or set()

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
        skip = (human_ids if team_color == "blue" else set()) | lost
        if skip:
            team = [r for r in team if r.id not in skip]
            if not team:
                sticky.pop(team_color, None)
                continue

        for robot, role in assign_roles(team, gamestate.ball.pos, sticky, team_color):
            if role == ATTACKER:
                attacker_rids.add(robot.id)
                command = attack(robot, gamestate, team_color, rl_skill, rl_enabled)
            elif role == SUPPORTER:
                command = support(robot, gamestate.ball, team_color, kicked_off)
            else:
                command = defend_goal(gamestate.ball.pos, team_color)
            targets[robot.id] = command.target
            if command.kick:
                kicks.append(robot.id)

    return targets, kicks, attacker_rids


# -- role assignment (tactics/role_assignments.py) ----------------------------

ATTACKER = "attacker"
SUPPORTER = "supporter"
DEFENDER = "defender"

# Incumbent attacker keeps the role unless a teammate is at least this much
# closer to the ball. Bigger than typical per-tick noise and the supporter's
# transient pass-by distance. Tuned on 9 x 6, scaled by FIELD_SCALE.
ATTACKER_HYSTERESIS = 0.4 * FIELD_SCALE


def assign_roles(
    team: list[RobotState],
    ball_pos: np.ndarray,
    sticky: dict[str, int],
    team_color: str,
) -> list[tuple[RobotState, str]]:
    """(robot, role) for every robot in `team`, attacker first. `sticky` holds
    each team's attacker from the last frame and is updated in place."""
    if len(team) == 1:
        sticky.pop(team_color, None)
        return [(team[0], DEFENDER)]

    # Roles purely by distance to ball: closest = attacker, middle =
    # supporter, furthest = defender.
    sorted_team = sorted(team, key=lambda r: distance(r.pos, ball_pos))

    # Sticky attacker with hysteresis. The supporter's path runs past the
    # ball, so per-tick "closest is attacker" flips the role mid-stride and
    # the wrong robot ends up emitting the kick. Keep the incumbent unless
    # a teammate is meaningfully closer.
    last_rid = sticky.get(team_color)
    incumbent = (
        next((r for r in sorted_team if r.id == last_rid), None)
        if last_rid is not None
        else None
    )
    if incumbent is not None and (
        distance(sorted_team[0].pos, ball_pos) + ATTACKER_HYSTERESIS
        >= distance(incumbent.pos, ball_pos)
    ):
        sorted_team = [incumbent] + [r for r in sorted_team if r.id != incumbent.id]
    sticky[team_color] = sorted_team[0].id

    roles = [ATTACKER, SUPPORTER]
    return [(r, roles[i] if i < len(roles) else DEFENDER) for i, r in enumerate(sorted_team)]


# -- attacker (tactics/attacker.py) -------------------------------------------

# Offset, in meters, placed on our side of the ball so body pushes forward.
# Must keep the ball inside the kick zone (ROBOT_RADIUS … ROBOT_RADIUS+
# KICK_ZONE_DEPTH ≈ 0.143 … 0.263 on 9 x 6) once the attacker arrives,
# otherwise the kick never fires. 0.25 lands the ball cleanly inside that band.
APPROACH_OFFSET = 0.25 * FIELD_SCALE
# Per-team y bias on the approach point so the two attackers don't converge
# head-on at the same point and pin the ball. Must stay below
# KICK_ZONE_HALF_WIDTH (0.10) plus ARRIVAL_THRESH (~0.04) so the ball is still
# inside the kick zone once the attacker stops at the approach point.
APPROACH_LATERAL = 0.05 * FIELD_SCALE
# Within this distance of the approach point, the attacker rotates to the kick
# direction. Farther away, it faces the motion direction for natural driving.
ATTACKER_ALIGN_DIST = 0.6 * FIELD_SCALE
# Contested-ball detection: an opposing forward this close to a stalled ball
# means we're in a head-on push stalemate. The attacker switches to a skewed
# approach to break it.
CONTESTED_OPP_DIST = 0.5 * FIELD_SCALE
CONTESTED_BALL_SPEED = 0.5  # m/s
# Kick-angle skew applied when contested. Both teams skew by the same sign,
# so since team_dir differs by π the absolute kick directions diverge by
# 2·SKEW — the two attackers end up on different diagonals around the ball
# instead of pinning head-on along the goal-to-goal axis.
CONTESTED_SKEW = math.pi / 6
# Lined up to push: behind the ball, at most this much farther back than the
# approach point (the controller stops anywhere within ARRIVAL_THRESH of it)...
LINEUP_SLACK = ARRIVAL_THRESH + 0.02 * FIELD_SCALE
# ...at most this far off the line from the ball to their goal (the kick
# zone's half-width: the ball then meets the robot's front)...
LINEUP_WIDTH = 0.10 * FIELD_SCALE
# ...and facing along that line to within this.
LINEUP_ANGLE = math.radians(25)
# Pushing: the target this far past the ball toward their goal, so the robot
# keeps driving into it instead of arriving and stopping.
PUSH_THROUGH = 0.3 * FIELD_SCALE
# Where to push the ball: a point this far behind their goal line, level with
# the ball but at least AIM_POST_MARGIN inside the posts. Aiming at the goal
# line's centre instead, a ball next to a post was pushed along the goal line,
# from a spot inside the end wall the robot could never reach.
AIM_DEPTH = GOAL_MOUTH_H / 2
AIM_POST_MARGIN = GOAL_MOUTH_H / 4
# The RL policy only runs with the robot at least this far behind the ball
# along the attack axis, meters (unscaled: RL runs on 9 x 6 only).
RL_BEHIND_BALL = 0.1


def attack(
    robot: RobotState,
    gamestate: GameState,
    team_color: str,
    rl_skill: "RLKickSkill | None" = None,
    rl_enabled: bool = False,
) -> Command:
    ball = gamestate.ball
    attacks_right = team_color == "blue"
    team_dir = attack_angle(team_color)

    opponents = gamestate.red if team_color == "blue" else gamestate.blue
    if is_contested(ball, opponents):
        return _break_standoff(ball, team_dir)

    # Policy was trained with robot west of ball (blue-attacks-right). Mirror
    # works cleanly only when the attacker is on its own side of the ball
    # along the attack axis; otherwise the policy drifts OOD and picks the
    # long way round. Fall back to classic there.
    if attacks_right:
        rl_in_dist = robot.pos[0] < ball.pos[0] - RL_BEHIND_BALL
    else:
        rl_in_dist = robot.pos[0] > ball.pos[0] + RL_BEHIND_BALL
    if rl_enabled and rl_skill is not None and rl_in_dist:
        # RL: lock to team_dir. Policy was trained with the robot at that
        # angle and its action frame assumes it. Only RL emits an auto-kick:
        # classic relies on body push so the AI doesn't kick the ball out from
        # under a human operator who's nudged near it.
        tx, ty = rl_skill.target(robot, ball, attacks_right=attacks_right)
        return Command(tx, ty, team_dir, kick=True)

    if ball.speed > MEET_BALL_SPEED:
        meet = ball_intercept(robot, ball)
        if meet.t > 0.0:
            ball = BallState(pos=meet.point, vel=np.zeros(2), speed=0.0)
    return _push_toward_goal(robot, ball, team_color, team_dir)


# A ball rolling faster than this (m/s) is met where the attacker can first reach it.
MEET_BALL_SPEED = 0.3


def is_contested(ball: BallState, opponents: list[RobotState]) -> bool:
    """An opponent is right on a slow ball: a head-on pushing standoff."""
    opp_min = min((distance(o.pos, ball.pos) for o in opponents), default=float("inf"))
    return opp_min < CONTESTED_OPP_DIST and ball.speed < CONTESTED_BALL_SPEED


def _break_standoff(ball: BallState, team_dir: float) -> Command:
    # Skew the kick off the goal axis so the two attackers no longer pin the
    # ball head-on. Whichever fires first sends the ball off its own diagonal
    # and out of the standoff. Fire the kicker every tick: sim/ball_contact.py's zone
    # check gates it so it only takes effect once the attacker is positioned.
    kick_angle = team_dir + CONTESTED_SKEW
    kick_dir = np.array([math.cos(kick_angle), math.sin(kick_angle)])
    pt = behind_ball(ball.pos, kick_dir, APPROACH_OFFSET)
    return Command(float(pt[0]), float(pt[1]), kick_angle, kick=True)


def _push_toward_goal(
    robot: RobotState, ball: BallState, team_color: str, team_dir: float
) -> Command:
    aim = aim_point(ball.pos, team_color)
    to_aim = aim - ball.pos
    n = float(np.linalg.norm(to_aim))
    if n < 1e-6:  # ball already at the aim point, behind the goal line: it's in
        return Command(float(ball.pos[0]), float(ball.pos[1]), team_dir)
    direction = to_aim / n
    push_angle = math.atan2(direction[1], direction[0])

    # Lined up: drive through the ball. Stopping at the approach point would
    # leave the robot parked short of the ball (it's farther back than the
    # robot and ball radii), so the ball would never move. While the robot
    # pushes it stays lined up; if the ball gets away, it lines up again.
    if lined_up(robot, ball.pos, direction, push_angle):
        pt = ball.pos + direction * PUSH_THROUGH
        return Command(float(pt[0]), float(pt[1]), push_angle)

    # Face the motion direction while navigating so the robot doesn't "shift"
    # sideways with its back to the target. Once near the approach point,
    # rotate to face along the push.
    pt = _approach_point(ball.pos, aim, team_color)
    ta = heading_while_driving(robot.pos, pt, push_angle, ATTACKER_ALIGN_DIST)
    return Command(float(pt[0]), float(pt[1]), ta)


def aim_point(ball_pos: np.ndarray, team_color: str) -> np.ndarray:
    """Where to push the ball: behind their goal line, level with the ball,
    kept inside the posts."""
    goal = opp_goal(team_color)
    into_goal = math.cos(attack_angle(team_color))  # +1 blue (+x), -1 red
    y = min(max(float(ball_pos[1]), GOAL_Y_MIN + AIM_POST_MARGIN), GOAL_Y_MAX - AIM_POST_MARGIN)
    return np.array([float(goal[0]) + into_goal * AIM_DEPTH, y])


def lined_up(
    robot: RobotState, ball_pos: np.ndarray, direction: np.ndarray, push_angle: float
) -> bool:
    """The robot is behind the ball on the push line, close, and facing along it."""
    rel = ball_pos - robot.pos
    along = float(rel @ direction)  # how far behind the ball, along the push
    across = abs(float(direction[0] * rel[1] - direction[1] * rel[0]))  # off the line
    return (
        0.0 < along <= APPROACH_OFFSET + LINEUP_SLACK
        and across <= LINEUP_WIDTH
        and abs(wrap_angle(robot.angle - push_angle)) <= LINEUP_ANGLE
    )


def _approach_point(
    ball_pos: np.ndarray, opp_goal_pos: np.ndarray, team_color: str
) -> np.ndarray:
    to_goal = opp_goal_pos - ball_pos
    n = np.linalg.norm(to_goal)
    if n < 1e-6:
        return ball_pos
    dir_goal = to_goal / n
    # Blue from -y, red from +y so the two attackers don't pin head-on at the
    # ball when they meet. Stays inside KICK_ZONE_HALF_WIDTH so the kick still
    # fires once the robot settles.
    lateral_y = -APPROACH_LATERAL if team_color == "blue" else APPROACH_LATERAL
    return behind_ball(ball_pos, dir_goal, APPROACH_OFFSET) + np.array([0.0, lateral_y])


# -- supporter (tactics/supporter.py) -----------------------------------------

# Distances are tuned on 9 x 6 and scaled by FIELD_SCALE (see attacker.py).
# This far past the ball toward the opponent's goal (behind it before
# kickoff), and this far to the side (+y).
SUPPORT_AHEAD = 1.5 * FIELD_SCALE
SUPPORT_SIDE = 1.0 * FIELD_SCALE


def support(robot: RobotState, ball: BallState, team_color: str, kicked_off: bool) -> Command:
    """Hold a point past the ball toward their goal, facing the ball. Before
    kickoff, hold it on our side instead."""
    goal = opp_goal(team_color)
    to_goal = (goal - ball.pos) / (np.linalg.norm(goal - ball.pos) + 1e-6)
    # Fixed +y perp so blue/red mirror across midfield instead of
    # point-mirroring through the ball (which sent red diagonally).
    perp = np.array([0.0, 1.0])
    if not kicked_off:
        pt = ball.pos - to_goal * SUPPORT_AHEAD + perp * SUPPORT_SIDE
    else:
        pt = ball.pos + to_goal * SUPPORT_AHEAD + perp * SUPPORT_SIDE
    # Face the ball, not the target.
    return Command(float(pt[0]), float(pt[1]), face_point(robot.pos, ball.pos, attack_angle(team_color)))


# -- defender (tactics/defender.py) -------------------------------------------

# Goalie's resting standoff in front of the goal. Bigger than the pure
# mouth-coverage value so lateral tracking has meaningful range when the
# ball is wide.
GOALIE_STANDOFF = 0.8 * FIELD_SCALE
# Once the ball is within RUSH_DIST of own goal, scale the standoff up
# toward RUSH_STANDOFF so the goalie commits forward to challenge.
GOALIE_RUSH_DIST = 2.5 * FIELD_SCALE
GOALIE_RUSH_STANDOFF = 1.6 * FIELD_SCALE
# Keep the goalie a hair inside the posts so it never wedges against a wall.
GOALIE_Y_MARGIN = 0.05 * FIELD_SCALE


def defend_goal(ball_pos: np.ndarray, team_color: str) -> Command:
    """Stand on the goal-to-ball line, farther out as the ball comes closer,
    inside the posts, facing the ball."""
    goal = our_goal(team_color)
    to_ball = ball_pos - goal
    n = float(np.linalg.norm(to_ball))
    if n < 1e-6:
        tx, ty = float(goal[0]), float(goal[1])
    else:
        if n < GOALIE_RUSH_DIST:
            t = 1.0 - n / GOALIE_RUSH_DIST
            standoff = GOALIE_STANDOFF + (GOALIE_RUSH_STANDOFF - GOALIE_STANDOFF) * t
        else:
            standoff = GOALIE_STANDOFF
        pt = goal + (to_ball / n) * standoff
        tx, ty = float(pt[0]), float(pt[1])

    ty = float(np.clip(ty, GOAL_Y_MIN + GOALIE_Y_MARGIN, GOAL_Y_MAX - GOALIE_Y_MARGIN))

    dx, dy = float(ball_pos[0] - tx), float(ball_pos[1] - ty)
    angle = math.atan2(dy, dx) if math.hypot(dx, dy) > 0.05 else (
        0.0 if team_color == "blue" else math.pi
    )
    return Command(tx, ty, angle)


# -- navigation (skills/navigate.py) ------------------------------------------

def face_point(pos: np.ndarray, toward: np.ndarray, fallback: float) -> float:
    """Heading from `pos` toward `toward`; `fallback` when they're on top of each other."""
    dx, dy = float(toward[0] - pos[0]), float(toward[1] - pos[1])
    if math.hypot(dx, dy) < 0.05:
        return fallback
    return math.atan2(dy, dx)


def heading_while_driving(
    pos: np.ndarray, target: np.ndarray, final_heading: float, align_dist: float
) -> float:
    """Face the way the robot is driving while far from `target`, so it doesn't
    slide sideways with its back to it; within `align_dist`, turn to
    `final_heading`."""
    dx, dy = float(target[0] - pos[0]), float(target[1] - pos[1])
    dist = math.hypot(dx, dy)
    return final_heading if dist < align_dist else math.atan2(dy, dx)


def behind_ball(ball_pos: np.ndarray, direction: np.ndarray, offset: float) -> np.ndarray:
    """The point `offset` behind the ball, opposite unit vector `direction`:
    where to stand to push or kick the ball that way."""
    return ball_pos - direction * offset
