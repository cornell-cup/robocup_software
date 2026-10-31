# app: RoboCup Control (`robocup-web`)

The whole stack in a browser: run and watch the **simulator**, drive the **real
robots** over Bluetooth, and set up the **camera** (PhotonVision, field
calibration, ball colour, live tracking). One server on port 5801; the pages
and any script use the same API. The [main README](../README.md) shows how it
fits with the rest.

## Set up

1. Do the repo setup once: `uv sync` at the repo root
   ([main README](../README.md#set-up-step-by-step)). The pages are already
   built and committed, so you need no Node.
2. Start the app. It opens http://localhost:5801 in your browser:
   ```powershell
   uv run robocup-web
   ```
3. **For the camera pages only:** get PhotonVision. In the header, click
   **Download** next to PhotonVision (about 160 MB, once), then **Start**. Or
   from a terminal: `uv run setup-photonvision`, then `uv run robocup-web --with-photonvision`.
4. **For the camera pages only:** set PhotonVision up once in its own UI
   (header link, port 5800): add the camera, an AprilTag 36h11 pipeline, lens
   calibration, and NetworkTables Server Address `127.0.0.1`. Step by step:
   [PhotonVision setup](../vision/tag-tracking/README.md#photonvision-setup).
   The header tells you what is still missing.
5. **For the Robots page only:** a flashed XRP, and Bluetooth on this computer
   ([robot/README.md](../robot/README.md), steps 1–4).

| Command | What it does |
| --- | --- |
| `uv run robocup-web` | Start the app and open a browser tab |
| `uv run robocup-web --with-photonvision` | Also start PhotonVision (stopped again when the app exits) |
| `uv run robocup-web --no-browser` | Don't open a tab |
| `uv run robocup-web --host 0.0.0.0` | Reachable from other machines. There is no login: anyone who can reach it can drive the robots |
| `uv run setup-photonvision` | Download PhotonVision and a Java for it, without the app |

## Try it

A first tour, from nothing to real robots playing. Stop after any step.

1. **Simulator, no hardware.** Simulator page → **Start simulator** → turn on
   **Strategy (AI)**. Click the field to send the selected robot somewhere, or
   pick **Keys** and drive it with W A S D.
2. **Drive a real robot.** Robots page → type its name (e.g. `XRP-01`) → **Add**.
   When it shows *Connected*, set *Driven by* to **Keyboard** and drive with
   W A S D, Q E, Space for the dribbler.
3. **Calibrate the camera field** (once, or after moving the camera). Field
   calibration page → **Connect** → enter the tag size and field size →
   **Start capture** → **Solve** → pick the origin → **Save calibration**.
4. **Make a ball profile** (once per ball and room). Ball profile page →
   **Connect** → name it → **Create profile** → drag a box over the ball → tune →
   **Save profile**.
5. **Track.** Tracking page → enter your tag size and ball profile → **Start tracking**.
   You see the camera and the field live. The **Drive robots** card there lets
   you drive while you watch.
6. **Real robots in the sim.** Simulator page → *Robots and ball:* **Live camera**,
   *Tag → robot* e.g. `9:0` → **Start simulator**. Then Robots page → set the
   robot to **Follow sim** → robot 0, and turn on **Strategy (AI)**. The real
   robot plays as robot 0, while the camera sees it.

**PhotonVision, in more detail.** It is too big for git (the jar is over
GitHub's 100 MB file limit), so the app downloads it: the jar of the version
our photonlibpy needs, and a Temurin 21 Java to run it (some other Javas,
Oracle's JDK 21 among them, ship an MSVC runtime too old for it), into the
`photonvision` folder next to the repo. Windows and Linux only: PhotonVision
publishes no macOS build. The app looks for that folder (the
`photonvision-*.jar` plus a Java) in `--photonvision-dir`, else
`$ROBOCUP_PHOTONVISION_DIR`, else `photonvision` next to the repo. It stops a
PhotonVision it started when it exits, and leaves one started any other way
alone. Setup PhotonVision still needs (no NetworkTables address, no camera
enabled) is read off its log and shown on the page. Its own UI (port 5800)
keeps the camera, its pipeline and its lens calibration; this app does the
rest.

## Pages

**Simulator** replaces the pygame window (`viz_node.py`).

- Start / stop the sim from the page (`run_pipeline.py --no-viz` under the hood), with the
  strategy colour and where the robots come from: **Simulated**, **Live camera** (the
  Tracking page's tracker) or a **Recording** of an earlier session. For the camera ones the
  page also takes the field size from the calibration, the tags to ignore (the corner tags,
  0-3, by default), a tag-to-robot map and axis flips -- run_pipeline's `--field`,
  `--ignore-tags`, `--tag-map`, `--flip-x/y`. A sim started in a terminal with `--no-viz`
  shows up too.
- Click the field to send the selected robot there (PD / time-optimal / MPC), or switch to
  **Keys** and drive it with `W A S D`, `Q E` to rotate. Keys move it in field directions
  (W = up the field), whichever way it faces, and while you drive a blue robot with keys
  the strategy leaves it alone. A robot the camera owns doesn't move in the sim: keys and
  clicks reach it only through a real robot set to *Follow sim* on the Robots page, and the
  Drive card says so.
- Strategy, dribble, ball-stuck reset and RL kick toggles; kick; score, clock, goals.
- The same keys as the pygame window: `1`–`4` controller, `5` strategy, `0` dribble,
  `9` ball reset, `R` RL kick, `K` / `Enter` kick.

Run the pygame viewer **or** this page, not both: both send on the manual-command port
(9093), and only one can own it. The page says so if it can't.

**Robots** drives the real XRP robots over Bluetooth (PestoLink), several at once, with
the same code as `python -m laptop` ([robot/README.md](../robot/README.md)):

- **Add** a robot by its `ROBOT_NAME` (8 characters at most), or **Scan** for the ones
  nearby. The server keeps a link to each and reconnects on its own; *Remove* stops and
  disconnects it. Only one program can hold a robot's link, so quit `python -m laptop`
  for a robot first.
- **By hand**: pick what drives it -- the keyboard (`W A S D`, `Q E` turn, `Space`
  dribbler, `1`–`3` speed) or a gamepad plugged into this computer (left stick moves,
  right stick turns, A runs the dribbler). Each input drives one robot, so a keyboard and
  two gamepads drive three robots.
- **Follow sim**: the robot takes the commands robot_node computes for one sim robot, so
  strategy, or a click / `W A S D` on the Simulator page, drives the real robot, closed-loop
  through the camera. Start the sim with **Live camera** and map the robot's tag to that sim
  robot (*Tag → robot*). It only moves while the camera sees it, and the tag's heading must
  point at the dribbler (the tracker's *heading offset*).

A robot stops on its own a quarter second after its commands stop: a closed tab, an
unplugged gamepad, a sim that went away. The **Tracking** page has a *Drive robots*
card with the same by-hand driving, so you can drive while watching the camera; while
the keyboard drives a robot, its keys go to the robot rather than to that page's shortcuts.

**Tracking** runs `track-combined` (headless, publishing on ZMQ port 5556 for the sim):
tag size, ball profile, *record this session* (to `vision/runs/`, for replaying), and under
*more options* every other flag it has, grouped: tags (tag height, heading offset, how long
lost tags are kept, the Kalman filter's noise), ball (min. match, radius, filter noise,
airborne), field (calibration file, size, origin, a made-up field), PhotonVision (camera,
NetworkTables server, stream, resolution) and output (poses/states in the log, a live-state
file, an HTTP port, the camera view's size).

While it runs the page shows the camera (the tracker's own drawn view: overlays and its
top-down plan) and the field drawn live as the Simulator draws it, together or one at a
time; point at the field for coordinates. *While tracking* has the tracker window's keys:
grid (`G`), trails (`T`), ball mask (`M`), the ball's min. match (slider, `-` / `=`),
wall/floor for a made-up field (`W`) and reset (`R`); the keys work on the page too. The
header strip counts frames dropped, PhotonVision results skipped and ball dropouts. *Show the
robots in the simulator* sets the Simulator page up to mirror it. Starting the tracker closes the Field
calibration page's PhotonVision connection, since both need it, and calibration refuses to
connect while tracking.

**Field calibration** is `calibrate-field`, live from PhotonVision, with buttons:

1. **Connect** to PhotonVision (it must point its NetworkTables server address at this
   machine). Close any running `track` / `track-combined` first: the NetworkTables server
   can only run once per machine.
2. Enter the **tag size** (black square edge, metres) and the field size, then
   **Start capture**: frames that see every reference tag are collected. **Solve**.
   With only one tag, turn on **One tag at a time** (`calibrate-field --sequential`):
   put it at each corner in the order listed, counterclockwise seen from above, let
   go until the count fills, then **Confirm corner** (`q`; `r` retries, **Back a
   corner** redoes the one before). Any tag id works; only one may be in view.
3. Pick the **origin**: corner, centre, typed coordinates, or click the picture (snaps to
   corners, edge midpoints and the centre). **Save** writes `vision/calib/field_pose.json`,
   which every tracker loads the next time it starts.

"Move the origin of the saved calibration" changes only the origin, like
`calibrate-field --pick-origin`.

**Ball profile** is `calibrate-ball`, but sampling **PhotonVision's camera stream**:
the same picture `track-combined` looks for the ball in, so the profile matches the
colours the tracker sees.

1. **Connect** to the stream (empty URL means PhotonVision on this machine, port 1181).
2. **Create** a profile (name and ball radius) or **Edit** a saved one.
3. **Drag a box** over the ball to sample it; Shift-drag adds to the sample. The view
   switches to *Detected*: what the profile accepts, outlined, the rest dimmed
   (*Likelihood* is the raw heatmap). Tune the threshold, saturation floor and the two
   spreads until there is one blob on the ball and nothing else, roll the ball around,
   then **Save**. Profiles go to `vision/calib/ball_color.json`; use one with
   `--ball-profile NAME`.

*Colours in the scene* charts the hues the room already has, to judge whether the
ball's colour stands out.

## API

Everything is under `/api/v1`; interactive docs at `/api/docs`. Errors come back as
HTTP 400 with `{"error": "..."}`.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/info` | name, version |
| GET | `/sim/status` | process status, `up`, toggles |
| GET | `/sim/world` | the latest world state (simulation_node's message, plus `attackers`) |
| POST | `/sim/start` | `{"strategy": true, "color": "blue", "field": [w, h] \| null, "field_from_calibration": bool, "vision": "tracker" \| "tcp://..." \| ".jsonl path" \| null, "ignore_tags": "0,1,2,3", "tag_map": "9:0", "flip_x": bool, "flip_y": bool, "replay_speed": 1}` |
| POST | `/sim/stop` | |
| GET | `/sim/log?after=N` | sim output lines after line N |
| POST | `/sim/command` | one command, see below |
| WS | `/sim/ws` | world state pushed ~30×/s; send commands as JSON text |
| GET | `/robots/status` | `{"robots": [{"name", "state", "connected", "error", "moving", "command_age_s", "follow", "tracked"}], "sim_up"}` |
| POST | `/robots/add`, `/robots/remove` | `{"name": "XRP-01"}` |
| POST | `/robots/follow` | `{"name", "sim_robot": 0}` to follow a sim robot, `null` to drive it by hand |
| POST | `/robots/scan` | `{"devices": [{"name", "rssi"}]}`, the Bluetooth devices nearby |
| WS | `/robots/ws` | robot status pushed 5×/s; send `{"type": "drive", "name", "forward", "left", "turn", "dribble"}` |
| GET | `/photonvision/status` | found jar / Java / versions, running, up, failure, setup hints |
| POST | `/photonvision/start`, `/photonvision/stop` | stop only stops one this app started |
| GET | `/photonvision/log?after=N` | PhotonVision's output lines after line N |
| GET | `/calib/status` | connection, detections, capture progress, result, overlay geometry, saved file |
| POST | `/calib/connect` | `{"camera", "nt_server", "stream", "res": [w, h]}`, all optional |
| POST | `/calib/disconnect`, `/calib/capture/stop`, `/calib/solve`, `/calib/load-saved`, `/calib/save` | |
| POST | `/calib/capture/start` | `{"tag_size", "field_w", "field_h", "min_frames"?, "layout"?: {"0": [x, y], ...}, "sequential"?, "frames_per_corner"?}` |
| POST | `/calib/corner` | one tag at a time: `{"action": "confirm" \| "retry" \| "back"}` |
| POST | `/calib/origin` | `{"mode": "corner" \| "center"}`, `{"mode": "field", "x", "y"}` or `{"mode": "pixel", "u", "v"}` |
| GET | `/tracking/status` | tracker process and its newest output (tags, ball, stats) |
| POST | `/tracking/start` | `{"tag_size", "ball_profile", "record"?, "tag_height"?, "heading_offset"?, "tag_coast"?, "tag_sigma_a"?, "tag_sigma_alpha"?, "ball_min_conc"?, "ball_radius_mm"?, "ball_sigma_a"?, "ball_sigma_a_air"?, "ball_sigma_px"?, "ball_airborne"?, "corner_origin"?, "field_pose"?, "field"?: [w, h], "synthetic_field"?, "mode"?, "cam_height"?, "pitch"?, "distance"?, "yaw"?, "pitch_wall"?, "photon_camera"?, "nt_server"?, "stream"?, "photon_res"?, "print_poses"?, "print_states"?, "json_out"?, "serve_http"?, "display_width"?}`; `/tracking/stop` |
| POST | `/tracking/control` | `{"key": "g" \| "t" \| "m" \| "w" \| "r"}` or `{"ball_min_conc": N}`: the tracker window's keys |
| WS | `/tracking/ws` | the tracker's output as it comes, `{"up", "age_s", "n", "doc"}` |
| GET | `/tracking/log?after=N`, `/tracking/stream.mjpg` | tracker output; its drawn view |
| GET | `/recordings` | recorded sessions, newest first |
| GET | `/ball/status` | stream, the profile being edited, mask stats, saved profiles |
| POST | `/ball/connect` | `{"stream": URL}`, optional; `/ball/disconnect` |
| POST | `/ball/new` | `{"name", "radius_mm"?, "note"?}`; `/ball/load` `{"name"}` |
| POST | `/ball/sample` | `{"box": [x0, y0, x1, y1], "add"?: true}` in stream pixels; `/ball/clear` |
| POST | `/ball/tune` | any of `threshold`, `s_min`, `h_spread`, `s_spread`, `radius_mm`, `name`, `note` |
| POST | `/ball/save` | writes the profile into ball_color.json |
| GET | `/ball/scene-hues` | hue histogram of the current picture |
| GET | `/ball/stream.mjpg?view=overlay\|mask\|camera` | the picture, with what the profile detects |
| GET | `/calib/stream.mjpg` | the camera picture, raw pixels (what the overlay coordinates are in) |

Sim commands (`/sim/command` or the socket):

```json
{"type": "target", "robot": 0, "x": 4.5, "y": 3.0, "mode": "pd"}      // mode: pd | time | mpc
{"type": "drive", "robot": 0, "vx": 1.0, "vy": 0.0, "w": 0.0}         // vx, vy in [-1, 1]; w rad/s, |w| <= 5
{"type": "kick", "robot": 0}
{"type": "strategy", "enabled": true}                                  // also: dribble, rl_kick, ball_stuck_reset
```

A robot being driven is stopped if its driver goes quiet for 0.4 s (closed tab,
dropped connection), so keep sending `drive` at ~20 Hz while it should move.

A real robot's `drive` (on `/robots/ws`) is in the robot's own frame: `forward` and `left`
in -1..1 (front = dribbler), `turn` in -1..1 counter-clockwise. The same rule applies,
with 0.25 s: keep sending while it should move.

From Python, for example:

```python
import json, urllib.request
cmd = {"type": "target", "robot": 1, "x": 2.0, "y": 1.5, "mode": "mpc"}
urllib.request.urlopen(urllib.request.Request(
    "http://localhost:5801/api/v1/sim/command", json.dumps(cmd).encode(),
    {"Content-Type": "application/json"}))
```

## Working on the pages

Two parts: `server/` is the Python package (`robocup_web`, FastAPI), and `web/` is
the pages, a React 19 + TypeScript app built with Vite into
`server/src/robocup_web/static/`, which the server serves. The built files are
committed, so running the app needs no Node; changing the pages does (Node 20+).

`web/src/`: `App.tsx` (header and routing), `pages/` (Simulator, Tracking,
Calibration, Ball), `components/` (FieldView canvas, Card, Toggle, ...), `lib/` (API client,
the live sim and tracking sockets, small stores), `styles/` (one global theme file plus one
per page).

```powershell
cd app/web
npm install
npm run dev      # http://localhost:5173 with hot reload; needs `uv run robocup-web` running for /api
npm run check    # type check
npm run build    # rebuild static/ -- commit the result with your change
```

Server code, in `server/src/robocup_web/`: `app.py` (routes), `procs.py` (starting and stopping
programs), `sim.py` (the sim link and process), `tracking.py` (the tracker), `calib.py`
(field calibration), `ball.py` (ball colour profiles), `photonvision.py` (starting
PhotonVision).
