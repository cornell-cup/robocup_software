"""Slots: the replaceable pieces of the team AI.

A Team names one function per slot:

    roles      who plays which role:   roles(team: list[Me], world) -> [(Me, role), ...]
    attacker   the robot on the ball:  attacker(me, world) -> Command
    supporter  the second robot:       supporter(me, world) -> Command
    defender   the third robot, or a robot playing alone: defender(me, world) -> Command

DEFAULT_TEAM holds the built-in ones (tactics/). Replace any of them:

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
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from strategy.api import Me, World
from strategy.command import Command
from strategy.tactics.attacker import attack
from strategy.tactics.defender import defend_goal
from strategy.tactics.role_assignments import ATTACKER, DEFENDER, SUPPORTER, assign_roles
from strategy.tactics.supporter import support

ROLES = (ATTACKER, SUPPORTER, DEFENDER)

Behavior = Callable[[Me, World], Command]
RoleAssigner = Callable[[list[Me], World], list[tuple[Me, str]]]


# -- the built-in slots: thin wrappers over tactics/ ---------------------------


def default_roles(team: list[Me], world: World) -> list[tuple[Me, str]]:
    """Closest to the ball attacks (and keeps the role until a teammate is
    clearly closer), the next supports, the rest defend; alone, defend."""
    by_id = {m.id: m for m in team}
    pairs = assign_roles([m.state for m in team], world.ball.pos, world._sticky, world.color)
    return [(by_id[r.id], role) for r, role in pairs]


def default_attacker(me: Me, world: World) -> Command:
    """tactics/attacker.py: break a standoff, the RL kick, or push through."""
    return attack(me.state, world.gamestate, world.color, world.rl_skill, world.rl_enabled)


def default_supporter(me: Me, world: World) -> Command:
    """tactics/supporter.py: wait ahead of the ball toward their goal."""
    return support(me.state, world.ball, world.color, world.kicked_off)


def default_defender(me: Me, world: World) -> Command:
    """tactics/defender.py: goalie on the line from our goal to the ball."""
    return defend_goal(world.ball.pos, world.color)


@dataclass(frozen=True)
class Team:
    roles: RoleAssigner = default_roles
    attacker: Behavior = default_attacker
    supporter: Behavior = default_supporter
    defender: Behavior = default_defender

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


def run_roles(team: Team, members: list[Me], world: World, errors: list[str] | None) -> list[tuple[Me, str]]:
    """The team's role assignment, checked; the built-in one if it fails."""
    if team.roles is default_roles:
        return default_roles(members, world)
    try:
        pairs = list(team.roles(members, world))
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
        return default_roles(members, world)


def run_behavior(team: Team, role: str, me: Me, world: World, errors: list[str] | None) -> Command:
    """One robot's command from its role's slot, checked; the built-in
    behaviour if the slot fails."""
    fn = team.behavior(role)
    default = DEFAULT_TEAM.behavior(role)
    if fn is default:
        return fn(me, world)
    try:
        command = fn(me, world)
        if not isinstance(command, Command):
            raise TypeError(f"must return a Command (e.g. me.go_to(...)), returned {command!r}")
        return command
    except Exception as e:  # noqa: BLE001 -- student code may raise anything
        _report(errors, world.color, role, me.id, e)
        return default(me, world)


def _report(errors: list[str] | None, color: str, slot: str, robot: int | None,
            e: BaseException) -> None:
    if errors is None:
        return
    where = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
    at = f" ({os.path.basename(where.filename)}:{where.lineno})" if where else ""
    who = "" if robot is None else f", robot {robot}"
    errors.append(f"{color} {slot}{who}: {type(e).__name__}: {e}{at}; using the built-in {slot}")
