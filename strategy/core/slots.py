"""Slots: the replaceable pieces of the team AI.

A Team names one function per slot:

    roles      who plays which role:   roles(team: list[Me], world) -> [(Me, role), ...]
    attacker   the robot on the ball:  attacker(me, world) -> Command
    supporter  the second robot:       supporter(me, world) -> Command
    defender   the third robot, or a robot playing alone: defender(me, world) -> Command
    goalie     a role for plays and your own roles(): goalie(me, world) -> Command
               (built-in: as the built-in defender)
    prepare    once a frame, before the others: prepare(world), for work the
               whole team shares (results in world.memory); built-in: nothing
    team       commands for any robots at once: team(robots, world) -> {robot: Command};
               the robots it leaves out go through roles; built-in: none
    kickoff    where robots stand before a kickoff: kickoff(robots, world) ->
               {robot: (x, y)} on our half; built-in: none
    pick_play  which of the team's PLAYS runs: pick_play(world) -> name; built-in: no plays

and the skills, one action each (skills/ball.py), called as methods of a
robot (`me.kick_toward(target)`) from any slot, the built-in attacker's too:

    go_to_ball(me, world)  kick_toward(me, world, target)  intercept(me, world)
    pass_to(me, world, mate)  dribble_to(me, world, target)

and, besides the slots, `plays` (PLAYS in a team file: a play's name -> its
roles, most important first) and `custom`, its own roles: functions by name
(`runner`, `marker`) that roles() or a play can give a robot.

DEFAULT_TEAM holds the built-in ones, from tactics/, written against the same
interface (strategy/api.py). Replace any of them:

    my_team = DEFAULT_TEAM.replace(supporter=get_open)

and give it to decide() / Brain for one colour; the rest keeps playing with
the defaults. A replaced slot that raises, or returns something that isn't
what it should (a role list that doesn't cover the team, say), falls back to
the built-in one for that frame, and the error is reported, so one bug never
stops the team. The built-in slots run unguarded, as before.
"""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from strategy.api import Me, World
from strategy.core.command import Command
from strategy.skills import ball as builtin_skills
from strategy.tactics.attacker import attacker as builtin_attacker
from strategy.tactics.defender import defender as builtin_defender
from strategy.tactics.defender import goalie as builtin_goalie
from strategy.tactics.role_assignments import ATTACKER, DEFENDER, SUPPORTER
from strategy.tactics.role_assignments import roles as builtin_roles
from strategy.tactics.supporter import supporter as builtin_supporter

GOALIE = "goalie"
ROLES = (ATTACKER, SUPPORTER, DEFENDER, GOALIE)  # the built-in roles
SKILLS = ("go_to_ball", "kick_toward", "intercept", "pass_to", "dribble_to")
# The slots: the functions a team file may define by these names.
SLOT_NAMES = ("roles", ATTACKER, SUPPORTER, DEFENDER, GOALIE, "prepare", "team", "kickoff", "pick_play",
              *SKILLS)

# Each slot's parameters (what it's called with) and what it gives back:
# for the Code page's hints and its lint.
SLOT_PARAMS: dict[str, tuple[tuple[str, ...], str]] = {
    "roles": (("team", "world"), "[(robot, role), ...] for every robot in team"),
    ATTACKER: (("me", "world"), "a Command"),
    SUPPORTER: (("me", "world"), "a Command"),
    DEFENDER: (("me", "world"), "a Command"),
    GOALIE: (("me", "world"), "a Command"),
    "prepare": (("world",), "nothing"),
    "team": (("robots", "world"), "{robot: Command} for the robots it commands"),
    "kickoff": (("robots", "world"), "{robot: (x, y)} on our half"),
    "pick_play": (("world",), "the name of one of the PLAYS"),
    "go_to_ball": (("me", "world"), "a Command"),
    "kick_toward": (("me", "world", "target"), "a Command"),
    "intercept": (("me", "world"), "a Command"),
    "pass_to": (("me", "world", "mate"), "a Command"),
    "dribble_to": (("me", "world", "target"), "a Command"),
}

# A replaced slot gets this long per call (a whole frame is 1/60 s). Slower,
# and the robot falls back to the built-in slot for that frame, as on an error.
SLOT_TIME_BUDGET_S = 0.010
# A newly picked play takes over once pick_play() has kept picking it this
# long, so the team doesn't flicker between two plays.
PLAY_SWITCH_S = 0.25

