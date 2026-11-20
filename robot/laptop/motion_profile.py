"""Motion tests for a real robot, and what one run of a test measured.

A test drives the robot with one fixed command (forward, left, turn in its own
frame, as body_axes takes them) until the camera says it has gone far enough,
then lets it coast to a stop. The camera's view of the run -- the robot's tag,
sample by sample -- goes to analyse(), which returns the numbers a profile is
built from: how far off course it went, how late it started, how fast it went,
how far it coasted.

No hardware here: the runner (the web app) sends the commands and records the
samples; this module only knows the tests and the maths.

Frames: field coordinates in metres, angles in radians counter-clockwise from
+x, as the tracker reports them (theta_deg, converted). "Along" is the way the
test drives, measured in the robot's frame at the start; "across" is to the
left of that, so a robot that drifts right has negative across.
"""

import math
from collections import namedtuple

#: One camera sample of the robot: time (s), position (m), heading (rad).
Sample = namedtuple("Sample", "t x y theta")

#: Moved this far (or turned this much) from the start pose: it has started.
#: Reaction time is the time to get this far, so it includes the first bit of
#: speeding up, not only the delay before the wheels turn: the camera's noise
#: hides smaller movements.
MOVE_M = 0.01
MOVE_RAD = math.radians(3.0)
#: Start and end poses average the samples this close to the start / the end,
#: so one noisy sample doesn't set them.
POSE_WINDOW_S = 0.2
#: Rates (speed, turn rate) are measured over at least this long.
RATE_WINDOW_S = 0.1
#: Acceleration is fitted from this much movement on: smaller is mostly the
#: camera's noise.
ACCEL_FROM_M = 0.004
ACCEL_FROM_RAD = math.radians(1.5)
#: Up to speed at this fraction of the steady rate; stopped below the second.
RISE_FRACTION = 0.9
STOPPED_FRACTION = 0.1
#: No sample for longer than this: the camera lost the tag.
GAP_S = 0.25


class MotionTest(object):
    """One kind of move. `direction_deg` is the way it drives in the robot's
    own frame (0 forward, 90 left); `turn` is +1 counter-clockwise, -1
    clockwise, 0 for a straight move. It ends at `distance_m` (or `angle_deg`
    for a spin), or after `timeout_s` if the robot never gets there."""

    def __init__(self, name, label, direction_deg=None, turn=0,
                 distance_m=0.5, angle_deg=360.0, timeout_s=6.0):
        self.name = name
        self.label = label
        self.direction_deg = direction_deg
        self.turn = turn
        self.distance_m = distance_m
        self.angle_deg = angle_deg
        self.timeout_s = timeout_s

    @property
    def spin(self):
        return self.turn != 0

    def command(self, speed):
        """(forward, left, turn) for this test at `speed` (0..1]."""
        if self.spin:
            return 0.0, 0.0, self.turn * speed
        a = math.radians(self.direction_deg)
        return _clean(speed * math.cos(a)), _clean(speed * math.sin(a)), 0.0

    def reached(self, start, sample, turned):
        """Whether `sample` is as far from `start` as the test goes. `turned`
        is the heading change so far, unwrapped (a TurnCounter's total)."""
        if self.spin:
            return abs(turned) >= math.radians(self.angle_deg)
        return math.hypot(sample.x - start.x, sample.y - start.y) >= self.distance_m

    def __repr__(self):
        return "MotionTest({!r})".format(self.name)


TESTS = [
    MotionTest("forward", "Forward", direction_deg=0),
    MotionTest("backward", "Backward", direction_deg=180),
    MotionTest("strafe_left", "Strafe left", direction_deg=90),
    MotionTest("strafe_right", "Strafe right", direction_deg=-90),
    MotionTest("diagonal_left", "Diagonal forward-left", direction_deg=45),
    MotionTest("diagonal_right", "Diagonal forward-right", direction_deg=-45),
    MotionTest("spin_ccw", "Spin counter-clockwise", turn=1),
    MotionTest("spin_cw", "Spin clockwise", turn=-1),
]
TESTS_BY_NAME = {test.name: test for test in TESTS}

#: Command levels each test runs at: a robot can be fine slow and bad fast.
DEFAULT_SPEEDS = (0.4, 0.7, 1.0)


