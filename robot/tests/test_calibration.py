import json
import unittest

from laptop.calibration import Compensator, fit_profile, fleet_scale


def record(test, speed, rate, ended="reached"):
    return {"test": test, "speed": speed, "ended": ended,
            "result": {"moved": True, "rate": rate}}


def profile(factor=1.0):
    names = ("forward", "backward", "strafe_left", "strafe_right",
             "spin_ccw", "spin_cw")
    return {"robot": "test", "records": [
        record(name, speed, factor * speed)
        for name in names for speed in (0.4, 0.7, 1.0)
    ]}


class CalibrationTest(unittest.TestCase):
    def test_profile_roundtrip_and_repeated_speed(self):
        data = profile()
        data["records"].append(record("forward", 0.4, 0.6))
        model = fit_profile(json.loads(json.dumps(data)))
        self.assertAlmostEqual(model.axes["forward_pos"].rate(0.4), 0.5)
        self.assertAlmostEqual(model.axes["forward_pos"].rate(0.7), 0.7)

    def test_excludes_interrupted_and_missing_measurements(self):
        data = profile()
        data["records"].append(record("forward", 0.7, 99, "tag_lost"))
        data["records"].append({"test": "forward", "speed": 0.7, "result": None})
        model = fit_profile(data)
        self.assertAlmostEqual(model.axes["forward_pos"].rate(0.7), 0.7)

    def test_calibration_matches_reference_at_measured_levels(self):
        reference = fit_profile(profile())
        robot = fit_profile(profile(0.5))
        scale = fleet_scale([robot], reference, margin=1.0)
        self.assertAlmostEqual(scale, 0.5)
        compensate = Compensator(robot, reference, scale)
        self.assertAlmostEqual(compensate(0.7, 0, 0)[0], 0.7)
        self.assertEqual(compensate(0, 0, 0), (0, 0, 0))

    def test_missing_axis_fails_closed(self):
        reference = fit_profile(profile())
        robot = fit_profile({"robot": "partial", "records": []})
        with self.assertRaises(ValueError):
            fleet_scale([robot], reference)
        with self.assertRaises(ValueError):
            Compensator(robot, reference)(0.5, 0, 0)

    def test_nonmonotonic_data_is_not_inverted(self):
        data = profile()
        data["records"] = [r for r in data["records"] if r["test"] != "forward"]
        data["records"] += [record("forward", 0.4, 0.6), record("forward", 0.7, 0.3)]
        model = fit_profile(data)
        self.assertIsNone(model.axes["forward_pos"].command_for(0.4))

    def test_unreachable_and_invalid_commands_are_rejected(self):
        reference = fit_profile(profile())
        robot = fit_profile(profile(0.5))
        compensate = Compensator(robot, reference)
        with self.assertRaises(ValueError):
            compensate(1, 0, 0)
        with self.assertRaises(ValueError):
            compensate(float("nan"), 0, 0)

    def test_motion_runner_report_can_be_fitted(self):
        from test_motion_report import session

        report = session(tests=["forward", "backward"], speeds=(0.4, 0.7, 1.0),
                         repeats=2, noise=False)
        model = fit_profile(report)
        self.assertEqual(model.robot, report["robot"])
        self.assertEqual(len(model.axes["forward_pos"].points), 3)
        self.assertGreater(model.axes["forward_pos"].rate(0.7), 0)
        self.assertEqual(model.axes["turn_pos"].points, ())


if __name__ == "__main__":
    unittest.main()