import json
import unittest

from laptop.fake_robot import Camera, FakeWorld, Flaws
from laptop.motion_profile import TESTS, TESTS_BY_NAME
from laptop.motion_report import build_report
from laptop.motion_runner import MotionRunner


def session(flaws=None, tests=None, speeds=(0.7,), repeats=1, noise=True):
    """A whole session on the fake robot, as the plain runs the page gets."""
    camera = Camera(noise_m=0.001, noise_deg=0.3) if noise else Camera()
    world = FakeWorld(flaws or Flaws(), camera)
    world.sleep(0.1)
    names = tests or [t.name for t in TESTS]
    runner = MotionRunner(world.fleet, world.feed, world.name,
                          [TESTS_BY_NAME[n] for n in names], speeds, repeats,
                          clock=world.clock, sleep=world.sleep)
    records = [r.to_dict() for r in runner.run()]
    return build_report(records, robot=world.name, tag_id=runner.tag_id, speeds=speeds,
                        tests=names, repeats=repeats, state="done")


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

    def test_wobbly_spin(self):
        r = session(Flaws(wander_m=0.04), tests=["spin_ccw"])
        self.assertTrue(found(r, "Wobbles when spinning"), r["findings"])

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

    def test_untested_abilities_have_no_grade(self):
        r = session(tests=["forward"])
        self.assertIsNone(ability(r, "turning")["grade"])
        self.assertIsNone(ability(r, "consistency")["grade"])
        self.assertIn("2 or more repeats", ability(r, "consistency")["detail"])


if __name__ == "__main__":
    unittest.main()