_STRATEGY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OWN_CODE_DIRS = tuple(os.path.join(_STRATEGY_DIR, d) for d in ("core", "world", "tactics", "skills"))
_OWN_CODE_FILES = (os.path.join(_STRATEGY_DIR, "api.py"),)


class SlotTooSlow(Exception):
    """A replaced slot took longer than SLOT_TIME_BUDGET_S."""


Behavior = Callable[[Me, World], Command]
RoleAssigner = Callable[[list[Me], World], list[tuple[Me, str]]]
Preparer = Callable[[World], None]
TeamPlan = Callable[[list[Me], World], Mapping[Any, Command]]
KickoffSpots = Callable[[list[Me], World], Mapping[Any, Any]]
PlayPicker = Callable[[World], "str | None"]


def builtin_prepare(world: World) -> None:
    """The built-in team has nothing to prepare."""


def builtin_team(robots: list[Me], world: World) -> dict[Any, Command]:
    """The built-in team commands every robot through its role."""
    return {}


def builtin_kickoff(robots: list[Me], world: World) -> dict[Any, Any]:
    """The built-in team's robots play their roles at a kickoff too."""
    return {}


def builtin_pick_play(world: World) -> str | None:
    """The built-in team has no plays (yet)."""
    return None


@dataclass(frozen=True)
class Team:
    roles: RoleAssigner = builtin_roles
    attacker: Behavior = builtin_attacker
    supporter: Behavior = builtin_supporter
    defender: Behavior = builtin_defender
    goalie: Behavior = builtin_goalie
    prepare: Preparer = builtin_prepare
    team: TeamPlan = builtin_team
    kickoff: KickoffSpots = builtin_kickoff
    pick_play: PlayPicker = builtin_pick_play
    go_to_ball: Callable[..., Command] = builtin_skills.go_to_ball
    kick_toward: Callable[..., Command] = builtin_skills.kick_toward
    intercept: Callable[..., Command] = builtin_skills.intercept
    pass_to: Callable[..., Command] = builtin_skills.pass_to
    dribble_to: Callable[..., Command] = builtin_skills.dribble_to
    # Not slots: a team's plays (name -> roles, most important first), and its
    # own roles (name -> function).
    plays: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    custom: Mapping[str, Behavior] = field(default_factory=dict)

    def replace(self, **slots: Callable) -> Team:
        """This team with some slots swapped, e.g. replace(supporter=get_open)."""
        unknown = set(slots) - set(SLOT_NAMES)
        if unknown:
            raise ValueError(f"no slot named {', '.join(sorted(unknown))}; slots are "
                             + ", ".join(SLOT_NAMES))
        for name, fn in slots.items():
            if not callable(fn):
                raise TypeError(f"slot {name} needs a function, got {fn!r}")
        return dataclasses.replace(self, **slots)

    def with_roles(self, plays: Mapping[str, tuple[str, ...]] | None = None,
                   custom: Mapping[str, Behavior] | None = None) -> Team:
        """This team with plays and its own roles."""
        return dataclasses.replace(self, plays=dict(plays or self.plays), custom=dict(custom or self.custom))

    def behavior(self, role: str) -> Behavior | None:
        """The function for a role: a built-in role's slot, or the team's own."""
        if role in ROLES:
            return getattr(self, role)
        return self.custom.get(role)

    def role_names(self) -> list[str]:
        return [*ROLES, *sorted(r for r in self.custom if r not in ROLES)]


DEFAULT_TEAM = Team()


def run_prepare(team: Team, world: World, errors: list[str] | None) -> None:
    """The team's prepare slot, once a frame before any robot's; if it
    fails, the frame goes on without it."""
    if team.prepare is DEFAULT_TEAM.prepare:
        return
    try:
        t0 = time.perf_counter()
        team.prepare(world)
        _check_time(t0)
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "prepare", None, e, fallback="skipping it this frame")


def _robot_id(key: Any, ids: set[int], slot: str) -> int:
    rid = key.id if isinstance(key, Me) else key
    if not isinstance(rid, (int, np.integer)) or int(rid) not in ids:
        raise ValueError(f"{slot} must use our robots (a robot, or its id: {sorted(ids)}), got {key!r}")
    return int(rid)


