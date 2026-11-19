import json
import unittest

from laptop.fake_robot import Camera, FakeWorld, Flaws
from laptop.motion_profile import TESTS, TESTS_BY_NAME
from laptop.motion_report import _wobble, build_report, compare_with_sim, hardware
from laptop.motion_runner import Body, MotionRunner


def session(flaws=None, tests=None, speeds=(0.7,), repeats=1, noise=True, body=None,
            tag_offset=(0.0, 0.0)):
    """A whole session on the fake robot, as the plain runs the page gets."""
    camera = (Camera(noise_m=0.001, noise_deg=0.3, tag_offset=tag_offset) if noise
              else Camera(tag_offset=tag_offset))
    body = body or Body()
    world = FakeWorld(flaws or Flaws(), camera)
    world.sleep(0.1)
    names = tests or [t.name for t in TESTS]
    runner = MotionRunner(world.fleet, world.feed, world.name,
                          [TESTS_BY_NAME[n] for n in names], speeds, repeats,
                          clock=world.clock, sleep=world.sleep, body=body)
    records = [r.to_dict() for r in runner.run()]
    return build_report(records, robot=world.name, tag_id=runner.tag_id, speeds=speeds,
                        tests=names, repeats=repeats, state="done", body=body.to_dict())


def ability(report, key):
    return next(a for a in report["abilities"] if a["key"] == key)


def found(report, words):
    return any(words in f for f in report["findings"])


class CleanRobotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = session(repeats=2)

    def test_every_ability_is_good(self):
        grades = {a["key"]: a["grade"] for a in self.report["abilities"]}
        self.assertEqual(grades, {k: "good" for k in grades}, self.report["findings"])

    def test_nothing_to_report(self):
        self.assertEqual(self.report["findings"], [])

    def test_averages_each_test_at_each_speed(self):
        by_test = self.report["by_test"]
        self.assertEqual(len(by_test), len(TESTS))
        forward = next(t for t in by_test if t["test"] == "forward")
        self.assertEqual((forward["runs"], forward["reached"]), (2, 2))
        self.assertAlmostEqual(forward["rate"], 0.35, delta=0.02)
        self.assertIsNotNone(forward["drift_sd_m"])

    def test_is_plain_json(self):
        again = json.loads(json.dumps(self.report))
        self.assertEqual(again["robot"], "FAKE-01")
        self.assertEqual(len(again["records"]), 2 * len(TESTS))

    def test_rebuilds_from_saved_runs(self):
        saved = json.loads(json.dumps(self.report["records"]))
        again = build_report(saved, robot="FAKE-01")
        self.assertEqual(again["abilities"], self.report["abilities"])


