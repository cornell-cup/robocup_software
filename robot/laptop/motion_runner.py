"""Runs motion tests on one robot: drive it, watch it with the camera, measure.

    runner = MotionRunner(fleet, feed, "XRP-01", tests, speeds)
    records = runner.run()          # blocks; call abort() from another thread
    runner.status()                 # from any thread, for a page to show

`fleet` is laptop.fleet.Fleet (or anything with drive/stop/status), `feed` has
snapshot() like the web app's TrackingFeed: {"doc": the tracker's newest
message, "n": messages so far}. `clock` must be on the same clock as the
tracker's sample times (`t_tags`, time.time() by default); `sleep` waits.
fake_robot.FakeWorld provides all four, for testing without hardware.

One robot on the field at a time: the runner takes whichever tag the camera
sees (other than `ignore_tags`, the corner tags), and refuses to start with
none or several.

For each run (one test, one speed, one repeat):
  1. wait until the robot has room for the move, holding still -- if it hasn't,
     drive it back to where the first run started (state "returning"), and if
     that doesn't work either, ask for it to be moved (state "reposition")
  2. record where it starts, standing still
  3. send the test's command 50 times a second until it has gone far enough,
     or the test times out
  4. stop it, record it coasting to rest, and measure the run (analyse()).
It stops the robot at once if the camera loses the tag, if it gets near the
edge of the field, or on abort().
"""

import math
import threading
import time

from .motion_profile import TurnCounter, Sample, analyse
from .protocol import body_axes

COMMAND_HZ = 50.0
#: Standing still this long gives the start pose.
START_S = 0.3
#: Recorded after the stop, while it coasts to rest.
SETTLE_S = 0.8
#: Stop at once if the camera hasn't seen the tag for this long.
LOST_STOP_S = 0.3
#: Keep the robot's centre this far inside the field's edges: about its
#: radius plus a margin.
KEEP_OUT_M = 0.15
#: Room left beyond a straight move's distance, for it to coast into.
COAST_ROOM_M = 0.1
#: Still: moved less than this over STILL_S.
STILL_M = 0.01
STILL_S = 0.5
#: Corner calibration tags, never the robot.
IGNORE_TAGS = (0, 1, 2, 3)

# Driving back to the start between runs: a gentle proportional controller,
# steered by the camera.
#: Command per metre (and per radian) still to go.
HOME_GAIN = 2.0
HOME_TURN_GAIN = 1.0
#: Commands stay between these: the top keeps it gentle, the bottom gets it
#: past a robot's dead band, where small commands do nothing.
HOME_MAX = 0.5
HOME_MIN = 0.2
#: Close enough to the start.
HOME_TOLERANCE_M = 0.02
HOME_TOLERANCE_RAD = math.radians(4.0)
#: Give up (and ask for it to be moved) after this long, or this long without
#: getting any closer.
HOME_TIMEOUT_S = 10.0
HOME_STUCK_S = 2.0


class RunRecord(object):
    """One run: what was asked, how it ended, and what was measured.

    ended: "reached" (went the test's distance), "timeout", "tag_lost",
    "edge" (stopped short of the field's edge) or "aborted".
    samples: the camera's view of it, [Sample], from before the start to rest.
    """

    def __init__(self, test, speed, repeat):
        self.test = test
        self.speed = speed
        self.repeat = repeat
        self.ended = None
        self.samples = []
        self.t_start = None
        self.t_stop = None
        self.result = None

    def to_dict(self):
        return {
            "test": self.test.name,
            "speed": self.speed,
            "repeat": self.repeat,
            "ended": self.ended,
            "result": self.result.to_dict() if self.result else None,
            "path": [[round(s.t - self.t_start, 3), round(s.x, 4), round(s.y, 4),
                      round(math.degrees(s.theta), 2)]
                     for s in self.samples] if self.t_start is not None else [],
        }


class RunnerError(Exception):
    """The tests can't start: no robot, no camera, no tag, several tags."""


