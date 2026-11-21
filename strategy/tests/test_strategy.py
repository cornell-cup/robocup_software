"""Unit tests for the strategy: world state, prediction, decide(), Brain, matches.

From the repo root (no hardware, no running sim):

    uv run python -m unittest discover -s strategy/tests -t .
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from strategy.api import Me, World
from strategy.core.brain import Brain
from strategy.core.command import Command
from strategy.core.loader import (
    TeamFile,
    TeamFileError,
    load_team,
    module_name_problem,
    replaced_slots,
)
from strategy.core.slots import DEFAULT_TEAM, Team
from strategy.core.team import decide
from strategy.core.worker import TeamWorker
from strategy.eval import baseline_v1, match
from strategy.tactics.attacker import (
    APPROACH_OFFSET,
    CONTESTED_SKEW,
    aim_point,
    attacker,
)
from strategy.tactics.defender import defend_goal
from strategy.tactics.role_assignments import (
    ATTACKER,
    DEFENDER,
    SUPPORTER,
    assign_roles,
)
from strategy.world.analysis import analyze, ball_intercept, drive_accel, time_to_reach
from strategy.world.prediction import (
    ball_rest_position,
    predict_ball_position,
    predict_ball_velocity,
)
from strategy.world.simconfig import BALL_DAMP, DT
from strategy.world.state import (
    GOAL_Y_MAX,
    GOAL_Y_MIN,
    ROBOT_RADIUS,
    BallState,
    RobotState,
    build_game_state,
)

ROOT = Path(__file__).resolve().parents[2]  # the repo


def attack_as(robot, gs, color):
    """The built-in attacker slot for one robot, as decide() runs it."""
    w = World(gs, color, gs.blue if color == "blue" else gs.red)
    me = next(m for m in w.teammates if m.id == robot.id)  # as decide() hands it over
    return attacker(me, w)


# Six robots well apart, roughly the sim's spawn layout (0-2 blue, 3-5 red).
SPAWNS = {
    0: (0.5, 3.0, 0.0), 1: (3.0, 1.5, 0.0), 2: (3.0, 4.5, 0.0),
    3: (8.5, 3.0, math.pi), 4: (6.0, 1.5, math.pi), 5: (6.0, 4.5, math.pi),
}


def world(
    robots: dict[int, tuple[float, float, float]] | None = None,
    ball: tuple[float, float, float, float] = (4.5, 3.0, 0.0, 0.0),
    t: float = 1.0,
    **extra: Any,
) -> dict[str, Any]:
    """A world-state message like simulation_node's. `robots` overrides SPAWNS."""
    pos = {**SPAWNS, **(robots or {})}
    raw: dict[str, Any] = {
        "t": t,
        "ball": {"x": ball[0], "y": ball[1], "vx": ball[2], "vy": ball[3]},
        "robots": {
            str(rid): {"x": x, "y": y, "angle": a, "vx": 0.0, "vy": 0.0, "omega": 0.0}
            for rid, (x, y, a) in pos.items()
        },
    }
    raw.update(extra)
    return raw


def in_front(x: float, y: float, angle: float, gap: float = 0.06) -> tuple[float, float]:
    """A point just in front of a robot at (x, y) facing `angle`."""
    d = ROBOT_RADIUS + gap
    return x + d * math.cos(angle), y + d * math.sin(angle)


class TestPossession(unittest.TestCase):
    def test_ball_in_front_is_ours(self):
        bx, by = in_front(3.0, 1.5, 0.0)
        gs = build_game_state(world(ball=(bx, by, 0, 0)), "blue")
        self.assertTrue(gs.blue[1].has_ball)
        self.assertEqual(gs.possession, "ours")
        self.assertEqual(gs.ball_holder, 1)

    def test_ball_behind_is_loose(self):
        gs = build_game_state(world(ball=(3.0 - ROBOT_RADIUS - 0.06, 1.5, 0, 0)), "blue")
        self.assertEqual(gs.possession, "loose")
        self.assertIsNone(gs.ball_holder)

    def test_possession_is_relative_to_our_color(self):
        bx, by = in_front(6.0, 1.5, math.pi)
        raw = world(ball=(bx, by, 0, 0))
        self.assertEqual(build_game_state(raw, "blue").possession, "theirs")
        self.assertEqual(build_game_state(raw, "red").possession, "ours")

    def test_closest_of_two_holds_it(self):
        # Blue 1 and red 4 face each other with the ball between, nearer red.
        robots = {1: (4.0, 3.0, 0.0), 4: (4.0 + 2 * ROBOT_RADIUS + 0.12, 3.0, math.pi)}
        gs = build_game_state(world(robots, ball=(4.0 + ROBOT_RADIUS + 0.08, 3.0, 0, 0)), "blue")
        self.assertEqual(gs.ball_holder, 4)
        self.assertEqual(gs.possession, "theirs")


class TestGameBlock(unittest.TestCase):
    def test_score_half_goals(self):
        raw = world(score={"blue": 2, "red": 1}, last_goal={"seq": 3},
                    game={"half": 2, "time_remaining": 42.0, "half_over": False})
        gs = build_game_state(raw, "red")
        self.assertEqual((gs.score_ours, gs.score_theirs), (1, 2))
        self.assertEqual((gs.half, gs.goal_seq, gs.time_remaining), (2, 3, 42.0))
        self.assertEqual(gs.game_phase, "play")

    def test_game_over_is_halt(self):
        gs = build_game_state(world(game={"half_over": True}))
        self.assertEqual(gs.game_phase, "halt")

    def test_older_messages_without_game_block(self):
        gs = build_game_state(world())
        self.assertEqual((gs.score_ours, gs.goal_seq, gs.half), (0, 0, 1))

    def test_camera_flags(self):
        raw = world()
        raw["robots"]["0"].update(source="vision", stale=True)
        gs = build_game_state(raw)
        self.assertTrue(gs.blue[0].from_camera and gs.blue[0].stale)
        self.assertFalse(gs.blue[1].from_camera)


class TestPrediction(unittest.TestCase):
    def test_still_ball_stays(self):
        b = BallState(pos=np.array([1.0, 2.0]))
        np.testing.assert_allclose(predict_ball_position(b, 3.0), [1.0, 2.0])

    def test_damped_motion(self):
        b = BallState(pos=np.array([1.0, 2.0]), vel=np.array([2.0, 0.0]), speed=2.0)
        k = BALL_DAMP
        np.testing.assert_allclose(ball_rest_position(b), [1.0 + 2.0 / k, 2.0])
        np.testing.assert_allclose(predict_ball_position(b, 100.0), ball_rest_position(b), atol=1e-6)
        np.testing.assert_allclose(predict_ball_velocity(b, 1.0), [2.0 * math.exp(-k), 0.0])
        # Slower than a constant-speed guess
        self.assertLess(predict_ball_position(b, 1.0)[0], 1.0 + 2.0)

    def test_matches_the_sim(self):
        """The model against PymunkWorld: a free-rolling ball for 1 s."""
        from pymunk_world import PymunkWorld

        w = PymunkWorld()
        w.reset_ball((4.5, 0.8))  # clear of every robot's spawn
        w.ball.velocity = (1.0, 0.0)
        start = BallState(pos=np.array([4.5, 0.8]), vel=np.array([1.0, 0.0]), speed=1.0)
        for _ in range(round(1.0 / DT)):
            w.step()
        predicted = predict_ball_position(start, 1.0)
        self.assertAlmostEqual(w.ball.position.x, predicted[0], delta=0.02)
        self.assertAlmostEqual(w.ball.position.y, predicted[1], delta=0.01)


def _measured_drive_time(heading: float, distance_m: float) -> float:
    """Drive robot 0 flat out along +x in the sim, heading fixed, and time how
    long it takes to cover `distance_m` from rest."""
    from pymunk_world import PymunkWorld
    from robot_node import RobotController

    w = PymunkWorld()
    for rid in range(1, 6):
        w.set_robot(rid, (-10.0 - rid, -10.0), 0.0)
    w.reset_ball((-5.0, -5.0))
    w.set_robot(0, (0.5, 3.0), heading)
    ctrl = RobotController(0)
    ctrl.set_target(8.5, 3.0, "2005_INVERSION", heading)
    state, t = w.step(), 0.0
    while state["robots"]["0"]["x"] - 0.5 < distance_m:
        state = w.step({0: ctrl.compute_wheels(state["robots"]["0"])})
        t += DT
    return t