def run_team_plan(team: Team, world: World, errors: list[str] | None) -> dict[int, Command]:
    """team(): commands for any of our robots, all at once. Robot id ->
    Command; {} (every robot through its role) if it fails."""
    if team.team is DEFAULT_TEAM.team:
        return {}
    try:
        t0 = time.perf_counter()
        plan = team.team(list(world.teammates), world)
        _check_time(t0)
        if plan is None:
            return {}
        if not isinstance(plan, Mapping):
            raise TypeError(f"must return a dict {{robot: me.go_to(...)}}, returned {plan!r}")
        ids = {m.id for m in world.teammates}
        out: dict[int, Command] = {}
        for key, command in plan.items():
            if not isinstance(command, Command):
                raise TypeError(f"must give each robot a Command (e.g. robot.go_to(...)), got {command!r}")
            out[_robot_id(key, ids, "team")] = command
        return out
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "team", None, e, fallback="every robot plays its role")
        return {}


def run_kickoff(team: Team, world: World, errors: list[str] | None) -> dict[int, Command]:
    """kickoff(), before a kickoff: where robots stand. Robot id -> a Command
    to go there facing the ball; {} if it fails."""
    if team.kickoff is DEFAULT_TEAM.kickoff:
        return {}
    try:
        t0 = time.perf_counter()
        spots = team.kickoff(list(world.teammates), world)
        _check_time(t0)
        if not spots:
            return {}
        if not isinstance(spots, Mapping):
            raise TypeError(f"must return a dict {{robot: (x, y)}}, returned {spots!r}")
        length, width = world.field_size
        by_id = {m.id: m for m in world.teammates}
        out: dict[int, Command] = {}
        for key, spot in spots.items():
            rid = _robot_id(key, set(by_id), "kickoff")
            x, y = (float(v) for v in np.asarray(spot, dtype=float).reshape(-1)[:2])
            ours = x <= length / 2 if world.color == "blue" else x >= length / 2
            if not (0 <= x <= length and 0 <= y <= width and ours):
                raise ValueError(f"robot {rid}'s spot ({x:.2f}, {y:.2f}) isn't on our half of the field")
            out[rid] = by_id[rid].go_to((x, y), face=world.ball.pos)
        return out
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "kickoff", None, e, fallback="those robots play their roles")
        return {}


def run_roles(team: Team, members: list[Me], world: World, errors: list[str] | None,
              play_state: dict[str, Any] | None = None) -> list[tuple[Me, str]]:
    """Who plays which role: the running play's roles if the team has plays,
    else its roles slot, checked; the built-in roles if that fails."""
    if not members:  # team() and kickoff() took every robot
        return []
    play = _current_play(team, world, errors, play_state)
    world.play = play
    if play is not None:
        return _play_roles(team.plays[play], members, world)
    if team.roles is DEFAULT_TEAM.roles:
        return team.roles(members, world)
    try:
        t0 = time.perf_counter()
        pairs = list(team.roles(members, world))
        _check_time(t0)
        ids = sorted(m.id for m, _ in pairs)
        if ids != sorted(m.id for m in members):
            raise ValueError(f"must give each robot ({sorted(m.id for m in members)}) exactly one role; "
                             + f"got {[(m.id, r) for m, r in pairs]}")
        for _, role in pairs:
            if team.behavior(role) is None:
                raise ValueError(f"{role!r} isn't a role: write def {role}(me, world) in team.py, "
                                 + f"or use one of {', '.join(team.role_names())}")
        return pairs
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "roles", None, e)
        return DEFAULT_TEAM.roles(members, world)


def _play_roles(roles: tuple[str, ...], members: list[Me], world: World) -> list[tuple[Me, str]]:
    """A play's roles, most important first, to the robots in the built-in
    order: closest to the ball first (keeping the robot that had the ball),
    then the next closest. More robots than roles: the extra ones take the
    last role; fewer: the first roles."""
    ordered = [m for m, _ in DEFAULT_TEAM.roles(members, world)]
    return [(m, roles[min(i, len(roles) - 1)]) for i, m in enumerate(ordered)]


