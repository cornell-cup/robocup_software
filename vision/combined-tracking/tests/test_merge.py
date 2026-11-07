"""Merging several cameras' tracks (combined_tracking.merge), and calibrating
several cameras into one field frame (calibrate_field --split).

    uv run python -m unittest discover -s vision/combined-tracking/tests -v
"""

from __future__ import annotations

import math
import unittest

import numpy as np
from ball_tracking.ball import BallFieldState
from combined_tracking.merge import (
    BallMerger,
    CameraBall,
    CameraGeometry,
    CameraTags,
    TagMerger,
)
from tag_tracking.filter import TagFieldState
from vision_core.field import Field, SyntheticFieldTransform
from vision_core.intrinsics import from_fov

from tag_tracking.photon_sim import split_height

FIELD = Field(2.6, 1.2)
W, H = 1280, 720
K = from_fov(W, H, 70.0)
TAG = 0.177
#: Each camera sees its half plus 0.3 m: the rig the merge is for.
HEIGHT = split_height(FIELD, 2, 0.3, K, (W, H), margin=TAG)


def camera(name: str, cx: float) -> CameraGeometry:
    """Straight down, HEIGHT up, over (cx, field middle)."""
    t = SyntheticFieldTransform.floor(FIELD, HEIGHT, 90.0, centre=(cx, FIELD.height / 2))
    return CameraGeometry(name, t, K, (W, H))


LEFT, RIGHT = camera("left", FIELD.width / 4), camera("right", 3 * FIELD.width / 4)


def tag(x: float, y: float, theta: float = 0.0, vx: float = 0.0, vy: float = 0.0,
        visible: bool = True, age: float = 0.0, tid: int = 9) -> TagFieldState:
    speed = math.hypot(vx, vy)
    return TagFieldState(tag_id=tid, x=x, y=y, theta_deg=theta, vx=vx, vy=vy, speed=speed,
                         direction_deg=math.degrees(math.atan2(vy, vx)) if speed else 0.0,
                         omega_deg=0.0, inside=FIELD.contains(x, y), visible=visible,
                         age=age, off_plane_m=0.0)


def ball(x: float, y: float, vx: float = 0.0, vy: float = 0.0, visible: bool = True,
         age: float = 0.0) -> BallFieldState:
    return BallFieldState(x=x, y=y, z=0.02, vx=vx, vy=vy, speed=math.hypot(vx, vy),
                          direction_deg=0.0, grounded=True, visible=visible, age=age)


def merge_tags(merger: TagMerger, left: list, right: list, t: float = 100.0,
               t_right: float | None = None):
    return merger.merge([CameraTags(LEFT, left, t),
                         CameraTags(RIGHT, right, t if t_right is None else t_right)])


class SigmaTest(unittest.TestCase):
    def test_centre_beats_edge(self):
        centre = LEFT.sigma_m(FIELD.width / 4, FIELD.height / 2)
        edge = LEFT.sigma_m(FIELD.width / 2 + 0.2, FIELD.height / 2)
        self.assertLess(centre, edge)

    def test_grows_smoothly_past_the_image_edge(self):
        xs = np.linspace(FIELD.width / 4, FIELD.width, 201)  # from the camera's centre out
        s = [LEFT.sigma_m(x, 0.6) for x in xs]
        self.assertTrue(all(b >= a for a, b in zip(s, s[1:])), "sigma not monotone")
        steps = np.diff(s) / s[:-1]
        self.assertLess(max(steps), 0.1, "sigma jumps somewhere")


