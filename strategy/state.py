import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Field geometry comes from sim/config.py so a custom --field reaches strategy.
_PYTHON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sim")
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

from config import FIELD_H, FIELD_SCALE, FIELD_W, GOAL_MOUTH_H, GOAL_Y_MAX, GOAL_Y_MIN, ROBOT_RADIUS  # noqa: E402, F401

from strategy.skills.kick import KICK_ZONE_DEPTH, KICK_ZONE_HALF_WIDTH  # noqa: E402

FIELD_LENGTH = FIELD_W  # meters, along x (9 by default)
FIELD_WIDTH = FIELD_H  # meters, along y (6 by default)
# 3v3. Robot ids 0..TEAM_BLUE_SIZE-1 are blue;
# ids TEAM_BLUE_SIZE..TEAM_BLUE_SIZE+TEAM_RED_SIZE-1 are red.
TEAM_BLUE_SIZE = 3
TEAM_RED_SIZE = 3


@dataclass
class BallState:
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2))  # [x, y] meters
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))  # [vx, vy] meters/sec
    speed: float = 0.0  # meters/sec


@dataclass
class RobotState:
    id: int = 0
    pos: np.ndarray = field(default_factory=lambda: np.zeros(2))  # [x, y] meters
    vel: np.ndarray = field(default_factory=lambda: np.zeros(2))  # [vx, vy] meters/sec
    angle: float = 0.0  # radians, 0 = facing +x
    omega: float = 0.0  # angular velocity, rad/sec
    has_ball: bool = False  # ball in front of it, where it could kick or dribble it
    # Mirrored from the camera ("source": "vision"). Such a robot's body only
    # moves with the real robot, and only while the camera sees it.
    from_camera: bool = False
    # The camera hasn't seen it this instant (world state "stale"). Short gaps
    # are normal; the strategy node decides when a robot counts as lost.
    stale: bool = False


@dataclass
class GameState:
    timestamp: float = 0  # sim time, seconds

    # Ball
    ball: BallState = field(default_factory=BallState)

    # Teams — indices match robot IDs
    blue: list[RobotState] = field(default_factory=list)  # 3 robots
    red: list[RobotState] = field(default_factory=list)  # 3 robots

    # Computed once per frame
    our_team: list[RobotState] = field(
        default_factory=list
    )  # alias for whichever team we control
    opponents_team: list[RobotState] = field(default_factory=list)  # opponents
    possession: str = "loose"  # "ours", "theirs", "loose"
    ball_holder: int | None = None  # id of the robot that has the ball, if any
    # "play", "kickoff" (ball at the centre, waiting for the first touch) or
    # "halt" (game over). build_game_state sets "play" or "halt"; the strategy
    # node sets "kickoff", since that needs memory across frames.
    game_phase: str = "play"

    # From the sim's game block (world state "game", "score", "last_goal")
    score_ours: int = 0
    score_theirs: int = 0
    half: int = 1  # 1, 2; 3 and 4 are overtime
    time_remaining: float = 0.0  # seconds left in this half
    goal_seq: int = 0  # goals scored so far, by anyone; changes on every goal


# Goals (Fixed Positions)
BLUE_GOAL = np.array([0.0, FIELD_WIDTH / 2])  # [0.0, 3.0] on 9 x 6
RED_GOAL = np.array([FIELD_LENGTH, FIELD_WIDTH / 2])  # [9.0, 3.0] on 9 x 6

# Goal mouth bounds (GOAL_MOUTH_H, GOAL_Y_MIN, GOAL_Y_MAX) are imported from config.


# Builder
def build_game_state(raw: dict[str, Any], our_color: str = "blue") -> GameState:
    gamestate = GameState()
    gamestate.timestamp = raw.get("t", 0.0)

    # ball
    ball = raw.get("ball", {})
    vel = np.array([ball.get("vx", 0.0), ball.get("vy", 0.0)])
    gamestate.ball = BallState(
        pos=np.array([ball.get("x", FIELD_LENGTH / 2), ball.get("y", FIELD_WIDTH / 2)]),
        vel=vel,
        speed=float(np.linalg.norm(vel)),
    )

    # robots
    robots_raw = raw.get("robots", {})
    for i in range(TEAM_BLUE_SIZE):
        blue_robot = robots_raw.get(str(i), {})
        gamestate.blue.append(_parse_robot(i, blue_robot))
    for i in range(TEAM_RED_SIZE):
        red_robot = robots_raw.get(str(i + TEAM_BLUE_SIZE), {})
        gamestate.red.append(_parse_robot(i + TEAM_BLUE_SIZE, red_robot))

    # Which robot, if any, has the ball in front of it
    _compute_has_ball(gamestate)

    # Team Aliases
    if our_color == "blue":
        gamestate.our_team = gamestate.blue
        gamestate.opponents_team = gamestate.red
    else:
        gamestate.our_team = gamestate.red
        gamestate.opponents_team = gamestate.blue

    # Possession of ball
    if any(robot.has_ball for robot in gamestate.our_team):
        gamestate.possession = "ours"
    elif any(robot.has_ball for robot in gamestate.opponents_team):
        gamestate.possession = "theirs"
    else:
        gamestate.possession = "loose"

    _parse_game(gamestate, raw, our_color)
    return gamestate


# Helper
def _parse_robot(rid: int, raw: dict[str, Any]) -> RobotState:
    return RobotState(
        id=rid,
        pos=np.array([raw.get("x", 0.0), raw.get("y", 0.0)]),
        vel=np.array([raw.get("vx", 0.0), raw.get("vy", 0.0)]),
        angle=raw.get("angle", 0.0),
        omega=raw.get("omega", 0.0),
        from_camera=raw.get("source") == "vision",
        stale=bool(raw.get("stale", False)),
    )


def _parse_game(gamestate: GameState, raw: dict[str, Any], our_color: str) -> None:
    game = raw.get("game") or {}
    score = raw.get("score") or {}
    theirs = "red" if our_color == "blue" else "blue"
    gamestate.score_ours = int(score.get(our_color, 0))
    gamestate.score_theirs = int(score.get(theirs, 0))
    gamestate.half = int(game.get("half", 1))
    gamestate.time_remaining = float(game.get("time_remaining", 0.0))
    gamestate.game_phase = "halt" if game.get("half_over") else "play"
    gamestate.goal_seq = int((raw.get("last_goal") or {}).get("seq", 0))


def ball_in_front(robot: RobotState, ball_pos: np.ndarray) -> bool:
    """The ball is in the robot's kick zone: in front of it, close enough to
    kick or dribble. Same box as skills/kick.py's try_kick_ball."""
    dx, dy = float(ball_pos[0] - robot.pos[0]), float(ball_pos[1] - robot.pos[1])
    c, s = math.cos(robot.angle), math.sin(robot.angle)
    local_x = c * dx + s * dy
    local_y = -s * dx + c * dy
    return (
        ROBOT_RADIUS <= local_x <= ROBOT_RADIUS + KICK_ZONE_DEPTH
        and abs(local_y) <= KICK_ZONE_HALF_WIDTH
    )


def _compute_has_ball(gamestate: GameState) -> None:
    # The ball's centre can never come closer to a robot's centre than the two
    # radii, so "has ball" is a box in front of the robot, not a distance.
    # If several robots have it in front, the closest one holds it.
    holders = [
        r for r in gamestate.blue + gamestate.red if ball_in_front(r, gamestate.ball.pos)
    ]
    if not holders:
        return
    best = min(holders, key=lambda r: float(np.linalg.norm(gamestate.ball.pos - r.pos)))
    best.has_ball = True
    gamestate.ball_holder = best.id
