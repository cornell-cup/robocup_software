# RoboCup Python Simulation

A Python port of the Godot-based RoboCup simulation. Replaces the Godot engine with a pure-Python stack: Pymunk for physics, Pygame for visualization, and ZMQ for inter-process messaging.

## Project Overview

The pipeline runs four independent processes that communicate over ZMQ sockets:

```
┌──────────────────────────────────────────────────────────────┐
│                      run_pipeline.py                         │
│              (spawns and monitors all nodes)                 │
└──────────────────────────────────────────────────────────────┘

  simulation_node.py    strategy_node.py    robot_node.py       viz_node.py
  ──────────────────    ────────────────    ─────────────       ───────────
  Pymunk physics        world state ──→     ←── wheel cmds     click → target
  world state ────────→ decide targets ──→  robot control  ──→ Pygame renderer
  (VISION_PORT PUB)     (STRATEGY_PORT PUB) (COMMAND_PORT PUSH) (MANUAL_PORT PUB)
```

- **SimNode** runs the physics world and publishes robot positions/velocities.
- **StrategyNode** subscribes to world state, runs autonomous strategy, and publishes target positions.
- **RobotNode** reads world state, strategy targets, and manual targets, then pushes wheel speed commands. It also publishes each command per body axis on `REAL_PORT`, so real robots can follow sim robots (see [robot/](../robot/)).
- **VizNode** renders the field and lets the user set targets by clicking. Press **5** to toggle strategy on/off.

## Key Features

- Three interchangeable robot controllers (selectable at runtime):
  - **PD (Dynamic Inversion)** — closed-form PD control with imposed error dynamics
  - **Time-Optimal (Bang-Bang)** — full acceleration until braking distance, then deceleration
  - **MPC** — greedy rollout over 8 candidate inputs across a 10-step horizon
- Omnidirectional drive with 3-wheel inverse kinematics
- Physics faithful to the original Godot scene (same damping constants, gains, and kinematics)
- Arrival telemetry: prints path length, elapsed time, and average speed on arrival

## Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| [Pymunk](http://www.pymunk.org/) | ≥ 6.0 | Rigid-body physics (replaces Godot's physics engine) |
| [Pygame](https://www.pygame.org/) | ≥ 2.0 | Real-time 2-D visualization |
| [PyZMQ](https://pyzmq.readthedocs.io/) | ≥ 24.0 | Inter-process messaging (PUB/SUB, PUSH/PULL) |
| [NumPy](https://numpy.org/) | ≥ 1.21 | Linear algebra for controllers |

Python **3.12**, the version the whole repo is pinned to (root `.python-version`). The sim code uses `from __future__ import annotations` and was originally written to run on 3.9+, but only 3.12 is set up and tested, and the tracker needs 3.12 or 3.13.

## Folder Structure

```
python/
├── config.py            # All shared constants (ports, field, robot hardware, gains)
├── simulation_node.py   # Pymunk physics engine — publishes world state
├── robot_node.py        # Control loop — reads state, outputs wheel speeds
├── viz_node.py          # Pygame renderer + mouse/keyboard input
├── vision_bridge.py     # Camera tracking → sim coordinates (--vision)
├── run_pipeline.py      # Launcher — spawns all four nodes as subprocesses
└── requirements.txt     # Python dependencies

strategy/
├── strategy_node.py     # Autonomous strategy
├── state.py             # GameState dataclass + builder
├── geometry.py          # Spatial queries (distances, goal positions)
├── prediction.py        # Ball trajectory prediction + intercept
├── plays/               # Team-level play selection (offensive, defensive, kickoff)
├── tactics/             # Per-robot role assignment (attacker, supporter, defender)
├── skills/              # Low-level actions (navigate, kick, dribble)
├── BT/                  # Behavior tree engine
└── RL/                  # Reinforcement learning (future)
```

## Setup

The sim shares one environment with the tracker: `.venv/` at the repo root
(`robocup_software/`), built by [uv](https://docs.astral.sh/uv/) from the root
`pyproject.toml`. There is no separate venv in `robocup/`.

```powershell
# From the repo root (robocup_software), once and after each git pull
uv sync                 # sim + tracker + pyzmq
uv sync --extra rl      # instead, if you use strategy/rl/ (adds gymnasium, stable-baselines3, torch, tensorboard)

# Then, from sim/
uv run python run_pipeline.py
```

| Flag | Meaning |
| --- | --- |
| `uv sync` | Make `.venv` match the lockfile exactly. **Removes the RL packages** if they were installed. |
| `uv sync --extra rl` | Same, plus the RL packages. Pass it every time you sync if you use RL. |
| `uv run python X.py` | Run a script in the root `.venv` from any folder. Never removes packages. |
| `uv run --extra rl python X.py` | Same, installing the RL packages first if missing. |

Not needed any more: `uv run --no-project` (the sim used to have no project of
its own) and `uv venv` / `uv pip install` into `robocup/.venv`. Full flag
reference: [root README, Setup](../README.md#setup).

Without uv: `pip install -r requirements.txt` from this folder, in a Python 3.12 venv.

## Running

```bash
# Start all four nodes (simulation + strategy + robot controller + visualization)
python run_pipeline.py

# Headless mode — skip the Pygame window (e.g. for CI or SSH)
python run_pipeline.py --no-viz

# Skip the strategy node
python run_pipeline.py --no-strategy

# Control the red team instead of blue
python run_pipeline.py --color red

# Mirror the real field from the camera, or replay a recording (see Vision Mirror)
python run_pipeline.py --vision tcp://localhost:5556
python run_pipeline.py --vision ../vision/test.jsonl
python run_pipeline.py --vision tcp://localhost:5556 --field 1.93 1.32   # sim field at the real table's size
```

Press **Ctrl+C** to stop all nodes cleanly.

## Development Workflow

### Running a single node manually

Each node can be run in isolation for debugging:

```bash
python simulation_node.py                              # starts physics, publishes on port 9090
python ../strategy/strategy_node.py --mode zmq  # strategy on port 9091
python robot_node.py                                   # c# connects to sim, reads port 5555, pushes to port 9092
python viz_node.py                                     # # connects to sim, renders, publishes targets on port 9093
```

### Controls (VizNode)

| Input | Action |
|-------|--------|
| Left click on field | Send target to robot |
| `1` | Switch to PD controller |
| `2` | Switch to Time-Optimal controller |
| `3` | Switch to MPC controller |
| `4` | Switch to MANUAL mode (WASD / gamepad) |
| `5` | Toggle autonomous strategy on/off |
| `WASD` | Manual movement (MANUAL mode) |
| `Q` / `E` | Rotation control |
| Gamepad | Left stick: move, Right stick: rotate |

### ZMQ Port Map

| Constant | Port | Direction | Description |
|----------|------|-----------|-------------|
| `VISION_PORT` | 9090 | SimNode → RobotNode, StrategyNode, VizNode | World state (JSON) |
| `STRATEGY_PORT` | 9091 | StrategyNode → RobotNode | Autonomous strategy targets (JSON) |
| `COMMAND_PORT` | 9092 | RobotNode → SimNode | Wheel speed commands (JSON) |
| `MANUAL_PORT` | 9093 | VizNode → RobotNode | Manual targets + strategy toggle (JSON) |
| `REAL_PORT` | 9094 | RobotNode → real robots | Every robot's command per body axis, for real XRPs that follow a sim robot (JSON) |

### Message Formats

```jsonc
// World state (VISION_PORT)
{ "t": 1234567890.0, "ball": { "x": 4.5, "y": 3.0, "vx": 0.2, "vy": -0.1 },
  "robots": { "0": { "x": 2.0, "y": 3.0, "vx": 0.0, "vy": 0.0, "angle": 0.0, "omega": 0.0 } } }

// Strategy target (STRATEGY_PORT)
{ "targets": { "0": { "x": 5.2, "y": 3.1 } } }

// Manual target (MANUAL_PORT)
{ "targets": { "0": { "x": 4.5, "y": 3.0, "mode": "2005_INVERSION" } } }

// Strategy toggle (MANUAL_PORT)
{ "strategy_enabled": true }

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

`--vision` feeds camera tracking from [vision/](../vision/)'s `track-combined` into SimNode. Tracked objects follow the camera; everything else stays simulated, so a real robot can play against sim robots. `vision_bridge.py` does the work.

### Before you start (once)

1. **Folders.** Both halves live in this monorepo: `F:\Cornell\CornellCupRobotics\robocup_software\robocup` (sim) and `F:\Cornell\CornellCupRobotics\robocup_software\vision` (tracker). Adjust the path if your clone is elsewhere.
2. **Environment.** One `uv sync` at the repo root covers the sim **and** the tracker, including pyzmq for `--zmq-pub`; see [Setup](#setup). Every sim command runs **from `sim`**. With uv, prefix it with `uv run python`; with pip, just `python`.
3. **Stay current.** `git pull` in `robocup_software`, then `uv sync` at the repo root (`uv sync --extra rl` if you use RL).
4. **PhotonVision** (live only). The tracker's camera is PhotonVision's: run it, set its pipeline to AprilTag 36h11, lock exposure and white balance, calibrate the lens in its UI, and point its NetworkTables Server Address at the tracker's machine. See vision's [PhotonVision setup](../vision/tag-tracking/README.md#photonvision-setup).
5. **Calibration** (live only). Without it tag and ball positions are off, and the sim drops sightings that land outside the field. The lens is PhotonVision's (step 4); the rest:
   ```powershell
   uv run calibrate-field --field 1.93 1.32     # your measured field W H in metres, once
   uv run calibrate-ball --profile mine --radius-mm 20   # only if your ball has no profile yet
   ```
   `calibrate-field` saves the field size in `calib/field_pose.json`, so `track-combined` needs no `--field` afterwards and every doc it sends carries the right size. Redo it (with `--field`) if you move the camera or change the table. Calibrations from before this was added have no size saved; `track-combined` then falls back to 1.2 x 0.8.

   `calibrate-field` also asks where `(0,0)` should be (click it, or `--origin center`, or `--no-origin` for the first corner). Any origin works with the sim: each doc's `field` block says where the first corner sits (`x0`, `y0`), and the sim measures from there. Axes stay the same; only the zero moves.

   Ball profiles live in `calib/ball_color.json` (`mine`, `lab`, `arkart`).

### A. Replay a recording (no camera)

```powershell
cd F:\Cornell\CornellCupRobotics\robocup_software\sim
uv run python run_pipeline.py --vision ..\vision\test.jsonl
```

- Add `--replay-speed 3` to fast-forward. The replay loops at the end.
- Add `--field 1.93 1.32` to replay at true size (see [D](#d-true-size-field)); `test.jsonl` was recorded on a 1.93 x 1.32 m field.
- `test.jsonl` is the one committed recording: 5 runs, about 7 min of playback (the gaps between runs are squashed to 1 s). Run 1 (~3 min) has tag 4 and the ball, run 2 (~1 min) the ball only, runs 3-5 (~1 min each) tag 1 with the ball mostly unseen. With auto mapping tag 4 claims robot 0 when it first lands on the field, and tag 1 then takes robot 1; `--tag-map 4:0,1:1` pins that layout.
- Any `--json-log` file from `track-combined` replays the same way, e.g. your own `..\vision\runs\myrun.jsonl` (see [C](#c-record-a-session)). Run logs are gitignored, so they stay local.

### B. Live (camera, two terminals)

**Terminal 1, tracker:**

```powershell
cd F:\Cornell\CornellCupRobotics\robocup_software\vision
uv run track-combined --ball-profile mine --tag-size 0.080 --zmq-pub "tcp://*:5556"
```

`--ball-profile` is your profile name and `--tag-size` your tag's measured black square in metres. No `--field`: the tracker uses the size saved by `calibrate-field`. It waits for PhotonVision to connect over NetworkTables, then reads the camera from PhotonVision's stream. Keep the quotes around `tcp://*:5556`. Leave the window on the first time to check detections; add `--no-window` once you trust it.

**Terminal 2, sim:**

```powershell
cd F:\Cornell\CornellCupRobotics\robocup_software\sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3
```

Each tag the camera sees on the field takes the lowest free robot (robot 0 first) and keeps it for the session; the terminal prints `tag 9 -> robot 0` and the sim window labels the robot `T9`. `--ignore-tags 0,1,2,3` keeps the field-calibration corner tags (ids 0-3 by default) from becoming robots; drop it if they're off the table, or if a robot wears one of those ids. To pin tags to robots instead, pass `--tag-map 9:0,12:1`. Start order doesn't matter; the sim reconnects if the tracker starts later.

Check the strip along the bottom of the sim window: `UP`, ~30 fps, `lat` under ~50 ms, few `cam drops` / `link drops`. Grey = camera isn't currently seeing that object.

Stop with **Ctrl+C** in each terminal.

HTTP fallback, e.g. with main's tracker: `--serve-http 8000` on the tracker, `--vision http://localhost:8000/state` on the sim. Polling misses and repeats frames, so prefer ZMQ.

### C. Record a session

Add `--json-log` to the tracker command. It can run at the same time as live mode:

```powershell
uv run track-combined --ball-profile mine --tag-size 0.080 --zmq-pub "tcp://*:5556" --json-log runs\myrun.jsonl
```

One JSON line per frame, appended (a second launch adds a new run to the same file). Replay it with A:

```powershell
uv run python run_pipeline.py --vision ..\vision\runs\myrun.jsonl
```

### D. True-size field

By default the sim field is 9 x 6 m and the real table is stretched onto it, so a 1.93 m table plays 4.7x too fast. `--field` makes the sim field the real size instead: camera metres map 1:1 and speeds are real.

```powershell
cd F:\Cornell\CornellCupRobotics\robocup_software\sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --field ..\vision\calib\field_pose.json
uv run python run_pipeline.py --vision ..\vision\test.jsonl --field 1.93 1.32
```

- `--field W H` in metres, or the tracker's `calib/field_pose.json` (reads its saved `field` size). Use the same size the tracker was calibrated with; the sim warns if the tracker's `field` differs by more than 5 mm.
- Every node gets it (SimNode, StrategyNode, RobotNode, VizNode), via the `ROBOCUP_FIELD` environment variable that `run_pipeline.py` sets. For a node run on its own: `$env:ROBOCUP_FIELD = "1.93 1.32"` (PowerShell) or `ROBOCUP_FIELD="1.93 1.32"` (bash).
- What follows the field: walls, goal mouth (same fraction of the field height as on 9 x 6, ~0.31 m on a 1.32 m table), spawn points, the strategy's field and goal positions, and the window, which stays ~900 px wide.
- What doesn't: robot and ball size, physics, and the strategy's tuned distances (approach offset, goalie standoff, ...) keep their 9 x 6 values, so simulated robots are oversized on a small table.
- The RL kick policy was trained on 9 x 6 and is off: `R` in the viewer does nothing, and the attacker always uses the classic approach.
- Works with or without `--vision`.

### Flags

Sim (`run_pipeline.py`, passed through to `simulation_node.py`):

| Flag | Meaning |
|------|---------|
| `--vision SRC` | `path.jsonl` replays a log (loops at the end, gaps between runs squashed to 1 s). `tcp://host:5556` is live over ZMQ. `http://host:8000/state` is live over HTTP. Omit for a normal sim game. |
| `--tag-map T:R,...` | AprilTag id → robot id, e.g. `9:0,12:1`. Default `auto`: each new on-field tag takes the lowest free robot. Mapped robots follow the camera and ignore click/WASD/strategy commands; robot 0 is the click/WASD robot. |
| `--ignore-tags IDS` | Tags never mirrored, e.g. `0,1,2,3` for the corner reference tags. |
| `--flip-x`, `--flip-y` | Mirror the camera field if a robot moves opposite to its tag (origin in a different corner). |
| `--replay-speed X` | Replay only. Default 1. |
| `--field W H` / `--field FILE` | True-size sim field (see [D](#d-true-size-field)). `FILE` is vision's `calib/field_pose.json`. Default 9 x 6 with the real field stretched onto it. |
| `--no-viz`, `--no-strategy` | As usual. |

Tracker (`track-combined`):

| Flag | Meaning |
|------|---------|
| `--zmq-pub "tcp://*:5556"` | Push every frame to the sim |
| `--serve-http PORT` | Serve the latest frame for HTTP polling |
| `--json-log FILE` | Record every frame for replay |
| `--no-window` | Headless, a few fps faster; Ctrl+C quits |
| `--ball-profile NAME` | Ball color profile from `calib/ball_color.json` |
| `--tag-size M` | Measured tag black-square size, metres (default 0.080) |
| `--photon-camera NAME` | PhotonVision camera nickname, if it publishes more than one |
| `--nt-server HOST` | Connect to an existing NetworkTables server instead of running one in the tracker |

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `can't open file ...\robocup\run_pipeline.py` | Run from `sim`, or call `python\run_pipeline.py`. |
| PowerShell: `The '<' operator is reserved` | A placeholder was pasted literally. Use a real value, e.g. `--ball-profile mine`. |
| Unknown ball profile | Name not in `vision/calib/ball_color.json`: `git pull`, or create it with `uv run calibrate-ball --profile NAME`. |
| `--zmq-pub` unrecognized, or "needs pyzmq" | Out of date, or launched in a different venv: `git pull`, `uv sync` at the repo root, and start it with `uv run`. |
| `error: Extra zmq is not defined` | Old command. Plain `uv sync` at the repo root already installs pyzmq. |
| `No module named 'torch'` (or `gymnasium`, `stable_baselines3`) | RL extra missing, or a plain `uv sync` removed it: `uv sync --extra rl`. |
| A `.venv` folder inside `robocup/` or `vision/` | Leftover from the old per-folder setup. Delete it; only the root `.venv` is used. |
| Strip says `DOWN` | Tracker not running, or `--vision` endpoint/port doesn't match `--zmq-pub`. |
| `Address in use` | Another pipeline or tracker is still running. Close it (check Task Manager for stray `python.exe`). |
| Robot doesn't follow the tag | Check the terminal for `tag N -> robot R` (auto) or `tag N seen but not in --tag-map`. No line at all means the tag never landed on the field: recalibrate. A robot parked in a corner is a corner reference tag: add `--ignore-tags 0,1,2,3`. |
| Robot follows but moves the wrong way | `--flip-x` and/or `--flip-y`. |
| A robot stays grey | Tag not seen recently, or its positions land off the field: recalibrate the field, and watch for the tracker's off-plane warning. |
| `--field ...field_pose.json`: no field width/height | The calibration predates saved sizes. Re-run `uv run calibrate-field --field W H`, or pass the numbers. |
| `tracker field ... != sim --field ...` | `--field` doesn't match what the tracker was calibrated with. Use the same numbers, or pass the calibration file. |
| Tracker: `no PhotonVision camera appeared on NetworkTables` | PhotonVision isn't running, or its NetworkTables Server Address doesn't point at the tracker's machine (`127.0.0.1` if same machine). See [PhotonVision setup](../vision/tag-tracking/README.md#photonvision-setup). |
| Tracker: `could not open PhotonVision's stream` | Check the stream URL in PhotonVision's camera view and pass it with `--stream URL`. |

### How it works

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

- **Reach circle:** objects move continuously, so `gap` seconds after the last accepted sighting each is inside a circle the physics allows. The limits come from the drive model in `config.py`: `ROBOT_MAX_ACCEL` = 2 x `MOTOR_MAX_FORCE` / `ROBOT_MASS` (3.57 m/s²) and `ROBOT_MAX_SPEED` = that / `LINEAR_DAMP` (1.19 m/s). A robot's circle is centred where its measured velocity carries it under damping, with the radius thrust can add in any direction (about 5 cm one frame out, mostly `VISION_GATE_MARGIN`). The ball can be kicked at any instant, so its circle is `MAX_BALL_SPEED` (6.5 m/s) x `gap` around it. `VISION_VEL_NOISE` covers the tracker's velocity error. Sightings outside are rejected (outlier gate, relocks after `VISION_GATE_RELOCK` rejects); predictions and dead-reckoning are clamped into it and speeds capped at `max_speed`.
- Sightings are dropped if the tracker marks them lost or they fall outside the real field (`VISION_FIELD_MARGIN`). Not-visible entries are the tracker's Kalman coast: with `VISION_TRACKER_COAST` they drive the body (`predict`) through short dropouts, clamped to the reach circle and the field, instead of handing it to sim physics. They never count as sightings, so they can't move the circle or extend the coast. `stale` is true for anything but `fresh`.
- Error above `VISION_SNAP_DIST` teleports instead of blending (first sighting, reacquire).
- Mirrored robots ignore wheel, kick and dribble commands. Goals are edge-triggered and a camera-owned ball is not reset.
- World state gains `source` / `stale` on robots and ball, and a `vision` stats block (fps, latency, transport drops from `seq` gaps, duplicates, rejects, tracker-coast entries used (`predicted`), per-object unseen %, and the tracker's own `stats` under `tracker`). VizNode greys stale objects and shows the stats along the bottom of the field.
- Reads both tracker formats: `seq` / `t_capture` / `lost` / `stats` (sim-feed branch) and `frame` / `run_id` (main). A new `run_id` is a tracker restart, not dropped frames. Replay keeps each frame's recorded capture-to-publish delay.

TODO(hardware): without `--field`, scaling a 1.93 m table up to 9 m inflates speeds x4.7. Fine for viewing, wrong for tuning control or RL; use `--field` (robots and strategy distances are still 9 x 6-sized there).

## Deployment Notes

The simulation node is a **drop-in replacement for a real camera system**. To run on physical hardware:

1. Run SimNode with `--vision` (see Vision Mirror); it keeps publishing the same world-state JSON on `VISION_PORT`.
2. `robot_node.py` and `viz_node.py` require no changes.
