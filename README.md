# robocup_software

Everything for Cornell Cup's RoboCup robots, in one repo:

- a **simulator**: 3 v 3 soccer for three-omniwheel robots, with a team AI that
  plays both sides;
- **camera tracking**: one overhead camera finds the robots' AprilTags (through
  PhotonVision) and the ball, in field metres;
- the **real robots**: XRPs driven over Bluetooth, by hand or by the AI;
- a **web app** that runs all of it from a browser.

They connect: the camera mirrors the real robots into the sim, and the sim's AI
can drive the real robots. Start with [Set up](#set-up-step-by-step), then pick
something from [Play with it](#play-with-it).

| Folder | What it is | Start here |
| --- | --- | --- |
| [`app/`](app/) | The web app, `uv run robocup-web`: simulator, robots, camera setup in a browser | [`app/README.md`](app/README.md) |
| [`sim/`](sim/) | The simulator: physics, robot controllers, Pygame window, camera mirror | [`sim/README.md`](sim/README.md) |
| [`strategy/`](strategy/) | The team AI, and the learned (RL) kick | [`strategy/README.md`](strategy/README.md) |
| [`vision/`](vision/) | Camera tracking of AprilTags and the ball, with PhotonVision | [`vision/README.md`](vision/README.md) |
| [`robot/`](robot/) | The real XRP robots: firmware, and the laptop driver (gamepad, keyboard, sim) | [`robot/README.md`](robot/README.md) |
| [`archive/`](archive/) | The original Godot version of the sim, for reference | |

## Set up step by step

Once per computer. Windows PowerShell shown; the same works on Linux / macOS.

1. **Install uv** (it manages Python and every package):
   ```powershell
   winget install --id=astral-sh.uv   # or: pip install uv
   ```
2. **Get the code** and go into it:
   ```powershell
   git clone https://github.com/cornell-cup/robocup_software.git
   cd robocup_software
   ```
3. **Install everything** into one environment, `.venv/` at the repo root
   (Python 3.12 is downloaded if you don't have it):
   ```powershell
   uv sync                  # add --extra rl if you train or evaluate the RL kick
   ```
   Run it again after every `git pull`. From now on, run every command with
   `uv run ...`, from any folder. There is no venv to activate, and no second
   venv in any subfolder.
4. **Check it works**, no hardware needed:
   ```powershell
   uv run selfcheck                 # the camera-to-field maths
   uv run robocup-web               # opens the web app on http://localhost:5801
   ```
   In the web app, **Simulator** → **Start simulator**: robots appear on the field.

That's enough for the simulator. The rest is only for what you use:

| To use | Also set up | How |
| --- | --- | --- |
| The camera | PhotonVision, once per camera: download, pipeline, lens calibration | Web app header → **Download**, then **Start**; then [PhotonVision setup](vision/tag-tracking/README.md#photonvision-setup) |
| The camera | The field and ball calibrations, once per room | Web app **Field calibration** and **Ball profile** pages ([app/README.md](app/README.md#try-it)) |
| A real robot | Flash its firmware and check its motors, once per robot | [robot/README.md](robot/README.md), steps 1–4 |
| The RL kick | The RL packages (large) | `uv sync --extra rl` |

**VS Code:** open the repo root (`robocup_software`) as the folder and pick
`.venv\Scripts\python.exe` as the interpreter; `pyrightconfig.json` does the rest.

## Play with it

Each one stands alone; the later ones build on the camera and robot setup above.

### 1. Watch the AI play a simulated game (no hardware)

In the browser: `uv run robocup-web` → **Simulator** → **Start simulator** →
turn on **Strategy (AI)**. Click the field to send the selected robot
somewhere, or pick **Keys** and drive it with W A S D, Q E.

Or in a Pygame window:

```powershell
cd sim
uv run python run_pipeline.py      # then: 5 = strategy on, click = send robot 0, 4 = drive it with W A S D
```

### 2. Replay a recorded camera session (no hardware)

```powershell
cd sim
uv run python run_pipeline.py --vision ..\vision\test.jsonl --field 1.93 1.32
```

Robots and the ball move as they did on the real table. In the browser: on the
Simulator page pick *Robots and ball:* **Recording**.

### 3. Drive a real robot

With a flashed robot switched on and this computer's Bluetooth on:

```powershell
cd robot
uv run python -m laptop XRP-01 --keyboard   # W A S D move, Q E turn, Space dribbler
uv run python -m laptop XRP-01              # or a gamepad
uv run python -m laptop XRP-01 XRP-02       # several robots, one gamepad each
```

In the browser: **Robots** page → add `XRP-01` → *Driven by:* **Keyboard** or a gamepad.

### 4. Track the robots and the ball with the camera

PhotonVision running and set up, field and ball calibrated:
web app **Tracking** page → tag size, ball profile → **Start tracking**. You see
the camera and the field live; the *Drive robots* card there drives a robot
while you watch.

From a terminal (measure your tag's black square for `--tag-size`):

```powershell
cd vision
uv run track-combined --ball-profile mine --tag-size 0.177
```

No camera? `uv run photon-sim` stands in for PhotonVision (tags only); see
[vision/README.md](vision/README.md).

### 5. Mirror the real robots into the sim

The camera's robots and ball appear in the sim and play with the simulated ones.
In the browser: start tracking (4), then **Simulator** → *Robots and ball:*
**Live camera** → **Start simulator**. From two terminals:

```powershell
# terminal 1
cd vision
uv run track-combined --ball-profile mine --tag-size 0.177 --zmq-pub "tcp://*:5556"
# terminal 2
cd sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3 --field ..\vision\calib\field_pose.json
```

Each tag on the field claims the lowest free robot (the terminal prints
`tag 9 -> robot 0`); `--tag-map 9:0` pins it. `--ignore-tags 0,1,2,3` keeps the
field's corner tags from becoming robots. Add `--json-log runs\myrun.jsonl` to
the tracker to record the session for (2). Details and troubleshooting:
[sim/README.md](sim/README.md#vision-mirror).

### 6. Let the AI play the real robots

The loop closes through the camera: the sim sees the real robot, the AI and
controllers compute its command, and the robot gets it over Bluetooth, only
while the camera sees it.

1. Do (5), pinning each robot's tag to a sim robot: `--tag-map 9:0,12:1`, or
   *Tag → robot* on the Simulator page.
2. Make the robots follow those sim robots: on the **Robots** page set each to
   **Follow sim** → its robot number, or
   ```powershell
   cd robot
   uv run python -m laptop XRP-01 XRP-02 --sim 0,1
   ```
3. Turn strategy on (Game card, or key `5`). Robots 0 and 1 play for real.

Before the first run: the tag's heading must point at the robot's dribbler
(the tracker's heading offset), and `MAX_EFFORT` in the robot's `config.py`
caps its speed. Start slow. More in [robot/README.md](robot/README.md), step 7.

## How it fits together

```
 vision/                                               sim/  and  strategy/
 ───────                                               ────────────────────
 PhotonVision → track-combined ── ZMQ :5556 ──→ simulation_node ── :9090 world state ──→ strategy_node
                               (or .jsonl log)        ↑                    │                    │
                                                      │                    ↓              :9091 targets
                                              :9092 wheel cmds        viz_node                  ↓
                                                      └──────────── robot_node ←── :9093 clicks/keys
                                                                         │
                                                              :9094 body cmds (real robots)
                                                                         ↓
                                     XRP robots ←── Bluetooth ── python -m laptop --sim / robocup-web Robots page
```

The loop closes through the camera: a real robot's tag is mirrored into the sim,
strategy and robot_node compute its command as for any sim robot, and the robot
gets that command over Bluetooth, only while the camera sees it.

| Port | From → To | Carries |
| --- | --- | --- |
| 9090 | sim → robot, strategy, viz | World state JSON (ball + 6 robots; 0–2 blue, 3–5 red) |
| 9091 | strategy → robot | Autonomous targets |
| 9092 | robot → sim | Wheel speed commands |
| 9093 | viz → robot / strategy | Manual targets, strategy / RL / kick toggles |
| 9094 | robot → real robots | Each robot's command per body axis (`python -m laptop --sim`, the web app's Robots page) |
| 5556 | tracker → sim | Live camera tracking (`--zmq-pub` / `--vision`) |
| 5810 | PhotonVision → tracker | NetworkTables: tag corners and lens calibration (the tracker is the server unless `--nt-server`) |
| 1181 | PhotonVision → tracker | PhotonVision's raw camera stream (MJPEG), for the overlay and the ball |
| 5800 | browser → PhotonVision | PhotonVision's UI: camera, pipeline, lens calibration |
| 5801 | browser → robocup-web | This repo's web app: simulator, real robots, field calibration, ball profiles |

## Repository layout

```
robocup_software/
├── pyproject.toml              # the one uv environment for everything (see Setup)
├── uv.lock                     # exact pinned versions, committed
├── .python-version             # 3.12
├── pyrightconfig.json          # editor type checking for the whole repo (VS Code / Pylance)
├── .gitignore                  # repo-wide: caches, venv, tracker run logs (runs/, *.jsonl)
├── sim/                        # the simulator pipeline
│   ├── run_pipeline.py         # launcher: spawns the four nodes below
│   ├── simulation_node.py      # physics (pymunk_world.py), publishes world state
│   ├── robot_node.py           # PD / time-optimal / MPC controllers -> wheel speeds
│   ├── viz_node.py             # Pygame field view, mouse/keyboard/gamepad input
│   ├── vision_bridge.py        # camera tracking -> sim (--vision)
│   ├── config.py               # all shared constants: ports, field, hardware, gains
│   └── requirements.txt
├── strategy/                   # team strategy (Python package `strategy`)
│   ├── strategy_node.py        # entry point: world state -> robot targets
│   ├── state.py, geometry.py, prediction.py
│   ├── skills/                 # kick, dribble, rl_kick (plays/, tactics/, BT/ are stubs)
│   └── rl/                     # PPO kick skill: env, reward, train, eval, checkpoints/
├── vision/                     # camera tracking (members of the root uv workspace)
│   ├── vision-core/            # field geometry, camera<->field transform, Kalman filter
│   ├── tag-tracking/           # AprilTag tracking from PhotonVision + field calibration, photon-sim
│   ├── ball-tracking/          # color-based ball tracking + ball color calibration
│   ├── combined-tracking/      # tags + ball from one camera, publishes to other programs
│   ├── calib/                  # intrinsics.json, field_pose.json, ball_color.json
│   └── test.jsonl              # recorded tracking session for replay
├── app/                        # web app (`uv run robocup-web`)
│   ├── server/                 # Python API (FastAPI); serves the built pages
│   └── web/                    # the pages (React), built into server/.../static/
├── robot/                      # the real XRP robot (from robocup-omnidirectional)
│   ├── firmware/               # MicroPython for the XRP: robot.py, mixing.py, lib/pestolink.py
│   ├── laptop/                 # `python -m laptop`: gamepad / keyboard -> PestoLink over BLE
│   └── tests/                  # unit tests: packets, wheel mixing, fleet, sim following
└── archive/godot/              # original Godot version of the sim, reference only
```

## Reference

### Setup details

The whole repo uses **one environment**: `.venv/` at the repo root, set up by
[uv](https://docs.astral.sh/uv/) from the root `pyproject.toml` and `uv.lock`.
Python is 3.12 (pinned by `.python-version`; 3.13 also works). `pyproject.toml`
still caps it below 3.14 until the rest of the stack (pygame, pymunk, torch) is
checked there.

PhotonVision, which detects the AprilTags, is not part of this environment: it
runs on its own (on a coprocessor or this machine) and the trackers read it
over NetworkTables with `photonlibpy`, whose version must match the
PhotonVision build. The web app downloads the matching build for you.

#### uv commands and flags

| Command | What it does | When |
| --- | --- | --- |
| `uv sync` | Makes `.venv` match `uv.lock` **exactly**: sim packages + tracking packages + pyzmq. Removes anything else, including the RL packages. | First setup, and after every `git pull` |
| `uv sync --extra rl` | Same, plus the RL extra: `gymnasium`, `stable-baselines3`, `torch`, `tensorboard` (large download). | Instead of plain `uv sync`, if you use `strategy/rl/`. Pass it **every** time you sync, or they get removed. |
| `uv sync --all-extras` | Same as `--extra rl` today (it is the only extra). | Same |
| `uv sync --locked` | Fails instead of updating `uv.lock` if `pyproject.toml` changed without re-locking. | CI, or to check the lockfile is current |
| `uv run <command>` | Runs a command in `.venv`, syncing it first if `uv.lock` changed. Never removes packages, so it keeps the RL extra. | Every command: `uv run track`, `uv run python run_pipeline.py` |
| `uv run --extra rl <command>` | Runs with the RL extra installed (adds it if missing). | RL scripts on a venv synced without `--extra rl` |
| `uv add <pkg>` | Adds a dependency to the root `pyproject.toml`, re-locks, installs. | New sim dependency (also add it to `sim/requirements.txt` for pip users) |
| `uv add --optional rl <pkg>` | Same, into the RL extra. | New RL dependency |
| `uv lock` | Re-resolves `uv.lock` after a hand edit of `pyproject.toml`. | After editing `pyproject.toml` |

**Old flags you no longer need.** Earlier docs said `uv sync --all-packages --extra zmq`
(tracker) and `uv run --no-project` (sim). Neither is needed now:
pyzmq is always installed, and the root project already includes every package.
`uv sync --extra zmq` now **errors** (`Extra zmq is not defined`), and
`--all-packages` without `--extra rl` uninstalls the RL packages.

**`Failed to hardlink files` warning.** Harmless: uv's cache (on `C:`) and the
repo are on different drives, so uv copies instead of linking. To silence it and
speed installs, keep the cache on the same drive, e.g.
`[Environment]::SetEnvironmentVariable("UV_CACHE_DIR", "F:\.uv-cache", "User")`,
or set `UV_LINK_MODE=copy` to just hide the warning.

**VS Code / Pyright:** select `.venv\Scripts\python.exe` at the repo root as the
interpreter. The root `pyrightconfig.json` already points there and tells Pylance
where the sim's script-style imports (`config`, `strategy.*`) live. Open
the repo root (`robocup_software`) as the VS Code folder.

#### Without uv (pip)

Possible, but you manage the interpreter and install order yourself:

- **Simulator:** `pip install -r sim/requirements.txt` (includes the RL packages). Use Python 3.12. The sim code avoids newer syntax and was originally written for 3.9+, but the repo is only set up and tested on 3.12.
- **Tracker:** Python 3.12 or 3.13, and the packages must be installed in order; see
  [`vision/README.md`](vision/README.md#not-using-uv).

Use one venv for both if you want the tracker and sim to talk to each other.

### Command reference

Run simulator commands from `sim/` (they use relative paths) and
tracker commands from `vision/` (so `runs/...` logs land there). Both
use the same root `.venv`. Commands below are written for uv; with an activated
pip venv, drop the `uv run`.

#### Simulator

| Command | What it does |
| --- | --- |
| `uv run python run_pipeline.py` | Full game: sim + strategy + robot controller + viewer |
| `uv run python run_pipeline.py --no-viz` | Headless (CI / SSH) |
| `uv run python run_pipeline.py --color red` | The strategy's own team is red (it plans both teams either way) |
| `uv run python run_pipeline.py --vision ../vision/test.jsonl` | Replay a recorded tracking session into the sim |
| `uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3` | Mirror the live camera into the sim |
| `uv run python run_pipeline.py --vision tcp://localhost:5556 --field ../vision/calib/field_pose.json` | Live, with the sim field at the real table's size |
| `uv run python simulation_node.py` | Physics node alone |
| `uv run python ../strategy/strategy_node.py` | Strategy node alone |
| `uv run python robot_node.py` | Controller node alone |
| `uv run python viz_node.py` | Viewer alone |
| `uv run robocup-web` | The simulator in a browser instead of the Pygame viewer: start/stop, click/WASD driving, toggles; plus the real robots and the camera setup ([details](app/README.md)) |

**`run_pipeline.py` flags** (the vision flags are passed through to `simulation_node.py`):

| Flag | Meaning | Default |
| --- | --- | --- |
| `--no-viz` | Don't start the Pygame viewer | off |
| `--no-strategy` | Don't start the strategy node | off |
| `--color blue\|red` | Team the strategy node controls | `blue` |
| `--vision SRC` | Mirror camera tracking: `tcp://host:port` (ZMQ), `http://host:port/state`, or a `.jsonl` log to replay (loops) | off (pure sim) |
| `--tag-map T:R,...` | Pin AprilTag ids to robot ids, e.g. `9:0,12:1` | auto: each new tag takes the lowest free robot |
| `--ignore-tags IDS` | Tags never mirrored, e.g. `0,1,2,3` for the field corner tags | none |
| `--flip-x`, `--flip-y` | Mirror camera axes if a robot moves opposite to its tag | off |
| `--replay-speed X` | Playback speed for `.jsonl` replay | `1` |
| `--field W H` / `--field FILE` | True-size sim field in metres, or the tracker's `calib/field_pose.json` | 9 × 6 m (real field stretched onto it) |

**Running nodes individually:**

| Command | Flags |
| --- | --- |
| `simulation_node.py` | `--headless` (run physics as fast as possible), `--seed N` (seed + jitter initial positions), plus the vision flags above |
| `strategy_node.py` | `--mode zmq\|tcp` (default `zmq`), `--color blue\|red`, `--rl-checkpoint PATH` |

To give a standalone node a custom field size, set `ROBOCUP_FIELD="1.93 1.32"`
in the environment (`run_pipeline.py` does this for you).

#### Viewer controls

| Input | Action |
| --- | --- |
| Left click | Send robot 0 to that point |
| `1` / `2` / `3` | Controller: PD / time-optimal (bang-bang) / MPC |
| `4` | Manual mode: `WASD` to move, `Q` / `E` to rotate |
| `6` | Gamepad mode: left stick moves, right stick rotates |
| `5` | Toggle autonomous strategy |
| `R` | Toggle the RL kick policy (9 × 6 field only) |
| `K` / `Enter` | Kick with robot 0 |
| `0` | Toggle attacker dribbling |
| `9` | Toggle ball-stuck reset |

#### RL kick skill

Run from the repo root. Needs the RL extra: `uv sync --extra rl` once, or the
`--extra rl` in each command below adds it. A trained policy is committed at
`strategy/rl/checkpoints/first_final/final.zip`.

| Command | What it does |
| --- | --- |
| `uv run --extra rl python strategy/rl/env.py --check` | Sanity-check the Gymnasium env (`--episodes N`, default 5) |
| `uv run --extra rl python strategy/rl/train.py --check-only` | 10k-step smoke test, 1 env |
| `uv run --extra rl python strategy/rl/train.py --timesteps 500000` | Full PPO training run |
| `uv run --extra rl python strategy/rl/eval.py --checkpoint PATH` | Evaluate a checkpoint |

| Flag | Command | Meaning | Default |
| --- | --- | --- | --- |
| `--timesteps N` | train | Training steps | `500000` |
| `--n-envs N` | train | Parallel envs | `8` |
| `--seed N` | train | Random seed | `0` |
| `--run-name NAME` | train | Name for the checkpoint folder | timestamp |
| `--resume PATH` | train | Continue from a `.zip` checkpoint | off |
| `--device auto\|cpu\|cuda` | train | Torch device (CPU is usually as fast) | `auto` |
| `--checkpoint PATH` | eval | Checkpoint to evaluate (required) | — |
| `--episodes N` | eval | Episodes to run | `100` |
| `--seed-start N` | eval | First seed, kept apart from training seeds | `10000` |
| `--stochastic` | eval | Sample actions instead of using the deterministic policy | off |

#### Tracker (`vision`)

All commands are `uv run <command>`; add `--help` to any for its full flag list.

| Command | What it does | Hardware |
| --- | --- | --- |
| `selfcheck` | Proves the field math is correct | none |
| `track --synthetic-camera` | Whole tag pipeline on a fake field | none |
| `photon-sim` | Stand-in PhotonVision (synthetic field, NetworkTables, stream on 1181): run it, then `track` / `calibrate-field` / `track-combined` in another terminal. No ball | none |
| `serve-tag` | Shows a sized AprilTag on your phone (`--field-sheet out.png` prints all 4 corner tags) | phone |
| `calibrate-camera` | Measures a local webcam's lens from a waved ChArUco board → `calib/intrinsics.json`, read by `track-ball` only (`--save-board-png` to print the board, `--synthetic` to test). The tag tools use PhotonVision's lens calibration | webcam |
| `calibrate-field --field W H` | Locates the field from 4 corner tags → `calib/field_pose.json`, saves the field size | PhotonVision |
| `calibrate-ball --profile NAME --radius-mm R` | Samples the ball's color → `calib/ball_color.json` (required before ball tracking) | webcam |
| `track` | Track AprilTags (`--calibrate-live` calibrates the field first) | PhotonVision |
| `track-ball --ball-profile NAME` | Track the ball | webcam |
| `track-combined --ball-profile NAME` | Tags and ball from one camera, and publish them | PhotonVision |
| `unlock-camera` | Fix a camera left locked by a crashed run (dark green picture) | webcam |

**Most-used tracker flags:**

| Flag | Meaning | Default | Used by |
| --- | --- | --- | --- |
| `--field W H` | Real field size in metres; saved by `calibrate-field`, so later runs don't need it | saved size, else `1.2 0.8` | calibrate-field, track, track-ball, track-combined |
| `--tag-size M` | Measured black-square size of the tag, metres | `0.080` | track, calibrate-field, track-combined |
| `--ball-profile NAME` | Ball color profile from `calib/ball_color.json` (`mine`, `lab`, `arkart`, ...) | `test` | track-ball, track-combined |
| `--photon-camera NAME` | PhotonVision camera nickname | the only one published | track, calibrate-field, track-combined |
| `--nt-server HOST` | Connect to an existing NetworkTables server instead of running one here | run it here | track, calibrate-field, track-combined |
| `--stream URL` | PhotonVision's camera stream (`--no-stream`: tags only, on `track` / `calibrate-field`) | `http://<photonvision>:1181/stream.mjpg` | track, calibrate-field, track-combined |
| `--camera N` | Local webcam index | `0` | calibrate-camera, calibrate-ball, track-ball |
| `--cam-height M` | Camera height; ignored once `field_pose.json` exists | `1.5` | track, track-ball, track-combined |
| `--sequential` | Calibrate by moving one tag to each corner instead of 4 at once | off | calibrate-field, track --calibrate-live |
| `--origin X Y` / `--origin center` / `--no-origin` | Where `(0,0)` goes (skips the click picker) | ask by click | calibrate-field |
| `--no-window` | Headless, slightly faster | off | track-combined |
| `--zmq-pub "tcp://*:5556"` | Push every frame over ZMQ (what the sim's `--vision` reads) | off | track-combined |
| `--serve-http PORT` | Serve the latest frame at `GET /state` | off | track-combined |
| `--json-out PATH` | Overwrite a file with the current state every frame | off | track-combined |
| `--json-log PATH` | Append one JSON line per frame, for replay | off | track-combined |

Full list: [`vision/README.md`](vision/README.md#flags-that-matter).

#### Real robot (`robot`)

Run from `robot/`. First-time wiring, flashing and the motor check are in
[`robot/README.md`](robot/README.md); `uv sync` at the repo root replaces its
venv / `pip install` step.

| Command | What it does |
| --- | --- |
| `uv run python -m laptop XRP-07` | Drive robot `XRP-07` (its `ROBOT_NAME`) with the first gamepad |
| `uv run python -m laptop XRP-07 --keyboard` | Same, with the keyboard (WASD, Q/E rotate, Space dribbler) |
| `uv run python -m laptop --gamepad 1 XRP-07` | Use the second gamepad |
| `uv run python -m laptop XRP-01 XRP-02` | Several robots, one gamepad each (with `--keyboard`: Tab switches robot) |
| `uv run python -m laptop XRP-01 --sim 0` | `XRP-01` follows sim robot 0: strategy / sim clicks drive it, closed-loop through the camera |
| `uv run python -m laptop --scan` | List nearby Bluetooth devices |
| `uv run robocup-web`, then **Robots** | All of the above from the browser: add robots, drive by keyboard / gamepad, follow a sim robot |
| `uv run mpremote cp firmware/config.py firmware/mixing.py firmware/robot.py firmware/main.py :` | Upload the firmware (plus `lib/pestolink.py` the first time; see the robot README) |
| `uv run mpremote run firmware/motor_test.py + reset` | Spin each wheel to check wiring and direction |
| `uv run python -m unittest discover -t . -s tests` | Unit tests: packet format, wheel mixing, the multi-robot fleet, sim following (no hardware) |

`robot/` started as a copy of
[robocup-omnidirectional](https://github.com/cornell-cup/robocup-omnidirectional)
at commit `80eb3ad`; that repo has its history.

## Status and caveats

- `strategy/plays/`, `tactics/`, `BT/tree.py` and `skills/navigate.py`
  are empty placeholders for the planned plays → tactics → skills structure.
  The working strategy currently lives in `strategy_node.py`.
- The RL kick policy was trained on the 9 × 6 field and is disabled when
  `--field` changes the size.
- With `--field`, robot/ball sizes and the strategy's tuned distances keep
  their 9 × 6 values.
- There is no automated test suite beyond `robot/tests/`; `selfcheck` and the
  `--synthetic` modes are the closest thing for the rest.
- Real robots following the sim get robot_node's controller output as is: the
  gains and the sim's drive model (`sim/config.py`, `TODO(hardware)`) were tuned
  for the simulated robot, not measured on the XRP, so expect to tune them.
