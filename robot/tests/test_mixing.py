import unittest

from firmware import mixing
from firmware.mixing import compute_wheel_efforts

L, R, M4 = 0, 1, 2   # wheel indices, same order as MOTORS in robot.py


class WheelMixingTest(unittest.TestCase):
    # Test the geometry with every wheel unflipped; WHEEL_SIGNS is per robot.
    def setUp(self):
        self._signs = mixing.WHEEL_SIGNS
        mixing.WHEEL_SIGNS = [1, 1, 1]

    def tearDown(self):
        mixing.WHEEL_SIGNS = self._signs

    def assertEfforts(self, actual, expected):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=6)

    def test_stopped(self):
        self.assertEfforts(compute_wheel_efforts(0, 0, 0), [0, 0, 0])

    def test_forward_uses_only_the_front_pair(self):
        # Motor 4 sits at the back, rolling sideways, so it idles; L and R
        # (front-right and front-left) spin in opposite directions.
        efforts = compute_wheel_efforts(0, 0.5, 0)
        self.assertAlmostEqual(efforts[M4], 0)
        self.assertAlmostEqual(efforts[L], -efforts[R])
        self.assertGreater(efforts[L], 0)

    def test_strafe_right_drives_back_wheel_hardest(self):
        efforts = compute_wheel_efforts(0.5, 0, 0)
        self.assertEfforts(efforts, [-0.25, -0.25, 0.5])

    def test_pure_rotation_spins_all_wheels_the_same(self):
        efforts = compute_wheel_efforts(0, 0, 1)
        self.assertEfforts(efforts, [mixing.ROTATION_GAIN] * 3)

    def test_oversaturated_input_is_scaled_without_changing_direction(self):
        small = compute_wheel_efforts(0.1, 0.1, 0.1)
        big = compute_wheel_efforts(1, 1, 1)
        self.assertAlmostEqual(max(abs(e) for e in big), 1.0)
        scale = big[0] / small[0]
        self.assertEfforts(big, [e * scale for e in small])

    def test_wheel_sign_flips_one_wheel_only(self):
        base = compute_wheel_efforts(0.3, 0.6, 0.2)
        mixing.WHEEL_SIGNS = [1, -1, 1]
        flipped = compute_wheel_efforts(0.3, 0.6, 0.2)
        self.assertEfforts(flipped, [base[0], -base[1], base[2]])

    def test_one_angle_and_sign_per_wheel(self):
        self.assertEqual(len(mixing.WHEEL_ANGLES), len(mixing.WHEEL_SIGNS))


if __name__ == "__main__":
    unittest.main()
