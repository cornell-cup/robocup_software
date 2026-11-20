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

The robot's shape (`body`, a Body): every sample is moved from the tag to the
robot's centre before anything is measured, and the room and edge checks keep
the body's corners -- not just its centre -- EDGE_MARGIN_M inside the field.

For each run (one test, one speed, one repeat):
  1. wait until the robot has room for the move, holding still -- if it hasn't,
     drive it to the test's start spot, placed so the move runs through the
     middle of the field (state "returning"), and if that doesn't work either,
     ask for it to be moved (state "reposition"). A move that can't fit on
     the field from any spot is skipped ("no_room").
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
#: Keep the robot's body (its corners) this far inside the field's edges.
EDGE_MARGIN_M = 0.05
#: A robot whose size wasn't given: about an XRP's footprint.
DEFAULT_SIZE_M = 0.2
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


class Body(object):
    """A robot's footprint and where its tag sits on it. Metres: `length`
    front to back, `width` side to side, centred on the robot's centre (where
    it turns about); `tag_forward`, `tag_left`: the tag's centre from the
    robot's centre, in the robot's own frame -- None if nobody measured it
    (taken as centred, but the report then can't tell an off-centre tag from
    a robot that turns about the wrong point)."""

    def __init__(self, length=DEFAULT_SIZE_M, width=DEFAULT_SIZE_M, tag_forward=None, tag_left=None):
        self.length = length
        self.width = width
        self.tag_known = tag_forward is not None and tag_left is not None
        self.tag_forward = tag_forward or 0.0
        self.tag_left = tag_left or 0.0

    @property
    def reach(self):
        """Centre to the furthest corner: what it sweeps when it spins."""
        return math.hypot(self.length, self.width) / 2.0

    def centre(self, tag):
        """The robot's centre, from a Sample of its tag."""
        c, s = math.cos(tag.theta), math.sin(tag.theta)
        return Sample(tag.t, tag.x - (c * self.tag_forward - s * self.tag_left),
                      tag.y - (s * self.tag_forward + c * self.tag_left), tag.theta)

    def corners(self, pose):
        """The footprint's 4 corners in field coordinates, at `pose` (a Sample
        of the robot's centre)."""
        c, s = math.cos(pose.theta), math.sin(pose.theta)
        hl, hw = self.length / 2.0, self.width / 2.0
        return [(pose.x + c * f - s * l, pose.y + s * f + c * l)
                for f, l in ((hl, hw), (-hl, hw), (-hl, -hw), (hl, -hw))]

    def to_dict(self):
        return {"length_m": self.length, "width_m": self.width, "tag_known": self.tag_known,
                "tag_forward_m": self.tag_forward, "tag_left_m": self.tag_left}


