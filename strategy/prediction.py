import math

import numpy as np

from strategy.state import BallState, RobotState  # also puts sim/ on sys.path

from config import BALL_DAMP  # noqa: E402  (sim/config.py)

# The sim slows the ball by BALL_DAMP per second (pymunk_world._apply_damping:
# v *= 1 - k dt every substep), so v(t) = v0 e^(-k t) and the ball travels
# p(t) = p0 + v0 (1 - e^(-k t)) / k. It comes to rest at p0 + v0 / k.
# Collisions with robots and walls are not modelled.


def predict_ball_position(
    ball_state: BallState, time_ahead: float, damping: float = BALL_DAMP
) -> np.ndarray:
    """Where the ball will be in `time_ahead` seconds, slowing down as in the sim."""
    t = max(0.0, time_ahead)
    if damping <= 0.0:
        return ball_state.pos + ball_state.vel * t
    travel = (1.0 - math.exp(-damping * t)) / damping
    return ball_state.pos + ball_state.vel * travel


def predict_ball_velocity(
    ball_state: BallState, time_ahead: float, damping: float = BALL_DAMP
) -> np.ndarray:
    """The ball's velocity in `time_ahead` seconds."""
    return ball_state.vel * math.exp(-max(0.0, damping) * max(0.0, time_ahead))


def ball_rest_position(ball_state: BallState, damping: float = BALL_DAMP) -> np.ndarray:
    """Where the ball stops if nothing touches it."""
    if damping <= 0.0:
        return ball_state.pos.copy()  # never stops; no meaningful answer
    return ball_state.pos + ball_state.vel / damping


# find the earliest intercept point where the robot can meet the moving ball.
# at each step, checks if the robot could arrive at the ball's predicted position before the ball gets there.
# returns the intercept point / the ball's current position if no intercept is found.
def predict_intercept_point(
    ball_state: BallState,
    robot_state: RobotState,
    max_robot_speed: float,
    time_ahead: float,
    steps_ahead: int,
) -> np.ndarray:
    if ball_state.speed <= 0.01:
        return ball_state.pos.copy()
    robot_speed = float(np.linalg.norm(robot_state.vel))
    max_robot_speed = max(max_robot_speed, robot_speed)
    for i in range(1, steps_ahead + 1):
        t = (i / steps_ahead) * time_ahead
        future_ball_pos = predict_ball_position(ball_state, t)
        dist_to_future = np.linalg.norm(robot_state.pos - future_ball_pos)
        time_to_future = (
            dist_to_future / max_robot_speed if max_robot_speed > 0 else float("inf")
        )
        if time_to_future <= t:
            return future_ball_pos
    return ball_state.pos.copy()
