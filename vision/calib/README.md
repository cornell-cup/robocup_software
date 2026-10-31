# calib: shared camera calibration

The measured calibration every vision tool reads: where the field is, what the
ball looks like, and a local webcam's lens. It describes the **camera rig**, not
any one tool. Part of [vision](../README.md); the big picture is in the
[root README](../../README.md).

## Set up

Nothing to install beyond the repo setup (`uv sync` at the repo root). The
files are found through `vision_core.paths`, so it does not matter which folder
you launch from: `calib/` is always `vision/calib/`. A file is created the first
time a tool saves it.

## Try it

1. See what is saved now:

   ```powershell
   uv run calibrate-ball --list-profiles      # the ball profiles in ball_color.json
   ```

   `field_pose.json` and `intrinsics.json` are plain JSON; open them in any
   editor.

2. Remake a file on your own rig. The easy way is the web app
   (`uv run robocup-web`, then http://localhost:5801): the **Field calibration**
   page writes `field_pose.json`, the **Ball profile** page writes a profile into
   `ball_color.json`. From a terminal:

   ```powershell
   uv run calibrate-field --field 1.2 0.8 --tag-size 0.080   # your measured field and tag sizes
   uv run calibrate-ball --profile myball --radius-mm 20       # your ball's radius
   ```

3. Start any tracker (`uv run track`, `uv run track-combined --ball-profile myball`).
   Its startup line says the field pose was loaded from `calib/field_pose.json`.

## The files

| File | Made by | What it is |
| --- | --- | --- |
| `field_pose.json` | web app **Field calibration** page, `calibrate-field`, or `track --calibrate-live` | Where the field is, relative to the camera: an `R`/`t` solved from reference AprilTags seen through PhotonVision's camera, using its lens calibration. `track`, `track-ball` and `track-combined` load it automatically in place of the made-up field (for `track-ball` on a local webcam, it only holds if that is the same camera, in the same place). It also records the `field` size it was solved for, which every tracker uses as its `--field` default. An optional `origin: [x, y]` (metres from the first corner, set by the origin picker or `--origin`) moves `(0,0)` there; delete the key to go back to the corner. |
| `ball_color.json` | web app **Ball profile** page, or `calibrate-ball` | Named ball profiles: a measured hue/saturation histogram plus each ball's radius. Pick one with `--ball-profile`. The web app samples PhotonVision's stream (what `track-combined` sees); `calibrate-ball` samples a local webcam. |
| `intrinsics.json` | `calibrate-camera`, or `s` in `track-ball` | A local webcam's camera matrix: how zoomed-in the lens is, and (from `calibrate-camera`) real lens distortion coefficients. Read by `track-ball` only: the tag tools (`track`, `calibrate-field`, `track-combined`) use the lens calibration done in PhotonVision's UI, which lives in PhotonVision, not here. Stores the resolution it was measured at. Loaded as-is at that size, rescaled at another size with the same aspect ratio (e.g. 1280x960 from 640x480), and ignored for a different aspect ratio (4:3 vs 16:9), which needs its own `calibrate-camera` run. |

The files are committed, because this team shares one rig. On a different rig,
recalibrate rather than trusting them.

## Caveats

**`ball_color.json` is the most fragile of the three.** A histogram measured
under one set of lights does not transfer to another room, or to daylight
versus evening. Each profile carries an `h_spread`/`s_spread` (how far it
reaches beyond what was sampled) that absorbs a ball rolling between lamps and
shadows in the *same* room; a committed profile that predates those fields gets
the defaults on load. If the mask looks wrong, re-run the ball calibration
rather than assuming a committed profile still applies.

**`field_pose.json` is only true for one camera position.** Bump or re-aim the
camera and every position shifts. Recalibrate after any move. Pass
`--field-pose PATH` to keep a second pose (e.g. for a second camera) next to
this one, and `--out PATH` on `calibrate-field` to write it.

**If you end up on different webcams, gitignore `intrinsics.json`.** The loader
only rejects a mismatched *resolution*, so two different cameras both at
1280x720 would silently share one wrong `fx`, and every reported distance would
be off with nothing on screen to flag it.