class TestAnalysis(unittest.TestCase):
    def test_drive_accel_depends_on_direction(self):
        from strategy.world.simconfig import MOTOR_MAX_FORCE, ROBOT_MASS

        unit = MOTOR_MAX_FORCE / ROBOT_MASS
        self.assertAlmostEqual(float(drive_accel(0.0)), math.sqrt(3) * unit)  # straight ahead
        self.assertAlmostEqual(float(drive_accel(math.pi / 2)), 1.5 * unit)  # sideways
        self.assertAlmostEqual(float(drive_accel(math.pi / 3)), math.sqrt(3) * unit)  # 60 deg period

    def test_time_to_reach_matches_the_sim(self):
        for heading, label in ((0.0, "facing"), (-math.pi / 2, "sideways")):
            robot = RobotState(pos=np.array([0.5, 3.0]), angle=heading)
            for d in (0.5, 1.0, 2.0):
                model = time_to_reach(robot, np.array([0.5 + d, 3.0]))
                measured = _measured_drive_time(heading, d)
                self.assertAlmostEqual(model, measured, delta=0.06 * measured + DT,
                                       msg=f"{label}, {d} m")

    def test_moving_away_takes_longer(self):
        still = RobotState(pos=np.array([2.0, 3.0]))
        away = RobotState(pos=np.array([2.0, 3.0]), vel=np.array([-0.8, 0.0]))
        toward = RobotState(pos=np.array([2.0, 3.0]), vel=np.array([0.8, 0.0]))
        point = np.array([3.0, 3.0])
        self.assertLess(time_to_reach(toward, point), time_to_reach(still, point))
        self.assertGreater(time_to_reach(away, point), time_to_reach(still, point))
        self.assertEqual(time_to_reach(still, point, reach=2.0), 0.0)

    def test_intercepting_a_moving_ball(self):
        robot = RobotState(id=1, pos=np.array([3.0, 3.0]))
        still = ball_intercept(robot, BallState(pos=np.array([5.0, 3.0])))
        coming = ball_intercept(robot, BallState(pos=np.array([5.0, 3.0]),
                                                 vel=np.array([-1.0, 0.0]), speed=1.0))
        self.assertLess(coming.t, still.t)
        self.assertLess(coming.point[0], 5.0)  # met on the way
        # Too fast to catch: met where it stops.
        fast = BallState(pos=np.array([5.0, 3.0]), vel=np.array([3.0, 0.0]), speed=3.0)
        gone = ball_intercept(robot, fast)
        np.testing.assert_allclose(gone.point, ball_rest_position(fast))

    def test_race_and_threats(self):
        # Red 4 sits on the ball in blue's half; blue's robots are far away.
        robots = {4: (2.5, 2.0, math.pi), 1: (6.0, 5.5, 0.0), 2: (7.0, 0.5, 0.0)}
        a = analyze(build_game_state(world(robots, ball=(2.3, 2.0, 0, 0))))
        self.assertEqual(a.blue.their_first_to_ball.robot_id, 4)
        self.assertLess(a.blue.race_margin, 0)
        self.assertEqual(a.blue.first_threat_robot, 4)
        self.assertEqual([rid for rid, _ in a.blue.second_threats], [5, 3])  # nearest blue's goal first
        self.assertGreater(a.red.race_margin, 0)
        self.assertIsNone(a.red.first_threat_robot)

    def test_unavailable_robots_left_out(self):
        a = analyze(build_game_state(world()), unavailable={0, 3})
        self.assertNotIn(0, a.intercepts)
        self.assertNotIn(3, a.intercepts)


def corner_squeeze(seconds: float = 5.0) -> float:
    """Pin the ball in each corner and drive a robot flat out into it, as in a
    scrum. Returns the farthest the ball's centre ever got past the field edge
    (m); 0 if it stayed inside."""
    from pymunk_world import PymunkWorld
    from robot_node import RobotController

    from strategy.world.simconfig import BALL_RADIUS, FIELD_H, FIELD_W
    from strategy.world.simconfig import ROBOT_RADIUS as R

    worst = 0.0
    for cx, cy in ((0.0, 0.0), (FIELD_W, 0.0), (0.0, FIELD_H), (FIELD_W, FIELD_H)):
        w = PymunkWorld()
        for rid in range(1, 6):
            w.set_robot(rid, (-10.0 - rid, -10.0), 0.0)
        sx, sy = (1 if cx == 0 else -1), (1 if cy == 0 else -1)  # into the field
        w.reset_ball((cx + sx * BALL_RADIUS, cy + sy * BALL_RADIUS))
        heading = math.atan2(-sy, -sx)  # facing the corner
        w.set_robot(0, (cx + sx * (2 * BALL_RADIUS + R), cy + sy * (2 * BALL_RADIUS + R)), heading)
        ctrl = RobotController(0)
        ctrl.set_target(cx - sx, cy - sy, "2005_INVERSION", heading)  # 1 m past the corner
        state = w.step()
        for _ in range(round(seconds / DT)):
            state = w.step({0: ctrl.compute_wheels(state["robots"]["0"])})
            x, y = w.ball.position.x, w.ball.position.y
            worst = max(worst, -x, x - FIELD_W, -y, y - FIELD_H)
    return max(worst, 0.0)


class TestWalls(unittest.TestCase):
    def test_ball_cannot_be_squeezed_out_of_a_corner(self):
        from strategy.world.simconfig import BALL_RADIUS

        # Pressed into the wall a little is contact; out of the field is a bug.
        self.assertLess(corner_squeeze(), BALL_RADIUS)


class TestDecide(unittest.TestCase):
    def test_lone_robot_is_goalie(self):
        # Blue 1 and 2 driven by humans: blue 0 plays alone.
        gs = build_game_state(world(ball=(3.0, 4.0, 0, 0)))
        targets, kicks, attackers = decide(gs, human_blue_ids={1, 2})
        self.assertEqual(set(targets) & {0, 1, 2}, {0})
        x, y, _ = targets[0]
        self.assertLess(x, 2.0)
        self.assertTrue(GOAL_Y_MIN <= y <= GOAL_Y_MAX)
        self.assertFalse({0, 1, 2} & attackers)

    def test_closest_robot_attacks(self):
        gs = build_game_state(world(ball=(3.5, 1.5, 0, 0)))
        _, _, attackers = decide(gs, sticky_attackers={})
        self.assertIn(1, attackers)

    def test_attacker_sticks_unless_clearly_beaten(self):
        # Robot 2 is a bit closer than robot 1, but by less than the hysteresis.
        robots = {1: (3.0, 2.0, 0.0), 2: (3.0, 3.8, 0.0)}
        gs = build_game_state(world(robots, ball=(3.0, 2.95, 0, 0)))
        sticky = {"blue": 1}
        _, _, attackers = decide(gs, sticky_attackers=sticky)
        self.assertIn(1, attackers)
        self.assertEqual(sticky["blue"], 1)

    def test_lost_robots_get_no_target(self):
        gs = build_game_state(world())
        targets, _, _ = decide(gs, lost_ids={1, 4})
        self.assertNotIn(1, targets)
        self.assertNotIn(4, targets)
        self.assertIn(2, targets)

    def test_only_the_kicker_closest_to_the_ball_fires(self):
        from pymunk_world import PymunkWorld  # sim/, on the path via world.simconfig

        physics = PymunkWorld(seed=0)
        physics.robots[1].position = (4.0, 3.0)
        physics.robots[4].position = (5.2, 3.0)
        physics.ball.position = (4.4, 3.0)
        self.assertEqual(physics.closest_kicker([4, 1]), [1])
        self.assertEqual(physics.closest_kicker([4]), [4])