class RunResult(object):
    """What one run measured. Distances in metres, times in seconds, angles in
    degrees, rates in m/s or deg/s. None where the run can't tell (a robot
    that never moved has no reaction time).

    reaction_s: command to the first MOVE_M (or MOVE_RAD) of movement.
    rise_s: from then until up to RISE_FRACTION of the steady rate.
    rate: steady speed (m/s) or turn rate (deg/s), over the drive's second half.
    accel: how fast it sped up (m/s^2 or deg/s^2), from the distance covered
      until it was up to speed (see _accel).
    along_m, drift_m: where it ended, along the test's direction and to the
      left of it. drift_per_m is drift over along; direction_error_deg the
      angle it actually went at, left positive.
    heading_change_deg: how much it turned (a spin's whole turn, unwrapped).
    max_deviation_m: furthest off the line during the run; for a spin, how far
      its centre wandered.
    coast_m, coast_s: how far and how long it kept going after the stop.
    longest_gap_s: longest stretch without a camera sample."""

    FIELDS = (
        "test", "speed", "moved", "reaction_s", "rise_s", "rate", "accel", "along_m",
        "drift_m", "drift_per_m", "direction_error_deg", "heading_change_deg",
        "max_deviation_m", "coast_m", "coast_s", "longest_gap_s", "notes",
    )

    def __init__(self, test, speed, moved=False):
        # Spelled out, not set in a loop over FIELDS, so editors know them.
        self.test = test
        self.speed = speed
        self.moved = moved
        self.reaction_s = None
        self.rise_s = None
        self.rate = None
        self.accel = None
        self.along_m = None
        self.drift_m = None
        self.drift_per_m = None
        self.direction_error_deg = None
        self.heading_change_deg = None
        self.max_deviation_m = None
        self.coast_m = None
        self.coast_s = None
        self.longest_gap_s = None
        self.notes = []

    def to_dict(self):
        """Plain numbers, rounded, for JSON."""
        out = {}
        for field in self.FIELDS:
            value = getattr(self, field)
            out[field] = round(value, 4) if isinstance(value, float) else value
        return out


def analyse(test, speed, samples, t_start, t_stop):
    """What one run of `test` at `speed` did. `samples` cover a little before
    the command started (t_start) to after the robot settled; the command
    stopped at t_stop. Samples need not be evenly spaced."""
    samples = sorted(samples, key=lambda s: s.t)
    result = RunResult(test=test.name, speed=speed, moved=False)
    before = [s for s in samples if s.t <= t_start]
    during = [s for s in samples if s.t > t_start]
    if not before or len(during) < 2:
        result.notes.append("too few camera samples")
        return result

    start = _mean_pose([s for s in before if s.t >= before[-1].t - POSE_WINDOW_S])
    unwrapped = _unwrap(samples, start.theta)
    progress = [_progress(test, start, s, turned) for s, turned in zip(samples, unwrapped)]
    times = [s.t for s in samples]

    result.longest_gap_s = max(b.t - a.t for a, b in zip(during, during[1:]))
    if result.longest_gap_s > GAP_S:
        result.notes.append("camera lost the tag for {:.2f} s".format(result.longest_gap_s))

    # Started: the first sample past the movement threshold.
    t_move = None
    for s, turned in zip(samples, unwrapped):
        if s.t <= t_start:
            continue
        if test.spin and abs(turned) > MOVE_RAD:
            t_move = s.t
        elif not test.spin and math.hypot(s.x - start.x, s.y - start.y) > MOVE_M:
            t_move = s.t
        if t_move is not None:
            break
    if t_move is None or t_move >= t_stop:
        result.notes.append("never moved")
        return result
    result.moved = True
    result.reaction_s = t_move - t_start

    # Steady rate: a straight-line fit over the second half of the drive.
    t_mid = (t_move + t_stop) / 2.0
    steady = [(t, p) for t, p in zip(times, progress) if t_mid <= t <= t_stop]
    rate = _slope(steady)
    if rate is None or rate <= 0:
        result.notes.append("no steady speed (drive too short, or going backwards)")
    else:
        result.rate = math.degrees(rate) if test.spin else rate
        # Up to speed: the first moment the rate reaches RISE_FRACTION of it.
        for i, t in enumerate(times):
            if t_move <= t <= t_stop:
                r = _rate_at(times, progress, i)
                if r is not None and r >= RISE_FRACTION * rate:
                    result.rise_s = t - t_move
                    break
        if result.rise_s is not None:
            accel = _accel(times, progress, t_start, t_move + result.rise_s,
                           ACCEL_FROM_RAD if test.spin else ACCEL_FROM_M)
            if accel is not None and accel > 0:
                result.accel = math.degrees(accel) if test.spin else accel
        # Coasting: from the stop until the rate falls under STOPPED_FRACTION.
        for i, t in enumerate(times):
            if t >= t_stop:
                r = _rate_at(times, progress, i)
                if r is None or r < STOPPED_FRACTION * rate:
                    result.coast_s = t - t_stop
                    break

    end = _mean_pose([s for s in samples if s.t >= samples[-1].t - POSE_WINDOW_S])
    end_turned = unwrapped[-1]
    p_stop = _interpolate(times, progress, t_stop)
    p_end = _progress(test, start, end, end_turned)
    result.coast_m = None if test.spin else p_end - p_stop

    if test.spin:
        result.along_m = None
        result.heading_change_deg = math.degrees(end_turned)
        # A spin should stay put: how far the centre wandered.
        result.max_deviation_m = max(
            math.hypot(s.x - start.x, s.y - start.y) for s in samples if s.t >= t_start)
        result.drift_m = math.hypot(end.x - start.x, end.y - start.y)
        return result

    along, across = _body(test, start, end)
    result.along_m = along
    result.drift_m = across
    result.drift_per_m = across / along if along > MOVE_M else None
    result.direction_error_deg = math.degrees(math.atan2(across, along))
    result.heading_change_deg = math.degrees(end_turned)
    result.max_deviation_m = max(
        abs(_body(test, start, s)[1]) for s in samples if s.t >= t_start)
    return result


