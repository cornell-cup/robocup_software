"""Template team: copy it, change a function, play it.

Every function below is a slot, one piece of the team's behaviour, and each
starts out calling the built-in version, so this file plays exactly like the
built-in team. Change any of them; delete one to keep the built-in for that
slot. They run 60 times a second.

The file has three parts:

  1. Roles: what each robot does (attacker, supporter, defender), and who
     plays which role.
  2. Skills: one action with the ball each (kick toward a point, meet a
     rolling ball, pass, dribble). The built-in attacker uses kick_toward and
     intercept, so changing those changes how it plays.
  3. Ready to switch on: more slots, written out with an example each. Remove
     the # in front of one to use it.

In the web app's Code page (uv run robocup-web): save with Ctrl+S, then Play
in the simulator or run a Test match. The Guide tab explains everything here.
From a terminal, with this file as teams/my_team/team.py:

    uv run python -m strategy.eval.match --a teams/my_team   # against the frozen team v2
    cd sim; uv run python run_pipeline.py --blue ../teams/my_team

If a function raises an error, returns something it shouldn't, or takes longer
than 10 ms, the built-in version plays for that frame and the problem shows on
its line. To see what your code is thinking, draw on the field
(world.draw.line(a, b), .point(p), .circle(p, r), .arrow(a, b), .text(p, "hi"))
and show values beside it (world.show("name", value)).

Everything `me` and `world` offer: strategy/api.py. The built-in versions, to
read and copy from: strategy/tactics/ (roles) and strategy/skills/ball.py (skills).
"""

from strategy.api import Command, Me, World
from strategy.skills.ball import dribble_to as built_in_dribble_to
from strategy.skills.ball import go_to_ball as built_in_go_to_ball
from strategy.skills.ball import intercept as built_in_intercept
from strategy.skills.ball import kick_toward as built_in_kick_toward
from strategy.skills.ball import pass_to as built_in_pass_to
from strategy.tactics.attacker import attacker as built_in_attacker
from strategy.tactics.defender import defender as built_in_defender
from strategy.tactics.role_assignments import roles as built_in_roles
from strategy.tactics.supporter import supporter as built_in_supporter

# -- 1. Roles ---------------------------------------------------------------------------


def roles(team: list[Me], world: World) -> list[tuple[Me, str]]:
    """Who plays which role: a (robot, role) pair for every robot in `team`.
    A role is "attacker", "supporter", "defender", "goalie", or one of your
    own (part 3). Built-in: the robot closest to the ball attacks, the next
    supports, the rest defend; a robot playing alone defends."""
    return built_in_roles(team, world)


def attacker(me: Me, world: World) -> Command:
    """The robot going for the ball. Built-in: a rolling ball it meets with
    me.intercept(); otherwise me.kick_toward() their goal, pushing the ball in
    (tactics/attacker.py)."""
    return built_in_attacker(me, world)


def supporter(me: Me, world: World) -> Command:
    """The second robot. Built-in: wait ahead of the ball toward their goal,
    facing the ball (tactics/supporter.py). New... -> Example has another one."""
    return built_in_supporter(me, world)


def defender(me: Me, world: World) -> Command:
    """The third robot, or a robot playing alone. Built-in: goalie on the line
    from our goal to the ball, stepping out as the ball comes close
    (tactics/defender.py)."""
    return built_in_defender(me, world)


# -- 2. Skills: one action with the ball, called as me.kick_toward(...) and so on ------


def kick_toward(me: Me, world: World, target) -> Command:
    """Send the ball toward `target`. Built-in: get behind the ball on the line
    to `target`, then drive through it (a push: the kicker stays off). The
    built-in attacker uses this to push the ball at their goal."""
    return built_in_kick_toward(me, world, target)


def intercept(me: Me, world: World) -> Command:
    """Meet a rolling ball. Built-in: go where this robot can first reach it,
    then push it on toward their goal. The built-in attacker uses this when the
    ball rolls faster than 0.3 m/s."""
    return built_in_intercept(me, world)


def go_to_ball(me: Me, world: World) -> Command:
    """Drive to the ball. Built-in: straight there, facing it."""
    return built_in_go_to_ball(me, world)


def pass_to(me: Me, world: World, mate: Me) -> Command:
    """Pass to a teammate. Built-in: line up behind the ball toward where
    `mate` will be in half a second, and kick once lined up."""
    return built_in_pass_to(me, world, mate)


def dribble_to(me: Me, world: World, target) -> Command:
    """Carry the ball to `target`. Built-in: take it with the dribbler on, then
    drive there at most 0.6 m/s so it isn't lost."""
    return built_in_dribble_to(me, world, target)


# -- 3. Ready to switch on: remove the # in front of a block to use it --------------------
#
# The goalie role, for roles() and plays. Built-in: as the built-in defender.
#
# def goalie(me: Me, world: World) -> Command:
#     return me.go_to(world.our_goal, face=world.ball.pos)
#
#
# Once a frame, before any robot's function: work the whole team shares. Keep
# the results in world.memory. world.events says what just happened
# ("new_game", "goal_for", "goal_against", "half_time", "kickoff").
#
# def prepare(world: World) -> None:
#     if "goal_against" in world.events:
#         world.memory["conceded"] = world.memory.get("conceded", 0) + 1
#
#
# A role of your own: any function named like this is one. Give it to robots
# in roles() or in a play.
#
# def runner(me: Me, world: World) -> Command:
#     length, width = world.field_size
#     ahead = world.ball.pos[0] + (1.5 if world.attack_angle == 0 else -1.5)
#     return me.go_to((min(max(ahead, 0.5), length - 0.5), width * 0.85), face=world.ball.pos)
#
#
# Plays: a plan for each situation, its roles most important first (the robot
# closest to the ball gets the first). pick_play chooses one; a new choice
# takes over after a quarter of a second. world.play says which one runs.
#
# PLAYS = {
#     "attack": ["attacker", "runner", "goalie"],
#     "defend": ["attacker", "goalie", "goalie"],
# }
#
# def pick_play(world: World) -> str:
#     if world.us.race_margin < -0.3:  # they reach the ball well before us
#         return "defend"
#     return "attack"
#
#
# Commands for any robots at once (the rest play their roles): here, with the
# ball in our half, the two robots nearest our goal make a wall in front of it.
#
# def team(robots: list[Me], world: World) -> dict:
#     if world.ball.pos[0] > world.field_size[0] / 2:
#         return {}
#     goal = world.our_goal
#     wall = sorted(robots, key=lambda robot: robot.distance_to(goal))[:2]
#     inward = 0.5 if goal[0] < world.their_goal[0] else -0.5
#     return {robot: robot.go_to((goal[0] + inward, goal[1] + side), face=world.ball.pos)
#             for robot, side in zip(wall, (-0.25, 0.25))}
#
#
# Where robots stand before a kickoff, on our half. Leave out the robot taking
# the kickoff (here the one closest to the ball): it plays its role.
#
# def kickoff(robots: list[Me], world: World) -> dict:
#     length, width = world.field_size
#     x = length * (0.3 if world.attack_angle == 0 else 0.7)
#     taker = min(robots, key=lambda robot: robot.distance_to(world.ball.pos))
#     others = [robot for robot in robots if robot is not taker]
#     return {robot: (x, width * (i + 1) / (len(others) + 1)) for i, robot in enumerate(others)}
