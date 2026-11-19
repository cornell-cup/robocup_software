"""Motion tests on a real robot, from the browser (the Robot test page).

robot/laptop/motion_runner.py does the work: drive the robot, watch it with
the camera tracker, measure each run. This keeps one session at a time on a
thread of its own, with the robot reserved so nothing else drives it, and
hands the page its progress.

`fake: true` runs the same tests on robot/laptop/fake_robot.py's pretend robot
and camera, in real time, to try the page without a robot or a camera.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .procs import CommandError
from .robots import RealRobots, _laptop

#: Repeats of the whole set the page may ask for.
MAX_REPEATS = 5


def _modules():
    laptop = _laptop()
    import laptop.fake_robot
    import laptop.motion_profile
    import laptop.motion_runner

    return laptop


class MotionTests:
    def __init__(self, robots: RealRobots, feed: Any, clock: Any = time.time,
                 sleep: Any = time.sleep) -> None:
        # clock must be the tracker's (its sample times are time.time()).
        self._robots = robots
        self._feed = feed
        self._clock = clock
        self._sleep = sleep
        laptop = _modules()
        self._profile = laptop.motion_profile
        self._runner_module = laptop.motion_runner
        self._fake_module = laptop.fake_robot
        self._lock = threading.Lock()
        self._runner: Any = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._started: float | None = None
        self._fake = False

    # -- what can be run --------------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        return {
            "tests": [{"name": t.name, "label": t.label, "spin": t.spin,
                       "distance_m": t.distance_m, "angle_deg": t.angle_deg}
                      for t in self._profile.TESTS],
            "speeds": list(self._profile.DEFAULT_SPEEDS),
            "max_repeats": MAX_REPEATS,
        }

    # -- running ----------------------------------------------------------------

    def start(self, opts: dict[str, Any]) -> None:
        """{"robot", "tests": [names], "speeds": [0..1], "repeats", "return_home",
        "fake"}. Checks everything it can before returning, so a bad start is
        an error here, not a failed session later."""
        with self._lock:
            if self.running:
                raise CommandError("a motion test is already running; abort it first")
        tests = self._tests(opts.get("tests"))
        speeds = self._speeds(opts.get("speeds"))
        try:
            repeats = int(opts.get("repeats", 1))
        except (TypeError, ValueError):
            raise CommandError("repeats must be a whole number") from None
        if not 1 <= repeats <= MAX_REPEATS:
            raise CommandError(f"repeats must be 1-{MAX_REPEATS}")
        return_home = bool(opts.get("return_home", True))
        fake = bool(opts.get("fake"))

        if fake:
            world = self._fake_module.FakeWorld(
                camera=self._fake_module.Camera(noise_m=0.001, noise_deg=0.3))
            world.sleep(0.1)  # its camera's first message

            def sleep(dt: float) -> None:
                world.sleep(dt)
                time.sleep(dt)  # in real time, so the page can watch

            name, fleet, feed, clock = world.name, world.fleet, world.feed, world.clock
        else:
            name = str(opts.get("robot", "")).strip()
            if not name:
                raise CommandError("pick a robot")
            self._robots.reserve(name)
            fleet, feed, clock, sleep = self._robots.fleet, self._feed, self._clock, self._sleep

        runner = self._runner_module.MotionRunner(
            fleet, feed, name, tests, speeds, repeats,
            clock=clock, sleep=sleep, return_home=return_home)
        try:
            runner.check()
        except self._runner_module.RunnerError as e:
            if not fake:
                self._robots.release(name)
            raise CommandError(str(e)) from None

        with self._lock:
            self._runner, self._error, self._fake = runner, None, fake
            self._started = time.time()
            self._thread = threading.Thread(target=self._run, args=(runner, name, fake),
                                            name="motion-test", daemon=True)
            self._thread.start()

    def _run(self, runner: Any, name: str, fake: bool) -> None:
        try:
            runner.run()
        except self._runner_module.RunnerError as e:
            with self._lock:
                self._error = str(e)
        except Exception as e:  # keep the robot safe and say what happened
            with self._lock:
                self._error = f"the test stopped: {e}"
        finally:
            if not fake:
                self._robots.release(name)

    def abort(self) -> None:
        with self._lock:
            runner = self._runner
        if runner is not None:
            runner.abort()

    def close(self) -> None:
        self.abort()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- for the page -------------------------------------------------------------

    def status(self, records_from: int = 0) -> dict[str, Any]:
        """The session: the runner's status, and its finished runs from
        `records_from` on (the page asks only for the ones it hasn't got)."""
        with self._lock:
            runner, error = self._runner, self._error
            out: dict[str, Any] = {"running": self.running, "fake": self._fake,
                                   "started": self._started, "error": error}
        if runner is None:
            return {**out, "runner": None, "records": [], "records_from": 0}
        records = list(runner.records)
        return {**out, "runner": runner.status(),
                "records": [r.to_dict() for r in records[records_from:]],
                "records_from": records_from}

    # -- checking what the page sent ---------------------------------------------

    def _tests(self, names: Any) -> list[Any]:
        if not isinstance(names, list) or not names:
            raise CommandError("pick at least one test")
        by_name = self._profile.TESTS_BY_NAME
        unknown = [n for n in names if n not in by_name]
        if unknown:
            raise CommandError(f"unknown test {unknown[0]!r}")
        # The catalog's order, whatever order they were ticked in: opposite
        # moves stay next to each other.
        return [t for t in self._profile.TESTS if t.name in names]

    def _speeds(self, speeds: Any) -> list[float]:
        if speeds is None:
            return list(self._profile.DEFAULT_SPEEDS)
        try:
            values = sorted({float(s) for s in speeds})
        except (TypeError, ValueError):
            raise CommandError("speeds must be numbers") from None
        if not values or not all(0.05 <= s <= 1.0 for s in values):
            raise CommandError("speeds must be 0.05-1")
        return values
