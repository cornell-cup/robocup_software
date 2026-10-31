# robocup_software

Monorepo for the RoboCup project: a 2-D soccer simulator with autonomous team
strategy, and a single-camera vision system that tracks real robots and the
ball (AprilTags detected by PhotonVision). The two connect: the tracker can stream live positions into the sim, so a
real robot plays against simulated ones. The real robots are three-omniwheel
XRPs, driven from a laptop over Bluetooth (PestoLink).

| Folder | What it is | Details |
| --- | --- | --- |
| [`sim/`](sim/) | Simulator (Pymunk + Pygame + ZMQ) and robot controllers | [`sim/README.md`](sim/README.md) |
| [`strategy/`](strategy/) | Team strategy and the RL kick skill | [`strategy/README.md`](strategy/README.md) |
| [`vision/`](vision/) | Camera tracking of AprilTags (via PhotonVision) and a colored ball, in field metres | [`vision/README.md`](vision/README.md) |
| [`app/`](app/) | Web app: simulator, field calibration and ball profiles in a browser (`uv run robocup-web`) | [`app/README.md`](app/README.md) |
| [`robot/`](robot/) | The real XRP robot: MicroPython firmware, and a laptop driver (gamepad / keyboard → Bluetooth) | [`robot/README.md`](robot/README.md) |
| [`archive/`](archive/) | The original Godot version of the sim, for reference | |

This README is the map and quick reference. Each sub-README has the full flag
lists, calibration walkthroughs and troubleshooting.

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
│   └── tests/                  # unit tests for the packet format and wheel mixing
└── archive/godot/              # original Godot version of the sim, reference only
```

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

## Setup

The whole repo uses **one environment**: `.venv/` at the repo root, set up by
[uv](https://docs.astral.sh/uv/) from the root `pyproject.toml` and `uv.lock`.
It has the simulator, the four tracking packages, and pyzmq for linking them.
Python is 3.12 (pinned by `.python-version`; 3.13 also works). `pyproject.toml`
still caps it below 3.14 until the rest of the stack (pygame, pymunk, torch) is
checked there. uv downloads it if you don't have it.

PhotonVision, which detects the AprilTags, is not part of this environment: it
runs on its own (on a coprocessor or this machine) and the trackers read it
over NetworkTables with `photonlibpy`, whose version must match the
PhotonVision build. Set it up once as in
[tag-tracking's PhotonVision setup](vision/tag-tracking/README.md#photonvision-setup);
`uv run photon-sim` stands in for it when there is no hardware.

```powershell
winget install --id=astral-sh.uv   # once, if you don't have uv (or: pip install uv)

cd robocup_software
uv sync                            # create/update .venv
uv sync --extra rl                 # instead, if you train or evaluate the RL kick skill
```

Then run everything with `uv run ...`, from **any folder** in the repo. There is
no venv to activate, and no second venv inside any subfolder.

### uv commands and flags

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

### Without uv (pip)

Possible, but you manage the interpreter and install order yourself:

- **Simulator:** `pip install -r sim/requirements.txt` (includes the RL packages). Use Python 3.12. The sim code avoids newer syntax and was originally written for 3.9+, but the repo is only set up and tested on 3.12.
- **Tracker:** Python 3.12 or 3.13, and the packages must be installed in order; see
  [`vision/README.md`](vision/README.md#not-using-uv).

Use one venv for both if you want the tracker and sim to talk to each other.

## Command reference

Run simulator commands from `sim/` (they use relative paths) and
tracker commands from `vision/` (so `runs/...` logs land there). Both
use the same root `.venv`. Commands below are written for uv; with an activated
pip venv, drop the `uv run`.

### Simulator

| Command | What it does |
| --- | --- |
| `uv run python run_pipeline.py` | Full game: sim + strategy + robot controller + viewer |
| `uv run python run_pipeline.py --no-viz` | Headless (CI / SSH) |
| `uv run python run_pipeline.py --color red` | Strategy controls the red team |
| `uv run python run_pipeline.py --vision ../vision/test.jsonl` | Replay a recorded tracking session into the sim |
| `uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3` | Mirror the live camera into the sim |
| `uv run python run_pipeline.py --vision tcp://localhost:5556 --field ../vision/calib/field_pose.json` | Live, with the sim field at the real table's size |
| `uv run python simulation_node.py` | Physics node alone |
| `uv run python ../strategy/strategy_node.py` | Strategy node alone |
| `uv run python robot_node.py` | Controller node alone |
| `uv run python viz_node.py` | Viewer alone |
| `uv run robocup-web` | The simulator in a browser instead of the Pygame viewer: start/stop, click/WASD driving, toggles; plus field calibration ([details](app/README.md)) |

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

### Viewer controls

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

### RL kick skill

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

### Tracker (`vision`)

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

### Real robot (`robot`)

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

## Common workflows

**Play a simulated game:**

```bash
cd sim
uv run python run_pipeline.py
```

**Replay a recording, no camera:**

```bash
cd sim
uv run python run_pipeline.py --vision ../vision/test.jsonl --field 1.93 1.32
```

**Live real robot vs. sim robots** (two terminals):

```bash
# 1. tracker (PhotonVision set up and its lens calibrated; calibrate-field / calibrate-ball once beforehand)
cd vision
uv run track-combined --ball-profile mine --tag-size 0.080 --zmq-pub "tcp://*:5556"

# 2. sim
cd sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3
```

Each tag seen on the field claims the lowest free robot (the terminal prints
`tag 9 -> robot 0`). The strip along the bottom of the sim window shows link
status, fps and latency. Add `--json-log runs/myrun.jsonl` to the tracker to
record the session. See the
[Vision Mirror section](sim/README.md#vision-mirror) for details and
troubleshooting.

**Real robots played by the strategy** (three terminals, or the web app):

```bash
# 1. tracker, as above
# 2. sim, mirroring the camera; pin each robot's tag to a sim robot
cd sim
uv run python run_pipeline.py --vision tcp://localhost:5556 --ignore-tags 0,1,2,3 --tag-map 9:0,12:1
# 3. the robots follow sim robots 0 and 1 over Bluetooth
cd robot
uv run python -m laptop XRP-01 XRP-02 --sim 0,1
```

Turn strategy on (key `5` in the sim window) and robots 0 and 1 play for real.
Each moves only while the camera sees it. In the web app: start the tracker
(Tracking), start the sim with **Live camera** (Simulator), then on **Robots** set
each robot to **Follow sim**. The tag's heading must point at the robot's
dribbler (the tracker's `--heading-offset`), and `MAX_EFFORT` in the robot's
`config.py` caps its speed.

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
