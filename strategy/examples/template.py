"""Template team file: copy it, change a function, play it.

Each function below is a slot, one piece of the team's behaviour, and each
starts out calling the built-in version, so this file plays exactly like the
built-in team. Change any of them; delete one to keep the built-in for that
slot. Every function runs 60 times a second, for each robot playing that role.

Play your copy against the frozen team v1, from the repo root:

    uv run python -m strategy.eval.match --a my_team.py

or live in the sim (save the file while it runs and it reloads):

    cd sim
    uv run python run_pipeline.py --blue ../my_team.py

If a function raises an error, returns something that isn't a Command, or
takes longer than 10 ms, that robot uses the built-in slot for that frame and
the error is printed. To see what your code is thinking, draw on the field
(world.draw.line(a, b), .point(p), .circle(p, r), .arrow(a, b), .text(p, "hi"))
and show values beside it (world.show("name", value)). What `me` and `world` offer: strategy/api.py. The
built-in slots, to read and copy from: strategy/tactics/.
"""

from strategy.api import Command, Me, World
from strategy.tactics.attacker import attacker as built_in_attacker
from strategy.tactics.defender import defender as built_in_defender
from strategy.tactics.role_assignments import roles as built_in_roles
from strategy.tactics.supporter import supporter as built_in_supporter


def roles(team: list[Me], world: World) -> list[tuple[Me, str]]:
    """Who plays which role: a (robot, role) pair for every robot in `team`,
    each role one of "attacker", "supporter", "defender". Built-in: the robot
    closest to the ball attacks, the next supports, the rest defend; a robot
    playing alone defends."""
    return built_in_roles(team, world)


def attacker(me: Me, world: World) -> Command:
    """The robot going for the ball. Built-in: get behind the ball on the line
    to their goal and push it in (tactics/attacker.py)."""
    return built_in_attacker(me, world)


def supporter(me: Me, world: World) -> Command:
    """The second robot. Built-in: wait ahead of the ball toward their goal,
    facing the ball (tactics/supporter.py). strategy/examples/get_open.py has
    another one."""
    return built_in_supporter(me, world)


def defender(me: Me, world: World) -> Command:
    """The third robot, or a robot playing alone. Built-in: goalie on the line
    from our goal to the ball (tactics/defender.py)."""
    return built_in_defender(me, world)


# One more slot, empty in the built-in team: it runs once a frame before the
# robots' slots, for work the whole team shares. Keep the results in
# world.memory. world.events says what just happened ("goal_for", "kickoff",
# ...). Remove the # to use it.
#
# def prepare(world: World) -> None:
#     if "goal_against" in world.events:
#         world.memory["conceded"] = world.memory.get("conceded", 0) + 1