class TagMergeTest(unittest.TestCase):
    def test_one_camera_is_passed_through_untouched(self):
        states = [tag(0.5, 0.5, 30.0, vx=0.2)]
        out, t = TagMerger(FIELD).merge([CameraTags(LEFT, states, 5.0)])
        self.assertIs(out, states)
        self.assertEqual(t, 5.0)

    def test_tag_seen_by_one_camera_keeps_its_reading(self):
        out, _ = merge_tags(TagMerger(FIELD), [tag(0.4, 0.3)], [])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0].x, 0.4)
        self.assertAlmostEqual(out[0].y, 0.3)

    def test_agreeing_cameras_give_the_same_answer(self):
        out, _ = merge_tags(TagMerger(FIELD, ), [tag(1.3, 0.6)], [tag(1.3, 0.6)])
        self.assertAlmostEqual(out[0].x, 1.3)

    def test_weights_favour_the_camera_the_tag_is_central_to(self):
        m = TagMerger(FIELD)
        # Both cameras have had the tag a while (past the ramp-in).
        merge_tags(m, [tag(1.0, 0.6)], [tag(1.02, 0.6)], t=0.0)
        out, _ = merge_tags(m, [tag(1.0, 0.6)], [tag(1.02, 0.6)], t=1.0)
        # x = 1.0 is nearer the left camera's centre (0.65) than the right's (1.95).
        self.assertLess(out[0].x, 1.01)
        self.assertGreater(out[0].x, 1.0)
        self.assertAlmostEqual(m.disagreement[9], 0.02, places=6)

    def test_handoff_is_smooth_even_when_cameras_disagree(self):
        """A tag driven across the overlap at 0.3 m/s, the right camera
        reading it a constant 2 cm off. Each camera sees it only while the
        whole tag is in its picture; once lost, its track coasts (visible
        False, age growing) for 0.5 s and is dropped, as TagTracker does.
        The merged x must never step by much more than the tag's own motion:
        the 2 cm is blended in, not jumped."""
        m = TagMerger(FIELD, tag_size=TAG)
        reach = TAG / math.sqrt(2)
        offset, fps, speed, coast = 0.02, 30.0, 0.3, 0.5
        step = speed / fps
        xs = np.arange(0.6, 2.0, step)
        merged = []
        last_seen: dict[str, float] = {}  # camera -> when it last saw the tag
        for i, x in enumerate(xs):
            t = i / fps
            per_cam = []
            for cam, dx in ((LEFT, 0.0), (RIGHT, offset)):
                if cam.sees(x, 0.6, 0.0, reach):
                    last_seen[cam.name] = t
                    states = [tag(x + dx, 0.6, vx=speed)]
                elif cam.name in last_seen and t - last_seen[cam.name] <= coast:
                    states = [tag(x + dx, 0.6, vx=speed, visible=False,
                                  age=t - last_seen[cam.name])]
                else:
                    states = []
                per_cam.append(CameraTags(cam, states, t))
            out, _ = m.merge(per_cam)
            merged.append(out[0].x)
        jumps = np.abs(np.diff(merged) - step)
        # ~0.3 mm with feathering; ~2 mm without (merge.CameraGeometry.taper).
        self.assertLess(jumps.max(), 0.001, f"merged x jumped {jumps.max() * 1000:.1f} mm")
        self.assertAlmostEqual(merged[0], xs[0], places=6)
        self.assertAlmostEqual(merged[-1], xs[-1] + offset, places=6)

    def test_stale_track_counts_for_little(self):
        m = TagMerger(FIELD)
        merge_tags(m, [tag(1.3, 0.6)], [tag(1.3, 0.6)], t=0.0)
        out, _ = merge_tags(m, [tag(1.3, 0.6, visible=False, age=0.4)],
                            [tag(1.4, 0.6)], t=1.0)
        self.assertGreater(out[0].x, 1.39)

    def test_headings_average_across_the_wrap(self):
        m = TagMerger(FIELD)
        merge_tags(m, [tag(1.3, 0.6, 179.0)], [tag(1.3, 0.6, -179.0)], t=0.0)
        out, _ = merge_tags(m, [tag(1.3, 0.6, 179.0)], [tag(1.3, 0.6, -179.0)], t=1.0)
        self.assertAlmostEqual(abs(out[0].theta_deg), 180.0, delta=0.5)

    def test_older_reading_is_predicted_to_the_common_time(self):
        m = TagMerger(FIELD)
        # The right camera's result is 0.1 s older; the tag moves at 1 m/s.
        merge_tags(m, [], [tag(1.9, 0.6, vx=1.0)], t=0.0, t_right=-0.1)
        out, t = merge_tags(m, [], [tag(1.9, 0.6, vx=1.0)], t=1.0, t_right=0.9)
        self.assertEqual(t, 1.0)  # no left tag, but the left camera's clock still counts
        self.assertAlmostEqual(out[0].x, 2.0, places=6)

    def test_new_track_is_ramped_in(self):
        m = TagMerger(FIELD)
        for t in np.arange(0.0, 1.0, 1 / 30):
            merge_tags(m, [tag(1.3, 0.6)], [], t=float(t))
        # The right camera picks it up, reading 3 cm off: the first merged
        # value moves only a little of the way.
        out, _ = merge_tags(m, [tag(1.3, 0.6)], [tag(1.33, 0.6)], t=1.0)
        self.assertLess(out[0].x - 1.3, 0.005)


