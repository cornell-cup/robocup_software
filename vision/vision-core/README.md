# vision-core: shared camera and field code

The library every vision tool builds on: field geometry, the camera-to-field
transform, lens intrinsics, camera control, the Kalman filter and the drawing
helpers. It is not a tool you run; anything here is true about the **camera** or
the **field**, never about one task. Part of [vision](../README.md); the big
picture is in the [root README](../../README.md).

## Set up

Nothing beyond the repo setup: run `uv sync` at the repo root, which installs
`vision-core` editable into the shared `.venv`. Run everything with `uv run`,
from any folder.

## Try it

1. Prove the field math, with no camera:

   ```powershell
   uv run selfcheck
   ```

   You should see four sections of `[ok ]` lines ending in *"All checks
   passed."* It exercises `field.py` end to end: the made-up transform, the
   one measured from four reference tags, and the same tags received over
   NetworkTables.

2. Prove the lens-calibration math in `charuco.py`, with no camera or board:

   ```powershell
   uv run calibrate-camera --synthetic
   ```

3. Use it from your own code. Import from the submodules:

   ```powershell
   uv run python -c "from vision_core.field import Field; from vision_core.paths import calib_dir; print(Field(1.2, 0.8), calib_dir())"
   ```

The one command it owns is a repair tool: `uv run unlock-camera` puts a local
webcam back on auto exposure after a run crashed with it locked (the symptom:
a dark green picture in every app).

## Modules

| Module | Owns |
| --- | --- |
| `field.py` | **The swap point.** `Field`, `CameraFieldTransform`, `SyntheticFieldTransform` (the made-up field), `ReferenceTagFieldTransform` (the measured one: `from_detections`, `save`, `load`, `with_origin`), plus `field_pose_path`, `resolve_field`, `saved_field_size`, `saved_origin` for `calib/field_pose.json`. |
| `camera.py` | `open_camera` (always hands back a camera on auto), `read_frame`, `list_cameras`, `lock_camera` (manual exposure matched to auto's brightness, auto-WB off unless that tints the picture; each half reverts to auto if it does harm), `unlock_camera` (the lock outlives the process, so call it on exit), `read_key` (case-folded `waitKey`). Local webcams only; PhotonVision's camera is set up in PhotonVision. |
| `intrinsics.py` | `load`, `save`, `from_fov`, `hfov_of`, `scale_to`, `undistort_maps` (precomputed `cv2.remap` tables, for undistorting a whole frame). Keyed on resolution; rescaled for the same aspect ratio at another size, ignored across aspect ratios. |
| `charuco.py` | *Measures* the camera matrix from a real `cv2.aruco.CharucoBoard`: `make_board`, `calibrate_intrinsics` (Zhang's method via `cv2.calibrateCamera`, with a degenerate-view guard), plus `SyntheticChArucoCamera` for a self-test with no hardware. Camera-only, no AprilTag dependency, which is why it lives here and not in `tag-tracking`. |
| `kalman.py` | `KalmanFilter` (Mahalanobis gate, Joseph update, angle-wrapped residuals) plus constant-velocity `cv_transition` / `cv_process_noise` builders. The physics stays in each skill. |
| `planview.py` | `project` and `draw_field` for the video overlay; `PlanView` for the top-down panel; `side_by_side`. |
| `mjpeg.py` | `MjpegServer`: serves pictures at `http://<host>:<port>/stream.mjpg`, the way PhotonVision does. Used by `photon-sim`'s stand-in camera and `track-combined --mjpeg` (the web app's Tracking page). |
| `paths.py` | `repo_root()`, `calib_dir()`, `calib_path()`, so `calib/` resolves to `vision/calib/` regardless of the current folder. Requires an editable install. |

Import from the submodules. There is no re-exported surface in `__init__.py` to
keep in sync.

## Dependency direction

```
tag-tracking      ──→  vision-core        one direction, always
ball-tracking     ──→  vision-core        tag-tracking and ball-tracking never import each other
combined-tracking ──→  vision-core        the one exception: an integration that
                  ──→  tag-tracking       depends on both existing skills, reusing
                  ──→  ball-tracking      their detectors instead of reimplementing them
```

## Why `ReferenceTagFieldTransform` lives here

It reads AprilTags, so on the face of it it belongs in `tag-tracking`. It does
not: reference tags are **field infrastructure**, not tag business. The ball
tracker has no interest in AprilTags and still needs the field *located* before
it can say where a ball is on it. It loads the very same
`calib/field_pose.json`, through `field_pose_path()` here.

That is also why the vision packages are one uv workspace rather than
independent folders; see [vision/README.md](../README.md).