def _current_play(team: Team, world: World, errors: list[str] | None,
                  state: dict[str, Any] | None) -> str | None:
    """The play running now: pick_play()'s choice, once it has kept making
    it for PLAY_SWITCH_S (the first one straight away)."""
    if team.pick_play is DEFAULT_TEAM.pick_play or not team.plays:
        return None
    state = state if state is not None else {}
    try:
        t0 = time.perf_counter()
        wanted = team.pick_play(world)
        _check_time(t0)
        if wanted is not None and wanted not in team.plays:
            raise ValueError(f"returned {wanted!r}, which isn't one of the PLAYS ({', '.join(team.plays)})")
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "pick_play", None, e, fallback="keeping the play that runs")
        return state.get("play")
    now = world.time
    if wanted == state.get("play"):
        state.pop("wanted", None)
    elif state.get("play") is None and "play" not in state:
        state["play"] = wanted  # the first pick: no waiting
    elif wanted != state.get("wanted"):
        state["wanted"], state["since"] = wanted, now
    elif now - state.get("since", now) >= PLAY_SWITCH_S:
        state["play"] = wanted
        state.pop("wanted", None)
    return state.get("play")


def run_skill(name: str, me: Me, world: World, *args: Any) -> Command:
    """A skill for one robot (me.kick_toward(...) and the like): the team's
    own, checked, or the built-in one if it has none or it fails."""
    team = world._team if isinstance(world._team, Team) else DEFAULT_TEAM
    fn = getattr(team, name)
    default = getattr(DEFAULT_TEAM, name)
    if fn is default:
        return fn(me, world, *args)
    try:
        t0 = time.perf_counter()
        command = fn(me, world, *args)
        _check_time(t0)
        if not isinstance(command, Command):
            raise TypeError(f"must return a Command (e.g. me.go_to(...)), returned {command!r}")
        return command
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(world._errors, world.color, name, me.id, e)
        return default(me, world, *args)


def run_behavior(team: Team, role: str, me: Me, world: World, errors: list[str] | None) -> Command:
    """One robot's command from its role's function, checked; the built-in
    one (the built-in defender for the team's own roles) if it fails."""
    fn = team.behavior(role)
    default = DEFAULT_TEAM.behavior(role) or DEFAULT_TEAM.defender
    if fn is default:
        return fn(me, world)
    try:
        if fn is None:
            raise ValueError(f"no function for the role {role!r}")
        t0 = time.perf_counter()
        command = fn(me, world)
        _check_time(t0)
        if not isinstance(command, Command):
            raise TypeError(f"must return a Command (e.g. me.go_to(...)), returned {command!r}")
        return command
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, role, me.id, e,
                fallback=None if role in ROLES else "playing the built-in defender")
        return default(me, world)


def _check_time(t0: float) -> None:
    took = time.perf_counter() - t0
    if took > SLOT_TIME_BUDGET_S:
        raise SlotTooSlow(f"took {took * 1000:.0f} ms, over the "
                          + f"{SLOT_TIME_BUDGET_S * 1000:.0f} ms budget per frame")


def _report(errors: list[str] | None, color: str, slot: str, robot: int | None,
            e: BaseException, fallback: str | None = None) -> None:
    if errors is None:
        return
    where = None if isinstance(e, SlotTooSlow) else _your_line(e)
    at = f" ({os.path.basename(where.filename)}:{where.lineno})" if where else ""
    who = "" if robot is None else f", robot {robot}"
    then = fallback or f"using the built-in {slot}"
    errors.append(f"{color} {slot}{who}: {type(e).__name__}: {e}{at}; {then}")


def _your_line(e: BaseException) -> traceback.FrameSummary | None:
    """Where an error happened in the slot's own code: the deepest frame
    outside the strategy's machinery (api.py, core/, world/, tactics/,
    skills/), so a bad argument to me.go_to() points at the line that called
    it. The deepest frame of all if none is."""
    frames = traceback.extract_tb(e.__traceback__) if e.__traceback__ else []
    yours = [f for f in frames
             if not os.path.abspath(f.filename).startswith(_OWN_CODE_DIRS)
             and os.path.abspath(f.filename) not in _OWN_CODE_FILES]
    return (yours or frames or [None])[-1]