class TestTactics(unittest.TestCase):
    def test_attacker_meets_a_rolling_ball_where_it_can_reach_it(self):
        from strategy.tactics.attacker import MEET_BALL_SPEED, _push_toward_goal
        from strategy.world.analysis import ball_intercept

        robots = {0: (3.0, 3.0, 0.0)}
        gs = build_game_state(world(robots, ball=(5.0, 1.0, 0.0, 1.5)))  # rolling up the field
        robot, ball = gs.blue[0], gs.ball
        self.assertGreater(ball.speed, MEET_BALL_SPEED)
        meet = ball_intercept(robot, ball)
        command = attack_as(robot, gs, "blue")
        at_meet = _push_toward_goal(robot, BallState(pos=meet.point, vel=np.zeros(2), speed=0.0),
                                    "blue", 0.0)
        chasing = _push_toward_goal(robot, ball, "blue", 0.0)
        self.assertEqual(command.target, at_meet.target)
        self.assertGreater(command.y, chasing.y + 0.3)  # ahead of the ball, where it will be

    def test_lone_robot_defends(self):
        gs = build_game_state(world())
        sticky = {"blue": 0}
        self.assertEqual(assign_roles([gs.blue[1]], gs.ball.pos, sticky, "blue"),
                         [(gs.blue[1], DEFENDER)])
        self.assertNotIn("blue", sticky)

    def test_roles_by_distance(self):
        gs = build_game_state(world(ball=(3.2, 4.4, 0, 0)))
        roles = assign_roles(gs.blue, gs.ball.pos, {}, "blue")
        self.assertEqual([(r.id, role) for r, role in roles],
                         [(2, ATTACKER), (1, SUPPORTER), (0, DEFENDER)])

    def test_contested_ball_is_kicked_at_an_angle(self):
        robots = {1: (4.0, 3.0, 0.0), 4: (4.6, 3.0, math.pi)}
        gs = build_game_state(world(robots, ball=(4.3, 3.0, 0, 0)))
        command = attack_as(gs.blue[1], gs, "blue")
        self.assertTrue(command.kick)
        self.assertAlmostEqual(command.angle, CONTESTED_SKEW)

    def test_classic_attacker_gets_behind_the_ball(self):
        gs = build_game_state(world(ball=(5.0, 3.0, 0, 0)))  # nobody near it
        command = attack_as(gs.blue[1], gs, "blue")
        self.assertFalse(command.kick)
        self.assertLess(command.x, 5.0)  # blue attacks +x: stands on the -x side
        command = attack_as(gs.red[1], gs, "red")
        self.assertGreater(command.x, 5.0)

    def test_goalie_stays_inside_the_posts(self):
        command = defend_goal(np.array([1.0, 0.2]), "blue")  # ball wide, near the corner
        self.assertTrue(GOAL_Y_MIN <= command.y <= GOAL_Y_MAX)
        self.assertLess(command.x, 2.0)


class TestBrain(unittest.TestCase):
    def test_kickoff_after_every_goal(self):
        brain = Brain("blue")
        plan = brain.tick(world(t=0.1))
        self.assertEqual(plan.gamestate.game_phase, "kickoff")
        plan = brain.tick(world(ball=(4.5, 3.0, 1.0, 0.0), t=0.2))
        self.assertTrue(brain.kicked_off)
        self.assertEqual(plan.gamestate.game_phase, "play")
        brain.tick(world(t=0.3, last_goal={"seq": 1}))  # goal: ball back at the centre
        self.assertFalse(brain.kicked_off)

    def test_new_half_is_a_kickoff(self):
        brain = Brain("blue")
        brain.tick(world(ball=(4.5, 3.0, 1.0, 0.0), t=0.1, game={"half": 1}))
        self.assertTrue(brain.kicked_off)
        brain.tick(world(t=0.2, game={"half": 2}))
        self.assertFalse(brain.kicked_off)

    def test_camera_robot_lost_after_a_while(self):
        brain = Brain("blue")

        def tick(t: float, stale: bool):
            raw = world(t=t)
            raw["robots"]["1"].update(source="vision", stale=stale)
            return brain.tick(raw)

        self.assertIn(1, tick(1.0, stale=True).targets)  # just went stale: still planned
        self.assertIn(1, tick(1.3, stale=True).targets)
        self.assertNotIn(1, tick(1.6, stale=True).targets)  # 0.6 s: lost
        self.assertIn(1, tick(1.7, stale=False).targets)  # seen again

    def test_dribble_goes_to_our_attacker(self):
        brain = Brain("blue")
        brain.handle_control({"dribble_attacker": True})
        plan = brain.tick(world(ball=(3.5, 1.5, 0, 0)))
        # The toggle is for our attacker; every other robot the AI drives, red
        # too, is told "off" (a red team file can turn its own on).
        self.assertEqual(plan.dribble, {0: False, 1: True, 2: False, 3: False, 4: False, 5: False})

    def test_human_robots_left_alone(self):
        brain = Brain("blue")
        brain.handle_control({"human_blue_ids": [0]})
        plan = brain.tick(world())
        self.assertNotIn(0, plan.targets)
        self.assertNotIn(0, plan.dribble)


def lone_attacker_scores(ball_frac: tuple[float, float], limit_s: float = 30.0) -> float | None:
    """Blue robot 1 alone against a still ball (everyone else off the field):
    seconds until it scores in red's goal, None if not within `limit_s`."""
    from pymunk_world import PymunkWorld
    from robot_node import RobotController

    from strategy.world.simconfig import FIELD_H, FIELD_W

    brain = Brain("blue")
    brain.handle_control({"human_blue_ids": [0]})  # robot 2 stays off the field: 1 attacks
    w = PymunkWorld()
    for rid in (0, 2, 3, 4, 5):
        w.set_robot(rid, (-10.0 - rid, -10.0), 0.0)
    w.reset_ball((ball_frac[0] * FIELD_W, ball_frac[1] * FIELD_H))
    w.set_robot(1, (0.1 * FIELD_W, 0.5 * FIELD_H), 0.0)
    ctrl = RobotController(1)
    state = w.step()
    for _ in range(round(limit_s / DT)):
        plan = brain.tick({"t": state["t"], "ball": state["ball"], "robots": state["robots"]})
        ctrl.set_target(*plan.targets[1][:2], None, plan.targets[1][2])
        state = w.step({1: ctrl.compute_wheels(state["robots"]["1"])})
        if state["scoring_team"] is not None:
            return state["t"] if state["scoring_team"] == "red" else None
    return None


class TestPush(unittest.TestCase):
    def test_lined_up_attacker_drives_through_the_ball(self):
        ball = (5.0, 3.0)
        behind = (5.0 - APPROACH_OFFSET, 3.0)  # on the line to red's goal, facing it
        gs = build_game_state(world({1: (*behind, 0.0)}, ball=(*ball, 0, 0)))
        command = attack_as(gs.blue[1], gs, "blue")
        self.assertGreater(command.x, ball[0])  # past the ball, toward the goal
        self.assertAlmostEqual(command.angle, 0.0, places=6)

    def test_not_lined_up_gets_behind_first(self):
        ball = (5.0, 3.0)
        for robot in ((5.0 - APPROACH_OFFSET, 3.0, math.pi / 2),   # right spot, facing sideways
                      (5.0 + APPROACH_OFFSET, 3.0, 0.0),          # wrong side of the ball
                      (5.0 - APPROACH_OFFSET, 3.6, 0.0)):         # off the line
            gs = build_game_state(world({1: robot}, ball=(*ball, 0, 0)))
            self.assertLess(attack_as(gs.blue[1], gs, "blue").x, ball[0], msg=str(robot))

    def test_aim_is_inside_the_goal(self):
        from strategy.world.simconfig import FIELD_W

        for y in (0.0, 3.0, 6.0):
            for color, beyond in (("blue", lambda x: x > FIELD_W), ("red", lambda x: x < 0)):
                aim = aim_point(np.array([4.0, y]), color)
                self.assertTrue(beyond(aim[0]))
                self.assertTrue(GOAL_Y_MIN < aim[1] < GOAL_Y_MAX)

    def test_lone_attacker_scores(self):
        """It used to park behind the ball and never move it."""
        for spot in ((0.45, 0.4), (0.75, 0.6)):
            self.assertIsNotNone(lone_attacker_scores(spot), msg=str(spot))


SPOT = (2.0, 5.0)


def to_spot(me: Me, world: World) -> Command:
    return me.go_to(SPOT, face=world.ball.pos)


