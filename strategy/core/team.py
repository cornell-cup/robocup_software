"""The team AI's decision for one frame: both teams, every robot.

decide() gives each robot a role and runs that role's slot (slots.py): the
team's own, or the built-in one. Brain (brain.py) calls it every frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from strategy.api import World
from strategy.core.command import Command
from strategy.core.slots import (
    DEFAULT_TEAM,
    Team,
    run_behavior,
    run_kickoff,
    run_prepare,
    run_roles,
    run_team_plan,
)
from strategy.tactics.role_assignments import ATTACKER
from strategy.world.state import GameState

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill


@dataclass
class Extras:
    """What decide() gives besides targets, kicks and attackers, for the
    robots it plans: dribblers on, speed limits, and what each team's code
    drew on the field (world.draw) and showed (world.show)."""

    dribble: set[int] = field(default_factory=set)
    max_speed: dict[int, float] = field(default_factory=dict)  # robot id -> m/s
    drawings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # colour -> shapes
    shown: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)  # colour -> who -> name -> value


def team_events(events: list[str], color: str) -> list[str]:
    """Brain's colour-neutral events as one team sees them: "goal_blue" is
    "goal_for" for blue and "goal_against" for red."""
    out = []
    for e in events:
        if e.startswith("goal_"):
            out.append("goal_for" if e == f"goal_{color}" else "goal_against")
        else:
            out.append(e)
    return out


def decide(
    gamestate: GameState,
    rl_skill: "RLKickSkill | None" = None,
    rl_enabled: bool = False,
    sticky_attackers: dict[str, int] | None = None,
    kicked_off: bool = True,
    human_blue_ids: set[int] | None = None,
    lost_ids: set[int] | None = None,
    *,
    teams: dict[str, Team] | None = None,
    memory: dict[str, dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    events: list[str] | None = None,
    extras: Extras | None = None,
    colors: tuple[str, ...] = ("blue", "red"),
    play_state: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[int, tuple[float, float, float]], list[int], set[int]]:
    """One frame of the team AI, for both teams: who plays which role, and
    where each robot goes. Returns (targets, kicks, attackers): robot id ->
    (x, y, heading), robots firing their kicker, and each team's attacker.

    Each team runs its slots (slots.py): `teams` maps a colour to a Team,
    DEFAULT_TEAM (the built-in tactics/) for a colour not in it. `memory`
    holds each team's notes between frames (World.memory); `errors` collects
    what failed in replaced slots. `sticky_attackers` (each team's attacker
    last frame) is updated in place. `events` are what Brain saw happen
    since last frame ("goal_blue", "kickoff", ...; World.events); `extras`
    collects dribblers, speed limits, drawings and shown values. `colors`:
    the teams to plan (a team played by its own process is planned there).
    `play_state` keeps each team's running play between frames.

    Per team: prepare(); kickoff() spots (before a kickoff) and team()'s
    commands for the robots they name (team() wins); the other robots get
    roles (the running play's, or roles()) and their role's function.
    """
    extras = extras if extras is not None else Extras()
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    lost = lost_ids or set()
    teams = teams or {}
    memory = memory if memory is not None else {}
    play_state = play_state if play_state is not None else {}

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
        if team_color not in colors:
            continue
        # Drop the robots the AI can't drive: human-driven ones (blue only)
        # and camera robots the camera has lost. The rest of the team gets
        # whichever roles fit its size (1: goalie, 2: attacker + supporter,
        # 3: + defender).
        skip = (human_ids if team_color == "blue" else set()) | lost
        if skip:
            team = [r for r in team if r.id not in skip]
            if not team:
                sticky.pop(team_color, None)
                continue

        spec = teams.get(team_color, DEFAULT_TEAM)
        world = World(
            gamestate, team_color, team,
            kicked_off=kicked_off, memory=memory.setdefault(team_color, {}),
            unavailable=skip, rl_skill=rl_skill, rl_enabled=rl_enabled, sticky=sticky,
            events=team_events(events or [], team_color),
        )
        run_prepare(spec, world, errors)
        planned: dict[int, Command] = {}
        if not kicked_off:
            planned.update(run_kickoff(spec, world, errors))
        planned.update(run_team_plan(spec, world, errors))
        rest = [m for m in world.teammates if m.id not in planned] if planned else world.teammates
        for me, role in run_roles(spec, rest, world, errors, play_state.setdefault(team_color, {})):
            if role == ATTACKER:
                attacker_rids.add(me.id)
            world._running_for(me.id)
            planned[me.id] = run_behavior(spec, role, me, world, errors)
            world._running_for(None)
        for rid, command in planned.items():
            targets[rid] = command.target
            if command.kick:
                kicks.append(rid)
            if command.dribble:
                extras.dribble.add(rid)
            if command.max_speed is not None:
                extras.max_speed[rid] = command.max_speed
        if world.draw.shapes:
            extras.drawings[team_color] = world.draw.shapes
        if world.show.values:
            extras.shown[team_color] = world.show.values

    # Both teams' kicks. When two robots kick in the same tick, the physics
    # fires only the one closest to the ball (sim/pymunk_world.py).
    return targets, kicks, attacker_rids


decide.takes_teams = True  # Brain passes teams / memory / errors / events / extras (the baselines don't take them)

