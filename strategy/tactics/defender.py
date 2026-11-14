"""Defender tactic: play goalie on the line from our goal to the ball.

Used by the defender in a team of three and by a robot playing alone.
"""

from __future__ import annotations

import math

import numpy as np

from strategy.command import Command
from strategy.geometry import our_goal
from strategy.state import FIELD_SCALE, GOAL_Y_MAX, GOAL_Y_MIN

# Distances are tuned on 9 x 6 and scaled by FIELD_SCALE (see attacker.py).

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
