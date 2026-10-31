# tag-tracking: AprilTags to field positions

Tracks AprilTags (on a robot, a phone or paper) and reports each one's position
on the field in metres, its heading, velocity and turn rate. PhotonVision finds
the tags; this package turns them into field coordinates, and also holds the
field calibration (`calibrate-field`), the math self-test (`selfcheck`) and a
stand-in PhotonVision for working with no hardware (`photon-sim`). Part of
[vision](../README.md); the big picture is in the [root README](../../README.md).

Until you calibrate the field, positions are reported on a **made-up field**: a
rectangle defined in software at a set distance from the camera. Once
`calib/field_pose.json` exists, they are on the real one. Nothing else changes.

## Set up

1. **Set up the repo**: `uv sync` at the repo root. One shared `.venv`; run
   everything with `uv run`, from any folder. See
   [vision's Set up](../README.md#set-up).
2. **Get PhotonVision running**: `uv run setup-photonvision` downloads it; *Start*
   in the web app's header (`uv run robocup-web`) runs it. See
   [app/README.md](../../app/README.md).
3. **Configure PhotonVision once per camera**: follow
   [PhotonVision setup](#photonvision-setup) below (AprilTag 36h11 pipeline,
   lens calibrated, NetworkTables pointed at this machine).
4. **Measure your tag**: the black square, edge to edge, in metres. Pass it as
   `--tag-size` every time. A phone tag from `serve-tag` is already 80 mm
   (`0.080`).
5. **Calibrate the field** when you want real positions:
   [One-time calibration](#one-time-calibration-making-it-real).

Steps 2-3 are only needed for a live camera. With no hardware, skip to Try it.

## Try it

**No hardware:**

1. `uv run selfcheck`. Four sections of `[ok ]`, ending in *"All checks
   passed."* ([details](#0--check-the-maths-no-camera-no-phone))
2. `uv run track --synthetic-camera`. A window with a simulated camera (green
   field, one tag orbiting it, outlined and labelled) and a plan view beside
   it. The tag swings outside the field now and then and its line turns red,
   `OUT`. ([details](#watching-it-work-with-no-hardware-track---synthetic-camera))
3. `uv run photon-sim`, then in a second terminal
   `uv run track --synthetic-field --mode floor`. The same view, but the tags
   arrive over NetworkTables from a stand-in PhotonVision.
   ([details](#no-hardware-photon-sim))

**Real camera** (Set up steps 1-4 done):

1. Put a tag in front of the camera: your robot's, or `uv run serve-tag` and
   open the URL it prints on your phone.
2. Track it:

   ```powershell
   uv run track --tag-size 0.080      # your tag's measured black square, in metres
   ```

   You should see PhotonVision's camera stream with your tag outlined and its
   field position written next to it, a plan view on the right, and one line
   per tag along the bottom. Move the tag and `x`, `y`, `th` follow.
   ([details](#2--track-it))

To track the tag and the ball together, use `track-combined` (the web app's
**Tracking** page runs it); see [combined-tracking](../combined-tracking/).

## PhotonVision setup

Once per camera:

1. **Run PhotonVision**, on a coprocessor or on this machine (`uv run
   setup-photonvision` to download it, *Start* in the web app to run it), and
   add the camera in its UI at `http://<host>:5800`.
2. **Pipeline**: type AprilTag, family **36h11**.
3. **Calibrate the lens in PhotonVision** (Cameras > Calibration), at the
   resolution the AprilTag pipeline runs at. The tag tools use this
   calibration, not `calib/intrinsics.json`, for the field pose and every tag
   position; with none, they stop and say so.
4. **NetworkTables**: Settings > Networking > NetworkTables Server Address =
   the machine running the trackers (`127.0.0.1` if it is this one). By
   default each tracker runs the NetworkTables server (port 5810) itself, the
   way a roboRIO would; `--nt-server HOST` connects to an existing server
   instead.

Exposure, white balance and decimation are PhotonVision camera/pipeline
settings, set in its UI. Lock exposure and white balance there if you use
`track-combined`, or the ball's colour drifts out from under its profile.

`photonlibpy`'s version must match the PhotonVision build; it refuses to talk
to any other. It is pinned to `>=2026.3.4,<2027` in this folder's
`pyproject.toml`. `setup-photonvision` downloads the PhotonVision release that
matches the installed `photonlibpy`; if you run a different PhotonVision,
change one to match the other.

Flags, on `track`, `calibrate-field` and `track-combined`:

| Flag | Meaning | Default |
| --- | --- | --- |
| `--photon-camera NAME` | Camera nickname in PhotonVision's UI | the only camera PhotonVision publishes (with several, it stops and asks) |
| `--nt-server HOST` | Connect to the NetworkTables server at `HOST` instead of running one here | run it here |
| `--stream URL` | PhotonVision's raw camera stream: the picture behind the overlay, and what `track-combined` finds the ball in | `http://<photonvision>:1181/stream.mjpg` |
| `--photon-res W H` | The resolution the pipeline runs at. PhotonVision does not publish it, so it is guessed from the calibration; set it if the startup line shows the wrong size | guessed |
| `--no-stream` | Don't open the stream: tags only, drawn on a blank canvas (`track`, `calibrate-field`) | off |

### No hardware: `photon-sim`

```powershell
uv run photon-sim                                # a stand-in PhotonVision, then in another terminal:
uv run track --synthetic-field --mode floor      # or calibrate-field --out test.json, track-combined
```

Renders the synthetic field (four reference tags on the corners, one tag
driving around), bends it through a distorted lens, finds the tags with
PhotonVision's own detector, and publishes them the way PhotonVision does:
same NetworkTables topics, same lens-calibration topics, and the picture as an
MJPEG stream on port 1181. It does not imitate PhotonVision's UI or camera
settings, and has no ball. Start order doesn't matter; it connects once the
tracker's server is up.

`--synthetic-field --mode floor` matches its default rig (1.2 × 0.8 m field,
camera 1.5 m up, straight down); without it, `track` loads your real
`calib/field_pose.json`, which says nothing about the simulated camera. For
the same reason, `calibrate-field` against it saves over the real file unless
you pass `--out`. Flags: `--name` (camera nickname, default `overhead`),
`--nt-server` (default `127.0.0.1`), `--tag-size`, `--field W H`,
`--cam-height`, `--pitch`, `--width`, `--height`, `--hfov`, `--k1` (lens
distortion, `0` for a pinhole), `--fps`, `--stream-port`, `--stream-scale`.

## Walkthrough

### 0 · Check the maths (no camera, no phone)

```powershell
uv run selfcheck
```

Renders synthetic pictures of a tag at known field positions, pushes them through
the real detector (PhotonVision's, run locally) and the real
`vision_core/field.py`, and checks the numbers come back.

You should see four sections, all `[ok ]`, ending in *"All checks passed."*
Section 3 is the one that matters for RoboCup: it puts a tag at a known spot,
recovers the field pose from four reference tags, and prints the same tag under
both transforms:

```
same tag, invented transform: x=0.750 y=0.546 th=+29.8
same tag, measured transform: x=0.750 y=0.547 th=+29.8
```

Same numbers, same format, two completely different ways of deciding where the
field is. That is the property this project exists to protect.

Section 4 is the live path: a frame rendered through a distorted lens, its
tags sent through a stand-in PhotonVision over real NetworkTables, then the
field pose and a tag's position checked at the other end. It runs its own
NetworkTables server, so close any tracker running on this machine first.

If this fails, the problem is in the geometry. If it passes and the live view
looks wrong, the problem is the camera, PhotonVision's lens calibration, or the
tag size.

### 1 · Put a tag on the phone at a known size

```powershell
uv run serve-tag                      # tag id 0, 80 mm
uv run serve-tag --tag-id 7 --size-mm 100
```

It prints a URL like `http://10.48.151.49:8000/`. Open that on the phone. Both
devices need to be on the same wifi.

The page sizes the tag in **real millimetres**, with no printer and no ruler: you
drag a slider until an on-screen box matches any bank card. Cards are made to
ISO/IEC 7810 ID-1, 85.60 × 53.98 mm, worldwide. That gives the page an exact
pixels-per-millimetre for your phone, and it renders the tag at the size you ask
for.

What you should see: the calibration box, a size field, and a line reading
`--tag-size 0.0800`. That is the number the tracker needs. There is also a bar
that should measure exactly 50 mm if you happen to have a ruler and want to
confirm the card trick worked.

Tap **Full screen tag** for a clean white screen with just the tag. Turn
brightness up and auto-rotate off. Tap the top-right corner to get back.

Rather print? `uv run serve-tag --save-png tag.png` writes the tag as a PNG.

> The size that matters is the **black square**, edge to edge, including the
> black border ring but not the white margin. That is what the detector measures.
> The page's number is already that measurement. For a printed or robot tag,
> measure it with a ruler.

### 2 · Track it

With PhotonVision [set up](#photonvision-setup) and its camera pointed at the phone,
leave the server running and open a second terminal:

```powershell
uv run track --tag-size 0.080         # the phone page's number; your own measurement for any other tag
```

You should see one window, PhotonVision's camera stream on the left and a plan
view on the right:

- a **green rectangle** in the scene: the field, with corners labelled
  `(0,0) (W,0) (W,H) (0,H)`, a 20 cm grid, and red/green arrows for field +X
  and +Y at the origin;
- your tag **outlined in color** when detected, labelled with its field position;
- a **plan view** with the tag as a solid dot (the filtered track), a small
  hollow ring (this frame's raw reading), a heading arrow, a white velocity
  arrow pointing to where it will be in half a second, and a fading trail;
- a **black strip along the bottom** with one line per tag:

```
id  0   x +0.612   y +0.402   th   +3.4   v 0.31 m/s   turn  +12 d/s   off-plane +1.190   IN
```

Move the phone left and the `x` falls; move it up and `y` rises; rotate it and
`th` turns. Carry it past the edge of the green rectangle and the line turns red
and reads `OUT`, and the plan-view dot goes hollow.

**Velocity comes from a filter, not from differencing frames.** Each tag id
gets its own small Kalman filter ([`filter.py`](src/tag_tracking/filter.py), on
top of `vision_core.kalman`, the same core the ball tracker uses). Hold the tag
still and watch the hollow ring jitter around the solid dot: that jitter,
divided by the frame time, is what frame-to-frame differencing would call
velocity. Cover the tag briefly and the dot turns grey and keeps moving on its
last velocity, marked `coasting`; after half a second unseen it is dropped.
The filter steps by PhotonVision's capture times, so the fps on screen is the
rate its results arrive at.

Keys: `q` quit · `g` grid · `t` trails · `r` reset trails and tracks · `w` wall/floor.

Useful flags:

```powershell
uv run track --field 1.2 0.8 --distance 1.8      # made-up field: its size, and how far out it floats
uv run track --mode floor                        # made-up field flat, camera overhead (--cam-height, default 1.5 m)
uv run track --tag-height 0.10                   # tag on top of a robot, 10 cm above the field
uv run track --heading-offset 90                 # tag not mounted square to the way the robot drives
uv run track --photon-camera overhead            # PhotonVision has several cameras: pick one by nickname
uv run track --no-stream --display-width 1300    # tags only on a blank canvas, shrink the window
uv run track --print-poses                       # stream id/x/y/theta/vx/vy/speed to stdout
uv run track --sigma-a 3                         # filter lags a fast robot? allow harder acceleration (m/s^2)
```

## What `off-plane` is telling you

`x` and `y` are where the ray through the tag's centre pixel meets a plane
`--tag-height` metres above the field (`0` for a tag lying on it, the shell
height for a tag on top of a robot). They depend only on the field pose and
where the tag is in the picture, not on how big the tag looks. So set
`--tag-height` for a robot: without it, a raised tag is projected along its
view ray to the floor, which pushes it away from the point under the camera.

`off-plane` is the other estimate: how far the tag is from that plane, ranged
from its apparent size (`--tag-size` and the lens `fx`). On a calibrated field
with the right `--tag-size` and `--tag-height`, it reads close to `0`. Far from
`0` means the tag size or the lens calibration is off.

On the made-up wall field, held at arm's length with the field floating at
1.8 m, you will see `off-plane` around `+1.2`. That is correct: the phone really
is about 1.2 m in front of the imaginary rectangle. Pushing the phone toward the
camera while keeping it in the same spot on screen changes `off-plane`, not `x`
and `y`.

If you want the phone to physically sweep the plane, use
`--distance 0.6 --field 0.5 0.3`: a smaller field close enough to reach. A
1.2 × 0.8 m field simply does not fit in a 60° view at arm's length.

## Does lens calibration matter?

**It is not optional:** the live tools take the lens from PhotonVision's
calibration ([setup](#photonvision-setup), step 3) and stop if there is none.
There is no guessed lens to fall back on. It is still worth knowing what a
sloppy calibration costs.

`fx` converts apparent tag size in pixels into metric distance. Get it wrong by
20% and every *distance from the camera* ranged from tag size (`off-plane`,
and each reference tag's depth during field calibration) is wrong by 20%.

On the made-up field that is survivable: **the made-up field is defined in the
same camera frame the tag is measured in.** The field plane is at 1.8 m in the
camera's units; the tag's depth is computed in those same units. The error
is common to both, so "where is the tag on the field" and "is it inside the
bounds" stay self-consistent even with a sloppy `fx`. The demo is internally
honest; it is just not metrically true.

What does *not* cancel:

- **Lens distortion.** On a cheap wide webcam this bends straight lines by a
  few percent near the frame edges. PhotonVision's calibration measures it, and
  the tag corners and the picture are undistorted with it before anything
  else, provided it was done at the pipeline's resolution.
- **Absolute scale.** If you tape-measure the phone at 50 cm and the numbers
  disagree, that gap is your `fx` error (or your tag size, below).

> One catch worth knowing: **tag-size error and `fx` error are the same error.**
> Only their product sets the depth. You cannot separate them by looking at one
> tag, so measure the tag size independently (a ruler, or step 1's card trick);
> if distances are still off after that, redo the calibration in PhotonVision
> rather than fudging `--tag-size`, which will silently break the moment you
> use a differently sized tag. `calibrate-field` uses `--tag-size` too (it
> solves on all four corners of every reference tag), so pass the measured
> size there as well.

**Where it matters most: the real field.** With reference tags, the camera pose
is solved from their measured image positions against real surveyed field
coordinates. There is no longer a shared fiction for errors to cancel into:
intrinsic error becomes real position error, and distortion becomes a
position-dependent bias that is worst at the frame edges, which is exactly where
corner reference tags live. That is what PhotonVision's calibration and
`calibrate-field`, below, are for.

## One-time calibration: making it real

Two independent, one-time steps: the lens, in PhotonVision, and the field,
with `calibrate-field` (or the web app's **Field calibration** page, which does
the same in a browser, including one tag at a time). The field step saves
`calib/field_pose.json` in `vision/calib/` (shared with every tool, via
`vision_core.paths`), which `track` picks up automatically from then on. Skip it
and nothing changes: you get the made-up field exactly as above.

### Camera intrinsics, in PhotonVision

Real focal length, principal point, and lens distortion, measured in
PhotonVision's UI (Cameras > Calibration); see
[PhotonVision setup](#photonvision-setup). It travels to the trackers over
NetworkTables with the tags, so there is nothing to save here. Calibrate at the
resolution the AprilTag pipeline runs at, and move the board through a range
of distances and tilts, and into the corners of the frame, not just the
centre. Distortion is only visible away from the image centre, so a lazy
session that only shows the board dead-centre will recover a fine `fx` and a
useless distortion estimate.

`calibrate-camera` (ChArUco, math in `vision_core.charuco`) still exists and
writes `calib/intrinsics.json`, but the tag tools do not read that file; only
`track-ball`, which opens its own local webcam, does:

```powershell
uv run calibrate-camera --synthetic          # try it first: a digital board, no camera or board needed
uv run calibrate-camera --save-board-png board.png   # print this
uv run calibrate-camera                      # then: local webcam, wave the printed board around
```

### Field pose: `calibrate-field`

Point the camera at the four reference AprilTags fixed to the real field, once,
and solve for where the field actually is. This wraps
`ReferenceTagFieldTransform` in `vision_core/field.py`, which does the
underlying `solvePnP` math (see [How the field transform works](#how-the-field-transform-works)).
Running it as its own command adds what raw `solvePnP` doesn't: several frames
instead of one noisy shot, averaged, with a report on how well they agree. It
lives in this package, not vision-core, because it needs a tag detector
(PhotonVision, or `detect.py`'s local one for `--synthetic`) to see the tags at
all. Live, the tags and the lens come from PhotonVision; the picture for the
preview and the origin picker is its camera stream, or a blank canvas with
`--no-stream`, since the solve only needs the tags.

The easy way is the web app (`uv run robocup-web`, **Field calibration** page):
Connect, enter tag and field size, Start capture, Solve, pick the origin, Save.
See [app/README.md](../../app/README.md). From a terminal:

```powershell
uv run calibrate-field --synthetic                        # try it first: a digital field, no hardware needed
uv run calibrate-field --field 1.2 0.8 --tag-size 0.080   # your real field and tag sizes; tags at ids 0-3 on the corners
uv run calibrate-field --layout 0:0,0 1:1.2,0 2:1.2,0.8 3:0,0.8  # or name ids/positions explicitly
uv run calibrate-field --origin center                    # (0,0) at the field centre, no picker
uv run calibrate-field --pick-origin                      # change only the origin, keep the saved pose
```

Hold the camera steady on the field; it captures frames until all four
reference tags have been seen 10+ times (`--min-frames`), then reports
rotation/translation spread across those frames. A small spread means the solve
is stable, a large one means something is loose or a tag ID is ambiguous. Press
`q` to finish once it says enough are captured. Saves to
`calib/field_pose.json`, together with the `--field` size, so you pass your
measured size **once, here**: `track`, `track-ball` and `track-combined` all
default to it, and warn if you pass a different `--field` later. A file saved
without a size falls back to `1.2 0.8`; re-run `calibrate-field --field W H` (or
`--pick-origin --field W H`) to stamp it.

#### Choosing where `(0,0)` is

Right after the solve (same run, camera still open) a second window asks
where the origin should be. The field is drawn over the live picture:

- **click** a spot on the field; it snaps to a corner, edge midpoint or the
  centre if you click within a few pixels of one (grey dots)
- **`c`** puts it at the centre
- **Enter** accepts what's shown
- **`d`** or **Esc** keeps the first corner

The red/green axes jump to wherever you click, so what you see is what every
tracker will report. Calibration comes first because a click is only a
position once the field pose is known. The origin is saved as `"origin"`
next to the pose in `calib/field_pose.json`; `track`, `track-ball` and
`track-combined` all pick it up, shift the rectangle, in/out test and plan
view to match, and show `origin (x, y)` in the window header. Leave it out
and nothing changes.

To change it later without recalibrating: `uv run calibrate-field
--pick-origin`. To ignore it for one run: `--corner-origin` on any tracker.
Re-running calibration (here, in the web app, or `track --calibrate-live`)
keeps a saved origin unless you pick a new one: it is a point on the field,
not on the camera.

> **Cheapest real-hardware test: paper.**
> Print (or `uv run serve-tag --save-png`) 4 same-size AprilTags, or use
> `uv run serve-tag --field-sheet field.png` to lay all 4 out on one A4-sized
> sheet already positioned at a field rectangle's corners. Measure whatever
> comes out of the printer with a ruler, then
> `uv run calibrate-field --field <measured W> <measured H> --tag-size <measured tag>`.
> You do need PhotonVision's lens calibration first; there is no guessed-lens
> fallback.

#### One tag instead of four: `--sequential`

No field to fix four tags to, or nothing to fix them with? Carry a single tag
to each corner instead: a phone, a print-out held by hand, or a robot that
drives itself there. The camera stays fixed; the tag doesn't need to stay put
anywhere except while it's being measured. The web app's **One tag at a time**
switch does the same.

```powershell
uv run calibrate-field --sequential --synthetic          # try it first: no hardware needed
uv run calibrate-field --sequential --field 1.2 0.8      # real run: one tag, moved to each corner in turn
```

A window prompts you through the 4 corners one at a time: `corner 1/4: place
ONE tag at field (0, 0)`, and so on. At each one, hold the tag there (exactly
one tag must be visible; a second one in frame is treated as ambiguous and
ignored), watch it capture samples (`--frames-per-corner`, default 30), and
press `q` once it says enough are in to lock that corner in and move to the
next (`r` re-does the current corner, Esc aborts). Any tag id works. After the
fourth corner it solves once, from the four averaged points, and reports
**reprojection error** in pixels: project each known corner back through the
solved pose and see how far off the tag's measured position lands; under ~1px
is a good, unambiguous solve. There is no frame-to-frame agreement to report
here the way the simultaneous mode has (there's only one solve), which is why
the quality signal is different.

Same idea from `track` in one step:

```powershell
uv run track --calibrate-live --sequential
```

### One command instead of two: `track --calibrate-live`

```powershell
uv run track --calibrate-live
```

Does exactly what `calibrate-field` does (point a real camera at the reference
tags; it captures and solves live in the same window) and then, instead of
exiting, continues straight into the normal tracking loop using the pose it
just solved. Also saves to `calib/field_pose.json` (or `--field-pose PATH`), so
a later plain `uv run track` picks it up too. Use this when you just want to see
the whole real pipeline work in one session; use the separate `calibrate-field`
command when you want to calibrate once and walk away, or fine-tune the
layout/tag-size flags without re-tracking each time.

### Watching it work with no hardware: `track --synthetic-camera`

```powershell
uv run track --synthetic-camera
```

Opens the exact same window as step 2, but nothing is plugged in: the field,
the camera, and the tracked tag are all simulated (`tag_tracking.synthetic`).
On startup it prints a live calibration, solving the field pose from 4
simulated reference tags (the same math `calibrate-field --synthetic` uses),
and then the main loop runs exactly as it would against PhotonVision, except
the frames come from a digital camera and the tags from the local detector
(`detect.py`: WPILib's `AprilTagDetector`, the one PhotonVision runs). One tag
orbits the field on its own, swinging outside the boundary on the flat sides
of its path so you also see the red `OUT` state without carrying anything past
the edge yourself.

It is a flag on `track`, not a separate command, on purpose: the overlay, plan
view, readout, and every key are all the *same code* `track` always runs; only
the source of frames changes. `w` (wall/floor) prints an explanation instead of
acting, since there is no real camera pose to toggle here. To run the real
PhotonVision path with no hardware instead, see
[`photon-sim`](#no-hardware-photon-sim).

### What changes once `field_pose.json` exists

Nothing, until you run `track`. Then it picks up `calib/field_pose.json`
automatically: it prints `loaded calibrated field pose from ...`, the on-screen
header says `measured, averaged over N frames...` instead of
`synthetic wall, ... m ahead`, and the `w` (wall/floor) key stops doing
anything, because there is no longer a "which fake pose" to toggle. Pass
`--synthetic-field` to go back to the made-up field for a demo without
disturbing the saved calibration, or `--field-pose PATH` to point at a
different saved pose (e.g. one solved for a second camera).

A custom origin from `calibrate-field` is picked up the same way; pass
`--corner-origin` to report from the first corner instead for one run.

Both `--synthetic` modes never overwrite your real calibration: they only save
if you also pass `--out`.

## How the field transform works

`calibrate-field` above is the operational way to locate the field. What
follows is what it wraps, and the place to look if you want a layout that isn't
4 tags at rectangle corners, or to call the solve from your own code.

Everything about where the field is lives in `vision-core/src/vision_core/field.py`,
not in this package, because the ball tracker needs exactly the same answer.

The contract is `CameraFieldTransform`: a rigid transform holding `R` and `t`
such that `p_camera = R @ p_field + t`. Subclasses only decide **how those are
obtained**; every conversion, and the `TagFieldPose` that comes out, is
implemented once in the base class.

- `SyntheticFieldTransform`: the made-up field. Invents the pose from a
  distance and an angle (`wall`) or a camera height and tilt (`floor`).
  `build_transform()` in `track.py` builds it when no calibration is loaded.
- `ReferenceTagFieldTransform`: the measured field. Give it the detections, a
  `layout` of `tag_id -> (x, y)` in metres, and the camera matrix; it runs
  `solvePnP` and fills in the same `R` and `t`. `.save()` / `.load()` write and
  read `calib/field_pose.json`; `track` loads it whenever that file exists.

From your own code:

```python
layout = {0: (0.0, 0.0), 1: (1.2, 0.0), 2: (1.2, 0.8), 3: (0.0, 0.8)}
transform = ReferenceTagFieldTransform.from_detections(dets, layout, K, dist)
```

Downstream (`tag_field_pose`, the overlay, the plan view, the `(id, x, y, theta)`
output) does not change between the two, and `selfcheck` section 3 exercises
exactly this substitution.

## Conventions

**Camera frame** (OpenCV): +X right, +Y down, +Z forward out of the lens. Metres.

**Field frame**: origin at one corner, +X along `width`, +Y along `height`,
+Z along the surface normal. Right-handed. `(0,0)` is the origin corner and
`(width, height)` the far one, or, with a custom origin saved by
`calibrate-field`, `(0,0)` is that point and the axes keep their directions.

**theta**: rotation about the field normal, degrees in `(-180, 180]`, measured
from field +X, counter-clockwise positive, **0 when the tag is upright**. Use
`--heading-offset` if a robot's tag is not mounted square to the way it drives.

The detector's own tag frame has +X pointing to the tag's visual *left*, a 180°
rotation from what you would guess. That is handled once, in `TAG_VISUAL_RIGHT`
in `src/tag_tracking/pose.py`, and `selfcheck` fails loudly if it ever changes.

The camera view is **not mirrored**. It shows what the camera sees, so the
overlay lands on the tag exactly, and the plan view agrees with the video.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No detection at all | Screen too dim, or glare. Max brightness, tilt away from windows. |
| Detection flickers | Phone auto-dimming, or motion blur. Move slower, add light. |
| Detected but position jumps | Tag too small in frame. Use `--size-mm 100`, or move closer. |
| Distances feel wrong, or `off-plane` far from 0 on a calibrated field | Tag size, `--tag-height`, or PhotonVision's lens calibration. See [What `off-plane` is telling you](#what-off-plane-is-telling-you) and [Does lens calibration matter?](#does-lens-calibration-matter) |
| `no PhotonVision camera appeared on NetworkTables` | PhotonVision not running, no camera enabled, or its NetworkTables Server Address doesn't point at this machine (`127.0.0.1` if same machine). A firewall on this machine can block it too. |
| `publishes several cameras` | Pick one with `--photon-camera NAME`. |
| `publishes no lens calibration` | Calibrate in PhotonVision's UI (Cameras > Calibration), at the pipeline's resolution. |
| `sending targets that are not AprilTags` | The PhotonVision pipeline isn't AprilTag / 36h11. |
| `could not open PhotonVision's stream` | `track` and `calibrate-field` carry on with a blank canvas; `track-combined` stops. Check the URL in PhotonVision's camera view and pass `--stream URL`. |
| Overlay doesn't line up with the tags | The resolution guess is wrong: pass `--photon-res W H` with the pipeline's resolution. |
| `selfcheck` section 4 fails, no result over NetworkTables | A tracker (or the web app's Field calibration connection) on this machine is already running the NetworkTables server. Close it and re-run. |
| Keys do nothing, mouse works | Click the video window first. If letters are still ignored, an input method is swallowing them: switch to English, or press `Esc`, which no IME intercepts. |
| `q` does nothing in a calibrate tool | By design until enough frames are captured. The window's counter shows how many; `Esc` always aborts. |
| Window too big for the screen | `--display-width 1300` |
| Made-up field not visible in frame | `--distance` too small crops it, too large shrinks it. Default 1.8 m fits 1.2 × 0.8. |

## Layout

```
src/tag_tracking/
  photon.py           PhotonTags, PhotonStream -- tags, lens and picture from PhotonVision
  photon_sim.py       photon-sim: a stand-in PhotonVision for testing with no hardware
  detect.py           TagDetection, LocalTagDetector (WPILib's), pose solved from the corners
  pose.py             TagFieldPose, tag_field_pose, TAG_VISUAL_RIGHT -- the tag-shaped bits
  filter.py           TagFieldState, TagTracker -- one Kalman filter per tag: velocity, coasting
  track.py            live tracking, overlay, plan view
  serve_tag.py        serves the tag to the phone/paper at a known physical size
  selfcheck.py        synthetic end-to-end verification, no hardware
  synthetic.py        the digital field + reference tags calibration is tested against
  calibrate_field.py  calibrate-field: field pose from reference tags
  calibrate_camera.py calibrate-camera: CLI wrapper around vision_core.charuco
print/                sample printable assets (field_a4.png, tag PNGs, board.png)
```

and, from the shared floor one level up (full list in
[vision-core's README](../vision-core/)):

```
../vision-core/src/vision_core/
  field.py       Field, CameraFieldTransform, Synthetic/ReferenceTag transforms
  camera.py      open_camera, list_cameras, lock_camera
  intrinsics.py  load / save / from_fov / hfov_of
  kalman.py      the linear Kalman filter both trackers build on: gate, Joseph update, angle wrap
  charuco.py     *measure* the camera matrix -- ChArUco board calibration + its digital self-test
  planview.py    draw_field on the video, PlanView beside it
  mjpeg.py       MJPEG server: photon-sim's stream, track-combined --mjpeg
../calib/        field_pose.json (calibrate-field) and intrinsics.json
                 (calibrate-camera; read by track-ball, not here) land here
```

`pose.py` holds what is *about AprilTags*; `field.py` holds what is *about the
field*. The line matters: re-mounting the reference tags changes `field.py`'s
inputs and nothing in this folder. `charuco.py` is camera-only calibration math
and lives in vision-core for the same reason; `calibrate_field.py` needs a tag
detector to see the reference tags at all, so it stays here.