class FindsFlawsTest(unittest.TestCase):
    def test_drift(self):
        r = session(Flaws(drift_deg=-4.0), tests=["forward", "backward"])
        self.assertEqual(ability(r, "straight")["grade"], "poor")
        self.assertTrue(found(r, "Veers right of its line when driving forward"), r["findings"])

    def test_curving(self):
        r = session(Flaws(curve_deg_per_m=12.0), tests=["forward", "backward"])
        self.assertTrue(found(r, "Turns left while driving forward"), r["findings"])
        self.assertIn(ability(r, "straight")["grade"], ("fair", "poor"))

    def test_veering_every_way_is_one_finding(self):
        r = session(Flaws(drift_deg=-3.0), tests=["forward", "backward", "strafe_left", "strafe_right"])
        veers = [f for f in r["findings"] if f.startswith("Veers")]
        self.assertEqual(len(veers), 1, r["findings"])
        self.assertIn("Veers right of its line in every direction", veers[0])

    def test_slow_strafing(self):
        r = session(Flaws(strafe_factor=0.6), tests=["forward", "strafe_left", "strafe_right"])
        self.assertEqual(ability(r, "strafing")["grade"], "poor")
        self.assertTrue(found(r, "Strafes 40% slower than it drives forward"), r["findings"])

    def test_slow_clockwise_turning(self):
        r = session(Flaws(cw_factor=0.7), tests=["spin_ccw", "spin_cw"])
        self.assertEqual(ability(r, "turning")["grade"], "poor")
        self.assertTrue(found(r, "Turns clockwise 30% slower than counter-clockwise"), r["findings"])

    def test_off_centre_turning_with_the_tag_measured(self):
        r = session(Flaws(wander_m=0.04), tests=["spin_ccw"], body=Body(tag_forward=0, tag_left=0))
        self.assertTrue(found(r, "Turns about a point 40 mm back from its centre"), r["findings"])
        self.assertEqual(ability(r, "turning")["grade"], "fair")

    def test_dead_band(self):
        r = session(Flaws(dead_band=0.5), tests=["forward"], speeds=(0.4, 0.7))
        self.assertTrue(found(r, "Doesn't move at 0.4 (forward)"), r["findings"])
        self.assertTrue(found(r, "1 run timed out"), r["findings"])

    def test_slow_to_react(self):
        r = session(Flaws(lag_s=0.4), tests=["forward"])
        self.assertEqual(ability(r, "response")["grade"], "poor")
        self.assertTrue(found(r, "Slow to react"), r["findings"])

    def test_coasting(self):
        r = session(Flaws(accel=0.8), tests=["forward", "backward"], speeds=(1.0,))
        self.assertTrue(found(r, "Coasts"), r["findings"])

    def test_nothing_turned_on_the_spot_is_nothing(self):
        r = session(tests=["spin_ccw", "spin_cw"], body=Body(tag_forward=0, tag_left=0))
        self.assertEqual(ability(r, "turning")["grade"], "good")
        self.assertEqual(r["findings"], [])

    def test_untested_abilities_have_no_grade(self):
        r = session(tests=["forward"])
        self.assertIsNone(ability(r, "turning")["grade"])
        self.assertIsNone(ability(r, "consistency")["grade"])
        self.assertIn("2 or more repeats", ability(r, "consistency")["detail"])


class TagOffsetTest(unittest.TestCase):
    TESTS = ["spin_ccw", "spin_cw"]

    def test_finds_where_the_tag_sits(self):
        r = session(tests=self.TESTS, tag_offset=(0.03, -0.01))
        off = r["tag_offset"]
        self.assertAlmostEqual(off["forward_m"], 0.03, delta=0.002)
        self.assertAlmostEqual(off["left_m"], -0.01, delta=0.002)
        self.assertFalse(off["known"])

    def test_an_unmeasured_tag_is_ambiguous_not_a_flaw(self):
        r = session(tests=self.TESTS, tag_offset=(0.03, -0.01))
        self.assertTrue(found(r, "Spinning, the tag circles a point 30 mm back and 10 mm left from it"),
                        r["findings"])
        self.assertTrue(found(r, "measure where the tag is"))
        self.assertEqual(ability(r, "turning")["grade"], "good")
        self.assertIn("tag position not measured", ability(r, "turning")["value"])

    def test_entering_it_takes_it_out(self):
        r = session(tests=self.TESTS, tag_offset=(0.03, -0.01),
                    body=Body(tag_forward=0.03, tag_left=-0.01))
        self.assertEqual(r["findings"], [])
        self.assertLess(r["tag_offset"]["off_by_m"], 0.002)
        # The runs are measured from the robot's centre: it stayed put.
        spin = next(rec for rec in r["records"] if rec["test"] == "spin_ccw")
        self.assertLess(spin["result"]["max_deviation_m"], 0.006)

    def test_real_wobble_is_what_the_fit_leaves(self):
        r = session(tests=self.TESTS, tag_offset=(0.03, 0.0), body=Body(tag_forward=0.03, tag_left=0))
        self.assertLess(_wobble(r["records"][0]), 0.006)