def crashes(me: Me, world: World) -> Command:
    return 1 / 0


def returns_junk(me: Me, world: World) -> Command:
    return (1.0, 2.0)  # not a Command


class TestSlots(unittest.TestCase):
    def test_default_team_is_the_built_in_one(self):
        gs = build_game_state(world(ball=(4.0, 2.5, 0.3, 0.1)))
        a = decide(gs, sticky_attackers={})
        b = decide(gs, sticky_attackers={}, teams={"blue": DEFAULT_TEAM, "red": Team()})
        self.assertEqual(a, b)

    def test_replacing_a_slot_changes_only_that_team(self):
        gs = build_game_state(world(ball=(4.5, 3.0, 0, 0)))
        base_targets, _, attackers = decide(gs, sticky_attackers={})
        targets, _, _ = decide(gs, sticky_attackers={},
                               teams={"blue": DEFAULT_TEAM.replace(supporter=to_spot)})
        blue_supporter = assign_roles(gs.blue, gs.ball.pos, {}, "blue")[1][0].id
        self.assertEqual(targets[blue_supporter][:2], SPOT)
        changed = {rid for rid in targets if targets[rid] != base_targets[rid]}
        self.assertEqual(changed, {blue_supporter})

    def test_custom_roles(self):
        def all_defend(team, w):
            return [(m, DEFENDER) for m in team]

        gs = build_game_state(world())
        _, kicks, attackers = decide(gs, sticky_attackers={},
                                     teams={"red": DEFAULT_TEAM.replace(roles=all_defend)})
        self.assertEqual(attackers & {3, 4, 5}, set())
        self.assertTrue(attackers & {0, 1, 2})  # blue still has its attacker

    def test_broken_slots_fall_back_to_built_in(self):
        gs = build_game_state(world(ball=(4.5, 3.0, 0, 0)))
        expected = decide(gs, sticky_attackers={})
        for bad in (crashes, returns_junk):
            errors: list[str] = []
            got = decide(gs, sticky_attackers={}, errors=errors,
                         teams={"blue": DEFAULT_TEAM.replace(supporter=bad, attacker=bad)})
            self.assertEqual(got, expected, bad.__name__)
            self.assertEqual(len(errors), 2, errors)  # attacker and supporter
            self.assertTrue(all("using the built-in" in e for e in errors), errors)

    def test_bad_role_list_falls_back(self):
        def forgets_a_robot(team, w):
            return [(team[0], ATTACKER)]

        gs = build_game_state(world())
        errors: list[str] = []
        got = decide(gs, sticky_attackers={}, errors=errors,
                     teams={"blue": DEFAULT_TEAM.replace(roles=forgets_a_robot)})
        self.assertEqual(got, decide(gs, sticky_attackers={}))
        self.assertIn("exactly one role", errors[0])

    def test_unknown_slot_is_refused(self):
        with self.assertRaises(ValueError):
            DEFAULT_TEAM.replace(goalkeeper=to_spot)
        with self.assertRaises(TypeError):
            DEFAULT_TEAM.replace(supporter="to_spot")

    def test_brain_reports_each_error_once_and_keeps_memory(self):
        def counts(me, w):
            w.memory["n"] = w.memory.get("n", 0) + 1
            return me.stop()

        logged: list[str] = []
        brain = Brain("blue", log=logged.append,
                      teams={"blue": DEFAULT_TEAM.replace(supporter=crashes, defender=counts)})
        for t in (1.0, 1.1, 1.2):
            plan = brain.tick(world(t=t))
        self.assertEqual(len(plan.errors), 1)
        self.assertEqual(sum("ZeroDivisionError" in m for m in logged), 1)
        self.assertEqual(brain.memory["blue"]["n"], 3)
        with self.assertRaises(ValueError):  # v0 has no slots
            Brain("blue", decide_fn=match.STRATEGIES["v0"], teams={"blue": DEFAULT_TEAM})

    def test_me_and_world_helpers(self):
        gs = build_game_state(world({1: (3.0, 1.5, 0.0), 4: (5.0, 3.0, math.pi)}))
        w = World(gs, "blue", gs.blue)
        me = w.teammates[1]
        self.assertEqual(me.go_to((4.0, 1.5)).angle, 0.0)  # faces where it goes
        self.assertAlmostEqual(me.go_to((3.0, 3.0), face=math.pi).angle, math.pi)
        self.assertAlmostEqual(me.face((3.0, 2.5)).angle, math.pi / 2)
        self.assertTrue(me.go_to((4.0, 1.5), kick=True).kick)
        self.assertFalse(w.lane_clear((4.0, 3.0), (6.0, 3.0)))  # red 4 sits on it
        self.assertTrue(w.lane_clear((4.0, 1.0), (6.0, 1.0)))
        self.assertIs(w.analysis, w.analysis)  # computed once per frame
        self.assertEqual(w.us.color, "blue")

    def test_a_team_with_a_replaced_slot_plays_a_match(self):
        r = match.play_game(decide, decide, True, seed=1, duration_s=5,
                            team_a=DEFAULT_TEAM.replace(supporter=to_spot))
        self.assertEqual(r.ticks, round(5 / DT))


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestTeamFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def test_template_plays_like_the_built_in_team(self):
        team = load_team(EXAMPLES / "template.py")
        self.assertEqual(replaced_slots(team), ["roles", "attacker", "supporter", "defender"])
        a = match.play_game(decide, decide, True, seed=2, duration_s=10, team_a=team)
        b = match.play_game(decide, decide, True, seed=2, duration_s=10)
        self.assertEqual((a.goals_a, a.goals_b, a.kicks_a, a.kicks_b, a.possession_a, a.attacking_a),
                         (b.goals_a, b.goals_b, b.kicks_a, b.kicks_b, b.possession_a, b.attacking_a))
        self.assertEqual(a.errors_a, [])

    def test_example_replaces_only_its_slot(self):
        team = load_team(EXAMPLES / "get_open.py")
        self.assertEqual(replaced_slots(team), ["supporter"])
        r = match.play_game(decide, decide, True, seed=1, duration_s=10, team_a=team)
        self.assertEqual(r.errors_a, [])

    def test_load_errors_say_where(self):
        cases = {
            "syntax.py": ("def supporter(me, world)\n    return me.stop()\n", "syntax.py:1: syntax error"),
            "crash.py": ("x = 1\nraise RuntimeError('boom')\n", "crash.py:2: RuntimeError: boom"),
            "empty.py": ("def helper():\n    pass\n", "defines none of the slots"),
        }
        for name, (code, expected) in cases.items():
            with self.assertRaises(TeamFileError) as ctx:
                load_team(self.write(name, code))
            self.assertIn(expected, str(ctx.exception), name)
        with self.assertRaises(TeamFileError):
            load_team(self.dir / "missing.py")

    def test_errors_while_playing_point_at_your_line(self):
        team = load_team(self.write("buggy.py", """\
            def supporter(me, world):
                spot = world.ball.pos
                return me.go_to(spot, face="the ball")
            """))
        errors: list[str] = []
        gs = build_game_state(world())
        expected = decide(gs, sticky_attackers={})
        self.assertEqual(decide(gs, sticky_attackers={}, teams={"blue": team}, errors=errors), expected)
        self.assertIn("buggy.py:3", errors[0])  # their line, not inside api.py

    def test_saved_edits_reload_and_broken_ones_keep_the_last(self):
        path = self.write("live.py", "def supporter(me, world):\n    return me.stop()\n")
        live = TeamFile(path)
        self.assertEqual(replaced_slots(live.team), ["supporter"])
        self.assertFalse(live.reload_if_changed())

        path.write_text("def supporter(me, world)\n    return me.stop()\n", encoding="utf-8")
        self.assertTrue(live.reload_if_changed())
        self.assertIn("syntax error", live.error)
        self.assertEqual(replaced_slots(live.team), ["supporter"])  # the last one that loaded

        path.write_text("def attacker(me, world):\n    return me.stop()\n\n"
                        "def supporter(me, world):\n    return me.stop()\n", encoding="utf-8")
        self.assertTrue(live.reload_if_changed())
        self.assertIsNone(live.error)
        self.assertEqual(replaced_slots(live.team), ["attacker", "supporter"])

    def test_slow_slots_fall_back(self):
        team = load_team(self.write("slow.py", """\
            import time

            def supporter(me, world):
                time.sleep(0.03)
                return me.stop()
            """))
        errors: list[str] = []
        gs = build_game_state(world())
        got = decide(gs, sticky_attackers={}, teams={"blue": team}, errors=errors)
        self.assertEqual(got, decide(gs, sticky_attackers={}))
        self.assertIn("budget", errors[0])

    def test_match_sides_are_names_or_files(self):
        self.assertEqual(match.resolve("v1")[2], "v1")
        fn, path, label = match.resolve(str(EXAMPLES / "get_open.py"))
        self.assertIs(fn, decide)  # the team itself plays in its own process
        self.assertEqual((label, path), ("get_open.py", EXAMPLES / "get_open.py"))
        with self.assertRaises(ValueError):
            match.resolve("v9")

    def test_brain_picks_up_a_new_team(self):
        logged: list[str] = []
        brain = Brain("blue", log=logged.append)
        brain.set_team("blue", load_team(self.write("a.py", "def supporter(me, w):\n    return 1/0\n")))
        brain.tick(world(t=1.0))
        brain.set_team("blue", load_team(self.write("b.py", "def supporter(me, w):\n    return 1/0\n")))
        brain.tick(world(t=1.1))
        self.assertEqual(sum("ZeroDivisionError" in m for m in logged), 2)  # reported afresh
        brain.set_team("blue", None)
        self.assertEqual(brain.teams, {})


