import unittest

from laptop.fake_robot import Camera, FakeWorld, Flaws
from laptop.motion_profile import TESTS_BY_NAME
from laptop.motion_runner import MotionRunner, RunnerError

FORWARD, BACKWARD = TESTS_BY_NAME["forward"], TESTS_BY_NAME["backward"]
STRAFE_LEFT, SPIN = TESTS_BY_NAME["strafe_left"], TESTS_BY_NAME["spin_ccw"]
CORNERS = [(0, 0.0, 0.0), (1, 1.93, 0.0), (2, 1.93, 1.32), (3, 0.0, 1.32)]


def runner_for(world, tests, speeds=(0.7,), repeats=1, sleep=None, **kw):
    return MotionRunner(world.fleet, world.feed, world.name, tests, speeds, repeats,
                        clock=world.clock, sleep=sleep or world.sleep, **kw)


def started(world):
    """A world whose camera has sent its first message."""
    world.sleep(0.1)
    return world


class PreconditionTest(unittest.TestCase):
    def assert_refused(self, runner, words):
        with self.assertRaises(RunnerError) as caught:
            runner.run()
        self.assertIn(words, str(caught.exception))
        self.assertEqual(runner.status()["state"], "error")

    def test_unknown_robot(self):
        world = started(FakeWorld())
        runner = MotionRunner(world.fleet, world.feed, "XRP-99", [FORWARD], [0.7],
                              clock=world.clock, sleep=world.sleep)
        self.assert_refused(runner, "isn't connected")

    def test_no_camera(self):
        world = FakeWorld(camera=Camera(lag_s=1.0))  # nothing through yet
        self.assert_refused(runner_for(world, [FORWARD]), "no camera")

    def test_no_robot_tag(self):
        world = started(FakeWorld(camera=Camera(lost=[(0, 99)], other_tags=CORNERS)))
        self.assert_refused(runner_for(world, [FORWARD]), "sees no robot tag")

    def test_several_robot_tags(self):
        world = started(FakeWorld(camera=Camera(other_tags=[(9, 0.5, 0.5)])))
        self.assert_refused(runner_for(world, [FORWARD]), "several robot tags (7, 9)")

    def test_corner_tags_are_not_robots(self):
        world = started(FakeWorld(camera=Camera(other_tags=CORNERS)))
        runner = runner_for(world, [FORWARD])
        runner.run()
        self.assertEqual(runner.tag_id, 7)


class RunTest(unittest.TestCase):
    def test_runs_the_plan_in_order(self):
        world = started(FakeWorld(camera=Camera(noise_m=0.001, noise_deg=0.3)))
        runner = runner_for(world, [FORWARD, BACKWARD], speeds=(0.4, 0.7), repeats=2)
        records = runner.run()
        self.assertEqual([(r.test.name, r.speed, r.repeat) for r in records], [
            ("forward", 0.4, 1), ("backward", 0.4, 1), ("forward", 0.4, 2), ("backward", 0.4, 2),
            ("forward", 0.7, 1), ("backward", 0.7, 1), ("forward", 0.7, 2), ("backward", 0.7, 2),
        ])
        self.assertTrue(all(r.ended == "reached" for r in records))
        self.assertTrue(all(r.result.moved for r in records))
        for r in records:
            self.assertAlmostEqual(r.result.rate, r.speed * 0.5, delta=0.02)
        status = runner.status()
        self.assertEqual((status["state"], status["done"], status["total"]), ("done", 8, 8))
        self.assertFalse(world.fleet.status()[0]["moving"])

    def test_spin_ends_after_a_full_turn(self):
        world = started(FakeWorld())
        record = runner_for(world, [SPIN]).run()[0]
        self.assertEqual(record.ended, "reached")
        self.assertGreater(record.result.heading_change_deg, 355)

    def test_robot_that_cannot_move_times_out(self):
        world = started(FakeWorld(Flaws(dead_band=0.9)))
        record = runner_for(world, [FORWARD]).run()[0]
        self.assertEqual(record.ended, "timeout")
        self.assertFalse(record.result.moved)
        self.assertAlmostEqual(record.t_stop - record.t_start, FORWARD.timeout_s, delta=0.05)

    def test_record_is_plain_json(self):
        import json
        world = started(FakeWorld())
        d = json.loads(json.dumps(runner_for(world, [FORWARD]).run()[0].to_dict()))
        self.assertEqual((d["test"], d["ended"]), ("forward", "reached"))
        self.assertGreater(len(d["path"]), 20)
        self.assertLess(d["path"][0][0], 0)  # samples from before the command


class SafetyTest(unittest.TestCase):
    def test_stops_when_the_camera_loses_the_tag(self):
        world = started(FakeWorld(camera=Camera(lost=[(1.2, 99)])))
        runner = runner_for(world, [FORWARD])
        record = runner.run()[0]
        self.assertEqual(record.ended, "tag_lost")
        # Stopped within LOST_STOP_S plus a command period of losing it.
        self.assertLess(record.t_stop, 1.2 + 0.3 + 0.05)
        self.assertFalse(world.fleet.status()[0]["moving"])

    def test_stops_short_of_the_edge(self):
        # Facing +x with room to spare, but drifting hard towards y = 0.
        world = started(FakeWorld(Flaws(drift_deg=-50.0), y=0.45))
        record = runner_for(world, [FORWARD]).run()[0]
        self.assertEqual(record.ended, "edge")
        self.assertGreater(world.y, 0.1)

    def test_abort_stops_the_robot_and_the_plan(self):
        world = started(FakeWorld())
        holder = {}

        def sleep(dt):
            world.sleep(dt)
            if world.clock() > 2.0:
                holder["runner"].abort()

        runner = runner_for(world, [FORWARD, BACKWARD], speeds=(0.4,), sleep=sleep)
        holder["runner"] = runner
        records = runner.run()
        self.assertEqual(records[-1].ended, "aborted")
        self.assertEqual(runner.status()["state"], "aborted")
        self.assertLess(len(records), 2)
        self.assertFalse(world.fleet.status()[0]["moving"])


class RepositionTest(unittest.TestCase):
    def test_asks_for_room_and_waits_until_it_has_it(self):
        # Near the right edge, facing it: no room to go forward.
        world = started(FakeWorld(x=1.6))
        holder, asked = {}, []

        def sleep(dt):
            world.sleep(dt)
            status = holder["runner"].status()
            if status["state"] == "reposition":
                asked.append(status["message"])
                if len(asked) == 25:     # half a second later, someone moves it
                    world.x = 0.6

        runner = runner_for(world, [FORWARD], sleep=sleep)
        holder["runner"] = runner
        record = runner.run()[0]
        self.assertTrue(asked)
        self.assertIn("Forward at 0.7", asked[0])
        self.assertEqual(record.ended, "reached")
        self.assertAlmostEqual(record.samples[0].x, 0.6, delta=0.01)

    def test_forward_then_backward_has_room_without_help(self):
        world = started(FakeWorld())
        runner = runner_for(world, [FORWARD, BACKWARD])
        records = runner.run()
        self.assertEqual([r.ended for r in records], ["reached", "reached"])

    def test_abort_while_waiting_for_room(self):
        world = started(FakeWorld(x=1.7))
        holder = {}

        def sleep(dt):
            world.sleep(dt)
            if world.clock() > 3.0:
                holder["runner"].abort()

        runner = runner_for(world, [FORWARD], sleep=sleep)
        holder["runner"] = runner
        records = runner.run()
        self.assertEqual(records[0].ended, "aborted")
        self.assertIsNone(records[0].result)


if __name__ == "__main__":
    unittest.main()
