"""Who plays which role: closest to the ball attacks, the next supports, the
rest defend. A robot playing alone defends (goalie).

The incumbent attacker keeps the role unless a teammate is clearly closer, so
roles don't flip mid-run. The design replaces this with priority + cost over
every assignment (strategy/README.md, Design, principle 4).
"""

from __future__ import annotations

import numpy as np

from strategy.geometry import distance
from strategy.state import FIELD_SCALE, RobotState

ATTACKER = "attacker"
SUPPORTER = "supporter"
DEFENDER = "defender"

# Incumbent attacker keeps the role unless a teammate is at least this much
# closer to the ball. Bigger than typical per-tick noise and the supporter's
# transient pass-by distance. Tuned on 9 x 6, scaled by FIELD_SCALE.
ATTACKER_HYSTERESIS = 0.4 * FIELD_SCALE


def assign_roles(
    team: list[RobotState],
    ball_pos: np.ndarray,
    sticky: dict[str, int],
    team_color: str,
) -> list[tuple[RobotState, str]]:
    """(robot, role) for every robot in `team`, attacker first. `sticky` holds
    each team's attacker from the last frame and is updated in place."""
    if len(team) == 1:
        sticky.pop(team_color, None)
        return [(team[0], DEFENDER)]

    # Roles purely by distance to ball: closest = attacker, middle =
    # supporter, furthest = defender.
    sorted_team = sorted(team, key=lambda r: distance(r.pos, ball_pos))

    # Sticky attacker with hysteresis. The supporter's path runs past the
    # ball, so per-tick "closest is attacker" flips the role mid-stride and
    # the wrong robot ends up emitting the kick. Keep the incumbent unless
    # a teammate is meaningfully closer.
    last_rid = sticky.get(team_color)
    incumbent = (
        next((r for r in sorted_team if r.id == last_rid), None)
        if last_rid is not None
        else None
    )
    if incumbent is not None and (
        distance(sorted_team[0].pos, ball_pos) + ATTACKER_HYSTERESIS
        >= distance(incumbent.pos, ball_pos)
    ):
        sorted_team = [incumbent] + [r for r in sorted_team if r.id != incumbent.id]
    sticky[team_color] = sorted_team[0].id

    roles = [ATTACKER, SUPPORTER]
    return [(r, roles[i] if i < len(roles) else DEFENDER) for i, r in enumerate(sorted_team)]
