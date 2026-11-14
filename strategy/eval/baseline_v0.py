"""Frozen copy of strategy_node.decide() as of 2026-11-14 (v0).

The reference opponent for the match runner (strategy/eval/match.py): each step
of the redesign has to match or beat it. Do not edit or "fix" this file; a
change here moves the bar every later version is measured against. Copied
verbatim, except: this header; `lost_ids` added to decide()'s signature
(ignored: v0 planned lost robots too); and the closer-kick-wins rule at the end
of decide() moved out to strategy_node.resolve_kick_conflict, which Brain and
the match runner apply, so v0 plays exactly as before.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from strategy.geometry import distance, opp_goal, our_goal
from strategy.state import FIELD_LENGTH, GOAL_Y_MAX, GOAL_Y_MIN, GameState

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
    # Placeholder for Decision Tree
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    ball = gamestate.ball

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
        attacks_right = team_color == "blue"
        team_dir = 0.0 if attacks_right else math.pi

        # Drop human-driven robots so the AI plans only for what it controls.
        # The remaining team falls into whichever role-count branch fits its
        # size (1 → goalie, 2 → attacker+supporter, 3 → +defender).
        if team_color == "blue" and human_ids:
            team = [r for r in team if r.id not in human_ids]
            if not team:
                sticky.pop(team_color, None)
                continue

        # Single-robot teams act as the lone goalie: stay on the line from
        # own goal to the ball at a small standoff. No kick is emitted.
        if len(team) == 1:
            robot = team[0]
            tx, ty, ta = _goalie_target(ball.pos, team_color)
            targets[robot.id] = (tx, ty, ta)
            sticky.pop(team_color, None)
            continue

        # Roles purely by distance to ball: closest = attacker, middle =
        # supporter, furthest = defender. Sticky hysteresis keeps the
        # attacker stable while the supporter's path runs past the ball.
        sorted_team = sorted(team, key=lambda r: distance(r.pos, ball.pos))

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
            distance(sorted_team[0].pos, ball.pos) + ATTACKER_HYSTERESIS
            >= distance(incumbent.pos, ball.pos)
        ):
            sorted_team = [incumbent] + [r for r in sorted_team if r.id != incumbent.id]
        sticky[team_color] = sorted_team[0].id

        for i, robot in enumerate(sorted_team):
            if i == 0:  # attacker
                attacker_rids.add(robot.id)
                # Policy was trained with robot west of ball (blue-attacks-right).
                # Mirror works cleanly only when the attacker is on its own side
                # of the ball along the attack axis; otherwise the policy drifts
                # OOD and picks the long way round. Fall back to classic there.
                if attacks_right:
                    rl_in_dist = robot.pos[0] < ball.pos[0] - 0.1
                else:
                    rl_in_dist = robot.pos[0] > ball.pos[0] + 0.1

                opp_team_list = gamestate.red if team_color == "blue" else gamestate.blue
                opp_min = min(
                    (distance(o.pos, ball.pos) for o in opp_team_list),
                    default=float("inf"),
                )
                contested = (
                    opp_min < CONTESTED_OPP_DIST and ball.speed < CONTESTED_BALL_SPEED
                )

                if contested:
                    # Skew the kick off the goal axis so the two attackers no
                    # longer pin the ball head-on. Whichever fires first sends
                    # the ball off its own diagonal and out of the standoff.
                    kick_angle = team_dir + CONTESTED_SKEW
                    kick_dir = np.array([math.cos(kick_angle), math.sin(kick_angle)])
                    pt = ball.pos - kick_dir * APPROACH_OFFSET
                    targets[robot.id] = (float(pt[0]), float(pt[1]), kick_angle)
                    # Fire the kicker every tick — kick.py's zone check gates it
                    # so it only takes effect once the attacker is positioned.
                    kicks.append(robot.id)
                elif rl_enabled and rl_skill is not None and rl_in_dist:
                    # RL: lock to team_dir. Policy was trained with the robot
                    # at that angle and its action frame assumes it.
                    tx, ty = rl_skill.target(robot, ball, attacks_right=attacks_right)
                    targets[robot.id] = (tx, ty, team_dir)
                    # Only RL emits an auto-kick. Classic relies on body push
                    # so the AI doesn't kick the ball out from under a human
                    # operator who's nudged near it.
                    kicks.append(robot.id)
                else:
                    # Classic: face the motion direction while navigating so the
                    # robot doesn't "shift" sideways with its back to the target.
                    # Once near the approach point, rotate to team_dir to line
                    # up the kick.
                    pt = _approach_behind_ball(ball.pos, opp_goal(team_color), team_color)
                    dx, dy = float(pt[0] - robot.pos[0]), float(pt[1] - robot.pos[1])
                    dist = math.hypot(dx, dy)
                    ta = team_dir if dist < ATTACKER_ALIGN_DIST else math.atan2(dy, dx)
                    targets[robot.id] = (float(pt[0]), float(pt[1]), ta)
            elif i == 1:  # supporter — sits past the ball toward opp goal
                goal = opp_goal(team_color)
                to_goal = (goal - ball.pos) / (np.linalg.norm(goal - ball.pos) + 1e-6)
                # Fixed +y perp so blue/red mirror across midfield instead of
                # point-mirroring through the ball (which sent red diagonally).
                perp = np.array([0.0, 1.0])
                if not kicked_off:
                    # Pre-kickoff: hold on own side so the two supporters don't
                    # cross paths and collide while the attackers set up.
                    pt = ball.pos - to_goal * 1.5 + perp * 1.0
                else:
                    pt = ball.pos + to_goal * 1.5 + perp * 1.0
                targets[robot.id] = (
                    float(pt[0]), float(pt[1]),
                    _face_point(robot.pos, ball.pos, team_dir),  # face the ball, not the target
                )
            else:  # defender — plays goalie on the goal-to-ball line
                tx, ty, ta = _goalie_target(ball.pos, team_color)
                targets[robot.id] = (tx, ty, ta)

    # (v0 kept only the closer of two opposing kicks here. That rule now lives
    # in strategy_node.resolve_kick_conflict, applied by Brain or the match
    # runner, so it can settle kicks between two different strategies.)
    return targets, kicks, attacker_rids


def _ball_in_opp_half_for(team_color: str, ball_x: float) -> bool:
    # Blue attacks +x (right half); red attacks -x (left half).
    if team_color == "blue":
        return ball_x > FIELD_LENGTH / 2.0
    return ball_x < FIELD_LENGTH / 2.0


def _face_point(
    pos: np.ndarray, toward: np.ndarray, fallback: float
) -> float:
    dx, dy = float(toward[0] - pos[0]), float(toward[1] - pos[1])
    if math.hypot(dx, dy) < 0.05:
        return fallback
    return math.atan2(dy, dx)


# Offset, in meters, placed on our side of the ball so body pushes forward.
# Must keep the ball inside the kick zone (ROBOT_RADIUS … ROBOT_RADIUS+
# KICK_ZONE_DEPTH ≈ 0.143 … 0.263) once the attacker arrives, otherwise the
# kick never fires. 0.25 lands the ball cleanly inside that band.
APPROACH_OFFSET = 0.25
# Per-team y bias on the approach point so the two attackers don't converge
# head-on at the same point and pin the ball. Must stay below
# KICK_ZONE_HALF_WIDTH (0.10) plus ARRIVAL_THRESH (~0.04) so the ball is still
# inside the kick zone once the attacker stops at the approach point.
APPROACH_LATERAL = 0.05
# Within this distance of the approach point, the attacker rotates to the kick
# direction. Farther away, it faces the motion direction for natural driving.
ATTACKER_ALIGN_DIST = 0.6
# Incumbent attacker keeps the role unless a teammate is at least this much
# closer to the ball. Bigger than typical per-tick noise and the supporter's
# transient pass-by distance.
ATTACKER_HYSTERESIS = 0.4
# Contested-ball detection: an opposing forward this close to a stalled ball
# means we're in a head-on push stalemate. The attacker switches to a skewed
# approach to break it.
CONTESTED_OPP_DIST = 0.5
CONTESTED_BALL_SPEED = 0.5
# Kick-angle skew applied when contested. Both teams skew by the same sign,
# so since team_dir differs by π the absolute kick directions diverge by
# 2·SKEW — the two attackers end up on different diagonals around the ball
# instead of pinning head-on along the goal-to-goal axis.
CONTESTED_SKEW = math.pi / 6


# Goalie's resting standoff in front of the goal. Bigger than the pure
# mouth-coverage value so lateral tracking has meaningful range when the
# ball is wide.
GOALIE_STANDOFF = 0.8
# Once the ball is within RUSH_DIST of own goal, scale the standoff up
# toward RUSH_STANDOFF so the goalie commits forward to challenge.
GOALIE_RUSH_DIST = 2.5
GOALIE_RUSH_STANDOFF = 1.6
# Keep the goalie a hair inside the posts so it never wedges against a wall.
GOALIE_Y_MARGIN = 0.05


def _goalie_target(
    ball_pos: np.ndarray, team_color: str
) -> tuple[float, float, float]:
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
    return tx, ty, angle


def _approach_behind_ball(
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
    return ball_pos - dir_goal * APPROACH_OFFSET + np.array([0.0, lateral_y])
