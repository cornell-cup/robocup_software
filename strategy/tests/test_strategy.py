"""Unit tests for the strategy: world state, prediction, decide(), Brain, matches.

From the repo root (no hardware, no running sim):

    uv run python -m unittest discover -s strategy/tests -t .
"""

from __future__ import annotations

import math
import unittest
from typing import Any

import numpy as np

from strategy import strategy_node
from strategy.analysis import analyze, ball_intercept, drive_accel, time_to_reach
from strategy.eval import baseline_v0, match
from strategy.prediction import (
    ball_rest_position,
    predict_ball_position,
    predict_ball_velocity,
)
from strategy.state import (
    GOAL_Y_MAX,
    GOAL_Y_MIN,
    ROBOT_RADIUS,
    BallState,
    RobotState,
    build_game_state,
)
from strategy.strategy_node import Brain, decide, resolve_kick_conflict
from strategy.tactics.attacker import CONTESTED_SKEW, attack
from strategy.tactics.defender import defend_goal
from strategy.tactics.role_assignments import ATTACKER, DEFENDER, SUPPORTER, assign_roles

from config import BALL_DAMP, DT, FIELD_IS_DEFAULT  # sim/config.py, on sys.path via strategy

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
        from config import MOTOR_MAX_FORCE, ROBOT_MASS

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

    def test_kick_conflict_keeps_closer(self):
        robots = {1: (4.0, 3.0, 0.0), 4: (5.2, 3.0, math.pi)}
        gs = build_game_state(world(robots, ball=(4.4, 3.0, 0, 0)))
        self.assertEqual(resolve_kick_conflict([4, 1], gs), [1])
        self.assertEqual(resolve_kick_conflict([4], gs), [4])


class TestTactics(unittest.TestCase):
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
        command = attack(gs.blue[1], gs, "blue")
        self.assertTrue(command.kick)
        self.assertAlmostEqual(command.angle, CONTESTED_SKEW)

    def test_classic_attacker_gets_behind_the_ball(self):
        gs = build_game_state(world(ball=(5.0, 3.0, 0, 0)))  # nobody near it
        command = attack(gs.blue[1], gs, "blue")
        self.assertFalse(command.kick)
        self.assertLess(command.x, 5.0)  # blue attacks +x: stands on the -x side
        command = attack(gs.red[1], gs, "red")
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
        self.assertEqual(plan.dribble, {0: False, 1: True, 2: False})

    def test_human_robots_left_alone(self):
        brain = Brain("blue")
        brain.handle_control({"human_blue_ids": [0]})
        plan = brain.tick(world())
        self.assertNotIn(0, plan.targets)
        self.assertNotIn(0, plan.dribble)


@unittest.skipUnless(FIELD_IS_DEFAULT, "v0 equivalence holds on the 9 x 6 field")
class TestMatch(unittest.TestCase):
    def test_current_plays_like_v0_on_default_field(self):
        """No change so far alters a pure-sim game on 9 x 6: same game, same result.
        When a later step changes how it plays on purpose, drop this test."""
        a = match.play_game(strategy_node.decide, baseline_v0.decide, True, seed=3, duration_s=15)
        b = match.play_game(baseline_v0.decide, baseline_v0.decide, True, seed=3, duration_s=15)
        self.assertEqual(
            (a.goals_a, a.goals_b, a.possession_a, a.possession_b, a.kicks_a, a.kicks_b, a.attacking_a),
            (b.goals_a, b.goals_b, b.possession_a, b.possession_b, b.kicks_a, b.kicks_b, b.attacking_a),
        )

    def test_games_are_reproducible(self):
        a = match.play_game(decide, decide, False, seed=7, duration_s=5)
        b = match.play_game(decide, decide, False, seed=7, duration_s=5)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
