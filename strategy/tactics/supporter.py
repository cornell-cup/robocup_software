"""Supporter tactic: wait ahead of the ball toward the opponent's goal."""

from __future__ import annotations

import numpy as np

from strategy.command import Command
from strategy.geometry import attack_angle, opp_goal
from strategy.skills.navigate import face_point
from strategy.state import FIELD_SCALE, BallState, RobotState

# Distances are tuned on 9 x 6 and scaled by FIELD_SCALE (see attacker.py).
# This far past the ball toward the opponent's goal (behind it before
# kickoff), and this far to the side (+y).
SUPPORT_AHEAD = 1.5 * FIELD_SCALE
SUPPORT_SIDE = 1.0 * FIELD_SCALE


def support(robot: RobotState, ball: BallState, team_color: str, kicked_off: bool) -> Command:
    """Hold a point past the ball toward their goal, facing the ball. Before
    kickoff, hold it on our side instead, so the two teams' supporters don't
    cross paths and collide while the attackers set up."""
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
