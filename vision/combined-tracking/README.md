# Combined tracking: robot tags and the ball, one camera

`track-combined` follows every AprilTag and the ball at the same time, from
PhotonVision's overhead camera, and reports both in field metres. It is the
tracker the web app's Tracking page runs, and the one the simulator mirrors.
For where it fits, see the [vision README](../README.md) and the
[repo README](../../README.md).

The tags come from PhotonVision over NetworkTables. The ball is found in
PhotonVision's camera stream, undistorted with the lens calibration done in
PhotonVision. Same field, same lens, same window.

## Set up

Do these once.

1. **Install.** At the repo root, run `uv sync`. That builds the one shared
   `.venv` for everything, pyzmq for `--zmq-pub` included. Run every command
   below with `uv run`, from any folder.
2. **Set up PhotonVision.** Follow
   [tag-tracking's PhotonVision setup](../tag-tracking/README.md#photonvision-setup):
   an AprilTag 36h11 pipeline, the lens calibrated in PhotonVision's UI, and its
   NetworkTables Server Address pointed at this machine. Lock exposure and white
   balance there too, or the ball's colour drifts out from under its profile.
3. **Measure your tag.** Measure the edge of the tag's black square, in metres.
   You pass it as `--tag-size` (the *tag size* box on the web pages). A wrong
   size scales every tag position.
4. **Calibrate the field.** Use the web app's **Field calibration** page, or
   `uv run calibrate-field --field W H` with your field's measured size (see
   [tag-tracking](../tag-tracking/README.md)). It saves
   `vision/calib/field_pose.json`, which `track-combined` loads on its own.
   Without it you get a made-up field, and ball positions come out on the wrong
   scale (see [Off-plane readings](#off-plane-readings)).
5. **Make a ball colour profile.** Use the web app's **Ball profile** page. It
   samples PhotonVision's stream, so the profile matches the picture
   `track-combined` sees. The `calibrate-ball` command still opens a local
   webcam by index (`--camera`), not PhotonVision's stream, so a profile made
   with it is measured through a different capture path. Profiles live in
   `vision/calib/ball_color.json`; list them with
   `uv run calibrate-ball --list-profiles`.

## Try it

### In the browser (the easy way)

1. Start the web app: `uv run robocup-web`. It opens http://localhost:5801.
   Start PhotonVision from the header if it isn't running.
2. Open **Tracking**. Enter your tag size, pick your ball profile, and press
   **Start**.
3. You should see the camera with its overlays and plan view, and the field
   drawn live. Move a tag and roll the ball: both follow.
4. Press **Show the robots in the simulator**, then start the sim on the
   **Simulator** page with **Live camera**. The sim robots now follow the tags.

The page runs `track-combined --no-window --control --zmq-pub tcp://*:5556
--mjpeg 5802` with your settings, and *more options* has every other flag.
See [app/README.md](../../app/README.md#pages).

### From a terminal

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177   # your profile, your tag's black square in metres
```

You see one window:

- **Left:** the camera feed with the field overlay, every tag outlined, and the
  ball circled and cross-marked.
- **Right:** one plan view with each tag (a coloured dot with heading and
  velocity arrows) and the ball (a magenta dot with a velocity arrow, ringed
  when airborne).
- **Bottom:** one readout line per tag, then the ball's line.

Keys: `q`/Esc quit, `g` grid, `t` trails, `m` mask overlay, `w` wall/floor
(only when the field is not calibrated), `r` reset trails and tracks,
`-`/`=` lower/raise `--ball-min-conc`. Ctrl+C quits too.

### With no hardware

[`photon-sim`](../tag-tracking/README.md#no-hardware-photon-sim) stands in for
PhotonVision. The tags work end to end, but it renders no ball, so the ball
line stays empty.

```powershell
uv run photon-sim                                                            # terminal 1
uv run track-combined --synthetic-field --tag-size 0.080 --ball-profile mine # terminal 2
```

`--tag-size 0.080` is right here because that is the size `photon-sim` draws.
`--synthetic-field` because a saved `field_pose.json` belongs to your real
camera, not the stand-in. `photon-sim` serves its picture on port 1181, like
PhotonVision, so stop a real PhotonVision on this machine first.

### Feed the simulator

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --zmq-pub "tcp://*:5556"   # terminal 1
cd sim                                                                                 # terminal 2, from the repo root
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3
```

Each tag on the field takes a sim robot. Details, recording and replay are in
the sim's [Vision Mirror](../../sim/README.md#vision-mirror). Real XRP robots
can then follow their sim robots through this loop: see
[robot/README.md](../../robot/README.md#7-several-robots-the-simulator-the-web-app).

## Flags

Shared flags (`--field`, `--mode`, `--field-pose`, ...) are named the same as in
`track`/`track-ball`. Tag and ball tuning flags carry a prefix, since each
tracker has its own filter (its own `sigma_a`, for one). `uv run track-combined
--help` lists them all.

| Flag | For |
| --- | --- |
| `--tag-size`, `--heading-offset`, `--tag-height`, `--tag-sigma-a`, `--tag-sigma-alpha`, `--tag-coast` | The tag tracker. `--tag-size` is the black square's edge in metres. `--tag-height` is how far the tag sits above the field (0 lying on it, the shell height for a tag on top of a robot). `--tag-coast` is how many seconds a tag is still reported on its predicted motion after it was last seen (default 0.5). AprilTag decimation is a PhotonVision pipeline setting, not a flag |
| `--ball-profile`, `--ball-radius`, `--ball-sigma-a`, `--ball-sigma-a-air`, `--ball-sigma-px`, `--ball-min-conc`, `--ball-airborne` | The ball tracker. `--ball-min-conc` is the mean colour likelihood (0-255) a blob needs to count as the ball (default 80); below it no ball is published. The ball stays on the table unless you pass `--ball-airborne`: with the camera ~3 m up the ball is a few pixels across, too small to tell height from |
| `--ball-rest-mm` | A ball that stops (under 0.1 m/s for 0.25 s) is held still: velocity 0, position the running average of what is seen, so it doesn't wander by millimetres. It follows again once the last 3 measurements average this far away (default 6 mm; 0 turns it off). Docs carry `ball.resting` |
| `--json-out`, `--serve-http`, `--json-log`, `--zmq-pub`, `--no-window` | Publishing to another program. See [Feeding another program](#feeding-another-program) |
| `--mjpeg PORT` | Also serve what the window shows as MJPEG at `http://<host>:PORT/stream.mjpg`, even with `--no-window`. The web app's Tracking page uses it |
| `--control` | Take the window's keys as lines on stdin too (`g`, `t`, `m`, `w`, `r`, `q`, `-`, `=`, or `conc N`), for a program driving a `--no-window` tracker. What they set is in each doc's `stats.view` |
| `--print-poses`, `--print-states` | Also print tag lines and ball lines to stdout, tab-separated, one per tag/ball per frame |
| `--display-width` | Scale the window to this width in pixels |
| `--list-profiles` | Print the stored ball profiles |
| `--photon-camera`, `--nt-server`, `--stream`, `--photon-res` | PhotonVision: which camera, where the NetworkTables server is (default: run it here), the stream URL (default port 1181), the pipeline resolution. See [tag-tracking's PhotonVision setup](../tag-tracking/README.md#photonvision-setup). There is no `--no-stream`: the ball needs the picture |
| everything else | Field and calibration: `--field`, `--mode`, `--cam-height`, `--pitch`, `--distance`, `--yaw`, `--pitch-wall`, `--field-pose`, `--corner-origin`, `--synthetic-field` |

`--mode` defaults to `floor` here, not `wall` as in plain `track`, because the
robot-and-ball rig lies flat. It only matters on the made-up field.

## Feeding another program

Four opt-in ways to publish what's tracked to another process: a simulator, a
robot's control loop, anything in any language. All four send the same
per-frame JSON (see [Shape](#shape)); use one or several. For a reader that
wants every frame as it happens (a simulator mirroring the field), use
[`--zmq-pub`](#a-push-socket----zmq-pub-endpoint), usually with
[`--no-window`](#headless----no-window).

### A local file — `--json-out`

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --json-out live_state.json
```

Writes the current frame to that path, **overwritten every frame**. A reader
re-reads the file whenever it wants the latest state. The reader needs access
to this machine's files (same machine, or a shared drive).

The write is **atomic**: each frame goes to a sibling `<path>.tmp`, then
`os.replace` moves it over the real path in one step (atomic on Windows and
POSIX). A reader polling on its own schedule always sees a complete frame,
never a half-written one.

### A tiny HTTP server — `--serve-http PORT`

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --serve-http 8000
```

Prints the URL to use (`http://localhost:8000/state`, and the LAN address for a
reader on another machine). A reader does a plain `GET` whenever it wants the
latest state; `GET /` returns the same. CORS is open
(`Access-Control-Allow-Origin: *`), so a browser page can `fetch()` it directly.

It runs on a background thread in the same process, and serves whatever the
latest frame was: no history, no queue.

### A growing log — `--json-log PATH`

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --json-log runs\session.jsonl
```

**Appends** one JSON object per frame as its own line
([JSON Lines](https://jsonlines.org/)), so a reader `readline()`s or `tail -f`s
it and always gets whole records. The path is relative to the folder you run
from, and missing folders are created. Use it to replay or analyse a session
afterwards: the sim replays these files (`run_pipeline.py --vision
..\vision\runs\session.jsonl`, if you recorded from `vision\`). A second launch
adds a new run to the same file; `run_id` tells them apart.

Each line is flushed at once, so `tail -f` sees it the moment the frame is
processed. Nothing rotates or truncates the file: at 30 fps it grows roughly
10 MB per ten minutes. `runs/`, `*.jsonl`, `*.json.tmp` and `live_state.json`
are gitignored so a session doesn't land in a commit by accident.

### A push socket — `--zmq-pub ENDPOINT`

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --no-window --zmq-pub "tcp://*:5556"
```

The other three wait to be asked, so a reader can skip frames or read one
twice. This one **pushes**: every frame goes out on a
[ZeroMQ](https://zeromq.org/) `PUB` socket bound to `ENDPOINT` as soon as it is
processed. A subscriber gets each frame once, with no polling.

Each message is **one frame of UTF-8 JSON**, the same doc as below. There is no
topic frame, so subscribe to `""`. A reader that only wants the newest frame
should set `CONFLATE`:

```python
import json, zmq

sock = zmq.Context().socket(zmq.SUB)
sock.setsockopt(zmq.CONFLATE, 1)          # keep only the newest; set before connect
sock.setsockopt_string(zmq.SUBSCRIBE, "")
sock.connect("tcp://TRACKER-IP:5556")     # the tracker binds; readers connect
while True:
    doc = json.loads(sock.recv_string())
```

`PUB` never blocks the tracker. The send high-water mark is 4, not ZMQ's default
1000, since a queue of stale frames is worse than none: a subscriber that falls
behind has frames dropped, and the gap shows in `seq`. A subscriber that
connects mid-run starts at the next frame.

pyzmq is an optional extra (`combined-tracking[zmq]`). The repo root's
`pyproject.toml` asks for it, so `uv sync` there installs it. Without it,
`--zmq-pub` exits at once with the install command.

### Headless — `--no-window`

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --no-window --zmq-pub "tcp://*:5556"
```

No window and **nothing drawn**: `cv2.imshow` is never called. Use it when the
only reader is another program, which then gets the frames drawing would have
cost (on a laptop, about 30 fps headless against 24 with the window). There are
no keys, so **Ctrl+C quits**, and still shuts down cleanly. Add `--mjpeg PORT`
to draw anyway and serve the picture instead of showing it.

### Shape

One object per frame, whether it's the current state from
`--json-out`/`--serve-http`, one line of `--json-log`, or one `--zmq-pub`
message:

```json
{
  "seq": 1842, "run_id": "20260919-162120", "frame": 1234,
  "timestamp": 1790437358.3149,
  "t_capture": 1790437358.2909,
  "t_tags": 1790437358.2618,
  "t_publish": 1790437358.3149,
  "field": { "width": 1.2, "height": 0.8, "x0": 0.0, "y0": 0.0 },
  "tags": [
    {
      "id": 4, "x": 0.612, "y": 0.388, "theta_deg": 19.98,
      "vx": 0.051, "vy": -0.012, "speed": 0.052, "direction_deg": -13.2, "omega_deg": 1.4,
      "inside": true, "visible": true, "off_plane_m": 0.004, "age": 0.0, "lost": false
    }
  ],
  "ball": {
    "x": 0.246, "y": 0.121, "z": 0.020,
    "vx": 0.310, "vy": -0.042, "speed": 0.313, "direction_deg": -7.7,
    "grounded": true, "visible": true, "inside": true, "age": 0.0, "lost": false,
    "resting": false
  },
  "stats": {
    "fps": 29.98, "tag_ms": 14.87, "ball_ms": 8.86, "latency_ms": 23.96,
    "capture_dropped": 4, "photon_latency_ms": 18.2, "photon_skipped": 0,
    "ball_dropouts": 57,
    "view": { "grid": true, "trails": true, "mask": true, "mode": "floor", "ball_min_conc": 80.0 }
  }
}
```

All positions are in **metres, field coordinates**, the convention every readout
and plan view in this project uses (see the [vision README's
Conventions](../README.md#conventions)): origin at one field corner, `+X` along
`width`, `+Y` along `height`, or at the custom origin picked in
`calibrate-field`. `field.width`/`field.height` are the calibrated size, so a
reader can scale to the real field without being told. `field.x0`/`field.y0`
are where the first corner sits: `0, 0` by default, `-0.6, -0.4` with the origin
at the centre of a 1.2 x 0.8 m field. The rectangle is always `x0..x0+width`,
`y0..y0+height`. Velocities are m/s, angles degrees, `-180..180`. Every key
above is always present; only `ball` can be `null`.

**Frame identity and timing.** All times are `time.time()`, Unix epoch seconds,
on the tracker's clock.

| Key | Meaning |
| --- | --- |
| `seq` | Frame counter, `+1` for every stream frame processed, from 0 at startup. Never repeats or goes backwards within a run. A gap is a frame *this reader* missed (a slow `--zmq-pub` subscriber, an HTTP poll that skipped one), never one the tracker skipped; those are in `stats.capture_dropped`. Restarts at 0 with the tracker. |
| `run_id`, `frame` | `run_id` is fixed for one launch; `frame` counts camera frames. Use them to split an appended `--json-log` into runs, and to tell a stalled loop (time jumps, `frame` +1) from missing lines (`frame` skips). |
| `t_capture` | Taken right after the frame arrived from PhotonVision's stream. Exposure, PhotonVision, JPEG encoding and the network before that are not included. |
| `t_tags` | When PhotonVision captured the frame `tags` were measured in, on the same clock: the result's arrival time minus the latency PhotonVision reports. Results arrive on their own schedule, so this is usually a little before `t_capture`. While PhotonVision is silent and tracks coast, it moves forward with them. `null` until the first result. The sim's vision bridge times tag sightings by it. |
| `t_publish` | When this doc was built, after detection and filtering. |
| `timestamp` | Kept for older readers. Same value as `t_publish`. Use `t_capture` for "when was this true" and `t_publish` for "when was this sent". |

**`tags`** is a list: one entry per tracked tag id, possibly none.

| Key | Meaning |
| --- | --- |
| `x`, `y`, `theta_deg` | Filtered position, and the heading read off the tag |
| `vx`, `vy`, `speed`, `direction_deg`, `omega_deg` | From the filter. `direction_deg` is the direction of *travel*, 0 below 0.05 m/s. `omega_deg` is turn rate in deg/s, CCW positive |
| `inside` | Within the field rectangle |
| `visible` | Detected in PhotonVision's latest result. `false` while coasting through a short dropout, including when PhotonVision sends nothing for more than 0.25 s |
| `off_plane_m` | Last measured height off the field plane, a diagnostic. A tag on the field reads within ~1 cm of 0; see [Off-plane readings](#off-plane-readings) |
| `age` | Seconds since last detected; 0 while visible |
| `lost` | `true` once `age` exceeds `--tag-coast` (default 0.5 s). A lost tag is **dropped from the list** that same frame, so every listed tag has `lost: false`. The key is there so a reader can treat tags and the ball with the same code |

**`ball`** is `null` until the ball is first seen, then an object from then on:

| Key | Meaning |
| --- | --- |
| `x`, `y`, `z` | Filtered position. `z` equals the ball radius when grounded |
| `vx`, `vy`, `speed`, `direction_deg` | In-plane velocity from the filter |
| `grounded` | On the field, so `x`/`y` come from the precise ray/plane intersection. `false` means airborne (only with `--ball-airborne`), and the position then comes from the much weaker range-by-size estimate |
| `visible` | Detected this frame |
| `inside` | Within the field rectangle |
| `age` | Seconds since last detected; 0 while visible. Keeps counting while lost |
| `lost` | `true` once `age` exceeds the ball filter's coast limit (0.35 s) |
| `resting` | Held still by `--ball-rest-mm`: velocity reads 0 and the position is an average |

A ball goes through three states:

- **visible** (`visible: true`, `lost: false`): measured this frame.
- **coasting** (`visible: false`, `lost: false`, `age` ≤ 0.35 s): a short
  dropout, such as motion blur or a hand passing over. Position is extrapolated
  on the last velocity.
- **lost** (`visible: false`, `lost: true`): unseen for longer. The tracker
  **holds the last position** (`x`/`y`/`z` freeze where the coast ran out) and
  `vx`/`vy`/`speed`/`direction_deg` read **0**, so a reader extrapolating on them
  stays put. The next sighting starts a fresh track wherever the ball turns up.
  Treat `lost: true` as "no ball". `ball` does **not** go back to `null`, so
  "never seen" and "seen, then lost" stay distinct.

> Before `lost` existed, a hidden ball was extrapolated forever. In one recorded
> session it coasted at 0.5 m/s for 13 s and was published at `y = -6.4` on a
> 0.8 m field. In older logs, filter out `visible: false` frames where
> `age > 0.35`: they are not positions.

**`stats`** is the tracker's health, measured on the frame this doc describes:

| Key | Meaning |
| --- | --- |
| `fps` | Stream frames processed per second, smoothed. Not the stream's rate: when processing is slower, frames are dropped (`capture_dropped`) |
| `tag_ms` | Reading PhotonVision's latest result, field pose and tag filter, this frame. Detection itself happens in PhotonVision and is not counted |
| `ball_ms` | Ball mask, blob detection and ball filter, this frame |
| `latency_ms` | `t_publish − t_capture`: how old the stream frame was when this doc went out |
| `photon_latency_ms` | PhotonVision's own latency for its latest result, as it reports it |
| `photon_skipped` | Cumulative. PhotonVision results passed over for a newer one between two frames. The tag filter steps by PhotonVision's capture times, so a skipped result costs a measurement, not accuracy |
| `capture_dropped` | Cumulative. Stream frames overwritten by a newer one before the tracker got to them: the tracker is slower than the stream. Not a detection failure |
| `ball_dropouts` | Cumulative. Frames where a ball track existed but the ball wasn't detected (coasting or lost). This *is* a detection failure |
| `view` | What the keys (or `--control`) have set: grid, trails, mask, mode, `ball_min_conc` |

## Latency: two clocks, newest-only

The tags and the ball run on their own clocks. The ball steps once per stream
frame. The tags step once per PhotonVision result, by PhotonVision's capture
times, since results arrive on their own schedule. If PhotonVision goes quiet for
more than 0.25 s, the tag tracks step on as if it saw nothing, so they coast and
drop instead of freezing in place.

PhotonVision's stream is read on its own thread, which keeps **only the newest
frame**. In a plain read-then-process loop, frames that arrive during processing
queue up, and each read returns an older frame than the newest, the lag growing
with the queue. Here a frame the tracker was too slow to take is overwritten and
counted in `stats.capture_dropped`, so the tracker always works on the latest
picture. Likewise only the newest PhotonVision result is used, the rest counted
in `stats.photon_skipped`. The filters get the real time between the frames or
results they did process, so a dropped one does not distort velocity.

Capture format, resolution and frame rate are PhotonVision camera settings
(there is no `--mjpg`). If the stream runs at a different resolution from the
pipeline, frames are scaled to match the tag corners and the lens calibration,
and the startup lines say so.

## Off-plane readings

`off_plane_m` is a tag's measured distance from the field plane. **Positive is
towards the camera**, so a lifted tag reads positive. A tag lying on the field
reads within about a centimetre of zero. If a tag's median `|off_plane_m|` over
60 sightings exceeds 5 cm, the tracker prints a warning once for that tag. A
tag mounted on a robot reads its height here unless you pass `--tag-height`.

A large **negative** value is not a tag held in the air. It means the tag looks
*further away* than the plane it lies on, which is impossible, so the plane or
the tag's range is wrong. The usual cause is running without
`calib/field_pose.json`, on the made-up field (camera `--cam-height 1.5` m,
straight down). Tag and ball positions are then on **different scales**:

- A tag's `x`/`y` come from its own 3D pose, ranged from the tag's printed size.
  The assumed height cancels out, so its sideways offsets are right in metres,
  as long as `--tag-size` matches the printed tag.
- The ball's `x`/`y` come from where its view ray meets the *assumed* plane. If
  the real floor is further than 1.5 m away, every ball position is pulled
  towards the point under the camera by `1.5 / real distance`.

In one recorded session, tags read `-0.66` m (tag 0) and `-1.4` m (tag 4). With
those tags lying flat, the ball's offsets from the field centre came out about
0.5–0.7× their true size: 20–30 cm of disagreement with the tags at the field
edge. The fix is `calibrate-field` (and PhotonVision's lens calibration), not a
code change.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `no ball profile 'NAME'` at start | That name isn't in `vision/calib/ball_color.json`; the message lists the ones you have. The default, `test`, exists only if you made it. Make one on the Ball profile page |
| `no PhotonVision camera appeared on NetworkTables` | PhotonVision isn't running, has no camera enabled, or its NetworkTables Server Address doesn't point at this machine (`127.0.0.1` if it runs here). Also check that nothing else here runs the NetworkTables server already (the web app's Tracking or Field calibration page, a `track`): only one can run per machine |
| `PhotonVision publishes several cameras` | Pick one with `--photon-camera NAME` (its nickname in PhotonVision's UI) |
| Tags and ball disagree by 10-30 cm | No field calibration, or a wrong `--tag-size`. See [Off-plane readings](#off-plane-readings) |
| Ball flickers in and out, or drifts after a while | Exposure or white balance is on auto in PhotonVision, or the profile was measured off a different picture. Lock them, and re-measure on the Ball profile page. Tune `--ball-min-conc` live with `-`/`=`; each blob's value is drawn on the video |
| `photon-sim` fails with a socket permission error | Port 1181 is taken, usually by a running PhotonVision |
| `stats.capture_dropped` keeps climbing | The tracker is slower than the stream. Try `--no-window` |

## Why a separate package

`track` and `track-ball` still work on their own; use them when you only need
one. This package is an **integration** of both: it calls straight into their
detectors and filters (`TagTracker`, `BallTracker`, `draw_tag_marks`,
`draw_ball_marks`, ...) instead of reimplementing them, so improvements to
either show up here.

Making either skill import the other would make `tag-tracking` and
`ball-tracking` depend on each other, which breaks the workspace's rule: skills
depend on `vision-core`, never on each other. A third package that depends on
*both* keeps the rule. `tag-tracking` and `ball-tracking` still don't know about
each other, and still install and run standalone.

## Layout

```
src/combined_tracking/
  track_combined.py   PhotonVision's tags + the ball from its stream, one loop
```

Everything else lives in `tag-tracking`, `ball-tracking` or `vision-core`. This
file is deliberately thin:

- argument parsing;
- one frame loop, fed by PhotonVision's stream on a newest-frame-only thread and
  its tags over NetworkTables (`PhotonStream`, `PhotonTags` in
  `tag_tracking.photon`);
- drawing that puts both trackers' output on one readout strip and one plan
  view (neither `draw_readout` nor `draw_plan` can be called twice without
  their fixed screen regions colliding);
- the four publishers. `build_state_doc` builds the one dict that each then
  writes atomically, serves over HTTP, appends to a log, or pushes on a socket.