class RunRecord(object):
    """One run: what was asked, how it ended, and what was measured.

    ended: "reached" (went the test's distance), "timeout", "tag_lost",
    "edge" (stopped short of the field's edge), "no_room" (the field is too
    small for the move: skipped) or "aborted".
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
        self.start = None      # the pose it set off from: a Sample
        self.result = None
        self.camera_ages = []  # how old each camera message was when it came, seconds

    def to_dict(self):
        return {
            "test": self.test.name,
            "speed": self.speed,
            "repeat": self.repeat,
            "ended": self.ended,
            "start": _pose(self.start),
            "camera_latency_s": (round(sum(self.camera_ages) / len(self.camera_ages), 4)
                                 if self.camera_ages else None),
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
                 body=None, margin_m=EDGE_MARGIN_M, return_home=True):
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
        self.body = body or Body()
        self.margin_m = margin_m
        self.return_home = return_home
        # Where the first run started: its heading is the one every run starts
        # at, and with no field to place start spots on, it's where the robot
        # drives back to.
        self.home = None
        self.spot = None       # the start spot of the run in progress: a Sample
        self.tag_id = None
        self.records = []
        self._aborted = threading.Event()
        self._lock = threading.Lock()
        self._state = "idle"
        self._message = ""
        self._current = None   # the RunRecord in progress
        self._last_n = -1
        self._camera_age = None
        self._pose = None      # the newest camera sample of the robot's centre

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
                "field": self._field(),
                "body": self.body.to_dict(),
                "margin_m": self.margin_m,
                "home": _pose(self.spot),
                # where the robot is (its centre), whatever it's doing
                "pose": _pose(self._pose),
                "current": None if current is None else {
                    "test": current.test.name, "label": current.test.label,
                    "speed": current.speed, "repeat": current.repeat,
                    "start": _pose(current.start),
                    "path": [[round(s.x, 4), round(s.y, 4)] for s in list(current.samples)],
                },
            }

    # -- running ----------------------------------------------------------------

    def run(self):
        """Every run in the plan, in order. Returns the RunRecords; raises
        RunnerError if the tests can't start."""
        try:
            self.check()
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

    def check(self):
        """Raise RunnerError unless the tests can start: the robot connected,
        the camera up, exactly one robot tag in view. Picks that tag."""
        robots = {r["name"]: r for r in self.fleet.status()}
        if self.name not in robots:
            raise RunnerError("{} isn't connected: add it on the Robots page".format(self.name))
        if not robots[self.name]["connected"]:
            raise RunnerError("{} isn't connected yet".format(self.name))
        doc = self.feed.snapshot().get("doc")
        if doc is None:
            raise RunnerError("no camera: start tracking first")
        tags = [t["id"] for t in _visible(doc) if t["id"] not in self.ignore_tags]
        if not tags:
            raise RunnerError("the camera sees no robot tag: put one robot on the field")
        if len(tags) > 1:
            raise RunnerError("the camera sees several robot tags ({}): test one robot at a "
                              "time".format(", ".join(str(t) for t in sorted(tags))))
        self.tag_id = tags[0]

    def _run_one(self, record):
        test = record.test
        what = "{} at {:g}, run {}".format(test.label, record.speed, record.repeat)

        # 1. Room for the move, standing still: drive to its start spot once
        # if it hasn't, then ask for help.
        self._set("starting", what)
        self.spot = self._start_spot(test)
        if self.spot is not None and not self._has_room(test, self.spot):
            record.ended = "no_room"
            return
        tried = not self.return_home or self.spot is None
        while not self._ready(test):
            if self._aborted.is_set():
                record.ended = "aborted"
                return
            if not tried:
                tried = True
                self._set("returning", "Driving to the start spot for: " + what)
                self._go_to(self.spot)
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
        start = record.start = record.samples[-1]
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
            elif new and not self._inside(new[-1], 0.0, spinning=test.spin):
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

    def _start_spot(self, test):
        """Where `test` should start: facing the first run's heading, placed so
        the move (and its coasting) is centred on the field. The first run's
        own start if there's no field to place it on. Before the first run,
        the robot's heading now; None if the camera can't see it."""
        ref = self.home
        if ref is None:
            ref, _ = self._sample()
        if ref is None:
            return None
        field = self._field()
        if field is None:
            return ref
        cx = field.get("x0", 0.0) + field["width"] / 2.0
        cy = field.get("y0", 0.0) + field["height"] / 2.0
        if test.spin:
            return Sample(0.0, cx, cy, ref.theta)
        a = ref.theta + math.radians(test.direction_deg)
        back = (test.distance_m + COAST_ROOM_M) / 2.0
        return Sample(0.0, cx - back * math.cos(a), cy - back * math.sin(a), ref.theta)

    def _go_to(self, home):
        """Drive to pose `home`, steered by the camera. Returns whether it got
        there; stops the robot either way."""
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
                if not self._inside(sample, -self.margin_m / 2.0, spinning=True):
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
        if new and doc.get("t_publish") and doc.get("t_tags"):
            # Capture to publish: the camera's lag. Timings use capture
            # times, so it isn't counted against the robot; it's reported.
            self._camera_age = doc["t_publish"] - doc["t_tags"]
        tag = next((t for t in _visible(doc) if t["id"] == self.tag_id), None)
        if tag is None:
            return None, False
        t = doc.get("t_tags") or doc.get("t_capture")
        self._pose = self.body.centre(Sample(t, tag["x"], tag["y"], math.radians(tag["theta_deg"])))
        return self._pose, new

    def _take(self, record):
        """Add the camera's new sample, if any, to the record; returns [it] or []."""
        sample, new = self._sample()
        if sample is None or not new:
            return []
        if record.samples and sample.t <= record.samples[-1].t:
            return []
        record.samples.append(sample)
        if self._camera_age is not None:
            record.camera_ages.append(self._camera_age)
        return [sample]

    def _field(self):
        doc = self.feed.snapshot().get("doc") or {}
        return doc.get("field")

    def _inside(self, pose, extra, spinning=False):
        """Whether the robot's body at `pose` is at least margin_m + extra
        inside the field: its corners, or for a robot spinning (or about to)
        the whole circle they sweep."""
        field = self._field()
        if field is None:
            return True  # no field in the message: nothing to check against
        m = self.margin_m + extra
        x0, y0 = field.get("x0", 0.0), field.get("y0", 0.0)
        x1, y1 = x0 + field["width"], y0 + field["height"]
        if spinning:
            r = self.body.reach + m
            return x0 + r <= pose.x <= x1 - r and y0 + r <= pose.y <= y1 - r
        return all(x0 + m <= x <= x1 - m and y0 + m <= y <= y1 - m
                   for x, y in self.body.corners(pose))

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
            return self._inside(start, 0.0, spinning=True)
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


def _pose(sample):
    """[x, y, heading in degrees] for the page, or None."""
    if sample is None:
        return None
    return [round(sample.x, 4), round(sample.y, 4), round(math.degrees(sample.theta), 2)]


def _visible(doc):
    """The tags the camera sees in this message. The tracker keeps a tag it
    has just lost for a moment at a predicted position, marked not visible:
    that's a guess, not a sample."""
    return [t for t in doc.get("tags", []) if t.get("visible", True)]


def _limit(v):
    """A command between HOME_MIN and HOME_MAX."""
    return max(HOME_MIN, min(HOME_MAX, v))


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi
