"""A pretend robot and camera, for testing motion tests without hardware.

FakeWorld stands in for the three things a motion-test runner talks to:

    world.fleet.drive(name, axes, buttons)   like laptop.fleet.Fleet
    world.feed.snapshot()                    like the web app's TrackingFeed
    world.clock(), world.sleep(dt)           time, which only moves in sleep()

Time is simulated: sleep() steps the robot forward instead of waiting, so a
test of many 6-second runs finishes in a moment.

The robot can be given the flaws a real one has (Flaws): it drifts, curves,
strafes slower than it drives, turns slower one way, ignores small commands,
responds late, speeds up and slows down gradually, and wobbles when it spins.
The camera samples it at a fixed rate, with noise and lag, and can lose the
tag. A test can then check that the motion tests find each flaw.
"""

import math
import random

from .fleet import COMMAND_TIMEOUT_S

#: Physics steps this often; well under the camera's and the commands' periods.
STEP_S = 0.002


class Flaws(object):
    """How the pretend robot moves. Defaults are a clean robot.

    max_speed: m/s at full forward. max_turn_deg: deg/s at full turn.
    strafe_factor: sideways speed as a fraction of forward speed.
    cw_factor: clockwise turn rate as a fraction of counter-clockwise.
    dead_band: commands smaller than this (per axis) do nothing.
    lag_s: from drive() to the wheels responding (Bluetooth, firmware).
    accel: m/s^2 the speed can change by; turn_accel_deg: deg/s^2.
    drift_deg: it moves this far counter-clockwise of where it is told to.
    curve_deg_per_m: its heading turns this much per metre driven.
    wander_m: when spinning, its centre circles at this radius.
    """

    def __init__(self, max_speed=0.5, max_turn_deg=240.0, strafe_factor=1.0,
                 cw_factor=1.0, dead_band=0.0, lag_s=0.0, accel=4.0,
                 turn_accel_deg=2000.0, drift_deg=0.0, curve_deg_per_m=0.0,
                 wander_m=0.0):
        self.max_speed = max_speed
        self.max_turn_deg = max_turn_deg
        self.strafe_factor = strafe_factor
        self.cw_factor = cw_factor
        self.dead_band = dead_band
        self.lag_s = lag_s
        self.accel = accel
        self.turn_accel_deg = turn_accel_deg
        self.drift_deg = drift_deg
        self.curve_deg_per_m = curve_deg_per_m
        self.wander_m = wander_m


class Camera(object):
    """How the pretend camera sees the robot's tag.

    rate_hz: samples per second. noise_m, noise_deg: random error per sample.
    lag_s: from a sample's capture to it showing up in snapshot().
    lost: (from, to) windows of time when it can't see the tag.
    other_tags: [(id, x, y)] tags that never move (corner tags, say).
    """

    def __init__(self, rate_hz=30.0, noise_m=0.0, noise_deg=0.0, lag_s=0.0,
                 lost=(), other_tags=()):
        self.rate_hz = rate_hz
        self.noise_m = noise_m
        self.noise_deg = noise_deg
        self.lag_s = lag_s
        self.lost = list(lost)
        self.other_tags = list(other_tags)


