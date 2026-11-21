"""Attacker tactic: get behind the ball and move it toward the opponent's goal.

Checked in order:
  1. contested: an opponent is also on a slow ball, so kick it off at an angle
     to break the head-on standoff;
  2. RL kick: when turned on and the robot is on its own side of the ball,
     the learned policy picks the target and the kicker fires;
  3. a rolling ball (faster than MEET_BALL_SPEED): me.intercept(), meet it
     where the robot can first reach it and play it on toward their goal
     (+0.58 goals a game against v1 over 80 games of 120 s, 2026-11-20);
  4. otherwise me.kick_toward() their goal: get behind the ball on the line to
     the goal, facing along it, then drive through it.

3 and 4 are skills (skills/ball.py): a team that replaces intercept or
kick_toward gets its own version used here too.
"""

from __future__ import annotations

import math

import numpy as np

from strategy.api import Me, World
from strategy.core.command import Command
from strategy.skills.ball import (  # noqa: F401  (re-exported: the attacker's tuning, as before)
    ALIGN_DIST as ATTACKER_ALIGN_DIST,
)
from strategy.skills.ball import (  # noqa: F401
    APPROACH_LATERAL,
    APPROACH_OFFSET,
    LINEUP_ANGLE,
    LINEUP_SLACK,
    LINEUP_WIDTH,
    MEET_BALL_SPEED,
    PUSH_THROUGH,
    aim_point,
    lined_up,
    push_through,
)
from strategy.skills.navigate import behind_ball
from strategy.world.geometry import attack_angle, distance
from strategy.world.state import FIELD_SCALE, BallState, RobotState

# Contested-ball detection: an opposing forward this close to a stalled ball
# means we're in a head-on push stalemate. The attacker switches to a skewed
# approach to break it. Tuned on 9 x 6, scaled by FIELD_SCALE.
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


def attacker(me: Me, world: World) -> Command:
    """The built-in attacker slot."""
    robot, gamestate, team_color = me.state, world.gamestate, world.color
    rl_skill, rl_enabled = world.rl_skill, world.rl_enabled
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
        return me.intercept()
    return me.kick_toward(aim_point(ball.pos, team_color))


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


def _push_toward_goal(robot: RobotState, ball: BallState, team_color: str, team_dir: float) -> Command:
    """Push the ball toward their goal from where it is (as kick_toward does)."""
    return push_through(robot, ball.pos, aim_point(ball.pos, team_color), team_color)