class MotionRunner(object):
    def __init__(self, fleet, feed, name, tests, speeds, repeats=1,
                 clock=time.time, sleep=time.sleep, ignore_tags=IGNORE_TAGS,
                 keep_out_m=KEEP_OUT_M, return_home=True):
        self.fleet = fleet
        self.feed = feed
        self.name = name
        # Each repeat runs the whole set of tests, so opposite moves (forward,
        # backward) follow each other and mostly bring the robot back.
        self.plan = [(test, speed, r) for speed in speeds for r in range(1, repeats + 1)
                     for test in tests]
        self.clock = clock
        self.sleep = sleep
        self.ignore_tags = set(ignore_tags)
        self.keep_out_m = keep_out_m
        self.return_home = return_home
        self.home = None       # where the first run started: a Sample
        self.tag_id = None
        self.records = []
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""
        self._current = None   # the RunRecord in progress
        self._last_n = -1

    # -- from other threads -----------------------------------------------------

    def abort(self):
        self._aborted.set()

    def status(self):
        with self._lock:
            current = self._current
            return {
                "state": self._state,
                "message": self._message,
                "robot": self.name,
                "tag_id": self.tag_id,
                "done": len(self.records),
                "total": len(self.plan),
                "current": None if current is None else {
                    "test": current.test.name, "label": current.test.label,
                    "speed": current.speed, "repeat": current.repeat,
                    "path": [[round(s.x, 4), round(s.y, 4)] for s in list(current.samples)],
                },
            }

    # -- running ----------------------------------------------------------------

    def run(self):
        """Every run in the plan, in order. Returns the RunRecords; raises
        RunnerError if the tests can't start."""
        try:
            self._check()
            for test, speed, repeat in self.plan:
                if self._aborted.is_set():
                    break
                record = RunRecord(test, speed, repeat)
                with self._lock:
                    self._current = record
                self._run_one(record)
                with self._lock:
                    self.records.append(record)
                    self._current = None
            self._set("aborted" if self._aborted.is_set() else "done",
                      "Aborted" if self._aborted.is_set() else "All runs done")
        except RunnerError as e:
            self._set("error", str(e))
            raise
        finally:
            self.fleet.stop(self.name)
        return self.records

    def _check(self):
        robots = {r["name"]: r for r in self.fleet.status()}
        if self.name not in robots:
            raise RunnerError("{} isn't connected: add it on the Robots page".format(self.name))
        if not robots[self.name]["connected"]:
            raise RunnerError("{} isn't connected yet".format(self.name))
        doc = self.feed.snapshot().get("doc")
        if doc is None:
            raise RunnerError("no camera: start tracking first")
        tags = [t["id"] for t in doc.get("tags", []) if t["id"] not in self.ignore_tags]
        if not tags:
            raise RunnerError("the camera sees no robot tag: put one robot on the field")
        if len(tags) > 1:
            raise RunnerError("the camera sees several robot tags ({}): test one robot at a "
                              "time".format(", ".join(str(t) for t in sorted(tags))))
        self.tag_id = tags[0]

    def _run_one(self, record):
        test = record.test
        what = "{} at {:g}, run {}".format(test.label, record.speed, record.repeat)

        # 1. Room for the move, standing still: drive back to the start once
        # if it hasn't, then ask for help.
        self._set("starting", what)
        tried_home = not self.return_home or self.home is None
        while not self._ready(test):
            if self._aborted.is_set():
                record.ended = "aborted"
                return
            if not tried_home:
                tried_home = True
                self._set("returning", "Driving back to the start for: " + what)
                self._go_home()
                continue
            self._set("reposition", "Move the robot to give it room for: " + what)
            self._wait()

        # 2. Where it starts.
        self._set("running", what)
        start_by = self.clock() + START_S
        while self.clock() < start_by:
            self._wait()
            self._take(record)
        if not record.samples:
            record.ended = "tag_lost"
            return
        start = record.samples[-1]
        if self.home is None:
            self.home = start
        counter = TurnCounter(start.theta)

        # 3. Drive.
        axes = body_axes(*test.command(record.speed))
        record.t_start = self.clock()
        seen_at = record.t_start
        while True:
            self.fleet.drive(self.name, axes, [])
            self.sleep(1.0 / COMMAND_HZ)
            now = self.clock()
            new = self._take(record)
            for s in new:
                counter.add(s.theta)
            if new:
                seen_at = now
            if self._aborted.is_set():
                record.ended = "aborted"
            elif now - seen_at > LOST_STOP_S:
                record.ended = "tag_lost"
            elif new and test.reached(start, new[-1], counter.total):
                record.ended = "reached"
            elif new and not self._inside(new[-1], 0.0):
                record.ended = "edge"
            elif now - record.t_start >= test.timeout_s:
                record.ended = "timeout"
            if record.ended:
                break

        # 4. Stop, let it settle, measure.
        record.t_stop = self.clock()
        self.fleet.stop(self.name)
        settle_by = record.t_stop + SETTLE_S
        while self.clock() < settle_by:
            self._wait()
            self._take(record)
        record.result = analyse(test, record.speed, record.samples,
                                record.t_start, record.t_stop)

    def _go_home(self):
        """Drive to self.home, steered by the camera. Returns whether it got
        there; stops the robot either way."""
        home = self.home
        if home is None:
            return False
        t0 = seen_at = self.clock()
        best, best_at = float("inf"), t0
        try:
            while not self._aborted.is_set():
                now = self.clock()
                sample, new = self._sample()
                if new:
                    seen_at = now
                if now - seen_at > LOST_STOP_S:
                    return False
                if sample is None:
                    self._wait()
                    continue
                # Heading the wrong way (a tag heading that's off steers it
                # astray): stop before the edge. Half the margin, so a run that
                # stopped at the margin can still drive back from there.
                if not self._inside(sample, -self.keep_out_m / 2.0):
                    return False
                dx, dy = home.x - sample.x, home.y - sample.y
                dist = math.hypot(dx, dy)
                turn_err = _wrap(home.theta - sample.theta)
                if dist <= HOME_TOLERANCE_M and abs(turn_err) <= HOME_TOLERANCE_RAD:
                    return True
                if dist < best - 0.005:
                    best, best_at = dist, now
                if now - t0 > HOME_TIMEOUT_S or now - best_at > HOME_STUCK_S:
                    return False
                # Field-frame error into the robot's own frame.
                c, s = math.cos(sample.theta), math.sin(sample.theta)
                forward, left = dx * c + dy * s, -dx * s + dy * c
                move = 0.0 if dist <= HOME_TOLERANCE_M else _limit(HOME_GAIN * dist)
                if dist > 0:
                    forward, left = forward / dist * move, left / dist * move
                turn = 0.0 if abs(turn_err) <= HOME_TOLERANCE_RAD else math.copysign(
                    _limit(HOME_TURN_GAIN * abs(turn_err)), turn_err)
                self.fleet.drive(self.name, body_axes(forward, left, turn), [])
                self._wait()
            return False
        finally:
            self.fleet.stop(self.name)

    # -- the camera -------------------------------------------------------------

    def _sample(self):
        """The newest camera sample of the robot's tag, and whether it is new
        since the last call: (Sample or None, new)."""
        snap = self.feed.snapshot()
        doc = snap.get("doc")
        if doc is None:
            return None, False
        new = snap.get("n") != self._last_n
        self._last_n = snap.get("n")
        tag = next((t for t in doc.get("tags", []) if t["id"] == self.tag_id), None)
        if tag is None:
            return None, False
        t = doc.get("t_tags") or doc.get("t_capture")
        return Sample(t, tag["x"], tag["y"], math.radians(tag["theta_deg"])), new

    def _take(self, record):
        """Add the camera's new sample, if any, to the record; returns [it] or []."""
        sample, new = self._sample()
        if sample is None or not new:
            return []
        if record.samples and sample.t <= record.samples[-1].t:
            return []
        record.samples.append(sample)
        return [sample]

    def _field(self):
        doc = self.feed.snapshot().get("doc") or {}
        return doc.get("field")

    def _inside(self, pose, extra):
        """Whether `pose` is at least keep_out_m + extra inside the field."""
        field = self._field()
        if field is None:
            return True  # no field in the message: nothing to check against
        m = self.keep_out_m + extra
        x0, y0 = field.get("x0", 0.0), field.get("y0", 0.0)
        return (x0 + m <= pose.x <= x0 + field["width"] - m
                and y0 + m <= pose.y <= y0 + field["height"] - m)

    def _ready(self, test):
        """The robot is seen, standing still, with room for the test."""
        still_by = self.clock() + STILL_S
        first = None
        while True:
            sample, _ = self._sample()
            if sample is None:
                return False
            if first is None:
                first = sample
            elif math.hypot(sample.x - first.x, sample.y - first.y) > STILL_M:
                return False
            if self.clock() >= still_by:
                break
            if self._aborted.is_set():
                return False
            self._wait()
        return self._has_room(test, sample)

    def _has_room(self, test, start):
        if test.spin:
            return self._inside(start, 0.0)
        a = start.theta + math.radians(test.direction_deg)
        reach = test.distance_m + COAST_ROOM_M
        end = Sample(start.t, start.x + reach * math.cos(a), start.y + reach * math.sin(a),
                     start.theta)
        return self._inside(start, 0.0) and self._inside(end, 0.0)

    # -- helpers ----------------------------------------------------------------

    def _wait(self):
        self.sleep(1.0 / COMMAND_HZ)

    def _set(self, state, message):
        with self._lock:
            self._state = state
            self._message = message


def _limit(v):
    """A command between HOME_MIN and HOME_MAX."""
    return max(HOME_MIN, min(HOME_MAX, v))


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi
