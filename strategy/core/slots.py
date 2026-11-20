"""Slots: the replaceable pieces of the team AI.

A Team names one function per slot:

    roles      who plays which role:   roles(team: list[Me], world) -> [(Me, role), ...]
    attacker   the robot on the ball:  attacker(me, world) -> Command
    supporter  the second robot:       supporter(me, world) -> Command
    defender   the third robot, or a robot playing alone: defender(me, world) -> Command
    prepare    once a frame, before the others: prepare(world), for work the
               whole team shares (results in world.memory); built-in: nothing

DEFAULT_TEAM holds the built-in ones, from tactics/, written against the same
interface (strategy/api.py). Replace any of them:

    my_team = DEFAULT_TEAM.replace(supporter=get_open)

and give it to decide() / Brain for one colour; the rest keeps playing with
the defaults. A replaced slot that raises, or returns something that isn't a
Command (or a role list that doesn't cover the team), falls back to the
built-in one for that frame, and the error is reported, so one bug never
stops the team. The built-in slots run unguarded, as before.
"""

from __future__ import annotations

import dataclasses
import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from strategy.api import Me, World
from strategy.core.command import Command
from strategy.tactics.attacker import attacker as builtin_attacker
from strategy.tactics.defender import defender as builtin_defender
from strategy.tactics.role_assignments import ATTACKER, DEFENDER, SUPPORTER
from strategy.tactics.role_assignments import roles as builtin_roles
from strategy.tactics.supporter import supporter as builtin_supporter

ROLES = (ATTACKER, SUPPORTER, DEFENDER)

# A replaced slot gets this long per call (a whole frame is 1/60 s). Slower,
# and the robot falls back to the built-in slot for that frame, as on an error.
SLOT_TIME_BUDGET_S = 0.010

_STRATEGY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OWN_CODE_DIRS = tuple(os.path.join(_STRATEGY_DIR, d) for d in ("core", "world", "tactics", "skills"))
_OWN_CODE_FILES = (os.path.join(_STRATEGY_DIR, "api.py"),)


class SlotTooSlow(Exception):
    """A replaced slot took longer than SLOT_TIME_BUDGET_S."""

Behavior = Callable[[Me, World], Command]
RoleAssigner = Callable[[list[Me], World], list[tuple[Me, str]]]
Preparer = Callable[[World], None]


def builtin_prepare(world: World) -> None:
    """The built-in team has nothing to prepare."""


@dataclass(frozen=True)
class Team:
    roles: RoleAssigner = builtin_roles
    attacker: Behavior = builtin_attacker
    supporter: Behavior = builtin_supporter
    defender: Behavior = builtin_defender
    prepare: Preparer = builtin_prepare

    def replace(self, **slots: Callable) -> "Team":
        """This team with some slots swapped, e.g. replace(supporter=get_open)."""
        unknown = set(slots) - {f.name for f in dataclasses.fields(self)}
        if unknown:
            raise ValueError(f"no slot named {', '.join(sorted(unknown))}; slots are "
                             + ", ".join(f.name for f in dataclasses.fields(self)))
        for name, fn in slots.items():
            if not callable(fn):
                raise TypeError(f"slot {name} needs a function, got {fn!r}")
        return dataclasses.replace(self, **slots)

    def behavior(self, role: str) -> Behavior:
        return getattr(self, role)


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


def run_roles(team: Team, members: list[Me], world: World, errors: list[str] | None) -> list[tuple[Me, str]]:
    """The team's role assignment, checked; the built-in one if it fails."""
    if team.roles is DEFAULT_TEAM.roles:
        return team.roles(members, world)
    try:
        t0 = time.perf_counter()
        pairs = list(team.roles(members, world))
        _check_time(t0)
        ids = sorted(m.id for m, _ in pairs)
        bad_role = next((r for _, r in pairs if r not in ROLES), None)
        if ids != sorted(m.id for m in members) or bad_role is not None:
            raise ValueError(
                f"must give each robot ({sorted(m.id for m in members)}) exactly one role "
                + f"from {', '.join(ROLES)}; got {[(m.id, r) for m, r in pairs]}"
            )
        return pairs
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, "roles", None, e)
        return DEFAULT_TEAM.roles(members, world)


def run_behavior(team: Team, role: str, me: Me, world: World, errors: list[str] | None) -> Command:
    """One robot's command from its role's slot, checked; the built-in
    behaviour if the slot fails."""
    fn = team.behavior(role)
    default = DEFAULT_TEAM.behavior(role)
    if fn is default:
        return fn(me, world)
    try:
        t0 = time.perf_counter()
        command = fn(me, world)
        _check_time(t0)
        if not isinstance(command, Command):
            raise TypeError(f"must return a Command (e.g. me.go_to(...)), returned {command!r}")
        return command
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, role, me.id, e)
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
