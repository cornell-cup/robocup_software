"""Attacker tactic: get behind the ball and move it toward the opponent's goal.

Three ways, checked in order:
  1. contested: an opponent is also on a slow ball, so kick it off at an angle
     to break the head-on standoff;
  2. RL kick: when turned on and the robot is on its own side of the ball,
     the learned policy picks the target and the kicker fires;
  3. classic: stand behind the ball, lined up with their goal, and push.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from strategy.command import Command
from strategy.geometry import attack_angle, distance, opp_goal
from strategy.skills.navigate import behind_ball, heading_while_driving
from strategy.state import FIELD_SCALE, BallState, GameState, RobotState

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill

# Distances below are tuned on the 9 x 6 field and scaled by FIELD_SCALE, like
# the robot, ball and kick zone (sim/config.py), so on a custom --field they
# keep the same size relative to the robots. Speeds are not scaled: the
# physics isn't either.

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

    return _push_toward_goal(robot, ball, team_color, team_dir)


def is_contested(ball: BallState, opponents: list[RobotState]) -> bool:
    """An opponent is right on a slow ball: a head-on pushing standoff."""
    opp_min = min((distance(o.pos, ball.pos) for o in opponents), default=float("inf"))
    return opp_min < CONTESTED_OPP_DIST and ball.speed < CONTESTED_BALL_SPEED


def _break_standoff(ball: BallState, team_dir: float) -> Command:
    # Skew the kick off the goal axis so the two attackers no longer pin the
    # ball head-on. Whichever fires first sends the ball off its own diagonal
    # and out of the standoff. Fire the kicker every tick: kick.py's zone
    # check gates it so it only takes effect once the attacker is positioned.
    kick_angle = team_dir + CONTESTED_SKEW
    kick_dir = np.array([math.cos(kick_angle), math.sin(kick_angle)])
    pt = behind_ball(ball.pos, kick_dir, APPROACH_OFFSET)
    return Command(float(pt[0]), float(pt[1]), kick_angle, kick=True)


def _push_toward_goal(
    robot: RobotState, ball: BallState, team_color: str, team_dir: float
) -> Command:
    # Face the motion direction while navigating so the robot doesn't "shift"
    # sideways with its back to the target. Once near the approach point,
    # rotate to team_dir to line up the push.
    pt = _approach_point(ball.pos, opp_goal(team_color), team_color)
    ta = heading_while_driving(robot.pos, pt, team_dir, ATTACKER_ALIGN_DIST)
    return Command(float(pt[0]), float(pt[1]), ta)


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