class FakeWorld(object):
    """One pretend robot named `name` carrying tag `tag_id`, starting at
    (x, y) facing `theta_deg`, on a field `field` (width, height) metres."""

    def __init__(self, flaws=None, camera=None, name="FAKE-01", tag_id=7,
                 x=0.96, y=0.66, theta_deg=0.0, field=(1.93, 1.32), seed=1):
        self.flaws = flaws or Flaws()
        self.camera = camera or Camera()
        self.name = name
        self.tag_id = tag_id
        self.field = field
        self.x, self.y, self.theta = x, y, math.radians(theta_deg)
        self.fleet = FakeFleet(self)
        self.feed = FakeFeed(self)
        self._rng = random.Random(seed)
        self._t = 0.0
        self._vx = self._vy = self._w = 0.0  # body frame: forward, left, ccw
        self._sent = []                       # [(t, (forward, left, turn))]
        self._last_drive = None
        self._next_capture = 0.0
        self._captures = []                   # [(t_capture, doc)], oldest first
        self._published = None
        self._n = 0
        self._capture()

    # -- time -----------------------------------------------------------------

    def clock(self):
        return self._t

    def sleep(self, dt):
        end = self._t + max(0.0, dt)
        while self._t < end - 1e-12:
            step = min(STEP_S, end - self._t)
            self._step(step)
            self._t += step
            if self._t >= self._next_capture - 1e-12:
                self._capture()
            self._publish()

    # -- the robot ------------------------------------------------------------

    def _command(self):
        """What the wheels are doing now: the newest command at least lag_s
        old, or nothing once the commands have stopped (the fleet's and the
        firmware's timeouts)."""
        if self._last_drive is None or self._t - self._last_drive > COMMAND_TIMEOUT_S:
            return 0.0, 0.0, 0.0
        cmd = (0.0, 0.0, 0.0)
        for t, sent in self._sent:
            if t <= self._t - self.flaws.lag_s:
                cmd = sent
        return cmd

    def _step(self, dt):
        f = self.flaws
        forward, left, turn = (0.0 if abs(v) < f.dead_band else v for v in self._command())
        a = math.radians(f.drift_deg)
        tx, ty = forward * f.max_speed, left * f.max_speed * f.strafe_factor
        tx, ty = tx * math.cos(a) - ty * math.sin(a), tx * math.sin(a) + ty * math.cos(a)
        tw = math.radians(turn * f.max_turn_deg * (f.cw_factor if turn < 0 else 1.0))

        # Speed up and slow down at most `accel`.
        dvx, dvy = tx - self._vx, ty - self._vy
        dv = math.hypot(dvx, dvy)
        if dv > f.accel * dt:
            dvx, dvy = dvx * f.accel * dt / dv, dvy * f.accel * dt / dv
        self._vx += dvx
        self._vy += dvy
        dw = tw - self._w
        lim = math.radians(f.turn_accel_deg) * dt
        self._w += max(-lim, min(lim, dw))

        speed = math.hypot(self._vx, self._vy)
        w = self._w + math.radians(f.curve_deg_per_m) * speed
        # A wobbly spin: the centre moves sideways as it turns, round a circle.
        vx, vy = self._vx, self._vy + self._w * f.wander_m
        c, s = math.cos(self.theta), math.sin(self.theta)
        self.x += (vx * c - vy * s) * dt
        self.y += (vx * s + vy * c) * dt
        self.theta = _wrap(self.theta + w * dt)

    def drive(self, axes):
        # body_axes gives (-left, -forward, -turn, 0): undo it.
        self._sent.append((self._t, (-axes[1], -axes[0], -axes[2])))
        self._sent = self._sent[-200:]
        self._last_drive = self._t

    def stop(self):
        self._last_drive = None

    # -- the camera -----------------------------------------------------------

    def _capture(self):
        cam = self.camera
        self._next_capture = self._t + 1.0 / cam.rate_hz
        tags = [{"id": i, "x": x, "y": y, "theta_deg": 0.0, "visible": True}
                for i, x, y in cam.other_tags]
        if not any(a <= self._t < b for a, b in cam.lost):
            theta = math.degrees(self.theta) + self._rng.gauss(0, cam.noise_deg)
            tags.append({
                "id": self.tag_id,
                "x": self.x + self._rng.gauss(0, cam.noise_m),
                "y": self.y + self._rng.gauss(0, cam.noise_m),
                "theta_deg": (theta + 180.0) % 360.0 - 180.0,
                "visible": True,
            })
        field = {"width": self.field[0], "height": self.field[1], "x0": 0.0, "y0": 0.0}
        self._captures.append(
            (self._t, {"t_tags": self._t, "t_capture": self._t, "field": field, "tags": tags}))

    def _publish(self):
        while self._captures and self._captures[0][0] <= self._t - self.camera.lag_s + 1e-12:
            self._published = self._captures.pop(0)[1]
            self._n += 1


class FakeFleet(object):
    """The parts of laptop.fleet.Fleet a motion test uses."""

    def __init__(self, world):
        self._world = world

    def drive(self, name, axes, buttons):
        if name != self._world.name:
            raise KeyError(name)
        self._world.drive(axes)

    def stop(self, name):
        if name == self._world.name:
            self._world.stop()

    def status(self):
        w = self._world
        return [{"name": w.name, "state": "connected", "connected": True, "error": None,
                 "moving": w._command() != (0.0, 0.0, 0.0), "command_age_s": None}]


class FakeFeed(object):
    """The web app's TrackingFeed.snapshot(), from the pretend camera."""

    def __init__(self, world):
        self._world = world

    def snapshot(self):
        w = self._world
        return {"up": w._published is not None, "age_s": 0.0, "n": w._n, "doc": w._published}


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi
