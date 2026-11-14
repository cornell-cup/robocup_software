"""Navigation skills: where to stand and which way to face.

No game knowledge here: tactics decide what to do, these work out the pose.
robot_node's controllers then drive the robot to it.
"""

from __future__ import annotations

import math

import numpy as np


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