class TestSeeingAndCommands(unittest.TestCase):
    """world.draw, world.show, world.events, prepare(), dribble= and max_speed=."""

    def team_brain(self, **slots) -> Brain:
        return Brain("blue", teams={"blue": DEFAULT_TEAM.replace(**slots)})

    def test_drawings_and_shown_values_come_with_the_plan(self):
        def supporter(me, w):
            spot = (6.0, 4.0)
            w.draw.line(w.ball.pos, spot, color="green")
            w.draw.text(spot, "here")
            w.show("distance", me.distance_to(spot))
            return me.go_to(spot)

        plan = self.team_brain(supporter=supporter).tick(world())
        shapes = plan.drawings["blue"]
        self.assertEqual([s["kind"] for s in shapes], ["line", "text"])
        self.assertEqual(shapes[0]["color"], "#3fb950")
        self.assertEqual(shapes[0]["to"], [6.0, 4.0])
        robot = shapes[0]["robot"]  # tagged with the robot whose slot drew it
        self.assertIn("distance", plan.shown["blue"][f"robot {robot}"])
        self.assertNotIn("red", plan.drawings)  # the built-in team draws nothing

    def test_a_bad_drawing_is_an_error_on_its_line(self):
        def supporter(me, w):
            w.draw.circle(me.pos, 0.3, color="sparkly")
            return me.stop()

        plan = self.team_brain(supporter=supporter).tick(world())
        self.assertTrue(any("color must be one of" in e and "using the built-in supporter" in e
                            for e in plan.errors), plan.errors)

    def test_drawing_too_much_is_capped(self):
        def supporter(me, w):
            for i in range(500):
                w.draw.point((i * 0.01, 1.0))
            return me.stop()

        plan = self.team_brain(supporter=supporter).tick(world())
        self.assertEqual(len(plan.drawings["blue"]), 100)

    def test_dribble_and_max_speed_reach_the_plan(self):
        def supporter(me, w):
            return me.go_to((6.0, 4.0), dribble=True, max_speed=0.4)

        plan = self.team_brain(supporter=supporter).tick(world())
        supporter_id = assign_roles(build_game_state(world()).blue, np.array([4.5, 3.0]), {}, "blue")[1][0].id
        self.assertTrue(plan.dribble[supporter_id])
        self.assertEqual(plan.max_speed, {supporter_id: 0.4})
        self.assertEqual(sum(plan.dribble.values()), 1)  # the dribble toggle is off
        with self.assertRaises(ValueError):
            Me(build_game_state(world()).blue[0]).go_to((1, 1), max_speed=0)

    def test_prepare_runs_once_a_frame_before_the_robots(self):
        def prepare(w):
            w.memory["frames"] = w.memory.get("frames", 0) + 1
            w.memory["spot"] = (6.0, 4.0)

        def supporter(me, w):
            return me.go_to(w.memory["spot"])

        brain = self.team_brain(prepare=prepare, supporter=supporter)
        for t in (1.0, 1.1, 1.2):
            plan = brain.tick(world(t=t))
        self.assertEqual(brain.memory["blue"]["frames"], 3)
        self.assertIn((6.0, 4.0), [t[:2] for t in plan.targets.values()])
        self.assertEqual(plan.errors, [])

    def test_events_new_game_goals_and_kickoff(self):
        def prepare(w):
            w.memory.setdefault("events", []).extend(w.events)

        brain = self.team_brain(prepare=prepare)
        brain.tick(world(t=1.0, score={"blue": 0, "red": 0}))
        brain.tick(world(t=1.1, score={"blue": 1, "red": 0}))
        brain.tick(world(t=1.2, ball=(4.5, 3.0, 1.0, 0.0), score={"blue": 1, "red": 1}))
        brain.tick(world(t=1.3, ball=(4.6, 3.0, 1.0, 0.0), score={"blue": 1, "red": 1}))
        self.assertEqual(brain.memory["blue"]["events"],
                         ["new_game", "goal_for", "goal_against", "kickoff"])

    def test_max_speed_caps_how_fast_the_robot_drives(self):
        from pymunk_world import PymunkWorld  # sim/, on the path via world.simconfig
        from robot_node import RobotController

        def top_speed(cap):
            physics = PymunkWorld(seed=0)
            control = RobotController(0)
            state = physics.step()
            fastest = 0.0
            for _ in range(240):  # 4 s
                r = state["robots"]["0"]
                control.set_target(r["x"] + 3.0, r["y"], None, 0.0, cap)
                state = physics.step({0: control.compute_wheels(r)})
                fastest = max(fastest, math.hypot(state["robots"]["0"]["vx"], state["robots"]["0"]["vy"]))
            return fastest

        self.assertLess(top_speed(0.4), 0.45)
        self.assertGreater(top_speed(None), 0.9)


