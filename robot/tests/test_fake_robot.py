import math
import unittest

from laptop.fake_robot import Camera, FakeWorld, Flaws
from laptop.fleet import COMMAND_TIMEOUT_S
from laptop.motion_profile import TESTS_BY_NAME, Sample, TurnCounter, analyse
from laptop.protocol import body_axes

COMMAND_HZ = 50.0


def drive_for(world, forward, left, turn, seconds):
    """Drive the fake as the runner will: the same command, 50 times a second."""
    end = world.clock() + seconds
    while world.clock() < end - 1e-9:
        world.fleet.drive(world.name, body_axes(forward, left, turn), [])
        world.sleep(1.0 / COMMAND_HZ)


def seen(world):
    """The fake's tag in the newest camera message, or None."""
    doc = world.feed.snapshot()["doc"]
    return next((t for t in doc["tags"] if t["id"] == world.tag_id), None)


def run_test(world, name, speed=0.7):
    """One motion test end to end: a sketch of what the runner will do."""
    test = TESTS_BY_NAME[name]
    samples, last_n = [], -1

    def record():
        nonlocal last_n
        snap = world.feed.snapshot()
        if snap["doc"] is not None and snap["n"] != last_n:
            last_n = snap["n"]
            tag = next((t for t in snap["doc"]["tags"] if t["id"] == world.tag_id), None)
            if tag:
                samples.append(Sample(snap["doc"]["t_tags"], tag["x"], tag["y"],
                                      math.radians(tag["theta_deg"])))

    for _ in range(int(0.3 * COMMAND_HZ)):  # the start pose
        world.sleep(1.0 / COMMAND_HZ)
        record()
    start = samples[-1]
    counter = TurnCounter(start.theta)
    t_start = world.clock()
    command = test.command(speed)
    while world.clock() - t_start < test.timeout_s:
        world.fleet.drive(world.name, body_axes(*command), [])
        world.sleep(1.0 / COMMAND_HZ)
        n = len(samples)
        record()
        for s in samples[n:]:
            counter.add(s.theta)
        if test.reached(start, samples[-1], counter.total):
            break
    t_stop = world.clock()
    world.fleet.stop(world.name)
    for _ in range(int(0.8 * COMMAND_HZ)):  # let it settle
        world.sleep(1.0 / COMMAND_HZ)
        record()
    return analyse(test, speed, samples, t_start, t_stop)