# -- helpers ------------------------------------------------------------------


def _clean(v):
    """Rounding noise to zero, so 'forward' sends exactly 0 sideways."""
    return 0.0 if abs(v) < 1e-9 else v


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _mean_pose(samples):
    """Average position, and the circular mean of the heading."""
    n = float(len(samples))
    return Sample(
        samples[-1].t,
        sum(s.x for s in samples) / n,
        sum(s.y for s in samples) / n,
        math.atan2(sum(math.sin(s.theta) for s in samples),
                   sum(math.cos(s.theta) for s in samples)),
    )


class TurnCounter(object):
    """Heading change from a start heading, unwrapped: a full spin counts as
    2 pi, not 0. Feed it every heading in order; samples must be close enough
    that the robot turns less than half a turn between them."""

    def __init__(self, theta0):
        self.total = 0.0
        self._last = theta0

    def add(self, theta):
        self.total += _wrap(theta - self._last)
        self._last = theta
        return self.total


def _unwrap(samples, theta0):
    counter = TurnCounter(theta0)
    return [counter.add(s.theta) for s in samples]


def _body(test, start, pose):
    """(along, across) of pose relative to start, in the test's direction."""
    dx, dy = pose.x - start.x, pose.y - start.y
    a = start.theta + math.radians(test.direction_deg)
    return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)


def _progress(test, start, pose, turned):
    """How far into the test a pose is: metres along, or radians turned the
    way the test spins."""
    if test.spin:
        return turned * test.turn
    return _body(test, start, pose)[0]


def _slope(points):
    """Least-squares slope of (t, value) points, or None for fewer than 2."""
    if len(points) < 2:
        return None
    n = float(len(points))
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    var = sum((t - mt) ** 2 for t, _ in points)
    if var == 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in points) / var


def _accel(times, values, t_from, t_to, least):
    """Average acceleration while speeding up, from the (time, value) points
    between t_from and t_to. Speeding up evenly from rest at an unknown moment
    t0 covers a(t - t0)^2 / 2, so its square root rises in a straight line
    of slope sqrt(a / 2): fit that line and the delay before it moved drops
    out. Only points past `least` count, where the camera's noise no longer
    dominates. None with fewer than 2."""
    pts = [(t, math.sqrt(v)) for t, v in zip(times, values)
           if t_from <= t <= t_to and v >= least]
    slope = _slope(pts)
    if slope is None or slope <= 0:
        return None
    return 2.0 * slope * slope


def _rate_at(times, values, i):
    """Rate of change at sample i, over RATE_WINDOW_S centred on it (a
    window ahead of it would read the speed of a moment later). None near
    either end, where the window doesn't fit."""
    half = RATE_WINDOW_S / 2.0
    lo = hi = None
    for j in range(i, -1, -1):
        if times[i] - times[j] >= half:
            lo = j
            break
    for j in range(i, len(times)):
        if times[j] - times[i] >= half:
            hi = j
            break
    if lo is None or hi is None:
        return None
    return (values[hi] - values[lo]) / (times[hi] - times[lo])


def _interpolate(times, values, t):
    if t <= times[0]:
        return values[0]
    for i in range(1, len(times)):
        if times[i] >= t:
            f = (t - times[i - 1]) / (times[i] - times[i - 1])
            return values[i - 1] + f * (values[i] - values[i - 1])
    return values[-1]
