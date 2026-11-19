import json
import math
import random
import unittest

from laptop.motion_profile import (
    DEFAULT_SPEEDS,
    MOVE_M,
    TESTS,
    TESTS_BY_NAME,
    Sample,
    TurnCounter,
    analyse,
)

HZ = 30.0
T_START = 1.0


def drive_path(direction_deg=0.0, theta0=0.0, x0=1.0, y0=0.6, speed=0.5,
               delay=0.1, rise=0.2, drive=1.0, coast=0.3, drift_deg=0.0,
               heading_rate_deg=0.0, spin_rate_deg=0.0, wander=0.0,
               noise=0.0, gap=None, seed=1):
    """Made-up camera samples of a robot told to move at T_START and to stop
    at T_START + drive. It starts moving `delay` later, ramps up to `speed`
    (m/s, or `spin_rate_deg` deg/s for a spin) over `rise` seconds, and slows
    to rest over `coast` seconds after the stop. A straight move heads
    `direction_deg + drift_deg` from the robot's starting heading; `gap`
    = (from, to) drops the samples in that window."""
    rng = random.Random(seed)
    t_stop = T_START + drive
    t_move = T_START + delay

    def level(t):
        """0..1: how much of full speed the robot has at time t."""
        if t < t_move:
            return 0.0
        if t < t_move + rise:
            return (t - t_move) / rise
        if t < t_stop:
            return 1.0
        if t < t_stop + coast:
            return 1.0 - (t - t_stop) / coast
        return 0.0

    samples, dist, turned = [], 0.0, 0.0
    t, dt = 0.0, 1.0 / HZ
    while t <= t_stop + coast + 0.6:
        if not (gap and gap[0] <= t < gap[1]):
            a = theta0 + math.radians(direction_deg + drift_deg)
            # A spinning robot whose centre circles round `wander` metres off.
            wx = wander * (math.cos(turned) - 1.0)
            wy = wander * math.sin(turned)
            samples.append(Sample(
                t,
                x0 + dist * math.cos(a) + wx + rng.gauss(0, noise),
                y0 + dist * math.sin(a) + wy + rng.gauss(0, noise),
                math.atan2(math.sin(theta0 + turned), math.cos(theta0 + turned)),
            ))
        step = level(t + dt / 2) * dt
        dist += speed * step
        turned += math.radians(heading_rate_deg) * step + math.radians(spin_rate_deg) * step
        t += dt
    return samples, t_stop


def first_cm(delay, speed, rise):
    """When drive_path's robot has moved MOVE_M: the delay, then speeding up
    evenly to `speed` over `rise` (distance speed t^2 / 2 rise)."""
    return delay + math.sqrt(2 * rise * MOVE_M / speed)


class CommandTest(unittest.TestCase):
    def test_straight_moves_send_no_turn(self):
        self.assertEqual(TESTS_BY_NAME["forward"].command(0.7), (0.7, 0.0, 0.0))
        self.assertEqual(TESTS_BY_NAME["strafe_left"].command(0.7), (0.0, 0.7, 0.0))
        self.assertEqual(TESTS_BY_NAME["strafe_right"].command(0.4), (0.0, -0.4, 0.0))
        self.assertEqual(TESTS_BY_NAME["backward"].command(1.0), (-1.0, 0.0, 0.0))

    def test_diagonal_splits_the_speed(self):
        f, l, turn = TESTS_BY_NAME["diagonal_right"].command(1.0)
        self.assertAlmostEqual(f, math.sqrt(0.5))
        self.assertAlmostEqual(l, -math.sqrt(0.5))
        self.assertEqual(turn, 0.0)

    def test_spins_only_turn(self):
        self.assertEqual(TESTS_BY_NAME["spin_ccw"].command(0.4), (0.0, 0.0, 0.4))
        self.assertEqual(TESTS_BY_NAME["spin_cw"].command(0.4), (0.0, 0.0, -0.4))

    def test_every_test_has_a_unique_name(self):
        self.assertEqual(len(TESTS_BY_NAME), len(TESTS))
        self.assertTrue(all(0 < s <= 1 for s in DEFAULT_SPEEDS))


class ReachedTest(unittest.TestCase):
    def test_straight_move_ends_at_its_distance(self):
        test = TESTS_BY_NAME["forward"]
        start = Sample(0, 1.0, 1.0, 0.0)
        self.assertFalse(test.reached(start, Sample(1, 1.49, 1.0, 0.0), 0.0))
        self.assertTrue(test.reached(start, Sample(1, 1.0, 1.5, 0.0), 0.0))

    def test_spin_ends_after_a_full_turn_not_when_heading_comes_back(self):
        test = TESTS_BY_NAME["spin_ccw"]
        start = Sample(0, 1.0, 1.0, 0.0)
        counter = TurnCounter(0.0)
        reached_at = None
        for i in range(1, 40):
            theta = math.atan2(math.sin(i * 0.2), math.cos(i * 0.2))
            if test.reached(start, Sample(i, 1.0, 1.0, theta), counter.add(theta)):
                reached_at = i
                break
        self.assertEqual(reached_at, 32)  # 32 * 0.2 rad > 2 pi > 31 * 0.2


class StraightRunTest(unittest.TestCase):
    def run_test(self, name, **path):
        samples, t_stop = drive_path(**path)
        return analyse(TESTS_BY_NAME[name], 0.7, samples, T_START, t_stop)

    def test_clean_forward_run(self):
        r = self.run_test("forward", speed=0.5, delay=0.1, rise=0.2, drive=1.0, coast=0.3)
        self.assertTrue(r.moved)
        self.assertEqual(r.notes, [])
        # The 0.1 s delay, plus speeding up through the first MOVE_M.
        self.assertAlmostEqual(r.reaction_s, first_cm(0.1, 0.5, 0.2), delta=1.5 / HZ)
        self.assertAlmostEqual(r.rate, 0.5, delta=0.01)
        # From the first MOVE_M to 90% of full speed, 0.9 of the way up the ramp.
        self.assertAlmostEqual(r.rise_s, 0.1 + 0.9 * 0.2 - first_cm(0.1, 0.5, 0.2), delta=1.5 / HZ)
        self.assertAlmostEqual(r.coast_m, 0.5 * 0.3 / 2, delta=0.01)
        self.assertAlmostEqual(r.coast_s, 0.3, delta=0.1)
        self.assertAlmostEqual(r.drift_m, 0.0, delta=1e-6)
        self.assertAlmostEqual(r.heading_change_deg, 0.0, delta=1e-6)
        # Drove 0.05 s ramp-up short of 1.0 s at 0.5 m/s, plus the coast.
        self.assertAlmostEqual(r.along_m, 0.5 * (0.9 - 0.1) + 0.075, delta=0.01)

    def test_measures_acceleration(self):
        # 0.5 m/s reached evenly over 0.2 s: 2.5 m/s^2.
        r = self.run_test("forward", speed=0.5, rise=0.2, noise=0.001, seed=4)
        self.assertAlmostEqual(r.accel, 2.5, delta=0.3)

    def test_drift_to_the_right_is_negative(self):
        r = self.run_test("forward", drift_deg=-3.0)
        self.assertLess(r.drift_m, 0)
        self.assertAlmostEqual(r.direction_error_deg, -3.0, delta=0.05)
        self.assertAlmostEqual(r.drift_per_m, math.tan(math.radians(-3.0)), delta=1e-3)
        self.assertAlmostEqual(r.max_deviation_m, abs(r.drift_m), delta=1e-6)

    def test_robot_facing_up_the_field_still_reads_forward(self):
        r = self.run_test("forward", theta0=math.radians(90))
        self.assertGreater(r.along_m, 0.4)
        self.assertAlmostEqual(r.drift_m, 0.0, delta=1e-6)

    def test_strafe_left_measures_along_the_strafe(self):
        r = self.run_test("strafe_left", direction_deg=90, drift_deg=2.0, theta0=math.radians(30))
        self.assertGreater(r.along_m, 0.4)
        self.assertAlmostEqual(r.direction_error_deg, 2.0, delta=0.05)

    def test_heading_drift_while_driving_straight(self):
        r = self.run_test("forward", heading_rate_deg=10.0)
        self.assertGreater(r.heading_change_deg, 5.0)
        self.assertAlmostEqual(r.drift_m, 0.0, delta=1e-6)

    def test_camera_noise_barely_moves_the_numbers(self):
        r = self.run_test("forward", noise=0.002, seed=7)
        self.assertTrue(r.moved)
        self.assertAlmostEqual(r.rate, 0.5, delta=0.03)
        self.assertAlmostEqual(r.drift_m, 0.0, delta=0.005)
        self.assertAlmostEqual(r.reaction_s, first_cm(0.1, 0.5, 0.2), delta=0.1)

    def test_robot_that_never_moves(self):
        r = self.run_test("forward", speed=0.0)
        self.assertFalse(r.moved)
        self.assertIsNone(r.reaction_s)
        self.assertIn("never moved", r.notes)

    def test_lost_tag_is_noted(self):
        r = self.run_test("forward", gap=(1.4, 1.8))
        self.assertTrue(r.moved)
        self.assertGreater(r.longest_gap_s, 0.35)
        self.assertTrue(any("lost the tag" in n for n in r.notes))

    def test_no_samples_before_the_start(self):
        samples, t_stop = drive_path()
        r = analyse(TESTS_BY_NAME["forward"], 0.7, [s for s in samples if s.t > T_START],
                    T_START, t_stop)
        self.assertFalse(r.moved)
        self.assertIn("too few camera samples", r.notes)

    def test_result_is_plain_json(self):
        r = self.run_test("forward", noise=0.001)
        d = json.loads(json.dumps(r.to_dict()))
        self.assertEqual(d["test"], "forward")
        self.assertEqual(d["speed"], 0.7)
        self.assertTrue(d["moved"])


class SpinRunTest(unittest.TestCase):
    def run_spin(self, name, **path):
        samples, t_stop = drive_path(speed=0.0, **path)
        return analyse(TESTS_BY_NAME[name], 0.7, samples, T_START, t_stop)

    def test_spin_measures_turn_rate_and_full_turns(self):
        r = self.run_spin("spin_ccw", spin_rate_deg=180.0, drive=2.0)
        self.assertTrue(r.moved)
        self.assertAlmostEqual(r.rate, 180.0, delta=3.0)
        # More than a full turn: unwrapped, not folded back into +-180.
        self.assertGreater(r.heading_change_deg, 300.0)
        self.assertAlmostEqual(r.max_deviation_m, 0.0, delta=1e-6)
        self.assertIsNone(r.along_m)

    def test_clockwise_spin_reads_a_positive_rate(self):
        r = self.run_spin("spin_cw", spin_rate_deg=-120.0)
        self.assertAlmostEqual(r.rate, 120.0, delta=3.0)
        self.assertLess(r.heading_change_deg, 0)

    def test_spin_centre_wander(self):
        r = self.run_spin("spin_ccw", spin_rate_deg=180.0, drive=2.0, wander=0.03)
        self.assertGreater(r.max_deviation_m, 0.05)

    def test_spin_the_wrong_way_has_no_steady_rate(self):
        r = self.run_spin("spin_ccw", spin_rate_deg=-120.0)
        self.assertIsNone(r.rate)
        self.assertTrue(any("no steady speed" in n for n in r.notes))


if __name__ == "__main__":
    unittest.main()