class BallMergeTest(unittest.TestCase):
    def merge(self, m: BallMerger, left, right, t: float):
        return m.merge([CameraBall(LEFT, left, t), CameraBall(RIGHT, right, t)])

    def test_one_camera_is_passed_through(self):
        b = ball(0.3, 0.3)
        out, _ = BallMerger(FIELD).merge([CameraBall(LEFT, b, 1.0)])
        self.assertIs(out, b)

    def test_both_cameras_on_the_ball_blend(self):
        out, _ = self.merge(BallMerger(FIELD), ball(1.30, 0.6), ball(1.31, 0.6), 1.0)
        self.assertTrue(1.30 <= out.x <= 1.31)

    def test_a_false_ball_far_away_is_ignored_once_tracking(self):
        m = BallMerger(FIELD)
        self.merge(m, ball(0.5, 0.6), None, 1.0)
        out, _ = self.merge(m, ball(0.51, 0.6), ball(2.2, 0.3), 1.033)
        self.assertAlmostEqual(out.x, 0.51, places=6)

    def test_reacquired_when_the_old_ball_is_gone(self):
        m = BallMerger(FIELD)
        self.merge(m, ball(0.5, 0.6), None, 1.0)
        out, _ = self.merge(m, None, ball(2.2, 0.3), 1.033)
        self.assertAlmostEqual(out.x, 2.2, places=6)

    def test_no_ball_anywhere(self):
        out, _ = self.merge(BallMerger(FIELD), None, None, 1.0)
        self.assertIsNone(out)


class SplitCalibrationTest(unittest.TestCase):
    """Two synthetic cameras, each seeing half the field: calibrate-field's
    solve, on each camera's own part of split_field_layout(), must put both in
    the same field frame -- a tag in the overlap reads the same in both."""

    def test_two_cameras_share_one_frame(self):
        from tag_tracking.calibrate_field import (
            calibrate_field,
            split_field_layout,
            split_part_layout,
        )
        from tag_tracking.detect import LocalTagDetector, for_tracking
        from tag_tracking.photon_sim import split_height
        from tag_tracking.pose import tag_field_pose
        from tag_tracking.synthetic import SyntheticFieldCamera

        tag_size = 0.08
        w, h = 1280, 720
        Kc = from_fov(w, h, 60.0)
        height = split_height(FIELD, 2, 0.3, Kc, (w, h), margin=tag_size)
        full = split_field_layout(FIELD, 2)
        self.assertEqual(sorted(full), [0, 1, 2, 3, 10, 11])
        probe = (13, FIELD.width / 2 + 0.05, 0.55, 20.0)  # in the overlap
        detector = LocalTagDetector()
        readings = []
        for k in range(2):
            truth = SyntheticFieldTransform.floor(
                FIELD, height, 90.0, centre=(FIELD.width * (k + 0.5) / 2, FIELD.height / 2))
            cam = SyntheticFieldCamera(full, truth, tag_size=tag_size, shape=(h, w), K=Kc,
                                       extra_tags=(probe,))
            # Any 4 of the full layout in view, and this camera's own 4: both work.
            for layout in (full, split_part_layout(FIELD, 2, k + 1)):
                views = [detector.detect(cam.read()) for _ in range(12)]
                result = calibrate_field(views, layout, Kc, min_frames=10, tag_size=tag_size)
                dt = float(np.linalg.norm(result.transform.t - truth.t))
                self.assertLess(dt, 0.02, f"camera {k} pose off by {dt * 1000:.1f} mm")
            dets = for_tracking(detector.detect(cam.read()), Kc, None, tag_size)
            p = next(d for d in dets if d.tag_id == probe[0])
            readings.append(tag_field_pose(p, result.transform, FIELD, K=Kc))
        a, b = readings
        apart = math.hypot(a.x - b.x, a.y - b.y)
        self.assertLess(apart, 0.01, f"the cameras put the overlap tag {apart * 100:.1f} cm apart")
        self.assertLess(abs(a.x - probe[1]), 0.015)


if __name__ == "__main__":
    unittest.main()
