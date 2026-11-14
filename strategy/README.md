# strategy: the team AI

The robots' brain. `strategy_node.py` reads the world state from the simulator
60 times a second, gives every robot a role and a target point, and publishes
the targets. `sim/robot_node.py` drives the robots to them, simulated or real
(see the [main README](../README.md) for how the pieces connect).

It plays **both teams**: blue attacks right (+x), red attacks left.

## Set up

Nothing beyond the repo setup (`uv sync` at the repo root, see the
[main README](../README.md#set-up-step-by-step)). The learned kick policy
(`rl/`) needs the RL extra: `uv sync --extra rl`.

## Try it

The strategy node starts with the rest of the simulator, so the easiest way is:

1. Start the simulator, in the browser or a terminal:
   - `uv run robocup-web`, then **Simulator** → **Start simulator** (keep *Strategy node* on), or
   - `cd sim` then `uv run python run_pipeline.py`.
2. Turn strategy on: **Strategy (AI)** in the Game card, or key `5` in the pygame window.
   Every robot nobody is driving starts playing.
3. Things to try:
   - **Attacker dribbles** (key `0`): the attacker on the strategy's team keeps the ball.
   - **RL kick** (key `R`, 9 × 6 field only): the attacker uses the learned kick policy.
   - Drive a robot yourself (click, or **Keys**) while the others play around it.
   - With a real robot mirrored by the camera and following a sim robot, strategy
     plays it for real ([robot/README.md](../robot/README.md), step 7).

To run the node on its own (the sim must be running):

```powershell
cd strategy
uv run python strategy_node.py                # --color red: route the dribbler to red's attacker
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `--mode zmq\|tcp` | How to get the world state (ZMQ is what the pipeline uses) | `zmq` |
| `--color blue\|red` | "Our" team: whose attacker gets the dribbler, and whose robots can be human-driven. Both teams are planned either way | `blue` |
| `--rl-checkpoint PATH` | Trained PPO kick policy, loaded when RL kick is first turned on | `rl/checkpoints/first_final/final.zip` |

## How it plays

This is how it plays today. Where it's going is in [Design](#design).

`decide()` in `strategy_node.py`, every tick, for each team:

| Robots left | Roles |
| --- | --- |
| 1 | Goalie: stays on the line from its goal to the ball |
| 2 | Attacker + supporter |
| 3 | Attacker + supporter + defender |

- **Roles by distance to the ball:** closest is the attacker, then the
  supporter, then the defender. The attacker keeps its role unless a teammate is
  clearly closer (0.4 m hysteresis), so roles don't flip mid-run.
- **Attacker:** goes to a point behind the ball, on the line to a spot inside
  the opponent's goal (level with the ball, kept inside the posts), and faces
  along it. Once lined up there, it drives through the ball toward that spot. If an opponent is also on the ball, it kicks it off at an
  angle to break the standoff. With RL kick on, the learned policy picks its
  target instead.
- **Supporter:** waits ahead of the ball toward the opponent's goal (on its own
  half before kickoff), facing the ball.
- **Defender:** plays goalie on the goal-to-ball line.
- **Human-driven robots** (gamepads in the pygame window) are left out, and the
  rest of the team is planned without them.

- **Kickoff:** after the start, every goal and every new half, supporters hold
  their own half until the ball first moves.
- **Robots the camera lost** (unseen for 0.5 s) are left out like human-driven
  ones, until the camera sees them again.

Tuning distances (`APPROACH_OFFSET`, `ATTACKER_HYSTERESIS`,
`GOALIE_STANDOFF`, ...) are constants at the top of each file in `tactics/`,
tuned on 9 x 6 and scaled by `FIELD_SCALE` on another field; dribble and kick
zones are in `skills/dribble.py` and `skills/kick.py`.

## Files

```
strategy/
├── strategy_node.py      # entry point: decide() runs the tactics for both teams; Brain (memory between frames)
├── state.py              # GameState, built from the world-state JSON each frame
├── command.py            # Command: what a tactic tells one robot (target, heading, kick)
├── analysis.py           # once per frame, in seconds: time to reach, ball intercepts, who wins the race, threats
├── tactics/
│   ├── role_assignments.py  # who attacks, supports, defends (closest to the ball attacks)
│   ├── attacker.py       # contested kick / RL kick / push toward goal
│   ├── supporter.py      # wait ahead of the ball
│   └── defender.py       # goalie on the goal-to-ball line
├── geometry.py           # distances, goals, interpolation
├── prediction.py         # ball prediction (slowing down as in the sim) and intercepts
├── eval/
│   ├── match.py          # headless matches, strategy against strategy
│   └── baseline_v0.py    # frozen copy of decide(), the bar to beat
├── tests/                # unit tests: python -m unittest discover -s strategy/tests -t .
├── skills/
│   ├── navigate.py       # poses: stand behind the ball, face a point, face where you drive
│   ├── kick.py           # kick zone check and impulse (used by the sim's physics)
│   ├── dribble.py        # dribble capture zone (used by the sim's physics)
│   └── rl_kick.py        # wraps the trained PPO policy as a skill
├── rl/                   # the learned kick: env, reward, train, eval, checkpoints/
├── plays/, BT/           # placeholders, see "Design" below
└── stp_architecture_and_execution.svg
```

RL commands (training, evaluation) are in the [main README](../README.md#rl-kick-skill).

## Messages

In (port 9090, from `sim/simulation_node.py`): the world state, see
[sim/README.md](../sim/README.md#message-formats). Robots 0–2 are blue, 3–5 red.

Out (port 9091, to `sim/robot_node.py`), after each tick:

```json
{
  "targets": {
    "0": { "x": 5.2, "y": 3.1, "angle": 0.0 },
    "1": { "x": 6.5, "y": 4.5, "angle": 1.57 }
  },
  "attackers": [0, 3]
}
```

`angle` is the heading to face (radians, counter-clockwise from +x). Kicks and
dribbler on/off go straight to the simulator (port 9092). robot_node only uses
the targets while strategy is turned on.

## Testing and matches

From the repo root, no hardware and no running sim needed:

```powershell
uv run python -m unittest discover -s strategy/tests -t .   # unit tests (~5 s)
uv run python -m strategy.eval.match                         # current vs v0: 10 games of 120 s (~3 min)
uv run python -m strategy.eval.match --a current --b current --games 4
$env:ROBOCUP_FIELD = "1.93 1.32"; uv run python -m strategy.eval.match   # on the real table's size
```

The match runner (`eval/match.py`) plays one strategy against another in one
process: the sim's physics, robot_node's controllers and one `Brain` per team,
about 8-10x faster than real time. Sides swap every game and each game is
seeded, so the same flags play the same games. It prints goals, the goal
difference per game with a 95% confidence interval, possession, territory,
kicks, and how often the ball got stuck (it resets a ball that sits still for
10 s, as the sim's `9` key does; `--no-stuck-reset` turns that off).

`v0` is the frozen baseline (`eval/baseline_v0.py`): `decide()` as it was on
2026-11-14. Every step of the [Design](#design) has to match or beat it. Don't
edit it. `current` is `strategy_node.decide`. To compare a new version, add it
to `STRATEGIES` in `eval/match.py`.

`Brain` (in `strategy_node.py`) is everything the strategy remembers between
frames around `decide()`: the sticky attacker, kickoff, lost camera robots, the
RL / dribble / human-driver toggles. The node's main loop and the match runner
both use it, so they behave the same.

## Known issues

Found while planning the redesign below.

Fixed (2026-11-14):

| Issue | Fix |
| --- | --- |
| Possession was always "loose": `DRIBBLER_RANGE` (0.05 m) was measured from the robot's centre, closer than the robot and ball radii (~0.19 m) allow | `has_ball` is now "ball in the kick zone" (`state.ball_in_front`); `ball_holder` says who |
| Kickoff was detected once, never after a goal or half time | `Brain` resets it on every goal and new half; `game_phase` is `"kickoff"` until the ball moves |
| The sim's score, half, time left and goals weren't read | `GameState` has `score_ours`, `score_theirs`, `half`, `time_remaining`, `goal_seq`; `game_phase` is `"halt"` once the game is over |
| Ball prediction assumed constant speed | `prediction.py` slows the ball as the sim does: `p(t) = p0 + v0 (1 - e^(-k t)) / k`, k = `BALL_DAMP`. A test checks it against the physics |
| Tuned distances ignored `--field` | They now scale with `FIELD_SCALE`, like the robot and the kick zone. A stopgap: the design moves decisions to time |
| Robots the camera lost were still planned | `GameState` keeps `from_camera` / `stale`; a camera robot unseen for 0.5 s is left out, and the team is planned without it |
| The attacker never pushed the ball: it drove to its spot behind the ball, counted as arrived there and stopped, short of the ball (261 mm away on 9 x 6, where touching is 186 mm). The ball only moved through collisions and contested kicks | `tactics/attacker.py`: once lined up it drives through the ball (`lined_up`, `PUSH_THROUGH`), aiming into the goal (`aim_point`). `sim/config.py`: `ARRIVAL_THRESH` shrinks with the robot (36 mm was more than a small-field robot's radius). A lone attacker now scores from anywhere, on both field sizes |
| The robots-only wall at the back of each goal came 69 mm into the field on 1.93 x 1.32 (its thickness didn't shrink with the field), so a robot couldn't push the ball over the goal line | `sim/pymunk_world.py`: placed by `GOAL_BACK_DEPTH`, scaled with the robot; unchanged on 9 x 6 |
| The scoreboard gave each goal to the wrong team: the sim reports whose goal the ball went into, and `simulation_node` added the point to that team. Match results reported before this fix had the two scores swapped | `simulation_node.py` and `eval/match.py` credit `pymunk_world.scorer(goal)`, the other team; a test checks a ball into red's goal is blue's |
| Robots squeezed the ball out of the field at the corners: the two walls' rounded ends left an 83 mm opening there (the 9 x 6 ball is 86 mm; on 1.93 x 1.32 it is 18 mm and rolled through, 4 times in 6 minutes) | `sim/pymunk_world.py`: the walls run on past each corner and overlap. `strategy/tests` drives a robot into a ball pinned in each corner for 5 s |

Open:

| Issue | Where | Effect |
| --- | --- | --- |
| The real kicker can't be fired | `robot/firmware/robot.py`, the Bluetooth packet and port 9094 carry no kick ([robot/README.md](../robot/README.md#1-wire-the-motors)) | Kicks and passes work in the sim only |

## Design

Where the strategy is going. Today `decide()` does everything in one function.
The plan keeps CMU's **Skills, Tactics and Plays** (STP) layering [1, 2]:
**plays** say what the team does, **tactics** what each robot does, **skills**
how it does it. It then changes how open play works, following what the
strongest Small Size League (SSL) teams do now [3, 4, 5, 6].

### What the teams we learn from do

| Source | What they do | What we take |
| --- | --- | --- |
| CMU STP [1] and adaptive plays [2] | A play has a condition for when it can start and one for when it's done (success, failure or timeout), and runs until one fires. Plays that work get picked more often | Plays commit instead of being re-decided every frame |
| CMDragons 2015, RoboCup champions, 48-0 over six games [3, 4] | No playbook in open play. One primary attacker acts on the ball's most likely path; a support attacker stays a fixed distance from the ball as a backup; defenders are placed from threats, each threat a task with a priority | Plays for set pieces only; open play is one continuous behaviour; threat-based defense |
| TIGERs Mannheim 2025 [5] | Every pass is generated, filtered, rated and selected. Each rating is kept apart (goal chance, interception, pressure) and chosen by priority, not summed. Interception comes from where each opponent could be t seconds from now | The attacker rates its options; decisions are made in time, not distance |
| RoboJackets [6] | A situation classifier (ball ours / free / theirs / contested, and which third of the field) picks the play; roles are requested with priorities and filled by cost | Keep "what is happening" apart from "what we do" |
| SCRAM [7] | When robots go to formation spots, pick the matching that minimises the longest trip, which keeps paths from crossing | Which robot goes to which spot |
| CMU learned skills [8] | Learn skills with reinforcement learning (RL), write tactics and plays by hand | The RL kick stays a skill |
| Behavior trees [12, 13, 14] | A tree works only if each part reports whether it applies right now and how far along it is | Every skill and play says when it applies |

### Principles

1. **The top level switches on game state, not on who has the ball.** Set
   pieces (stop, kickoff, free kick, after a goal) are separate plays. The rest
   is one open play, with an aggressiveness setting (how many robots attack)
   that follows ball position, score and time left [3, 4]. Switching on
   possession makes the team change plans whenever the ball changes hands,
   many times a second in a scramble.
2. **Plays commit.** Each play has `applicable(state)`, `done(state)` and a
   timeout, and the playbook picks again only when the current play ends or
   stops applying [1, 2].
3. **Decide in time, not distance.** Time for a robot to reach the ball, time
   before an opponent could cut off a pass [5]. Time accounts for field size
   and robot speed by itself, which fixes the `--field` issue above. The limits
   are already in `sim/config.py` (`ROBOT_MAX_SPEED`, `ROBOT_MAX_ACCEL`), and
   `sim/vision_bridge.py` already computes the same reach circle for its
   outlier gate.
4. **Roles are tasks with priorities.** For example: block the main threat,
   then primary attacker (cost: time to the ball), then support attacker or
   second defender. With 3 robots there are only 6 ways to hand out roles, so
   score them all and add a bonus for keeping the current role [3, 6]. Spots in
   a formation are matched by the longest-trip rule [7].
5. **The attacker rates its options instead of following a fixed script.**
   Candidates: push or shoot at points in the goal, dribble to a point, pass.
   Filter out the impossible ones, rate each separately (goal chance,
   interception risk, progress) and pick by priority: a good shot first, then a
   safe pass forward, then keeping the ball [4, 5]. `has_clear_shot` and
   `can_pass_ball` in `geometry.py` are the first versions of these ratings.
6. **Supporter and defenders follow CMDragons [3].** The supporter holds
   `ball + d u`, with `u` toward blocking our goal when an opponent is near the
   ball and toward their goal otherwise. Defenders stand on the line from each
   threat to our goal, or across the pass lane between threats.
7. **Learning stays at the skill level for now [8].** Skills say when they
   apply: the RL kick's in-distribution check (`rl_in_dist`) is one, and the
   attacker falls back to the classic approach when it fails. Learning above
   skills comes later: when to keep or pass [9], set pieces with several
   robots [10], or learning the tree itself [13]. RL-X [11] trains faster than
   Stable-Baselines3 if training time becomes the limit.
8. **Measure every change.** A headless match runner plays the new strategy
   against the old one, seeded, many games at a time, and reports goals,
   possession and stuck balls. Constants are tuned there, offline. TIGERs track
   their pass success and tune risk from it [5]; that kind of adaptation [2, 5]
   is for real opponents, since in the sim the AI plays itself.

### One tick

```
tick(state):
  world = analyze(state)          # once per frame: time to the ball for every robot,
                                  # threats, damped ball prediction, shot and pass ratings
  play  = playbook.current_or_select(world)       # switch on game state; plays commit
  roles = assign(play.tasks(world), our_robots)   # priority + cost over all assignments
  for robot, task in roles:
      command = task.tactic.step(world, robot)    # target, heading, kick, dribble
  return commands                                 # same message to robot_node as today
```

### Layers and files

| Layer | Job | Files |
| --- | --- | --- |
| Analysis | Everything measured once per frame, shared by the layers below | `analysis.py` (new), `prediction.py`, `geometry.py` |
| Plays | Pick what the team does; set pieces commit until done | `plays/playbook.py`, `plays/kickoff.py`; open play in `plays/offensive.py` and `plays/defensive.py`, to merge into one |
| Roles | Hand out tasks by priority and cost, keep roles stable | `tactics/role_assignments.py` |
| Tactics | One robot's behaviour; the attacker rates options | `tactics/attacker.py`, `tactics/supporter.py`, `tactics/defender.py` |
| Skills | Reusable moves with no game knowledge; each says when it applies | `skills/navigate.py`, `skills/kick.py`, `skills/dribble.py`, `skills/rl_kick.py` |
| Evaluation | Headless games, new strategy against old | `eval/match.py` (new) |

`BT/tree.py` isn't needed for this: the top level is a switch on game state in
`playbook.py`. A full behavior-tree engine [12, 13] can come back if the plays
outgrow the switch.

The strategy's own messages stay as they are, so robot_node, the Pygame window
and the web app need no change. One addition: the active play and each
robot's task (e.g. `"0": "attacker:line_up"`) in the targets message, so the
viewers can show them.

### Order of work

Each step has to match or beat the one before it in the match runner.

1. Fix the [known issues](#known-issues) in `state.py` and `prediction.py`;
   build the match runner; add tests for `decide()`. **Done 2026-11-14**
   ([Testing and matches](#testing-and-matches)).
2. Split `decide()` into tactics and skills without changing how it plays.
   **Done 2026-11-14:** `tactics/`, `skills/navigate.py`, `command.py`. Checked
   identical to the step-1 code on 20,000 random positions and whole games, on
   both 9 x 6 and 1.93 x 1.32.
3. Analysis: time to reach, damped ball prediction, threats. **Done
   2026-11-14** (`analysis.py`, not used by `decide()` yet): `time_to_reach`,
   `ball_intercept`, and `analyze()` giving each team the race to the ball
   (`race_margin`, seconds) and CMDragons' first- and second-level threats [3].
   The motion model is the sim's drive model and matches the physics to within
   6%: a robot drives fastest straight ahead or at 60° to it (~1.03 m/s) and
   slowest sideways (~0.89 m/s), so `config.ROBOT_MAX_ACCEL` (2x one motor)
   overstates it. About 2 ms per frame.
4. Threat-based defense and the CMDragons supporter.
5. Role assignment by priority and cost.
6. The attacker's option rating: shots, then passes.
7. Fire the real kicker: firmware, Bluetooth packet and port 9094.

Done ahead of step 6 (2026-11-14): the attacker pushes through the ball (see
[Known issues](#known-issues)). Against v0: on 1.93 x 1.32, 33-1 over 10
games of 120 s (v0 never touches the ball there); on 9 x 6, even, 15-16 over
30 games of 90 s (95% CI +/-0.29 goals per game).

### The first sketch

The original STP sketch (March 2026). It picks the open-play play from
possession, which the design above replaces. Set pieces still work this way.

![STP execution trace](./stp_architecture_and_execution.svg)

## References

Architecture and plays

1. B. Browning, J. Bruce, M. Bowling, M. Veloso. "STP: Skills, tactics, and
   plays for multi-robot control in adversarial environments." *Proc. IMechE
   Part I: Journal of Systems and Control Engineering* 219(1):33-52, 2005.
   https://webdocs.cs.ualberta.ca/~bowling/publications/b2hd-05cse-stp.html
2. M. Bowling, B. Browning, M. Veloso. "Plays as Effective Multiagent Plans
   Enabling Opponent-Adaptive Play Selection." ICAPS 2004, pp. 376-383.
   https://icaps04.icaps-conference.org/icapspapers/ICAPS04BowlingM.pdf

Small Size League teams

3. J. Biswas, P. Cooksey, S. Klee, J. P. Mendoza, R. Wang, D. Zhu, M. Veloso.
   "CMDragons 2015 Extended Team Description." RoboCup 2015.
   https://ssl.robocup.org/wp-content/uploads/2019/01/2015_ETDP_CMDragons.pdf
4. J. P. Mendoza et al. "Selectively Reactive Coordination for a Team of Robot
   Soccer Champions." AAAI 2016.
   https://ojs.aaai.org/index.php/AAAI/article/view/10415
5. M. Geiger, N. Ommer, A. Ryll, M. Ratzel. "TIGERs Mannheim Extended Team
   Description for RoboCup 2025." 2025.
   https://ssl.robocup.org/wp-content/uploads/2025/04/2025_ETDP_TIGERs-Mannheim.pdf
6. RoboJackets. `rj_gameplay`: play selector, situations, role requests.
   https://rj-rc-software.readthedocs.io/en/ros2/autoapi/rj_gameplay/play_selector/index.html

Role assignment

7. P. MacAlpine, E. Price, P. Stone. "SCRAM: Scalable Collision-avoiding Role
   Assignment with Minimal-Makespan for Formational Positioning." AAAI 2015.
   https://ojs.aaai.org/index.php/AAAI/article/view/9424

Learning

8. D. Schwab, Y. Zhu, M. Veloso. "Learning Skills for Small Size League
   RoboCup." RoboCup 2018 Symposium.
   https://www.cs.cmu.edu/~mmv/papers/18robocup-SchZhuVel.pdf
9. P. Stone, R. S. Sutton, G. Kuhlmann. "Reinforcement Learning for
   RoboCup-Soccer Keepaway." *Adaptive Behavior* 13(3):165-188, 2005.
   https://nn.cs.utexas.edu/?AB05
10. J. M. Catacora Ocana, F. Riccio, R. Capobianco, D. Nardi. "Cooperative
    Multi-Agent Deep Reinforcement Learning in a 2 Versus 2 Free-Kick Task."
    RoboCup 2019 Symposium.
    https://2019.robocup.org/downloads/program/OcanaEtAl2019.pdf
11. N. Bohlinger, K. Dorer. "RL-X: A Deep Reinforcement Learning Library (not
    only) for RoboCup." arXiv:2310.13396, 2023. https://arxiv.org/abs/2310.13396

Behavior trees

12. P. Ögren, C. I. Sprague. "Behavior Trees in Robot Control Systems."
    *Annual Review of Control, Robotics, and Autonomous Systems* 5, 2022.
    https://arxiv.org/abs/2203.13083
13. B. Banerjee. "Autonomous Acquisition of Behavior Trees for Robot Control."
    IROS 2018, pp. 3460-3467. https://ieeexplore.ieee.org/document/8594083
14. L. Haug, A. A. F. Brandão, A. Casals. "Coordinating Task Switching in a
    Robotics Multi-Agent System Using Behavior Trees" (IEEE Very Small Size
    Soccer). arXiv:2606.01170, 2026. https://arxiv.org/abs/2606.01170
