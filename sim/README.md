# sim: the RoboCup simulator

A 2-D soccer simulator for three-omniwheel robots, 3 v 3 (robots 0–2 blue,
3–5 red). Pymunk does the physics, the strategy node plays both teams, and you
watch and drive in a Pygame window or in the browser. It can also mirror the
real field from the camera, so real robots play alongside simulated ones, and
it can drive real XRP robots. The [main README](../README.md) shows how it
connects to the rest.

## Set up

1. Do the repo setup once: `uv sync` at the repo root
   ([main README](../README.md#set-up-step-by-step)). There is no separate venv here.
2. That's it for a simulated game. For the camera mirror you also need the
   tracker set up ([vision/README.md](../vision/README.md)).

Without uv: `pip install -r requirements.txt` from this folder, in a Python 3.12 venv.

## Try it

**In the browser (easiest):**

1. `uv run robocup-web` (opens http://localhost:5801).
2. **Simulator** page → **Start simulator**.
3. Turn on **Strategy (AI)** and watch the robots play. Click the field to send
   the selected robot somewhere, or pick **Keys** and drive it with W A S D, Q E.

**In a Pygame window:**

```powershell
cd sim
uv run python run_pipeline.py
```

1. Press `5` to turn the strategy on: all six robots play.
2. Click the field to send robot 0 there (PD controller). Press `2` or `3` to
   compare the time-optimal and MPC controllers.
3. Press `4` and drive robot 0 yourself with W A S D, Q E. Press `K` to kick.
4. With gamepads plugged in, press `6`: gamepad *N* drives blue robot *N*.

Press **Ctrl+C** in the terminal to stop every node.

Run the browser page **or** the Pygame window, not both: both use the manual
port (9093).

## Controls (Pygame window)

| Input | Action |
|-------|--------|
| Left click on the field | Send robot 0 there (with the current controller) |
| `1` / `2` / `3` | Controller: PD (dynamic inversion) / time-optimal (bang-bang) / MPC |
| `4` | Keyboard mode: `W A S D` move robot 0, `Q` / `E` rotate |
| `6` | Gamepad mode: gamepad *N* drives blue robot *N*; left stick moves, right stick rotates, A kicks, RB dribbles |
| `5` | Strategy on / off |
| `K` / `Enter` | Kick with robot 0 |
| `0` | Attacker dribbles on / off |
| `9` | Reset a stuck ball on / off |
| `R` | RL kick policy on / off (9 × 6 field only) |

## `run_pipeline.py` options

```powershell
uv run python run_pipeline.py                      # sim + strategy + robot control + Pygame window
uv run python run_pipeline.py --no-viz             # no window (the browser page, CI, SSH)
uv run python run_pipeline.py --no-strategy        # no strategy node
uv run python run_pipeline.py --color red          # the strategy's "own" team is red (see below)
uv run python run_pipeline.py --vision tcp://localhost:5556   # mirror the live camera
uv run python run_pipeline.py --vision ..\vision\test.jsonl  # replay a recording
uv run python run_pipeline.py --field 1.93 1.32    # sim field at the real table's size
```

The strategy always plans both teams; `--color` picks whose attacker gets the
dribbler and whose robots can be human-driven
([strategy/README.md](../strategy/README.md)).

| Flag | Meaning | Default |
|------|---------|---------|
| `--no-viz`, `--no-strategy` | Skip the Pygame window / the strategy node | both run |
| `--color blue\|red` | The strategy's own team | `blue` |
| `--vision SRC` | Mirror camera tracking: `tcp://host:5556` (ZMQ), `http://host:8000/state`, or a `.jsonl` log to replay | off |
| `--tag-map T:R,...` | Pin AprilTag ids to robot ids, e.g. `9:0,12:1` | auto: each new tag takes the lowest free robot |
| `--ignore-tags IDS` | Tags never mirrored, e.g. `0,1,2,3` for the field corner tags | none |
| `--flip-x`, `--flip-y` | Mirror the camera field if a robot moves opposite to its tag | off |
| `--replay-speed X` | Replay speed for a `.jsonl` log | `1` |
| `--field W H` / `--field FILE` | True-size field in metres, or vision's `calib/field_pose.json` (see [D](#d-true-size-field)) | 9 × 6 |

## How it works

`run_pipeline.py` starts four processes that talk over ZMQ:

```
  simulation_node.py    strategy_node.py    robot_node.py         viz_node.py
  ──────────────────    ────────────────    ─────────────         ───────────
  Pymunk physics        world state ──→     ←── targets, clicks   click → target
  world state ────────→ roles, targets ──→  controllers     ──→   Pygame renderer
  (VISION_PORT PUB)     (STRATEGY_PORT PUB) (COMMAND_PORT PUSH,   (MANUAL_PORT PUB)
                                             REAL_PORT PUB)
```

- **simulation_node** runs the physics, the game (score, halves, goals) and the
  camera mirror, and publishes the world state 60 times a second.
- **strategy_node** (in [strategy/](../strategy/)) gives every robot a role and a
  target point.
- **robot_node** turns targets (strategy's, or yours) into wheel speeds with one
  of three controllers: **PD** (dynamic inversion), **time-optimal**
  (bang-bang), **MPC** (greedy rollout of 8 inputs over 10 steps). It also
  publishes each command per body axis on `REAL_PORT`, so real XRPs can follow
  sim robots ([robot/README.md](../robot/README.md), step 7).
- **viz_node** draws the field and takes your clicks, keys and gamepads. The
  web app's Simulator page replaces it (`--no-viz`).

All shared constants (ports, field, robot hardware, controller gains) are in
`config.py`. Robot hardware values came from the original Godot demo and are
marked `TODO(hardware)`: they still need measuring on the real robot.

To debug one node, run it alone (the others connect when they start):

```powershell
uv run python simulation_node.py                 # physics, publishes on 9090
uv run python ..\strategy\strategy_node.py       # strategy, publishes on 9091
uv run python robot_node.py                      # controllers: 9090/9091/9093 in, 9092/9094 out
uv run python viz_node.py                        # window, publishes on 9093
```

A standalone node uses the 9 × 6 field unless `ROBOCUP_FIELD` is set:
`$env:ROBOCUP_FIELD = "1.93 1.32"` (PowerShell).

### Files

```
sim/
├── run_pipeline.py      # launcher: starts the four nodes, stops them on Ctrl+C
├── simulation_node.py   # physics + game, publishes the world state
├── pymunk_world.py      # the Pymunk world: robots, ball, walls, kicks, dribbling
├── robot_node.py        # controllers: targets -> wheel speeds (and body commands for real robots)
├── viz_node.py          # Pygame window: field, clicks, keys, gamepads
├── vision_bridge.py     # camera tracking -> sim (--vision)
├── config.py            # shared constants: ports, field, hardware, gains
├── sounds/              # goal, cheer, whistle, countdown, winner sounds for the window
└── requirements.txt     # for pip users
```

### Ports

| Constant | Port | Direction | Carries |
|----------|------|-----------|---------|
| `VISION_PORT` | 9090 | simulation_node → robot_node, strategy_node, viz_node, web app | World state |
| `STRATEGY_PORT` | 9091 | strategy_node → robot_node | Strategy targets |
| `COMMAND_PORT` | 9092 | robot_node, strategy_node → simulation_node | Wheel speeds, kicks, dribbler |
| `MANUAL_PORT` | 9093 | viz_node or the web app → robot_node, strategy_node | Clicks, keys, toggles |
| `REAL_PORT` | 9094 | robot_node → real robots | Each robot's command per body axis |

### Message formats

```jsonc
// World state (VISION_PORT), abridged. With --vision, mirrored robots also carry
// "source": "vision", "stale" and "tag", and a "vision" stats block is added.
{ "t": 12.3, "score": { "blue": 0, "red": 1 },
  "game": { "half": 1, "time_remaining": 87.0, "phase": "FIRST HALF", ... },
  "ball": { "x": 4.5, "y": 3.0, "vx": 0.2, "vy": -0.1 },
  "robots": { "0": { "x": 2.0, "y": 3.0, "vx": 0.0, "vy": 0.0, "angle": 0.0,
                     "omega": 0.0, "dribbling": false } },
  "field": { "width": 9.0, "height": 6.0, "num_robots": 6, ... } }

// Strategy targets (STRATEGY_PORT): angle = heading to face, radians
{ "targets": { "0": { "x": 5.2, "y": 3.1, "angle": 0.0 } }, "attackers": [0, 3] }

// Manual target, strategy toggle, direct drive (MANUAL_PORT)
{ "targets": { "0": { "x": 4.5, "y": 3.0, "mode": "2005_INVERSION" } } }
{ "strategy_enabled": true }
{ "direct": { "0": { "vx": 1.0, "vy": 0.0, "w": 0.0 } } }

// Wheel command (COMMAND_PORT)
{ "robot_id": 0, "wheel_speeds": [0.5, -0.3, 0.8] }

// Real-robot commands (REAL_PORT), one message per world-state frame
{ "t": 12.3, "robots": { "0": { "forward": 0.8, "left": -0.1, "turn": 0.05,
                                "dribble": false, "tracked": true } } }
```

`forward` / `left` / `turn` are the command robot_node would turn into wheel speeds,
scaled the same way but expressed per body axis (-1..1, turn counter-clockwise), for a
robot whose firmware mixes its own wheels. `dribble` is the sim's dribbler state for that
robot (world state's `dribbling`). `tracked` is true while the camera sees that robot
(`source: "vision"` and not `stale`); a real robot only follows the command then.
`robot/laptop/sim_follow.py` and the web app's Robots page read this port.

## Vision Mirror

`--vision` feeds camera tracking from [vision/](../vision/)'s `track-combined` into simulation_node. Tracked objects follow the camera; everything else stays simulated, so a real robot can play against sim robots. `vision_bridge.py` does the work.

The easy way is the web app: **Tracking** page → start the tracker, then
**Simulator** page → *Robots and ball: Live camera* → **Start simulator**. The
terminal way follows.

### Before you start (once)

1. **Environment.** One `uv sync` at the repo root covers the sim **and** the tracker, including pyzmq for `--zmq-pub`. Run sim commands **from `sim`** with `uv run python ...`.
2. **PhotonVision** (live only). The tracker's camera is PhotonVision's: run it, set its pipeline to AprilTag 36h11, lock exposure and white balance, calibrate the lens in its UI, and point its NetworkTables Server Address at the tracker's machine. See vision's [PhotonVision setup](../vision/tag-tracking/README.md#photonvision-setup).
3. **Calibration** (live only). Without it tag and ball positions are off, and the sim drops sightings that land outside the field. In the web app use the **Field calibration** and **Ball profile** pages; from a terminal:
   ```powershell
   uv run calibrate-field --field 1.93 1.32     # your measured field W H in metres, once
   uv run calibrate-ball --profile mine --radius-mm 20   # only if your ball has no profile yet
   ```
   `calibrate-field` saves the field size in `calib/field_pose.json`, so `track-combined` needs no `--field` afterwards and every doc it sends carries the right size. Redo it (with `--field`) if you move the camera or change the table.

   `calibrate-field` also asks where `(0,0)` should be (click it, or `--origin center`, or `--no-origin` for the first corner). Any origin works with the sim: each doc's `field` block says where the first corner sits (`x0`, `y0`), and the sim measures from there. Axes stay the same; only the zero moves.

   Ball profiles live in `vision/calib/ball_color.json`.

### A. Replay a recording (no camera)

```powershell
cd sim
uv run python run_pipeline.py --vision ..\vision\test.jsonl
```

- Add `--replay-speed 3` to fast-forward. The replay loops at the end.
- Add `--field 1.93 1.32` to replay at true size (see [D](#d-true-size-field)); `test.jsonl` was recorded on a 1.93 x 1.32 m field.
- `test.jsonl` is the one committed recording: 5 runs, about 7 min of playback (the gaps between runs are squashed to 1 s). Run 1 (~3 min) has tag 4 and the ball, run 2 (~1 min) the ball only, runs 3-5 (~1 min each) tag 1 with the ball mostly unseen. With auto mapping tag 4 claims robot 0 when it first lands on the field, and tag 1 then takes robot 1; `--tag-map 4:0,1:1` pins that layout.
- Any `--json-log` file from `track-combined` replays the same way, e.g. your own `..\vision\runs\myrun.jsonl` (see [C](#c-record-a-session)). Run logs are gitignored, so they stay local.

### B. Live (camera, two terminals)

**Terminal 1, tracker** (`--tag-size` is your tag's measured black square in metres, `--ball-profile` your profile name):

```powershell
cd vision
uv run track-combined --ball-profile mine --tag-size 0.177 --zmq-pub "tcp://*:5556"
```

No `--field`: the tracker uses the size saved by `calibrate-field`. It waits for PhotonVision to connect over NetworkTables, then reads the camera from PhotonVision's stream. Keep the quotes around `tcp://*:5556`. Leave the window on the first time to check detections; add `--no-window` once you trust it.

**Terminal 2, sim:**

```powershell
cd sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3
```

Each tag the camera sees on the field takes the lowest free robot (robot 0 first) and keeps it for the session; the terminal prints `tag 9 -> robot 0` and the sim window labels the robot `T9`. `--ignore-tags 0,1,2,3` keeps the field-calibration corner tags (ids 0-3 by default) from becoming robots; drop it if they're off the table, or if a robot wears one of those ids. To pin tags to robots instead, pass `--tag-map 9:0,12:1`. Start order doesn't matter; the sim reconnects if the tracker starts later.

Check the strip along the bottom of the sim window: `UP`, ~30 fps, `lat` under ~50 ms, few `cam drops` / `link drops`. Grey = camera isn't currently seeing that object.

Stop with **Ctrl+C** in each terminal.

HTTP fallback: `--serve-http 8000` on the tracker, `--vision http://localhost:8000/state` on the sim. Polling misses and repeats frames, so prefer ZMQ.

### C. Record a session

Add `--json-log` to the tracker command. It can run at the same time as live mode:

```powershell
uv run track-combined --ball-profile mine --tag-size 0.177 --zmq-pub "tcp://*:5556" --json-log runs\myrun.jsonl
```

One JSON line per frame, appended (a second launch adds a new run to the same file). The web app's Tracking page has a *record this session* switch that does the same. Replay it with A:

```powershell
uv run python run_pipeline.py --vision ..\vision\runs\myrun.jsonl
```

### D. True-size field

By default the sim field is 9 x 6 m and the real table is stretched onto it, so a 1.93 m table plays 4.7x too fast. `--field` makes the sim field the real size instead: camera metres map 1:1 and speeds are real. Use it whenever real robots are involved.

```powershell
cd sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --field ..\vision\calib\field_pose.json
uv run python run_pipeline.py --vision ..\vision\test.jsonl --field 1.93 1.32
```

- `--field W H` in metres, or the tracker's `calib/field_pose.json` (reads its saved `field` size). Use the same size the tracker was calibrated with; the sim warns if the tracker's `field` differs by more than 5 mm.
- Every node gets it, via the `ROBOCUP_FIELD` environment variable that `run_pipeline.py` sets.
- What follows the field: walls, goal mouth (same fraction of the field height as on 9 x 6, ~0.31 m on a 1.32 m table), spawn points, the strategy's field and goal positions, and the window, which stays ~900 px wide.
- What doesn't: robot and ball size, physics, and the strategy's tuned distances (approach offset, goalie standoff, ...) keep their 9 x 6 values, so simulated robots are oversized on a small table.
- The RL kick policy was trained on 9 x 6 and is off: `R` in the viewer does nothing, and the attacker always uses the classic approach.
- Works with or without `--vision`.

### E. Real robots following sim robots

With the camera mirror running, a real XRP can take the commands robot_node
computes for its sim robot, so strategy and your clicks drive the real robot.
Pin its tag to that robot (`--tag-map 9:0`), then on the web app's **Robots**
page set it to **Follow sim** → robot 0, or run
`uv run python -m laptop XRP-01 --sim 0` from `robot/`. It only moves while the
camera sees it. Details: [robot/README.md](../robot/README.md), step 7.

### Tracker flags used here

| Flag | Meaning |
|------|---------|
| `--zmq-pub "tcp://*:5556"` | Push every frame to the sim |
| `--serve-http PORT` | Serve the latest frame for HTTP polling |
| `--json-log FILE` | Record every frame for replay |
| `--no-window` | Headless, a few fps faster; Ctrl+C quits |
| `--ball-profile NAME` | Ball color profile from `calib/ball_color.json` |
| `--tag-size M` | Measured tag black-square size, metres (default 0.080 if left out) |
| `--photon-camera NAME` | PhotonVision camera nickname, if it publishes more than one |
| `--nt-server HOST` | Connect to an existing NetworkTables server instead of running one in the tracker |

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `can't open file ...\run_pipeline.py` | Run from `sim`. |
| PowerShell: `The '<' operator is reserved` | A placeholder was pasted literally. Use a real value, e.g. `--ball-profile mine`. |
| Unknown ball profile | Name not in `vision/calib/ball_color.json`: `git pull`, or create it (web app Ball profile page, or `uv run calibrate-ball --profile NAME`). |
| `--zmq-pub` unrecognized, or "needs pyzmq" | Out of date, or launched in a different venv: `git pull`, `uv sync` at the repo root, and start it with `uv run`. |
| `No module named 'torch'` (or `gymnasium`, `stable_baselines3`) | RL extra missing, or a plain `uv sync` removed it: `uv sync --extra rl`. |
| A `.venv` folder inside `sim/` or `vision/` | Leftover from the old per-folder setup. Delete it; only the root `.venv` is used. |
| Strip says `DOWN` | Tracker not running, or `--vision` endpoint/port doesn't match `--zmq-pub`. |
| `Address in use` | Another pipeline, viewer or the web app's Simulator is still running. Close it (check Task Manager for stray `python.exe`). |
| Robot doesn't follow the tag | Check the terminal for `tag N -> robot R` (auto) or `tag N seen but not in --tag-map`. No line at all means the tag never landed on the field: recalibrate. A robot parked in a corner is a corner reference tag: add `--ignore-tags 0,1,2,3`. |
| Robot follows but moves the wrong way | `--flip-x` and/or `--flip-y`. |
| A robot stays grey | Tag not seen recently, or its positions land off the field: recalibrate the field, and watch for the tracker's off-plane warning. |
| `--field ...field_pose.json`: no field width/height | The calibration predates saved sizes. Re-run `uv run calibrate-field --field W H`, or pass the numbers. |
| `tracker field ... != sim --field ...` | `--field` doesn't match what the tracker was calibrated with. Use the same numbers, or pass the calibration file. |
| Tracker: `no PhotonVision camera appeared on NetworkTables` | PhotonVision isn't running, or its NetworkTables Server Address doesn't point at the tracker's machine (`127.0.0.1` if same machine). See [PhotonVision setup](../vision/tag-tracking/README.md#photonvision-setup). |
| Tracker: `could not open PhotonVision's stream` | Check the stream URL in PhotonVision's camera view and pass it with `--stream URL`. |

### How the mirror works

- **Sources:** `http://.../state` (track-combined `--serve-http`, polled at 60 Hz), `tcp://host:port` (ZMQ SUB, CONFLATE), or a `.jsonl` log replayed at recorded speed (`--replay-speed`, loops at EOF).
- **Mapping:** auto by default (`VISION_TAG_MAP = None`): a tag claims a robot on its first visible, on-field sighting and keeps it; `--ignore-tags` / `VISION_IGNORE_TAGS` exclude ids. `--tag-map TAG:ROBOT,...` pins them instead and warns once about unmapped tags.
- **Coordinates:** positions are shifted to the field's first corner on arrival (`x - x0`, `y - y0` from the doc's `field`; missing = 0, as in older logs), so a custom tracker origin works. The real field (`field` `width`/`height`) is then scaled onto the sim field: 9 x 6 m by default, 1:1 with `--field`. `--flip-x/--flip-y` (`VISION_FLIP_X/Y`) pick the origin corner.
- **Per-object state**, from the age of the last accepted sighting:

| State | Age | Robot | Ball |
|-------|-----|-------|------|
| fresh | < `VISION_FRESH_S` | kinematic, velocity-servoed onto the dead-reckoned measurement | velocity-servoed |
| predict | < `VISION_*_COAST_S`, tracker still coasting | as fresh, onto the tracker's Kalman prediction | as fresh |
| coast | < `VISION_*_COAST_S` | keeps last velocity, damped | sim physics |
| lost  | older / never seen | frozen in place | back to sim |

- **Reach circle:** objects move continuously, so `gap` seconds after the last accepted sighting each is inside a circle the physics allows. A camera-mirrored robot's limits are `VISION_ROBOT_MAX_SPEED` / `VISION_ROBOT_MAX_ACCEL` (6 m/s, 40 m/s², generous, since a hand-moved tag or a real robot outruns the simulated motors); the circle is centred where its measured velocity carries it, plus `VISION_GATE_MARGIN`. The ball can be kicked at any instant, so its circle is `MAX_BALL_SPEED` (6.5 m/s) x `gap` around it. `VISION_VEL_NOISE` covers the tracker's velocity error. Sightings outside are rejected (outlier gate, relocks after `VISION_GATE_RELOCK` rejects); predictions and dead-reckoning are clamped into it.
- Sightings are dropped if the tracker marks them lost or they fall outside the real field (`VISION_FIELD_MARGIN`). Not-visible entries are the tracker's Kalman coast: with `VISION_TRACKER_COAST` they drive the body (`predict`) through short dropouts, clamped to the reach circle and the field, instead of handing it to sim physics. They never count as sightings, so they can't move the circle or extend the coast. `stale` is true for anything but `fresh`.
- A resting ball (slower than `VISION_BALL_REST_SPEED`, inside `VISION_BALL_REST_RADIUS` of where it settled) is held still, so detection jitter doesn't shake it.
- Error above `VISION_SNAP_DIST` teleports instead of blending (first sighting, reacquire).
- Mirrored robots ignore wheel, kick and dribble commands in the sim: the camera owns them. Goals are edge-triggered and a camera-owned ball is not reset.
- World state gains `source` / `stale` on robots and ball, and a `vision` stats block (fps, latency, transport drops from `seq` gaps, duplicates, rejects, tracker-coast entries used (`predicted`), per-object unseen %, and the tracker's own `stats` under `tracker`). viz_node greys stale objects and shows the stats along the bottom of the field.
- Reads both tracker formats: `seq` / `t_capture` / `lost` / `stats` and the older `frame` / `run_id`. A new `run_id` is a tracker restart, not dropped frames. Replay keeps each frame's recorded capture-to-publish delay.
