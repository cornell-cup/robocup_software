"""The team AI's decision for one frame: both teams, every robot.

decide() gives each robot a role and runs that role's slot (slots.py): the
team's own, or the built-in one. Brain (brain.py) calls it every frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from strategy.api import World
from strategy.core.slots import DEFAULT_TEAM, Team, run_behavior, run_roles
from strategy.tactics.role_assignments import ATTACKER
from strategy.world.geometry import distance
from strategy.world.state import GameState

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill


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
) -> tuple[dict[int, tuple[float, float, float]], list[int], set[int]]:
    """One frame of the team AI, for both teams: who plays which role, and
    where each robot goes. Returns (targets, kicks, attackers): robot id ->
    (x, y, heading), robots firing their kicker, and each team's attacker.

    Each team runs its slots (slots.py): `teams` maps a colour to a Team,
    DEFAULT_TEAM (the built-in tactics/) for a colour not in it. `memory`
    holds each team's notes between frames (World.memory); `errors` collects
    what failed in replaced slots. `sticky_attackers` (each team's attacker
    last frame) is updated in place.
    """
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    lost = lost_ids or set()
    teams = teams or {}
    memory = memory if memory is not None else {}

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
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
        )
        for me, role in run_roles(spec, world.teammates, world, errors):
            if role == ATTACKER:
                attacker_rids.add(me.id)
            command = run_behavior(spec, role, me, world, errors)
            targets[me.id] = command.target
            if command.kick:
                kicks.append(me.id)

    # Both teams' kicks. When both attackers kick in the same tick, Brain keeps
    # only the closer one (resolve_kick_conflict).
    return targets, kicks, attacker_rids


decide.takes_teams = True  # Brain passes teams / memory / errors (baseline v0 has none)


def resolve_kick_conflict(kicks: list[int], gamestate: GameState) -> list[int]:
    """Only the kicker closest to the ball kicks. Two opposing kicks in the
    same tick produce equal-opposite impulses that cancel, leaving the ball
    pinned between both robots."""
    if len(kicks) <= 1:
        return list(kicks)
    rid_to_bot = {r.id: r for r in gamestate.blue + gamestate.red}
    return [min(kicks, key=lambda rid: distance(rid_to_bot[rid].pos, gamestate.ball.pos))]
