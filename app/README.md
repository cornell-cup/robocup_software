# app: RoboCup Control (`robocup-web`)

The robocup stack in a browser: the simulator (view it, drive robots, start and
stop it), field calibration from PhotonVision, and ball colour profiles. One server; the pages and
any script talk to it through the same API.

```powershell
uv run robocup-web                       # http://localhost:5801, opens a browser tab
uv run robocup-web --with-photonvision   # and start PhotonVision as well
uv run robocup-web --no-browser
uv run robocup-web --host 0.0.0.0        # reachable from other machines (no login yet!)
```

**Getting PhotonVision.** It is too big for git (the jar is over GitHub's 100 MB
file limit), so the app downloads it: *Download* next to PhotonVision in the
header, on every page, or

```powershell
uv run setup-photonvision
```

Either one fetches the PhotonVision jar of the version our photonlibpy needs and a
Temurin 21 Java to run it (some other Javas, Oracle's JDK 21 among them, ship an
MSVC runtime too old for it) into the `photonvision` folder next to the repo,
about 160 MB. Windows and Linux only: PhotonVision publishes no macOS build.

**PhotonVision** can be started from the app instead of a terminal: *Start*
next to PhotonVision in the header, or pass `--with-photonvision`. The app looks for
PhotonVision's folder -- the `photonvision-*.jar` plus a Java to run it with,
e.g. a Temurin 21 JRE unzipped next to it -- in `--photonvision-dir`, else
`$ROBOCUP_PHOTONVISION_DIR`, else a `photonvision` folder next to the repo. It
stops a PhotonVision it started when it exits, and leaves one started any other
way alone. Setup PhotonVision still needs (no NetworkTables address, no camera
enabled) is read off its log and shown on the page.

PhotonVision's own UI stays where it is (port 5800): the camera, its pipeline
and its lens calibration are set up there. This app does what PhotonVision
cannot: the field, the simulator, and the ball colour profile.

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
  **Keys** and drive it with `W A S D`, `Q E` to rotate.
- Strategy, dribble, ball-stuck reset and RL kick toggles; kick; score, clock, goals.
- The same keys as the pygame window: `1`–`4` controller, `5` strategy, `0` dribble,
  `9` ball reset, `R` RL kick, `K` / `Enter` kick.

Run the pygame viewer **or** this page, not both: both send on the manual-command port
(9093), and only one can own it. The page says so if it can't.

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
| GET | `/photonvision/status` | found jar / Java / versions, running, up, failure, setup hints |
| POST | `/photonvision/start`, `/photonvision/stop` | stop only stops one this app started |
| GET | `/photonvision/log?after=N` | PhotonVision's output lines after line N |
| GET | `/calib/status` | connection, detections, capture progress, result, overlay geometry, saved file |
| POST | `/calib/connect` | `{"camera", "nt_server", "stream", "res": [w, h]}`, all optional |
| POST | `/calib/disconnect`, `/calib/capture/stop`, `/calib/solve`, `/calib/load-saved`, `/calib/save` | |
| POST | `/calib/capture/start` | `{"tag_size", "field_w", "field_h", "min_frames"?, "layout"?: {"0": [x, y], ...}}` |
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
