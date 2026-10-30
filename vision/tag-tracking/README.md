# Virtual-field AprilTag tracking

Track an AprilTag held on a phone, and report where it is on a **field that does
not physically exist** — a rectangle defined purely in software as sitting some
distance in front of the camera.

Built as a proof of concept for a RoboCup setup where the field will be real and
located by four reference AprilTags. The camera-to-field transform is isolated in
one file so that swap costs one subclass and changes nothing downstream.

**Skill 1** of this repo. The geometry it stands on lives in
[`vision-core`](../vision-core/), ready to be shared with the skills that follow.
Everything below runs with `uv run` from `vision/`, in the monorepo's
root `.venv`: see [Quick start](../README.md#quick-start).

Making that real (measuring the camera and the field instead of inventing them)
is a one-time calibration step — see [One-time calibration](#one-time-calibration-making-it-real)
once you have been through steps 0-2 below.

The camera itself belongs to [PhotonVision](https://photonvision.org): it
captures, finds the tags, and sends each one's id and corners over
NetworkTables. Everything from the pose onwards is still worked out here. The
live tools (`track`, `calibrate-field`, `track-combined`) need it set up once —
see [PhotonVision setup](#photonvision-setup) — or, with no hardware,
[`photon-sim`](#no-hardware-photon-sim) stands in for it.

## PhotonVision setup

Once per camera:

1. **Run PhotonVision**, on a coprocessor or on this machine, and add the
   camera in its UI at `http://<host>:5800`.
2. **Pipeline**: type AprilTag, family **36h11**.
3. **Calibrate the lens in PhotonVision** (Cameras > Calibration), at the
   resolution the AprilTag pipeline runs at. The tag tools use this
   calibration, not `calib/intrinsics.json`, for the field pose and every tag
   position; with none, they stop and say so.
4. **NetworkTables**: Settings > Networking > NetworkTables Server Address =
   the machine running the trackers (`127.0.0.1` if it is this one). By
   default each tracker runs the NetworkTables server itself, the way a
   roboRIO would; `--nt-server HOST` connects to an existing server instead.

Exposure, white balance and decimation are PhotonVision camera/pipeline
settings now, set in its UI. Lock exposure and white balance there if you use
`track-combined`, or the ball's colour drifts out from under its profile.

`photonlibpy`'s version must match the PhotonVision build — it refuses to talk
to any other. It is pinned to `>=2026.3.4,<2027` in `pyproject.toml`; if
PhotonVision is a different release, change one to match the other.

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
uv run track --synthetic-field --mode floor      # or calibrate-field, track-combined
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
`--nt-server`, `--tag-size`, `--field W H`, `--cam-height`, `--pitch`,
`--width`, `--height`, `--hfov`, `--k1` (lens distortion, `0` for a pinhole),
`--fps`, `--stream-port`, `--stream-scale`.

## Run it in order

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
same tag, measured transform: x=0.749 y=0.546 th=+29.8
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

It prints a URL like `http://10.48.151.49:8000/`. Open that on the phone — both
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

> The size that matters is the **black square**, edge to edge, including the
> black border ring but not the white margin. That is what the detector measures.
> The page's number is already that measurement.

### 2 · Track it

With PhotonVision [set up](#photonvision-setup) and its camera pointed at the phone,
leave the server running and open a second terminal:

```powershell
uv run track --tag-size 0.080
```

You should see one window, PhotonVision's camera stream on the left and a plan
view on the right:

- a **green rectangle** floating in the scene — the virtual field, with corners
  labelled `(0,0) (W,0) (W,H) (0,H)`, a 20 cm grid, and red/green arrows for
  field +X and +Y at the origin corner;
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
uv run track --field 1.2 0.8 --distance 1.8      # field size, and how far out it floats
uv run track --mode floor                        # field flat, camera overhead 1.5 m up
uv run track --photon-camera overhead            # PhotonVision has several cameras: pick one by nickname
uv run track --no-stream --display-width 1300    # tags only on a blank canvas, shrink the window
uv run track --print-poses                       # stream id/x/y/theta/vx/vy/speed to stdout
uv run track --sigma-a 3                         # filter lags a fast robot? allow harder acceleration (m/s^2)
```

## What `off-plane` is telling you

`x` and `y` are the tag's true 3D position expressed in field coordinates — not a
projection onto the field plane. `off-plane` is the third coordinate: how far the
tag is from the plane, along the field normal.

Held at arm's length with the field floating at 1.8 m, you will see `off-plane`
around `+1.2`. That is correct and it is not an error: the phone really is about
1.2 m in front of the imaginary rectangle. Since the position is genuinely 3D,
pushing the phone toward the camera while keeping it in the same spot on screen
*does* change `x` and `y`, because parallax is real. That is the tracking working,
not drifting.

If you want the phone to physically sweep the plane, use
`--distance 0.6 --field 0.5 0.3`: a smaller field close enough to reach. A
1.2 × 0.8 m field simply does not fit in a 60° view at arm's length.

## Does lens calibration matter?

**It is not optional any more:** the live tools take the lens from
PhotonVision's calibration ([setup](#photonvision-setup), step 3) and stop if
there is none. There is no guessed lens to fall back on. It is still worth
knowing what a sloppy calibration costs.

`fx` converts apparent tag size in pixels into metric distance. Get it wrong by
20% and every reported *distance from the camera* is wrong by 20%.

On the made-up field that is survivable: **the virtual field is defined in the
same camera frame the tag is measured in.** The field plane is at 1.8 m in the
camera's units; the tag's depth is computed in those same units. The error
is common to both, so "where is the tag on the field" and "is it inside the
bounds" stay self-consistent even with a sloppy `fx`. The demo is internally
honest; it is just not metrically true.

What does *not* cancel:

- **Lens distortion.** On a cheap wide webcam this bends straight lines by a
  few percent near the frame edges. PhotonVision's calibration measures it, and
  the tag corners and the picture are undistorted with it before anything
  else — provided it was done at the pipeline's resolution.
- **Absolute scale.** If you tape-measure the phone at 50 cm and the numbers
  disagree, that gap is your `fx` error (or your tag size, below).

> One catch worth knowing: **tag-size error and `fx` error are the same error.**
> Only their product sets the depth. You cannot separate them by looking at one
> tag, so measure the tag size independently (step 1's card trick); if distances
> are still off after that, redo the calibration in PhotonVision rather than
> fudging `--tag-size`, which will silently break the moment you use a
> differently sized tag.

**Where it matters most: the real RoboCup field.** With four reference
tags, the camera pose is solved from their measured image positions against real
surveyed field coordinates. There is no longer a shared fiction for errors to
cancel into — intrinsic error becomes real position error, and distortion becomes
a position-dependent bias that is worst at the frame edges, which is exactly where
corner reference tags live. That is what PhotonVision's calibration and
`calibrate-field`, below, are for.

## One-time calibration: making it real

Two independent, one-time steps: the lens, in PhotonVision, and the field,
with `calibrate-field`. The field step saves `calib/field_pose.json` in
`vision/calib/` (shared with every skill, via `vision_core.paths`),
which `track` picks up automatically from then on. Skip it and nothing
changes — you get the made-up field exactly as above.

### Camera intrinsics — in PhotonVision

Real focal length, principal point, and lens distortion, measured in
PhotonVision's UI (Cameras > Calibration) — see
[PhotonVision setup](#photonvision-setup). It travels to the trackers over
NetworkTables with the tags, so there is nothing to save here. Calibrate at the
resolution the AprilTag pipeline runs at, and move the board through a range
of distances and tilts, and into the corners of the frame, not just the
centre — distortion is only visible away from the image centre, so a lazy
session that only shows the board dead-centre will recover a fine `fx` and a
useless distortion estimate.

`calibrate-camera` (ChArUco, math in `vision_core.charuco`) still exists and
writes `calib/intrinsics.json`, but the tag tools no longer read that file;
only `track-ball`, which opens its own local webcam, does:

```powershell
uv run calibrate-camera --synthetic          # try it first: a digital board, no camera or board needed
uv run calibrate-camera --save-board-png board.png   # print this
uv run calibrate-camera                      # then: local webcam, wave the printed board around
```

### Field pose — `calibrate-field`

Point a camera at the four reference AprilTags fixed to the real field, once,
and solve for where the field actually is — this wraps
`ReferenceTagFieldTransform` in `vision_core/field.py`, which already does the
underlying `solvePnP` math (see the next section). Running it as its own
command adds what raw `solvePnP` doesn't: several frames instead of one noisy
shot, averaged, with a report on how well they agree. This one lives in the
skill, not vision-core, because it needs the tag detector (PhotonVision, or
`detect.py`'s local one for `--synthetic`) to see the tags at all. Live, the
tags and the lens come from PhotonVision; the picture for the preview and the
origin picker is its camera stream, or a blank canvas with `--no-stream`,
since the solve only needs the tags.

```powershell
uv run calibrate-field --synthetic                        # try it first: a digital field, no hardware needed
uv run calibrate-field --field 1.2 0.8                    # your real field's size, tags at ids 0-3 on the corners
uv run calibrate-field --layout 0:0,0 1:1.2,0 2:1.2,0.8 3:0,0.8  # or name ids/positions explicitly
uv run calibrate-field --origin center                    # (0,0) at the field centre, no picker
uv run calibrate-field --pick-origin                      # change only the origin, keep the saved pose
```

Hold the camera steady on the field; it captures frames until all four
reference tags have been seen 10+ times, then reports rotation/translation
spread across those frames — a small spread means the solve is stable, a large
one means something is loose or a tag ID is ambiguous. Saves to
`calib/field_pose.json`, together with the `--field` size, so you pass your
measured size **once, here**: `track`, `track-ball` and `track-combined` all
default to it, and warn if you pass a different `--field` later. A file saved
before this has no size in it and falls back to `1.2 0.8`, as before — re-run
`calibrate-field --field W H` (or `--pick-origin --field W H`) to stamp it.

#### Choosing where `(0,0)` is

Right after the solve — same run, camera still open — a second window asks
where the origin should be. The field is drawn over the live picture:

- **click** a spot on the field — it snaps to a corner, edge midpoint or the
  centre if you click within a few pixels of one (grey dots)
- **`c`** puts it at the centre
- **Enter** accepts what's shown
- **`d`** or **Esc** keeps the first corner, exactly as before

The red/green axes jump to wherever you click, so what you see is what every
tracker will report. Calibration comes first because a click is only a
position once the field pose is known. The origin is saved as `"origin"`
next to the pose in `calib/field_pose.json`; `track`, `track-ball` and
`track-combined` all pick it up, shift the rectangle, in/out test and plan
view to match, and show `origin (x, y)` in the window header. Leave it out
and nothing changes.

To change it later without recalibrating: `uv run calibrate-field
--pick-origin`. To ignore it for one run: `--corner-origin` on any tracker.
Re-running calibration (here or `track --calibrate-live`) keeps a saved origin
unless you pick a new one — it's a point on the field, not on the camera.

> **Cheapest real-hardware test: paper, no printer calibration needed.**
> Print (or `uv run serve-tag --save-png`) 4 same-size AprilTags, or use
> `uv run serve-tag --field-sheet field.png` to lay all 4 out on one A4-sized
> sheet already positioned at a field rectangle's corners. Measure whatever
> comes out of the printer with a ruler, then
> `uv run calibrate-field --field <measured W> <measured H>`. You do need
> PhotonVision's lens calibration first; there is no guessed-lens fallback.

#### One tag instead of four — `--sequential`

No field to fix four tags to, or nothing to fix them with? Carry a single tag
to each corner instead — a phone, a print-out held by hand, or a robot that
drives itself there. The camera stays fixed; the tag doesn't need to stay put
anywhere except while it's being measured.

```powershell
uv run calibrate-field --sequential --synthetic          # try it first: no hardware needed
uv run calibrate-field --sequential --field 1.2 0.8      # real run: one tag, moved to each corner in turn
```

A window prompts you through the 4 corners one at a time — `corner 1/4: place
ONE tag at field (0, 0)`, and so on. At each one: hold the tag there (exactly
one tag must be visible — a second one in frame is treated as ambiguous and
ignored), watch it capture samples, and press `q` once it says enough are in
to lock that corner in and move to the next (`r` re-does the current corner,
Esc aborts). After the fourth corner it solves once, from the four averaged
points, and reports **reprojection error** in pixels — project each known
corner back through the solved pose and see how far off the tag's measured
position lands; under ~1px is a good, unambiguous solve. There is no
frame-to-frame agreement to report here the way the simultaneous mode has
(there's only one solve), which is why the quality signal is different.

Same idea from `track` in one step:

```powershell
uv run track --calibrate-live --sequential
```

### One command instead of two — `track --calibrate-live`

```powershell
uv run track --calibrate-live
```

Does exactly what `calibrate-field` does — point a real camera at the
reference tags, it captures and solves live in the same window — and then,
instead of exiting, immediately continues into the normal tracking loop using
the pose it just solved. Also saves to `calib/field_pose.json` (or
`--field-pose PATH`), so a later plain `uv run track` picks it up too. Use
this when you just want to see the whole real pipeline work in one session;
use the separate `calibrate-field` command when you want to calibrate once
and walk away, or fine-tune the layout/tag-size flags without re-tracking
each time.

### Watching it work with no hardware — `track --synthetic-camera`

```powershell
uv run track --synthetic-camera
```

Opens the exact same window as step 2, but nothing is plugged in: the field,
the camera, and the tracked tag are all simulated (`tag_tracking.synthetic`).
On startup it prints a live calibration — solving the field pose from 4
simulated reference tags, the same math `calibrate-field --synthetic` uses —
and then the main loop runs exactly as it would against PhotonVision, except
the frames come from a digital camera and the tags from the local detector
(`detect.py`: WPILib's `AprilTagDetector`, the one PhotonVision runs). One tag
orbits the field on its own, swinging outside the boundary on the flat sides
of its path so you also see the red `OUT` state without carrying anything past
the edge yourself.

It is a flag on `track`, not a separate command, on purpose: the overlay, plan
view, readout, and every key are all the *same code* `track` always runs —
only the source of frames changes. `w` (wall/floor) prints an explanation
instead of acting, since there is no real camera pose to toggle here. To run
the real PhotonVision path with no hardware instead, see
[`photon-sim`](#no-hardware-photon-sim).

### What changes once `field_pose.json` exists

Nothing, until you run `track`. Then:

```powershell
uv run track --tag-size 0.080
```

picks up `calib/field_pose.json` automatically — the on-screen header will say
`measured, averaged over N frames...` instead of `synthetic wall, ... m ahead`,
and the `w` (wall/floor) key stops doing anything, because there is no longer
a "which fake pose" to toggle. Pass `--synthetic-field` to go back to the
made-up field for a demo without disturbing the saved calibration, or
`--field-pose PATH` to point at a different saved pose (e.g. one solved for a
second camera).

A custom origin from `calibrate-field` is picked up the same way; pass
`--corner-origin` to report from the first corner instead for one run.

Both `--synthetic` modes never overwrite your real calibration: they only save
if you also pass `--out`.

## Swapping in the real field

`calibrate-field` above is the operational way to do this. What follows is
what it wraps, and the place to look if you want a layout that isn't 4 tags at
rectangle corners, or to call the solve from your own code.

Everything about where the field is lives in `vision-core/src/vision_core/field.py`
— not in this skill, because the ball tracker needs exactly the same answer.

The contract is `CameraFieldTransform`: a rigid transform holding `R` and `t`
such that `p_camera = R @ p_field + t`. Subclasses only decide **how those are
obtained**; every conversion, and the `TagFieldPose` that comes out, is
implemented once in the base class.

- `SyntheticFieldTransform` — today. Invents the pose from a distance and an
  angle. Nothing physical corresponds to it.
- `ReferenceTagFieldTransform` — the RoboCup path. Already written. Give it the
  detections, a `layout` of `tag_id -> (x, y)` in metres, and the camera matrix;
  it runs `solvePnP` and fills in the same `R` and `t`.

To switch, change `build_transform()` in `track.py`:

```python
layout = {0: (0.0, 0.0), 1: (1.2, 0.0), 2: (1.2, 0.8), 3: (0.0, 0.8)}
transform = ReferenceTagFieldTransform.from_detections(dets, layout, K, dist)
```

Downstream — `tag_field_pose`, the overlay, the plan view, the `(id, x, y, theta)`
output — does not change, and `selfcheck` section 3 already exercises exactly this
substitution. `ReferenceTagFieldTransform` also has `.save()` / `.load()` so you
can solve the pose once from a good frame and reuse it for a fixed camera —
which is exactly what `calibrate-field` (previous section) automates, so you
will not usually write this by hand.

## Conventions

**Camera frame** (OpenCV): +X right, +Y down, +Z forward out of the lens. Metres.

**Field frame**: origin at one corner, +X along `width`, +Y along `height`,
+Z along the surface normal. Right-handed. `(0,0)` is the origin corner and
`(width, height)` the far one — or, with a custom origin saved by
`calibrate-field`, `(0,0)` is that point and the axes keep their directions.

**theta**: rotation about the field normal, degrees in `(-180, 180]`, measured
from field +X, counter-clockwise positive, **0 when the tag is upright**. Use
`--heading-offset` if a robot's tag is not mounted square to the way it drives.

The detector's own tag frame has +X pointing to the tag's visual *left* — a 180°
rotation from what you would guess. That is handled once, in `TAG_VISUAL_RIGHT`
in `src/tag_tracking/pose.py`, and `selfcheck` fails loudly if it ever changes.

The camera view is **not mirrored**. It shows what the camera sees, so the
overlay lands on the phone exactly, and the plan view agrees with the video.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No detection at all | Screen too dim, or glare. Max brightness, tilt away from windows. |
| Detection flickers | Phone auto-dimming, or motion blur — move slower, add light. |
| Detected but position jumps | Tag too small in frame. Use `--size-mm 100`, or move closer. |
| Distances feel wrong | Tag size, or PhotonVision's lens calibration. See [Does lens calibration matter?](#does-lens-calibration-matter) |
| `no PhotonVision camera appeared on NetworkTables` | PhotonVision not running, no camera enabled, or its NetworkTables Server Address doesn't point at this machine (`127.0.0.1` if same machine). A firewall on this machine can block it too. |
| `publishes several cameras` | Pick one with `--photon-camera NAME`. |
| `publishes no lens calibration` | Calibrate in PhotonVision's UI (Cameras > Calibration), at the pipeline's resolution. |
| `sending targets that are not AprilTags` | The PhotonVision pipeline isn't AprilTag / 36h11. |
| `could not open PhotonVision's stream` | `track` and `calibrate-field` carry on with a blank canvas; `track-combined` stops. Check the URL in PhotonVision's camera view and pass `--stream URL`. |
| Overlay doesn't line up with the tags | The resolution guess is wrong: pass `--photon-res W H` with the pipeline's resolution. |
| `selfcheck` section 4 fails, no result over NetworkTables | A tracker on this machine is already running the server. Close it and re-run. |
| Keys do nothing, mouse works | Click the video window first. If letters are still ignored, an input method is swallowing them — switch to English, or press `Esc`, which no IME intercepts. |
| `q` does nothing in a calibrate tool | By design until enough frames are captured. The window's counter shows how many; `Esc` always aborts. |
| Window too big for the screen | `--display-width 1300` |
| Field not visible in frame | `--distance` too small crops it, too large shrinks it. Default 1.8 m fits 1.2 × 0.8. |

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
  calibrate_field.py  calibrate-field: field pose from 4 reference tags
  calibrate_camera.py calibrate-camera: CLI wrapper around vision_core.charuco
print/                sample printable tag assets (field_a4.png, tag PNGs)
```

and, from the shared floor one level up:

```
../vision-core/src/vision_core/
  field.py       the swap point: Field, CameraFieldTransform, Synthetic/ReferenceTag
  camera.py      open_camera, list_cameras, lock_camera
  intrinsics.py  load / save / from_fov / hfov_of
  kalman.py      the linear Kalman filter both trackers build on: gate, Joseph update, angle wrap
  charuco.py     *measure* the camera matrix -- ChArUco board calibration + its digital self-test
  planview.py    draw_field on the video, PlanView beside it
../calib/        field_pose.json (calibrate-field) and intrinsics.json
                 (calibrate-camera; read by track-ball, not here) land here
```

`pose.py` holds what is *about AprilTags*; `field.py` holds what is *about the
field*. The line matters: when reference tags get mounted, `field.py` changes and
nothing in this folder does. `charuco.py` is camera-only calibration math and
lives in vision-core for the same reason; `calibrate_field.py` needs a tag
detector to see the reference tags at all, so it stays here.