def process_running(pid: int) -> bool:
    """Whether a process is still running (without touching it: on Windows,
    os.kill(pid, 0) would end it)."""
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) != 0  # 0: it has exited
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_within(pid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not process_running(pid):
            return True
        time.sleep(0.1)
    return False


def first_answer(worker, raw, within_s: float = 20.0):
    """Ask a worker until it answers (it imports and loads first), once a
    frame as the sim does: asked faster, every answer would arrive stale."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        answer = worker.plan(raw, True, "play", set(), set())
        if answer is not None:
            return answer
        time.sleep(1 / 60)
    raise AssertionError("the worker never answered")


class TestWorker(unittest.TestCase):
    """Team files in their own process (core/worker.py). These start real processes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.logged: list[str] = []
        self.workers: list[TeamWorker] = []

    def tearDown(self):
        for w in self.workers:
            w.close()
        self._tmp.cleanup()

    def start(self, code: str, **kw) -> TeamWorker:
        path = self.dir / "team.py"
        path.write_text(textwrap.dedent(code), encoding="utf-8")
        w = TeamWorker(path, "blue", log=self.logged.append, **kw)
        self.workers.append(w)
        return w

    def test_plays_the_file_and_prints_go_to_the_log(self):
        w = self.start("""\
            def supporter(me, world):
                if "said" not in world.memory:  # once is enough to prove the point
                    print("(a print from a team file: it goes to the log)")
                    world.memory["said"] = True
                return me.go_to((1.0, 5.0))
            """)
        answer = first_answer(w, world())
        supporter = assign_roles(build_game_state(world()).blue, np.array([4.5, 3.0]), {}, "blue")[1][0].id
        self.assertEqual(answer["targets"][str(supporter)][:2], [1.0, 5.0])
        self.assertEqual(set(answer["targets"]), {"0", "1", "2"})  # its colour only
        self.assertTrue(any("blue plays team.py: supporter replaced" in m for m in self.logged))

    def test_draws_dribbles_and_gets_the_game_events(self):
        w = self.start("""\
            def supporter(me, world):
                world.memory.setdefault("events", []).extend(world.events)
                world.draw.point((2.0, 2.0))
                world.show("seen", ",".join(world.memory["events"]))
                return me.go_to((1.0, 5.0), dribble=True, max_speed=0.5)
            """)
        raw = world()
        end = time.monotonic() + 20
        answer, events = None, ["goal_blue"]  # a goal on the first frame, while it's starting up
        while time.monotonic() < end:
            answer = w.plan(raw, True, "play", set(), set(), events)
            events = []
            if answer is not None:
                break
            time.sleep(1 / 60)
        assert answer is not None
        supporter = next(iter(answer["max_speed"]))
        self.assertEqual(answer["dribble"], [int(supporter)])
        self.assertEqual(answer["max_speed"][supporter], 0.5)
        self.assertEqual(answer["draw"][0]["at"], [2.0, 2.0])
        # Every request repeated the goal until the first answer; it counts once.
        self.assertEqual(answer["show"][f"robot {supporter}"]["seen"], "goal_for")

    def test_an_endless_loop_never_blocks_and_gets_restarted(self):
        w = self.start("""\
            def supporter(me, world):
                while True:
                    pass
            """, timeout_s=0.02, restart_after_s=0.3, startup_grace_s=3.0)
        took = []
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            t0 = time.monotonic()
            self.assertIsNone(w.plan(world(), True, "play", set(), set()))
            took.append(time.monotonic() - t0)
        self.assertLess(max(took), 0.3)  # a frame's wait, plus a restart now and then
        self.assertLess(sorted(took)[len(took) // 2], 0.01)  # once stuck, the node only glances
        self.assertGreaterEqual(w.restarts, 1)
        self.assertTrue(any("endless loop" in m for m in self.logged))

    def test_brain_plays_built_in_while_the_worker_is_stuck(self):
        w = self.start("""\
            def supporter(me, world):
                world.memory["n"] = world.memory.get("n", 0) + 1
                while world.memory["n"] > 3:  # answers three frames, then gets stuck
                    pass
                return me.go_to((1.0, 5.0))
            """, timeout_s=0.05)
        brain = Brain("blue", log=self.logged.append)
        brain.set_worker("blue", w)
        built_in = Brain("blue")
        end = time.monotonic() + 20
        while time.monotonic() < end:
            plan, reference = brain.tick(world()), built_in.tick(world())
            if any("isn't answering in time" in e for e in plan.errors):
                break
            time.sleep(1 / 60)  # once a frame, as the sim does
        self.assertTrue(w.ready)
        self.assertEqual(plan.targets, reference.targets)  # the built-in team meanwhile
        self.assertTrue(any("isn't answering in time" in e for e in plan.errors))

    def test_close_ends_the_worker_process_itself(self):
        # On Windows a venv's python.exe is a launcher with the real Python
        # as its child: closing has to end that child, not only the launcher.
        w = self.start("def supporter(me, world):\n    return me.stop()\n")
        first_answer(w, world())
        self.assertIsNotNone(w.pid)
        self.assertTrue(process_running(w.pid))
        w.close()
        self.assertTrue(gone_within(w.pid, 5))

    def test_a_restarted_stuck_worker_is_really_gone(self):
        w = self.start("""\
            def supporter(me, world):
                world.memory["n"] = world.memory.get("n", 0) + 1
                while world.memory["n"] > 3:  # answers three frames, then gets stuck
                    pass
                return me.stop()
            """, timeout_s=0.05, restart_after_s=0.3)
        first_answer(w, world())
        stuck = w.pid
        end = time.monotonic() + 20
        while w.restarts == 0 and time.monotonic() < end:
            w.plan(world(), True, "play", set(), set())
            time.sleep(1 / 60)
        self.assertGreaterEqual(w.restarts, 1)
        self.assertTrue(gone_within(stuck, 5))  # not left spinning in its loop

    def test_a_stuck_worker_exits_when_the_node_is_killed(self):
        team = self.dir / "loops.py"
        team.write_text("def supporter(me, world):\n    while True:\n        pass\n", encoding="utf-8")
        node = textwrap.dedent(f"""\
            import os, sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from strategy.core.worker import TeamWorker
            from strategy.tests.test_strategy import world
            w = TeamWorker({str(team)!r}, "blue", log=lambda m: None)
            end = time.monotonic() + 30
            while w.pid is None and time.monotonic() < end:
                w.plan(world(), True, "play", set(), set())
                time.sleep(1 / 60)
            print(os.getpid(), w.pid, flush=True)
            time.sleep(120)
            """)
        proc = subprocess.Popen([sys.executable, "-c", node], cwd=ROOT, stdout=subprocess.PIPE, text=True)
        try:
            node_pid, worker_pid = (int(x) for x in proc.stdout.readline().split())
            self.assertTrue(process_running(worker_pid))
            os.kill(node_pid, 9)  # the node itself, not a launcher in front of it
            self.assertTrue(gone_within(worker_pid, 10))
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_exits_when_the_node_goes_away(self):
        w = self.start("def supporter(me, world):\n    return me.stop()\n")
        first_answer(w, world())
        w._proc.stdin.close()  # what the node's death looks like from the worker
        w._proc.wait(timeout=10)
        self.assertIsNotNone(w._proc.returncode)

    def test_a_broken_file_plays_built_in_and_says_why(self):
        w = self.start("def supporter(me, world)\n    return me.stop()\n")
        answer = first_answer(w, world())
        self.assertEqual({int(r): tuple(t) for r, t in answer["targets"].items()},
                         {r: t for r, t in decide(build_game_state(world()), sticky_attackers={})[0].items()
                          if r < 3})
        self.assertTrue(any("syntax error" in m and "playing the built-in team" in m for m in self.logged))


class TestGuide(unittest.TestCase):
    """Every example in the students' guide (strategy/docs/guide.md) plays
    without an error, so the guide can't drift from the code. (```python
    blocks are whole team files; ```py blocks are excerpts of several files.)"""

    def test_every_example_plays(self):
        import re

        guide = Path(__file__).resolve().parents[1] / "docs" / "guide.md"
        blocks = re.findall(r"```python\n(.*?)```", guide.read_text(encoding="utf-8"), re.S)
        self.assertGreaterEqual(len(blocks), 5)
        with tempfile.TemporaryDirectory() as d:
            for i, code in enumerate(blocks):
                path = Path(d) / f"example_{i}.py"
                path.write_text(code, encoding="utf-8")
                with self.subTest(example=i, starts=code.splitlines()[0]):
                    r = match.play_game(decide, decide, i % 2 == 0, seed=i, duration_s=4,
                                        team_a=load_team(path))
                    self.assertEqual(r.errors_a, [])


class TestSkills(unittest.TestCase):
    """The skill slots (skills/ball.py) and the readings that came with them."""

    def plan(self, team: Team, raw: dict[str, Any] | None = None):
        errors: list[str] = []
        gs = build_game_state(raw or world(ball=(4.5, 3.0, 0, 0)))
        targets, kicks, attackers = decide(gs, sticky_attackers={}, teams={"blue": team}, errors=errors)
        return targets, kicks, attackers, errors

    def test_the_built_in_attacker_uses_your_kick_toward(self):
        def kick_toward(me, world, target):
            world.draw.point(target)
            return me.go_to((1.0, 1.0))

        targets, _, attackers, errors = self.plan(DEFAULT_TEAM.replace(kick_toward=kick_toward))
        self.assertEqual(errors, [])
        (blue_attacker,) = attackers & {0, 1, 2}
        self.assertEqual(targets[blue_attacker][:2], (1.0, 1.0))

    def test_the_built_in_attacker_uses_your_intercept_for_a_rolling_ball(self):
        def intercept(me, world):
            return me.go_to(world.ball.at(1.0))

        raw = world(ball=(4.5, 3.0, 1.5, 0.5))
        targets, _, attackers, errors = self.plan(DEFAULT_TEAM.replace(intercept=intercept), raw)
        self.assertEqual(errors, [])
        (blue_attacker,) = attackers & {0, 1, 2}
        ahead = build_game_state(raw).ball.at(1.0)
        self.assertAlmostEqual(targets[blue_attacker][0], float(ahead[0]), places=6)

    def test_a_broken_skill_falls_back_to_the_built_in_one(self):
        def kick_toward(me, world, target):
            return 1 / 0

        targets, _, _, errors = self.plan(DEFAULT_TEAM.replace(kick_toward=kick_toward))
        self.assertTrue(any("kick_toward" in e and "ZeroDivisionError" in e for e in errors), errors)
        self.assertEqual(targets, decide(build_game_state(world(ball=(4.5, 3.0, 0, 0))), sticky_attackers={})[0])

    def test_skills_as_methods_of_a_robot(self):
        gs = build_game_state(world({0: (3.6, 3.0, 0.0)}, ball=(4.0, 3.0, 0, 0)))
        w = World(gs, "blue", gs.blue)
        me, mate = w.teammates[0], w.teammates[1]
        self.assertEqual(me.go_to_ball().target[:2], (4.0, 3.0))
        self.assertIsInstance(me.kick_toward(w.their_goal), Command)
        self.assertIsInstance(me.pass_to(mate), Command)
        self.assertTrue(me.dribble_to((8.0, 3.0)).dribble)
        with self.assertRaisesRegex(RuntimeError, "for your own robots"):
            w.opponents[0].kick_toward(w.their_goal)

    def test_a_lined_up_pass_fires_the_kicker(self):
        # Robot 0 behind the ball, facing robot 1 straight ahead of it.
        gs = build_game_state(world({0: (3.75, 3.0, 0.0), 1: (6.0, 3.0, 0.0)}, ball=(4.0, 3.0, 0, 0)))
        w = World(gs, "blue", gs.blue)
        self.assertTrue(w.teammates[0].pass_to(w.teammates[1]).kick)

    def test_time_to_the_ball_ahead_and_the_score(self):
        raw = world({0: (1.0, 3.0, 0.0)}, ball=(4.0, 3.0, 1.0, 0.0), score={"blue": 2, "red": 1},
                    game={"half": 1, "time_remaining": 42.0})
        gs = build_game_state(raw)
        blue, red = World(gs, "blue", gs.blue), World(gs, "red", gs.red)
        near, far = blue.teammates[0].time_to((2.0, 3.0)), blue.teammates[0].time_to((6.0, 3.0))
        self.assertGreater(far, near)
        self.assertGreater(gs.ball.at(1.0)[0], 4.0)  # rolling on
        self.assertLess(gs.ball.at(1.0)[0], gs.ball.stops_at[0])  # slowing down
        self.assertEqual((blue.score, red.score), ((2, 1), (1, 2)))
        self.assertEqual(blue.time_left, 42.0)

    def test_a_team_file_replaces_a_skill(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "team.py"
            path.write_text("def go_to_ball(me, world):\n    return me.stop()\n", encoding="utf-8")
            self.assertEqual(replaced_slots(load_team(path)), ["go_to_ball"])


class TestPlaysAndTeam(unittest.TestCase):
    """Your own roles, goalie, PLAYS and pick_play, team() and kickoff()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, code: str) -> Team:
        path = self.dir / f"team_{len(list(self.dir.iterdir()))}.py"
        path.write_text(textwrap.dedent(code), encoding="utf-8")
        return load_team(path)

    def plan(self, team: Team, raw: dict[str, Any] | None = None, kicked_off: bool = True,
             play_state: dict[str, Any] | None = None):
        errors: list[str] = []
        targets, _, attackers = decide(build_game_state(raw or world()), sticky_attackers={},
                                       kicked_off=kicked_off, teams={"blue": team}, errors=errors,
                                       play_state=play_state)
        return targets, attackers, errors

    def test_your_own_role(self):
        team = self.load("""\
            def runner(me, world):
                return me.go_to((7.0, 5.0))

            def roles(team, world):
                return [(m, "runner") for m in team]
            """)
        targets, attackers, errors = self.plan(team)
        self.assertEqual(errors, [])
        self.assertEqual({targets[r][:2] for r in (0, 1, 2)}, {(7.0, 5.0)})
        self.assertFalse(attackers & {0, 1, 2})  # no blue attacker

    def test_a_role_without_a_function_is_an_error(self):
        team = self.load("""\
            def roles(team, world):
                return [(m, "wingback") for m in team]
            """)
        targets, _, errors = self.plan(team)
        self.assertTrue(any("'wingback' isn't a role" in e for e in errors), errors)
        self.assertEqual(set(targets), {0, 1, 2, 3, 4, 5})  # built-in roles instead

    def test_goalie_plays_like_the_built_in_defender(self):
        gs = build_game_state(world(ball=(2.0, 2.0, 0, 0)))
        me = Me(gs.blue[0])
        w = World(gs, "blue", gs.blue)
        self.assertEqual(DEFAULT_TEAM.goalie(me, w).target, DEFAULT_TEAM.defender(me, w).target)

    def test_plays_give_roles_and_switch_without_flicker(self):
        team = self.load("""\
            PLAYS = {
                "attack": ["attacker", "supporter", "goalie"],
                "park": ["parked"],
            }

            def parked(me, world):
                return me.go_to((1.0, 1.0))

            def pick_play(world):
                world.show("play", world.play)
                return "park" if world.ball.pos[0] < 3 else "attack"
            """)
        state: dict[str, Any] = {}
        attack = world(ball=(5.0, 3.0, 0, 0), t=1.0)
        targets, attackers, errors = self.plan(team, attack, play_state=state)
        self.assertEqual(errors, [])
        self.assertEqual(state["blue"]["play"], "attack")
        self.assertEqual(len(attackers & {0, 1, 2}), 1)
        # The ball goes left: "park" takes over only after 0.25 s of asking.
        for t, expect in ((1.1, "attack"), (1.2, "attack"), (1.4, "park")):
            targets, _, _ = self.plan(team, world(ball=(2.0, 3.0, 0, 0), t=t), play_state=state)
            self.assertEqual(state["blue"]["play"], expect, t)
        self.assertEqual({targets[r][:2] for r in (0, 1, 2)}, {(1.0, 1.0)})  # every robot parked

    def test_plays_are_checked_when_loading(self):
        with self.assertRaisesRegex(TeamFileError, "needs a def pick_play"):
            self.load('PLAYS = {"attack": ["attacker"]}\n')
        with self.assertRaisesRegex(TeamFileError, "has the role 'striker'"):
            self.load('PLAYS = {"attack": ["striker"]}\n\ndef pick_play(world):\n    return "attack"\n')
        team = self.load('PLAYS = {"attack": ["attacker", "goalie"]}\n\ndef pick_play(world):\n    return "attack"\n')
        self.assertIn("PLAYS (attack)", replaced_slots(team))

    def test_team_commands_some_robots_and_roles_do_the_rest(self):
        team = self.load("""\
            def team(robots, world):
                return {robots[0]: robots[0].go_to((8.0, 1.0)), 2: world.teammates[2].stop()}
            """)
        targets, _, errors = self.plan(team)
        self.assertEqual(errors, [])
        self.assertEqual(targets[0][:2], (8.0, 1.0))
        reference, _, _ = decide(build_game_state(world()), sticky_attackers={})
        self.assertIn(1, targets)  # robot 1 still went through its role
        self.assertEqual(set(targets), set(reference))

    def test_a_broken_team_plan_falls_back_to_roles(self):
        team = self.load("""\
            def team(robots, world):
                return {7: robots[0].stop()}
            """)
        targets, _, errors = self.plan(team)
        self.assertTrue(any("team must use our robots" in e for e in errors), errors)
        self.assertEqual(targets, decide(build_game_state(world()), sticky_attackers={})[0])

    def test_kickoff_spots_only_before_the_kickoff_and_on_our_half(self):
        team = self.load("""\
            def kickoff(robots, world):
                return {r: (2.0, 1.0 + r.id) for r in robots if r.id != 1}
            """)
        before, _, errors = self.plan(team, kicked_off=False)
        self.assertEqual(errors, [])
        self.assertEqual((before[0][:2], before[2][:2]), ((2.0, 1.0), (2.0, 3.0)))
        after, _, _ = self.plan(team, kicked_off=True)
        self.assertNotEqual(after[0][:2], (2.0, 1.0))
        wrong = self.load("def kickoff(robots, world):\n    return {robots[0]: (8.0, 3.0)}\n")
        _, _, errors = self.plan(wrong, kicked_off=False)
        self.assertTrue(any("isn't on our half" in e for e in errors), errors)


class TestWorkspaces(unittest.TestCase):
    """A team as a folder of files (core/loader.py), safe mode for someone
    else's (core/safety.py), and matches with each team in its own process."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.workers: list[TeamWorker] = []

    def tearDown(self):
        for w in self.workers:
            w.close()
        self._tmp.cleanup()

    def workspace(self, name: str, files: dict[str, str], safe: bool = False) -> Path:
        ws = self.dir / name
        ws.mkdir(exist_ok=True)
        for file, code in files.items():
            (ws / file).write_text(textwrap.dedent(code), encoding="utf-8")
        if safe:
            (ws / "workspace.json").write_text(json.dumps({"safe": True}), encoding="utf-8")
        return ws

    def answer(self, ws: Path) -> dict[str, Any]:
        w = TeamWorker(ws, "blue", log=lambda _m: None)
        self.workers.append(w)
        return first_answer(w, world())

    def test_team_py_imports_the_workspace_files(self):
        ws = self.workspace("anna", {
            "team.py": "from spots import spot\n\ndef supporter(me, world):\n    return me.go_to(spot())\n",
            "spots.py": "def spot():\n    return (1.0, 5.0)\n",
        })
        self.assertEqual(replaced_slots(load_team(ws)), ["supporter"])
        team = TeamFile(ws)
        time.sleep(0.05)
        (ws / "spots.py").write_text("def spot():\n    return (2.0, 4.0)\n", encoding="utf-8")
        self.assertTrue(team.reload_if_changed())  # a helper changed: the team reloads
        gs = build_game_state(world())
        targets, _, _ = decide(gs, sticky_attackers={}, teams={"blue": team.team})
        self.assertIn((2.0, 4.0), [t[:2] for t in targets.values()])

    def test_errors_name_the_file_and_line(self):
        ws = self.workspace("ben", {
            "team.py": "from spots import spot\n\ndef supporter(me, world):\n    return me.go_to(spot())\n",
            "spots.py": "def spot():\n    return (1.0, 5.0\n",
        })
        with self.assertRaisesRegex(TeamFileError, r"^spots.py:\d+: syntax error"):
            load_team(ws)
        (ws / "spots.py").write_text("def spot():\n    return 1 / 0\n", encoding="utf-8")
        errors: list[str] = []
        decide(build_game_state(world()), sticky_attackers={}, errors=errors,
               teams={"blue": load_team(ws)})
        self.assertTrue(any("ZeroDivisionError" in e and "(spots.py:2)" in e for e in errors), errors)
        self.assertIsNotNone(module_name_problem("random.py"))  # would hide Python's random
        self.assertIsNotNone(module_name_problem("my-helpers.py"))  # can't be imported
        self.assertIsNone(module_name_problem("passing.py"))

    def test_no_team_py_is_a_clear_error(self):
        ws = self.workspace("empty", {"spots.py": "x = 1\n"})
        with self.assertRaisesRegex(TeamFileError, "has no team.py"):
            load_team(ws)

    def test_safe_mode_refuses_files_programs_and_imports(self):
        ws = self.workspace("from_a_friend", {"team.py": """\
            def supporter(me, world):
                open("written_by_a_team.txt", "w").write("x")
                return me.stop()

            def defender(me, world):
                import os
                return me.stop()

            def attacker(me, world):
                # The classic way around an import guard: find os.system on a class.
                cls = [c for c in ().__class__.__base__.__subclasses__() if c.__name__ == "_wrap_close"][0]
                cls.__init__.__globals__["system"]("echo hi")
                return me.stop()

            def prepare(world):
                import math, numpy, spots          # allowed
                from strategy.api import Me         # allowed
                world.memory["ok"] = math.pi
            """, "spots.py": "X = 1\n"}, safe=True)
        errors = self.answer(ws)["errors"]
        joined = "\n".join(errors)
        self.assertIn("supporter", joined)
        self.assertIn("SafeModeError", joined)
        self.assertIn("can't import os", joined)
        self.assertIn("starting programs isn't allowed", joined)
        self.assertNotIn("prepare", joined)
        self.assertFalse((ROOT / "written_by_a_team.txt").exists())

    def test_own_workspace_is_trusted(self):
        ws = self.workspace("mine", {"team.py": """\
            import os

            def supporter(me, world):
                world.show("cwd", os.getcwd())
                return me.stop()
            """})
        self.assertEqual(self.answer(ws)["errors"], [])

    def test_match_between_two_workspaces(self):
        a = self.workspace("a", {"team.py": "def supporter(me, world):\n    return me.go_to((1.0, 5.0))\n"})
        b = self.workspace("b", {"team.py": "def supporter(me, world):\n    return me.go_to(world.ball.pos)\n"},
                           safe=True)
        results = match.run_match(str(a), str(b), games=2, duration_s=3, seed=0)
        self.assertEqual(len(results), 2)
        self.assertEqual([r.errors_a + r.errors_b for r in results], [[], []])

    def test_a_team_in_its_own_process_plays_as_in_process(self):
        get_open = EXAMPLES / "get_open.py"
        here = match.play_game(decide, baseline_v1.decide, True, seed=3, duration_s=8,
                               team_a=load_team(get_open))
        worker = TeamWorker(get_open, "blue", log=lambda _m: None, lockstep=True)
        self.workers.append(worker)
        there = match.play_game(decide, baseline_v1.decide, True, seed=3, duration_s=8, team_a=worker)
        self.assertEqual((here.goals_a, here.goals_b, here.kicks_a, here.kicks_b, here.possession_a),
                         (there.goals_a, there.goals_b, there.kicks_a, there.kicks_b, there.possession_a))

    def test_a_team_that_hangs_is_given_up_on(self):
        ws = self.workspace("loops", {"team.py": "def supporter(me, world):\n    while True:\n        pass\n"})
        results = match.run_match(str(ws), "v1", games=1, duration_s=2, seed=0)
        self.assertTrue(any("hung 3 times" in e for e in results[0].errors_a), results[0].errors_a)


class TestMatch(unittest.TestCase):
    def test_v2_plays_like_the_built_in_team(self):
        """baseline_v2 is a frozen copy of today's built-in team (strategy step
        2). When the built-in team changes on purpose, drop this test: v2 stays
        the bar."""
        from strategy.eval import baseline_v2

        gs = build_game_state(world(ball=(4.2, 2.6, 1.2, -0.6)))  # rolling: the attacker meets it
        self.assertEqual(decide(gs, sticky_attackers={}), baseline_v2.decide(gs, sticky_attackers={}))
        a = match.play_game(decide, decide, True, seed=4, duration_s=15)
        b = match.play_game(baseline_v2.decide, baseline_v2.decide, True, seed=4, duration_s=15)
        self.assertEqual(a, b)

    def test_v1_still_plays(self):
        """baseline_v1, the frozen built-in team of 2026-11-20 (before the
        attacker met rolling balls), stays the bar to beat: it still plays."""
        r = match.play_game(baseline_v1.decide, baseline_v1.decide, True, seed=4, duration_s=10)
        self.assertGreater(r.ticks, 0)

    def test_goal_goes_to_the_team_that_scored(self):
        """Blue attacks +x: a ball into the x = FIELD_W goal (red's) is blue's goal."""
        from pymunk_world import PymunkWorld, scorer

        from strategy.world.simconfig import FIELD_H, FIELD_W

        w = PymunkWorld()
        w.reset_ball((FIELD_W - 0.3, FIELD_H / 2))
        w.ball.velocity = (3.0, 0.0)
        goal = None
        for _ in range(60):
            goal = w.step()["scoring_team"] or goal
        self.assertEqual(goal, "red")  # whose goal it went into
        self.assertEqual(scorer(goal), "blue")  # who gets the point

    def test_games_are_reproducible(self):
        a = match.play_game(decide, decide, False, seed=7, duration_s=5)
        b = match.play_game(decide, decide, False, seed=7, duration_s=5)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
