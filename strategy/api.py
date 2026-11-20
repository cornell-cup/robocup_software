"""The interface every behaviour is written against, built-in or your own.

A behaviour is a function that gets one robot and the world, and returns what
that robot should do this frame:

    def get_open(me: Me, world: World) -> Command:
        spot = world.ball.pos + (world.their_goal - world.ball.pos) * 0.4
        return me.go_to(spot, face=world.ball.pos)

It runs 60 times a second for every robot playing that role. Positions are
in metres, angles in radians counter-clockwise from +x. Which robot plays
which role, and which function each role runs, is set by a Team (core/slots.py).
"""

from __future__ import annotations

import math
from functools import cached_property
from typing import TYPE_CHECKING, Any

import numpy as np

from strategy.core.command import Command
from strategy.world.geometry import attack_angle, is_blocking, opp_goal, our_goal
from strategy.world.state import ROBOT_RADIUS, BallState, GameState, RobotState

from strategy.world.simconfig import BALL_RADIUS

if TYPE_CHECKING:
    from strategy.world.analysis import Analysis, TeamView
    from strategy.skills.rl_kick import RLKickSkill

Point = Any  # anything with two numbers: (x, y), a list, a numpy array


def _xy(p: Point) -> np.ndarray:
    return np.asarray(p, dtype=float)[:2]


class Me:
    """One robot, and the commands it can be given."""

    def __init__(self, state: RobotState):
        self.state = state

    @property
    def id(self) -> int:
        return self.state.id

    @property
    def pos(self) -> np.ndarray:
        return self.state.pos

    @property
    def vel(self) -> np.ndarray:
        return self.state.vel

    @property
    def angle(self) -> float:
        return self.state.angle

    @property
    def has_ball(self) -> bool:
        """The ball is in front of it, close enough to kick or dribble."""
        return self.state.has_ball

    def distance_to(self, point: Point) -> float:
        d = _xy(point) - self.pos
        return float(math.hypot(d[0], d[1]))

    def angle_to(self, point: Point) -> float:
        d = _xy(point) - self.pos
        return math.atan2(float(d[1]), float(d[0]))

    def go_to(self, target: Point, face: Point | float | None = None, kick: bool = False) -> Command:
        """Drive to `target`. `face`: a point to look at, or a heading in
        radians; by default the robot faces where it's going. `kick`: fire
        the kicker (it only does anything with the ball in front)."""
        t = _xy(target)
        if face is None:
            # Facing where it goes; already there, it keeps its heading.
            heading = self.angle_to(t) if self.distance_to(t) > 0.25 * ROBOT_RADIUS else self.angle
        elif isinstance(face, (int, float)):
            heading = float(face)
        else:
            heading = self.angle_to(face)
        return Command(float(t[0]), float(t[1]), heading, kick=kick)

    def face(self, point: Point | float) -> Command:
        """Stay here and turn toward a point (or to a heading, in radians)."""
        return self.go_to(self.pos, face=point)

    def stop(self) -> Command:
        """Stay where it is, facing the way it faces."""
        return Command(float(self.pos[0]), float(self.pos[1]), self.angle)

    def __repr__(self) -> str:
        return f"Me(id={self.id}, pos=({self.pos[0]:.2f}, {self.pos[1]:.2f}))"


class World:
    """What one team can see this frame."""

    def __init__(
        self,
        gamestate: GameState,
        color: str,
        team: list[RobotState],
        *,
        kicked_off: bool = True,
        memory: dict[str, Any] | None = None,
        unavailable: set[int] | frozenset[int] = frozenset(),
        rl_skill: "RLKickSkill | None" = None,
        rl_enabled: bool = False,
        sticky: dict[str, int] | None = None,
    ):
        self.gamestate = gamestate
        self.color = color
        self.kicked_off = kicked_off
        # Survives from frame to frame, one per team: keep your own notes here.
        self.memory: dict[str, Any] = memory if memory is not None else {}
        self._unavailable = set(unavailable)
        self.teammates: list[Me] = [Me(r) for r in team]
        theirs = gamestate.red if color == "blue" else gamestate.blue
        self.opponents: list[Me] = [Me(r) for r in theirs if r.id not in self._unavailable]
        # The built-in attacker's learned kick.
        self.rl_skill = rl_skill
        self.rl_enabled = rl_enabled
        # Each team's attacker last frame, kept by the built-in roles slot.
        self.sticky: dict[str, int] = sticky if sticky is not None else {}

    @property
    def ball(self) -> BallState:
        return self.gamestate.ball

    @property
    def our_goal(self) -> np.ndarray:
        """Centre of the goal we defend."""
        return our_goal(self.color)

    @property
    def their_goal(self) -> np.ndarray:
        """Centre of the goal we attack."""
        return opp_goal(self.color)

    @property
    def attack_angle(self) -> float:
        """Heading toward their goal: 0 for blue (+x), pi for red."""
        return attack_angle(self.color)

    @property
    def phase(self) -> str:
        """"kickoff" until the ball first moves after a restart, "play", or "halt"."""
        return self.gamestate.game_phase

    @property
    def time(self) -> float:
        return self.gamestate.timestamp

    @cached_property
    def analysis(self) -> "Analysis":
        """Both teams' race to the ball and threats (world/analysis.py), computed
        the first time it's asked for in a frame."""
        from strategy.world.analysis import analyze

        return analyze(self.gamestate, self._unavailable)

    @property
    def us(self) -> "TeamView":
        """Our side of the analysis: who reaches the ball first, threats."""
        return self.analysis.team(self.color)

    def lane_clear(self, start: Point, end: Point, margin: float | None = None) -> bool:
        """No opponent within `margin` of the straight line from `start` to
        `end` (default: a robot and a ball, side by side)."""
        m = ROBOT_RADIUS + BALL_RADIUS if margin is None else margin
        return not is_blocking(_xy(start), _xy(end), [o.state for o in self.opponents], m)