class FakeRobotTest(unittest.TestCase):
    def test_drives_forward_the_way_it_faces(self):
        world = FakeWorld(theta_deg=90.0)
        drive_for(world, 1.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(world.x, 0.96, delta=1e-6)
        # 0.5 m/s, less the 0.0625 s it spends speeding up at 4 m/s^2.
        self.assertAlmostEqual(world.y - 0.66, 0.5 * (1.0 - 0.0625), delta=0.005)

    def test_left_is_to_its_left(self):
        world = FakeWorld(theta_deg=0.0)
        drive_for(world, 0.0, 1.0, 0.0, 0.5)
        self.assertGreater(world.y, 0.66 + 0.2)
        self.assertAlmostEqual(world.x, 0.96, delta=1e-6)

    def test_positive_turn_is_counter_clockwise(self):
        world = FakeWorld()
        drive_for(world, 0.0, 0.0, 0.5, 0.5)
        self.assertGreater(world.theta, math.radians(40))

    def test_stops_when_commands_stop(self):
        world = FakeWorld()
        world.fleet.drive(world.name, body_axes(1.0, 0.0, 0.0), [])
        world.sleep(1.0)
        # Only moved until the timeout, then braked.
        self.assertLess(world.x - 0.96, 0.5 * (COMMAND_TIMEOUT_S + 0.1))
        x = world.x
        world.sleep(0.5)
        self.assertAlmostEqual(world.x, x, delta=1e-9)

    def test_stop_is_immediate(self):
        world = FakeWorld()
        drive_for(world, 1.0, 0.0, 0.0, 0.5)
        world.fleet.stop(world.name)
        self.assertFalse(world.fleet.status()[0]["moving"])

    def test_other_names_are_not_this_robot(self):
        world = FakeWorld()
        with self.assertRaises(KeyError):
            world.fleet.drive("XRP-99", body_axes(1.0, 0.0, 0.0), [])

    def test_camera_rate_and_lag(self):
        world = FakeWorld(camera=Camera(rate_hz=30.0, lag_s=0.1))
        self.assertIsNone(world.feed.snapshot()["doc"])  # nothing through the lag yet
        world.sleep(0.5)
        n0 = world.feed.snapshot()["n"]
        world.sleep(1.0)
        snap = world.feed.snapshot()
        self.assertAlmostEqual(snap["n"] - n0, 30, delta=1)
        self.assertAlmostEqual(world.clock() - snap["doc"]["t_tags"], 0.1, delta=1.0 / 30 + 1e-6)

    def test_camera_loses_the_tag(self):
        world = FakeWorld(camera=Camera(lost=[(0.5, 1.0)], other_tags=[(0, 0.0, 0.0)]))
        world.sleep(0.7)
        self.assertIsNone(seen(world))
        self.assertEqual([t["id"] for t in world.feed.snapshot()["doc"]["tags"]], [0])
        world.sleep(0.5)
        self.assertIsNotNone(seen(world))

    def test_noise_is_repeatable_with_a_seed(self):
        a = FakeWorld(camera=Camera(noise_m=0.002), seed=3)
        b = FakeWorld(camera=Camera(noise_m=0.002), seed=3)
        a.sleep(0.5)
        b.sleep(0.5)
        self.assertEqual(seen(a), seen(b))


class FindsFlawsTest(unittest.TestCase):
    """The fake's flaws, measured through the motion tests."""

    def test_clean_robot(self):
        r = run_test(FakeWorld(camera=Camera(noise_m=0.001, noise_deg=0.3)), "forward")
        self.assertTrue(r.moved)
        self.assertEqual(r.notes, [])
        self.assertAlmostEqual(r.rate, 0.7 * 0.5, delta=0.01)
        self.assertAlmostEqual(r.along_m, 0.5, delta=0.05)
        self.assertLess(abs(r.drift_m), 0.005)

    def test_finds_drift(self):
        r = run_test(FakeWorld(Flaws(drift_deg=-4.0)), "forward")
        self.assertAlmostEqual(r.direction_error_deg, -4.0, delta=0.2)

    def test_finds_a_curve(self):
        r = run_test(FakeWorld(Flaws(curve_deg_per_m=20.0)), "forward")
        self.assertAlmostEqual(r.heading_change_deg, 20.0 * r.along_m, delta=1.0)

    def test_finds_slow_strafing(self):
        fwd = run_test(FakeWorld(Flaws(strafe_factor=0.6)), "forward")
        side = run_test(FakeWorld(Flaws(strafe_factor=0.6)), "strafe_left")
        self.assertAlmostEqual(side.rate / fwd.rate, 0.6, delta=0.03)

    def test_finds_slow_clockwise_turning(self):
        ccw = run_test(FakeWorld(Flaws(cw_factor=0.7)), "spin_ccw")
        cw = run_test(FakeWorld(Flaws(cw_factor=0.7)), "spin_cw")
        self.assertAlmostEqual(cw.rate / ccw.rate, 0.7, delta=0.03)
        self.assertGreater(abs(cw.heading_change_deg), 350)

    def test_finds_a_dead_band(self):
        world = FakeWorld(Flaws(dead_band=0.5))
        self.assertFalse(run_test(world, "forward", speed=0.4).moved)
        self.assertTrue(run_test(world, "forward", speed=0.7).moved)

    def test_finds_lag(self):
        quick = run_test(FakeWorld(), "forward")
        late = run_test(FakeWorld(Flaws(lag_s=0.15)), "forward")
        self.assertAlmostEqual(late.reaction_s - quick.reaction_s, 0.15, delta=0.04)

    def test_camera_lag_is_not_robot_lag(self):
        # Samples carry their capture time, so a slow camera doesn't make
        # the robot look slow to react.
        quick = run_test(FakeWorld(), "forward")
        slow_cam = run_test(FakeWorld(camera=Camera(lag_s=0.1)), "forward")
        self.assertAlmostEqual(slow_cam.reaction_s, quick.reaction_s, delta=0.04)

    def test_finds_coasting(self):
        r = run_test(FakeWorld(Flaws(accel=1.0)), "forward")
        # From 0.35 m/s at 1 m/s^2: 0.35^2 / 2 metres, 0.35 seconds.
        self.assertAlmostEqual(r.coast_m, 0.35 ** 2 / 2, delta=0.02)
        self.assertAlmostEqual(r.coast_s, 0.35, delta=0.1)

    def test_finds_a_wobbly_spin(self):
        steady = run_test(FakeWorld(), "spin_ccw")
        wobbly = run_test(FakeWorld(Flaws(wander_m=0.04)), "spin_ccw")
        self.assertLess(steady.max_deviation_m, 0.005)
        self.assertGreater(wobbly.max_deviation_m, 0.06)

    def test_lost_tag_shows_in_the_run(self):
        r = run_test(FakeWorld(camera=Camera(lost=[(0.8, 1.2)])), "forward")
        self.assertTrue(any("lost the tag" in n for n in r.notes))


if __name__ == "__main__":
    unittest.main()
