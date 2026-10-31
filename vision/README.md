# vision: camera tracking

The camera side of the stack. It finds the robots' AprilTags and the ball in one
overhead camera and reports them in **metres, in field coordinates**, with
filtered velocity. The simulator and strategy consume these positions; see the
[root README](../README.md) for how the pieces fit together.

The AprilTags are detected by [PhotonVision](https://photonvision.org), which
runs separately, owns the camera, and sends the tags here over NetworkTables.

| Folder | What it is | Output |
| --- | --- | --- |
| [`tag-tracking/`](tag-tracking/) | AprilTags on a robot or a phone; also field calibration, `selfcheck` and `photon-sim` | `TagFieldState`: position, heading, velocity, turn rate |
| [`ball-tracking/`](ball-tracking/) | A colored ball, found by its hue rather than a marker | `BallFieldState`: position, height, velocity, grounded |
| [`combined-tracking/`](combined-tracking/) | The robot's tag *and* the ball, one camera, one window. What the sim and the web app run | both of the above, together |
| [`vision-core/`](vision-core/) | Shared floor, not a skill: field geometry, the camera-to-field transform, intrinsics, camera control, the Kalman filter, drawing | |
| [`calib/`](calib/) | Measured calibration shared by every tool: `field_pose.json`, `ball_color.json`, `intrinsics.json` | |

<a id="quick-start"></a>

## Set up

Do this once. Steps 1-3 are needed for any live camera; steps 4-6 make the
numbers real.

1. **Set up the repo**: run `uv sync` at the repo root (see
   [root README, Setup](../README.md#set-up-step-by-step)). There is one shared `.venv` for
   the whole repo; run every command with `uv run`, from any folder. Nothing
   in `vision/` needs installing on its own.
2. **Get and start PhotonVision.** It is not a Python package, so `uv sync`
   does not install it. Download it with `uv run setup-photonvision`, or with
   *Download* in the header of the web app (`uv run robocup-web`, then
   http://localhost:5801). Start it with *Start* in that header, or
   `uv run robocup-web --with-photonvision`. See [app/README.md](../app/README.md).
3. **Configure PhotonVision once per camera**, in its UI at
   http://localhost:5800: AprilTag 36h11 pipeline, lens calibrated, and
   NetworkTables Server Address pointed at the machine running the trackers.
   Step by step: [PhotonVision setup](tag-tracking/#photonvision-setup).
4. **Measure your tag.** Measure the black square of your robot's AprilTag,
   edge to edge, in metres. Pass it as `--tag-size` (or the *tag size* box in
   the web app) every time. The default, `0.080`, only fits a phone tag from
   `serve-tag`.
5. **Calibrate the field** (optional to start, needed for real positions).
   Easiest: the web app's **Field calibration** page. Or from a terminal:

   ```powershell
   uv run calibrate-field --field 1.2 0.8 --tag-size 0.177   # your measured field and tag sizes
   ```

   This writes `calib/field_pose.json`, which every tracker loads from then on.
   Details: [tag-tracking, One-time calibration](tag-tracking/#one-time-calibration-making-it-real).
6. **Make a ball color profile** (only if you track the ball). There is no
   default ball. Easiest: the web app's **Ball profile** page, which samples
   PhotonVision's camera stream, the same picture `track-combined` uses. Or
   from a terminal, on a local webcam:

   ```powershell
   uv run calibrate-ball --profile myball --radius-mm 20   # drag a box over YOUR ball, press s
   ```

## Try it

**No hardware:**

1. `uv run selfcheck`. You should see four sections of `[ok ]` lines ending in
   *"All checks passed."*
2. `uv run track --synthetic-camera`. A window opens with a simulated camera
   view on the left (a green field, one tag orbiting it) and a top-down plan
   view on the right. Press `q` to quit.
3. `uv run photon-sim`, then in a second terminal
   `uv run track --synthetic-field --mode floor`. Same window, but the tags
   now arrive over NetworkTables from a stand-in PhotonVision, the way the
   real ones do. See [photon-sim](tag-tracking/#no-hardware-photon-sim).

**Real camera** (after Set up steps 1-3, plus 6 for the ball):

1. Easiest: run `uv run robocup-web`, open the **Tracking** page, enter the
   tag size and ball profile, and start it. You should see the camera with the
   tags and ball outlined, and the field drawn live beside it.
2. Or from a terminal:

   ```powershell
   uv run track-combined --ball-profile myball --tag-size 0.080   # tag and ball, one window
   uv run track --tag-size 0.080                                # tags only
   ```

   No robot tag handy? Run `uv run serve-tag`, open the URL it prints on your
   phone, and point the camera at it. Its 80 mm tag is `--tag-size 0.080`.

## Where to run from

Every `uv run` command works from any folder in the repo and uses the root
`.venv`. Relative output paths (`runs/...`, `session.jsonl`) land in the folder
you launch from, so launch from `vision/` if you want them here. `calib/`
always resolves to `vision/calib/`, whatever the folder. Each package also has
its own `pyproject.toml`, entry points and README.

### uv flags

| Command | What it does |
| --- | --- |
| `uv sync` | Make the root `.venv` match `uv.lock` exactly: the vision packages, pyzmq (for `--zmq-pub`), the simulator, the web app and the robot driver. Removes anything else. |
| `uv sync --extra rl` | Same, plus the RL packages (torch etc.). Only needed for `strategy/rl/`; if you use it, pass it every time, or a plain `uv sync` removes them. |
| `uv run <command>` | Run a command (`track`, `calibrate-ball`, ...) in the root `.venv`, syncing first if the lockfile changed. Never removes packages. |

Old instructions said `uv sync --all-packages --extra zmq`. Don't: pyzmq is
always installed now, `--extra zmq` errors (`Extra zmq is not defined`), and
`--all-packages` without `--extra rl` uninstalls the RL packages. Full
reference: [root README, Setup](../README.md#set-up-step-by-step).

## Command reference

**No hardware needed:**

| Command | What it does |
| --- | --- |
| `uv run selfcheck` | Proves the field math is correct |
| `uv run calibrate-camera --synthetic` | Proves lens-calibration math is correct |
| `uv run calibrate-field --synthetic` | Proves field-calibration math is correct |
| `uv run track --synthetic-camera` | Watch the whole pipeline work on a fake field |
| `uv run photon-sim` | A stand-in PhotonVision (synthetic field, real NetworkTables, stream on 1181), for running `track`, `calibrate-field` or `track-combined` with no hardware. See [tag-tracking](tag-tracking/#no-hardware-photon-sim). No ball |

**PhotonVision:**

| Command | What it does |
| --- | --- |
| `uv run setup-photonvision` | Download the PhotonVision jar matching our `photonlibpy`, plus a Java to run it, into a `photonvision` folder next to the repo |
| `uv run robocup-web --with-photonvision` | Start the web app and PhotonVision together. See [app/README.md](../app/README.md) |

**Prep, phone or printer:**

| Command | What it does |
| --- | --- |
| `uv run serve-tag` | One AprilTag on your phone, sized via the bank-card trick |
| `uv run serve-tag --field-sheet out.png` | All 4 reference tags laid out on one printable page |
| `uv run calibrate-camera --save-board-png board.png` | The ChArUco board to print, for `calibrate-camera` |

**Calibration, real camera:**

The lens for the tag tools is calibrated in PhotonVision's UI (Cameras >
Calibration), not here. See [PhotonVision setup](tag-tracking/#photonvision-setup).
The web app's **Field calibration** and **Ball profile** pages do the field and
ball steps in a browser.

| Command | What it does | Writes |
| --- | --- | --- |
| `uv run calibrate-field` | Measures the field position: point PhotonVision's camera at your 4 tags | `calib/field_pose.json` |
| `uv run calibrate-field --sequential` | Same, but one tag moved to each corner in turn (camera fixed) instead of 4 at once | `calib/field_pose.json` |
| `uv run calibrate-field --pick-origin` | Re-pick where `(0,0)` is (click it on the live view) without recalibrating | `calib/field_pose.json` |
| `uv run track --calibrate-live` | Field in one session, then starts tracking. Add `--sequential` for one tag | `calib/field_pose.json` |
| `uv run calibrate-ball` | Measures the ball's color: drag a box over it (local webcam) | `calib/ball_color.json` |
| `uv run calibrate-camera` | Measures a local webcam's lens: wave a printed board at it. Only `track-ball` reads the result; the tag tools use PhotonVision's calibration | `calib/intrinsics.json` |

The field step is optional to *start*. Without it the software believes
whatever `--cam-height` you type. Positions still move correctly; they just
aren't measured. PhotonVision's lens calibration is not optional: the tag
tools stop without one. Nor is the ball's color profile: there is no default
ball, so make one before `track-ball` or `track-combined` can find it.

**Tracking:**

| Command | What it does |
| --- | --- |
| `uv run track` | Tags. Auto-loads whatever is already calibrated |
| `uv run track-ball --ball-profile NAME` | Ball, on a local webcam. Same, and needs a color profile |
| `uv run track-combined --ball-profile NAME` | Both, one camera, one window. See [`combined-tracking/`](combined-tracking/). The web app's **Tracking** page runs this |
| `uv run unlock-camera` | Repair: a run that crashed with a local webcam locked leaves every app with a dark green picture. The ball tools (`track-ball`, `calibrate-ball`) also do this on start and exit. A PhotonVision camera's settings are PhotonVision's to fix |

`track`, `calibrate-field` and `track-combined` never open the camera
themselves: PhotonVision holds it, and they read its tags over NetworkTables.
Run one of them at a time (and close the web app's Field calibration
connection): each starts its own NetworkTables server unless given
`--nt-server`, and PhotonVision connects to one. `track-ball` still opens a
local webcam directly, so it cannot use a camera PhotonVision is holding.
`track-combined` is how you get the robot's tag and the ball from one camera:
the tags from PhotonVision, the ball from PhotonVision's camera stream.

### Getting live positions into another program

`track-combined` can publish what it is tracking to another process (a
simulator, another repo, anything) four independent ways. All are off unless
you ask; use one, or several at once. Full JSON shape and details in
[`combined-tracking`'s README](combined-tracking/#feeding-another-program).

| Command | What you get |
| --- | --- |
| `uv run track-combined --ball-profile NAME --json-out state.json` | One file, **overwritten** every frame: always "the current state" |
| `uv run track-combined --ball-profile NAME --serve-http 8000` | The same, over `GET http://localhost:8000/state`. Works from another machine or language, no filesystem access needed |
| `uv run track-combined --ball-profile NAME --json-log session.jsonl` | One JSON line **appended** per frame: a full history, not just "right now". `*.jsonl` and the `--json-out` files are gitignored: they are run output, not source |
| `uv run track-combined --ball-profile NAME --no-window --zmq-pub tcp://*:5556` | Every frame **pushed** on a ZeroMQ PUB socket as it happens, no window drawn. For a simulator mirroring the field live. pyzmq comes with `uv sync` |

### Flags that matter

`--field` and `--tag-size` are what make the numbers mean real metres. Always
pass your **ruler-measured** sizes, not the target you printed for. Both
default to a plausible demo size, so a bare command still runs, just against
the wrong physical size until you correct it.

| Flag | Meaning | Default | Used by |
| --- | --- | --- | --- |
| `--field W H` | Real field size, in metres. Saved with the calibration, so after `calibrate-field --field W H` every tracker uses it without being told | the saved size, else `1.2 0.8` | `calibrate-field`, `track` (incl. `--calibrate-live`, `--synthetic-camera`), `track-ball`, `track-combined` |
| `--tag-size` | Real black-square tag size, in metres. Measure yours | `0.080` | `track` (incl. `--calibrate-live`, `--synthetic-camera`), `calibrate-field`, `track-combined` |
| `--photon-camera NAME` | Which PhotonVision camera, by its nickname in the UI | the only one published | `track`, `calibrate-field`, `track-combined` |
| `--nt-server HOST` | Connect to an existing NetworkTables server instead of running one here | run it here | `track`, `calibrate-field`, `track-combined` |
| `--stream URL` / `--no-stream` | PhotonVision's camera stream, or none (tags on a blank canvas; not on `track-combined`, which needs it for the ball) | `http://<photonvision>:1181/stream.mjpg` | `track`, `calibrate-field`, `track-combined` |
| `--photon-res W H` | The pipeline's resolution, if the guess from the calibration is wrong | guessed | `track`, `calibrate-field`, `track-combined` |
| `--camera N` | Which local webcam index to use | `0` | `calibrate-camera`, `calibrate-ball`, `track-ball` |
| `--out PATH` | Where to save the calibration result | `vision/calib/...` | `calibrate-camera`, `calibrate-field` |
| `--field-pose PATH` | Field calibration file to load (or save, with `--calibrate-live`) | `calib/field_pose.json` | `track`, `track-ball`, `track-combined` |
| `--layout ID:X,Y ...` | Custom reference-tag positions, overrides the default 4 corners | corners of `--field` | `calibrate-field` |
| `--sequential` | One tag moved to each corner in turn (camera fixed), instead of 4 tags at once | off | `calibrate-field`, `track --calibrate-live` |
| `--frames-per-corner` | Samples to average at each corner | `30` | `calibrate-field --sequential`, `track --calibrate-live --sequential` |
| `--origin X Y` / `--origin center` | Put `(0,0)` here instead of the first corner, metres from that corner. Skips the click picker | ask by click | `calibrate-field` |
| `--no-origin` | Skip the origin picker, keep the first corner as `(0,0)` | off | `calibrate-field` |
| `--corner-origin` | Ignore a saved custom origin for this run, report from the first corner | off | `track`, `track-ball`, `track-combined` |
| `--tag-id` / `--size-mm` | Which tag id, and initial size in mm on the phone page | `0` / `80` | `serve-tag` |
| `--ball-profile` | Which named color profile to track | `test` | `track-ball`, `track-combined` |
| `--radius-mm` | The ball's real **radius**, stored in the profile | `20` | `calibrate-ball` |
| `--s-spread` / `--h-spread` | How far the color profile reaches beyond what was sampled, in histogram bins. Widen `--s-spread` if the ball vanishes in shade or under another lamp | `4` / `1` | `calibrate-ball` |
| `--cam-height` | Camera height above the field, metres. Ignored once `field_pose.json` exists | `1.5` | `track`, `track-ball`, `track-combined` |
| `--print-poses` / `--print-states` | Stream the numbers to the terminal | off | `track` / `track-ball`, both in `track-combined` |
| `--json-out PATH` | Write current detections to a file, overwritten every frame | off | `track-combined` |
| `--serve-http PORT` | Serve current detections over plain HTTP `GET` | off | `track-combined` |
| `--json-log PATH` | Append one JSON line per frame: a history, not just current state | off | `track-combined` |
| `--zmq-pub ENDPOINT` | Push every frame on a ZeroMQ PUB socket, e.g. `tcp://*:5556` | off | `track-combined` |
| `--no-window` | Headless: no window, no keys; Ctrl+C quits | off | `track-combined` |

Full per-command flag lists: `uv run <command> --help`. The calibration
workflow in detail: [`tag-tracking`'s README](tag-tracking/).

### Keys, in every window

`q` or `Esc` quits, `s` saves. The rest are per-tool and listed in each README.
Keys only reach the **video window**, so click it first. If letters do nothing
while the mouse still works, your input method is swallowing them: switch it
to English, or press `Esc`, which no IME intercepts.

## Not using uv?

It works with plain pip, with three catches. **Installing uv is still the easier
answer**: it is a single binary, needs no Python of its own, and does not touch
an existing setup:

```powershell
winget install --id=astral-sh.uv        # or: pip install uv
```

If you would rather use pip, make one venv at the repo root, the same place uv
would, so the simulator can use it too:

```powershell
cd robocup_software                     # the repo root, not vision
py -3.12 -m venv .venv                  # 3.12 or 3.13, see below
.venv\Scripts\activate
pip install -e vision/vision-core            # THIS ORDER MATTERS
pip install -e vision/tag-tracking
pip install -e vision/ball-tracking
pip install -e "vision/combined-tracking[zmq]"  # needs the two above; [zmq] adds pyzmq for --zmq-pub
pip install -r sim/requirements.txt        # optional: the simulator
```

This covers vision and the simulator only; the web app (`app/server`) and the
robot driver have their own dependencies, listed in the root `pyproject.toml`.

`-e` (editable) is required, not just convenient: `calib/` is found relative to
the installed `vision_core` source, so a non-editable install cannot find it.

1. **Python 3.12 or 3.13.** The root `pyproject.toml` caps uv below 3.14 until
   the rest of the stack (pygame, pymunk, torch) has been checked there. The
   packages' own `pyproject.toml`s don't, so pip will not stop you, but nothing
   says 3.14 works yet. The root's `.python-version` pins 3.12; uv obeys it
   automatically, pip does not. `photonlibpy` must match your PhotonVision
   build's version (see [PhotonVision setup](tag-tracking/#photonvision-setup));
   pip resolves it fresh within the pinned range.
2. **Install `vision-core` first.** The packages depend on it through
   `[tool.uv.sources] workspace = true`, which only uv understands. pip goes
   looking on PyPI instead, finds nothing, and stops with
   `No matching distribution found for vision-core`.
3. **There is no `pip install .` here.** This folder has no `pyproject.toml`;
   the uv workspace is defined at the repo root. Install the members directly.

The root `uv.lock` is committed and pins exact versions for uv users; pip users
resolve fresh and may get slightly different ones.

## Adding a skill

1. `mkdir new-skill/src/new_skill`, give it a `pyproject.toml` depending on
   `vision-core = { workspace = true }`.
2. Add it to `members` **and** `dependencies` in the root `pyproject.toml`, so
   `uv run <its-command>` works from anywhere in the repo.
3. Write a README that reads standalone.
4. Anything camera- or field-shaped you find yourself writing belongs in
   `vision-core`, not in the skill.

## Conventions

**Camera frame** (OpenCV): +X right, +Y down, +Z forward out of the lens. Metres.

**Field frame**: origin at one corner, +X along `width`, +Y along `height`,
+Z along the surface normal. Right-handed. `(0,0)` is the origin corner and
`(width, height)` the far one, unless you picked a **custom origin** in
`calibrate-field`, in which case `(0,0)` is that point instead. The axes keep
their directions; only the zero moves, and the window header says
`origin (x, y)` whenever one is in use.

**`--mode wall|floor`**: whether the made-up field (used until
`field_pose.json` exists) stands up facing the camera or lies flat. `floor` is
the physically meaningful one and assumes the camera is **hung over the field
centre looking straight down**, `--cam-height` up (1.5 m by default; set your
real mounting height). `wall` is easier to demo by hand. `track` defaults to
`wall`; `track-ball`, `track-combined` and `track --synthetic-camera` to
`floor`. The maths
works at any camera pose; both modes are covered by `selfcheck`.