class PartialRunsTest(unittest.TestCase):
    def test_runs_that_stopped_partway_are_not_graded(self):
        r = session(tests=["forward", "backward"], repeats=2)
        records = json.loads(json.dumps(r["records"]))
        # The first forward run "hit the edge" with a wild drift: not counted.
        records[0]["ended"] = "edge"
        records[0]["result"]["drift_per_m"] = 0.5
        records[0]["result"]["drift_m"] = 0.2
        again = build_report(records, robot="FAKE-01")
        self.assertEqual(ability(again, "straight")["grade"], "good")
        self.assertTrue(found(again, "1 run stopped at the field's edge"), again["findings"])
        forward = next(t for t in again["by_test"] if t["test"] == "forward")
        self.assertEqual((forward["runs"], forward["reached"], forward["moved"]), (2, 1, 1))


class HardwareTest(unittest.TestCase):
    def test_measures_the_robot_at_full_command(self):
        r = session(tests=["forward", "backward", "strafe_left", "strafe_right", "spin_ccw", "spin_cw"],
                    speeds=(0.7, 1.0))
        hw = r["hardware"]
        self.assertAlmostEqual(hw["top_speed"], 0.5, delta=0.02)      # Flaws().max_speed
        self.assertAlmostEqual(hw["accel"], 4.0, delta=0.5)           # Flaws().accel
        self.assertAlmostEqual(hw["strafe_speed"], 0.5, delta=0.02)
        self.assertAlmostEqual(hw["turn_rate"], 240.0, delta=5.0)     # Flaws().max_turn_deg
        self.assertAlmostEqual(hw["stop_m"], 0.5 ** 2 / (2 * 4.0), delta=0.01)

    def test_needs_a_run_at_full_command(self):
        hw = session(tests=["forward"], speeds=(0.7,))["hardware"]
        self.assertIsNone(hw["top_speed"])
        self.assertIsNone(hw["accel"])

    def test_no_comparison_without_the_sim(self):
        self.assertIsNone(session(tests=["forward"], speeds=(1.0,))["vs_sim"])

    def test_suggests_config_values(self):
        real = {"top_speed": 0.5, "accel": 2.0, "damping": 6.0, "stop_m": 0.08,
                "strafe_speed": None, "turn_rate": 200.0, "turn_accel": 1000.0}
        sim = {"top_speed": 0.8, "accel": 1.0, "damping": 3.0, "stop_m": 0.27,
               "strafe_speed": 0.8, "turn_rate": 800.0, "turn_accel": 1800.0}
        rows = {r["key"]: r for r in compare_with_sim(
            real, sim, {"MOTOR_MAX_FORCE": 1.5, "LINEAR_DAMP": 3.0, "ANGULAR_DAMP": 3.0})}
        self.assertAlmostEqual(rows["accel"]["ratio"], 2.0)
        self.assertEqual(rows["accel"]["suggest"], "MOTOR_MAX_FORCE ≈ 3.00 (now 1.50)")
        self.assertEqual(rows["damping"]["suggest"], "LINEAR_DAMP ≈ 6.0 (now 3)")
        # Twice the force doubles the sim's turn acceleration: 1600 deg/s top
        # rate at ANGULAR_DAMP 3, so 24 brings it down to the real 200.
        self.assertEqual(rows["turn_rate"]["suggest"], "ANGULAR_DAMP ≈ 24.0 (now 3)")
        self.assertIsNone(rows["strafe_speed"]["ratio"])

    def test_report_with_the_sim(self):
        sim = hardware(session(tests=["forward", "backward"], speeds=(1.0,))["records"])
        r = session(Flaws(accel=2.0), tests=["forward", "backward"], speeds=(1.0,))
        again = build_report(r["records"], robot="FAKE-01", sim=sim, sim_config={"MOTOR_MAX_FORCE": 1.0})
        accel = next(row for row in again["vs_sim"] if row["key"] == "accel")
        self.assertAlmostEqual(accel["ratio"], 0.5, delta=0.08)
        self.assertIn("MOTOR_MAX_FORCE", accel["suggest"])


if __name__ == "__main__":
    unittest.main()
