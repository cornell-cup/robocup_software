# What students can code: the design

Status: proposal, updated 2026-11-20. Today a team file can replace five
slots (`attacker`, `supporter`, `defender`, `roles`, `prepare`; see
[Write your own team](../README.md#write-your-own-team)), draw on the field
and play live or in test matches. This document designs everything a student
will be able to change, from a first "drive to the ball" to a whole team with
its own plays; how students keep their code (workspaces of several files),
share it (upload and download) and play each other; and the order we build it
in ([ROADMAP.md](../ROADMAP.md), student platform).

## Principles

1. **Low floor, high ceiling, wide walls** (Papert [1]; Resnick and
   Silverman [2]). A first program should take five minutes (low floor), a
   strong student should never run out of room (high ceiling), and there
   should be more than one kind of thing to make: a goalie, a passing play, a
   driving controller (wide walls).
2. **Replace one piece, keep the rest.** Every piece has a built-in version.
   Whatever a file doesn't define stays built-in, so a student's team always
   plays a whole game, whatever they've written so far.
3. **Built-in code is the textbook.** The built-in team is written against
   the same interface students use (`strategy/api.py`), so "read how the
   built-in does it, then change it" always works.
4. **You can see what your code thinks.** Drawing on the field, watched
   values, stepping frame by frame. Most of learning to program robots is
   finding out why the robot didn't do what you meant.
5. **It can't break the game, and it can't cheat.** Code runs in its own
   process with a time budget and falls back to the built-in piece when it
   fails (already the case). It reads the world and sends commands; it can't
   move robots or the ball directly, and the physics caps speeds and kicks.

## The levels

From smallest to biggest. Each level is optional, and they combine: a
student can write a skill, a role and a play in the same file.

| Level | What you write | Built-in today | Good for |
| --- | --- | --- | --- |
| 0. Settings | Numbers the built-in team uses | – | A first change with no code |
| 1. Skills | One action: drive to the ball, kick toward a point | Inside the attacker | First lessons, Blocks |
| 2. Roles | What one robot does in its role, every frame | `attacker`, `supporter`, `defender` | Most of the course |
| 3. Team | Who plays which role; plays; the whole team at once | `roles` | Strategy, coordination |
| 4. Driving | How a robot gets to its target (wheel speeds) | `sim/robot_node.py` | Control: a separate track |

### Level 0: settings

The built-in team's tuned numbers, under names a student can find:

```python
SETTINGS = {
    "supporter.ahead": 2.0,     # metres past the ball (built-in: 1.5)
    "defender.standoff": 0.6,   # goalie's distance from the goal (0.8)
}
```

The Code page shows them as sliders, each with a one-line explanation. Only
numbers the built-in code already uses (the constants at the top of
`tactics/*.py`), checked against a range, so a setting can't break anything.

### Level 1: skills

Skills are single actions. Built-in roles call them through `me`, so a
student's version is used everywhere the built-in team would use the skill:

```python
def go_to_ball(me, world):        # drive to the ball, facing it
def kick_toward(me, world, target):  # get behind the ball on the line to target; kick
def intercept(me, world):         # meet a moving ball where it will be
def pass_to(me, world, mate):     # kick_toward where `mate` will be
def dribble_to(me, world, target):  # dribbler on, carry the ball to target
```

```python
def kick_toward(me, world, target):
    behind = world.ball.pos - unit(target - world.ball.pos) * 0.25
    if me.distance_to(behind) > 0.05:
        return me.go_to(behind, face=target)
    return me.go_to(world.ball.pos, face=target, kick=True)
```

This means the built-in attacker has to be rewritten on top of these skills
(today its logic is one function). The match runner's fingerprint check has
to show the rewrite plays exactly as before.

### Level 2: roles

As today: `attacker`, `supporter`, `defender`, each `fn(me, world) ->
Command`. Two additions:

- **`goalie`** becomes its own role. Today the defender is the goalie. Split,
  a team of three is attacker, supporter and goalie by default, and a fourth
  robot (or a play) can add a field defender.
- **Your own roles.** Any other function named in a play or in `roles` is a
  role: `runner`, `marker`, `wall`. This is the "wide walls" part: students
  invent positions, not only rewrite ours.

### Level 3: team

Three ways to decide for the whole team, from simple to full control:

1. **`roles(team, world)`**, as today: a list of `(robot, role name)`.
2. **Plays.** A play is a plan for a situation: when it applies, which roles
   it needs, and optionally how they coordinate. The play picker chooses one
   each frame; switching takes some time so the team doesn't flicker between
   plays (as in CMU's STP [3, 4]).

   ```python
   PLAYS = {
       "attack": ["attacker", "supporter", "goalie"],
       "defend": ["marker", "goalie", "goalie2"],
   }

   def pick_play(world):
       if world.us.race_margin < -0.5:  # they get to the ball well before us
           return "defend"
       return "attack"
   ```

   The built-in team gets two or three plays (attack, defend, kickoff), and
   they have to beat v1 in the match runner before they become the default.
3. **`team(robots, world)`**: a command for every robot, all at once. For
   coordination one role function can't do: a pass and a run timed together,
   two robots trapping the ball. A robot it leaves out falls back to roles.

Plus **`kickoff(robots, world)`**: where each robot stands before the
kickoff (positions on our half; checked so they're legal).

When a file defines several of these, the more specific one wins:
`team()` > the play's roles > `roles()` > built-in.

### Level 4: driving

```python
def drive(me, target, dt):   # -> (vx, vy, w): velocity in the robot's frame
```

Replaces the controller that turns a target into wheel speeds (today PD /
time-optimal / MPC in `sim/robot_node.py`). This is a different subject
(control: overshoot, PID, saturation) and needs its own drills. It runs in
the sim first. On real robots only once it passes the driving drills, since a
bad controller drives into walls.

## The Brain stays fixed

A student's file is the team's brain in the sense of *decisions*. The
`Brain` class (`core/brain.py`) is the nervous system around it: it keeps
time, notices what happened, delivers the commands and catches crashes. It
isn't replaceable and works the same for every team, which is also what
makes test matches fair. A few decisions it makes today move out into
pieces students can write:

| The Brain today | Owner in this design |
| --- | --- |
| Kickoff and restarts (goal, new half, sim restart) | Brain: the referee's view, the same for both teams. Read as `world.phase`, `world.kicked_off`, and **new** `world.events` |
| Camera robots lost for 0.5 s are left out | Brain |
| Robots a person is driving are left alone | Brain |
| Team files in their own process; answers merged, errors reported once | Brain |
| Each team's `memory`, kept between frames | Brain keeps it; code fills it |
| The sticky attacker (`self.sticky`) | Stays: it already reaches the roles slot as `world.sticky`, and only the built-in `roles` uses it. A student's `roles` keeps its own in `world.memory` |
| Dribbler on for the attacker, when the toggle is on | Moves to the command: `go_to(..., dribble=True)`. The toggle stays, as a setting the built-in attacker reads, so the built-in team plays as before |
| RL kick on/off | Stays an operator switch for the built-in attacker; students can call it as a skill |
| Only the closer of two kickers fires | Moved into the sim (`PymunkWorld.closest_kicker`): it works around the physics (two kicks cancel) and applies to both teams alike |

Two additions for code:

- **`world.events`**: what happened since the last frame: `"kickoff"`,
  `"goal_for"`, `"goal_against"`, `"half_time"`. The Brain already notices
  these, and today only logs them.
- **`prepare(world)`**: runs once a frame before any robot's slot, for work
  the whole team shares (the best open spot, say), so it isn't redone for
  every robot inside the time budget. Results go in `world.memory`.

## Workspaces, sharing and playing each other

A student's team is a **workspace**: a folder of Python files, not one file.
Students keep several (versions, experiments), swap them with classmates,
and play them against each other.

### Workspaces

```
teams/
├── anna_team/             ← a workspace
│   ├── team.py            ← the slot functions; the team starts here
│   ├── passing.py         ← any other files, any names
│   └── positions.py
└── ben_v2/
    └── team.py
```

- `team.py` defines the slots (`attacker`, `supporter`, ..., later `team()`).
  The other files are the student's own modules: `from passing import
  best_pass_spot` in `team.py` just works (the loader puts the workspace on
  the import path while it loads).
- Saving any file reloads the whole workspace, helpers included.
- An error says which file and line (`passing.py:12: ...`); clicking it opens
  that file at that line.
- Flat folders for now (no subfolders): enough for a team, simple to show.
- An existing single file in `teams/` (`teams/my_team.py`) keeps working as a
  one-file workspace.

On the Code page: a workspace picker; one tab per file
(`team.py | passing.py | + | Guide`); add, rename and delete files; **New
workspace** from the template, an example or blank; **Duplicate** to keep a version before a big change. **Play** and
**Test match** use the whole workspace.

### Sharing: download and upload

- **Download** a workspace as `anna_team.zip` (its `.py` files and a small
  `workspace.json`: name, author as typed, when, the app version), or one
  file as `.py`.
- **Upload** a `.zip` (a whole workspace) or `.py` files (into the open
  workspace, or a new one); dragging them onto the page works too. Checked:
  at most 1 MB and 50 files, only `.py`, `.md` and `.json`, safe names (no
  paths that point out of the workspace), and a name that's taken asks before
  replacing anything.
- An uploaded workspace is marked as someone else's (`workspace.json`), so
  the page always runs it in safe mode (below) and says whose it is.
- One machine, files passed around: two students play each other on one
  laptop by uploading each other's zip. Playing across two laptops over the
  network can come later; it's much more work and nothing needs it yet.

### Playing each other

- **Live:** the Play card picks a workspace for each side,
  `Blue: anna_team ▾  Red: ben_v2 ▾` (or the built-in team). The simulator
  already takes a team for each side (`--blue` / `--red`).
- **Test match:** workspace against workspace, N games, sides swapped every
  game, the same seeds for every pairing; the result shows both names, goals,
  wins, goal difference with its range, and the errors of each side.
- **Head to head:** the page keeps each pairing's results, so "anna_team vs
  ben_v2: 3 wins, 1 draw, 2 losses" builds up over the session.
- **Class tournament** (a later step): every chosen workspace against every
  other, and a ranking table.
- Fair by construction: same physics, each team in its own process with the
  same time budget, one driving controller for everyone in competition
  (level 4 is off there), and neither team can see the other's code or
  memory: only positions on the field.

### Running other people's code safely

Until now team code was the student's own, run as trusted code. Uploading
changes that: a classmate's file runs on your computer. Before uploads ship:

- **Every team in its own process, everywhere.** Live play already does
  this; test matches will too (today the match runner loads both teams into
  its own process). A crash or endless loop in one team never touches the
  other, and two workspaces that both have a `passing.py` can't mix up.
- **Safe mode in that process.** Before the team's code loads, the worker
  installs an audit hook ([PEP 578](https://peps.python.org/pep-0578/)) that
  refuses, with a clear error on the line that tried: writing files, reading
  files outside the workspace and Python's own libraries, starting programs,
  network sockets, `ctypes`, and changing the hook. Imports are limited to a
  list: `math`, `random`, `numpy`, `collections`, `itertools`, `functools`,
  `dataclasses`, `typing`, `statistics`, the workspace's own files, and the
  strategy's `api`, `tactics` and `skills` (to read and reuse the built-in
  code). Plus a memory cap on the process.
- **Honest limit:** in CPython this is a strong fence for a classroom, not a
  proven sandbox; a determined expert might find a way out. Fine for
  classmates' code on our own laptops, with the web app on `localhost`. Code
  from strangers on the internet, or a hosted platform, would need a real
  sandbox: Python in the browser ([Pyodide](https://pyodide.org)) or a
  container.

### More control

Workspaces make room for bigger code, and the freedom that matters most for
students playing each other is **`team(robots, world)`**: one function that
commands every robot at once (level 3). It moves up the build order, right
after playing each other, together with your own roles, `goalie`,
`kickoff()` and plays.

## What code can read and do

What exists today is unmarked; **new** marks what this design adds.

**`me`, one robot**

| | |
| --- | --- |
| `id`, `pos`, `vel`, `angle`, `has_ball` | |
| `distance_to(p)`, `angle_to(p)` | |
| `time_to(p)` | **new**: seconds to get there (the analysis layer's `time_to_reach`) |
| `role` | **new**: this robot's role this frame |

**Commands (what a slot returns)**

| | |
| --- | --- |
| `me.go_to(p, face=..., kick=...)`, `me.face(p)`, `me.stop()` | |
| `go_to(..., dribble=True)` | **new**: dribbler on (the sim has one; today only a switch reaches it) |
| `go_to(..., max_speed=0.5)` | **new**: drive slower than full speed (careful near the ball) |
| `me.kick_toward(t)`, `me.pass_to(mate)`, `me.go_to_ball()`, ... | **new**: the level 1 skills, built-in or yours |
| kick strength | later: the sim's kick is one fixed strength, and the real robot can't fire its kicker yet |

**`world`, what the team sees**

| | |
| --- | --- |
| `ball.pos`, `ball.vel`, `teammates`, `opponents` | |
| `our_goal`, `their_goal`, `field_size`, `attack_angle` | |
| `phase`, `kicked_off`, `time`, `memory` | |
| `lane_clear(a, b)` | |
| `us` / `analysis`: who reaches the ball first, threats | |
| `ball.at(t)`, `ball.stops_at` | **new**: where the ball will be (`world/prediction.py`) |
| `score` (ours, theirs), `time_left` | **new**: already in `GameState`, not passed on yet |
| `in_field(p)`, `our_half(p)`, `clamp(p)` | **new**: small field helpers |
| `play` | **new**: the play running now |

**Seeing what it does (new)**

| | |
| --- | --- |
| `world.draw.point(p)`, `.line(a, b)`, `.circle(p, r)`, `.text(p, "open!")` | Drawn on the Code page's field for this frame, in the robot's colour |
| `world.show("margin", m)` | A live value in a table beside the field, per robot |
| `print(...)` | The log, as today |

Drawing is the first thing to build: every later lesson needs it.

## Rules and limits

| | Practice (Play) | Test match | Student vs student, tournament | Real robots |
| --- | --- | --- | --- | --- |
| Levels 0–3 | yes | yes | yes | yes |
| Level 4 (driving) | yes | yes | no (one controller for everyone, so it's about strategy) | after the driving drills |
| Someone else's workspace | safe mode | safe mode | safe mode | safe mode |
| Time | 10 ms a call, 15 ms a frame for the team | same | same | same |

- A slot that raises, is too slow or returns something wrong falls back to the
  built-in one for that frame and shows the error on its line (as today).
- Code reads the world; it can't move robots or the ball, read the other
  team's memory or code, or exceed the physics (speed, acceleration, kick).
- A student's own code runs as trusted Python on their own computer, as
  now; a workspace from someone else runs in safe mode (see [Running other
  people's code safely](#running-other-peoples-code-safely)). The web app
  stays on `localhost` while students use it. Hosting it for many students
  online would need a real sandbox (or Pyodide in the browser).

## Learning path

Each lesson is a drill: a set-up situation, the piece the student writes, and
a pass condition the page checks. Blocks cover lessons 1 to 6.

| # | Lesson | You write | Drill: pass when |
| --- | --- | --- | --- |
| 1 | Drive to the ball | `go_to_ball` | touches the ball from 3 starting spots, within 5 s each |
| 2 | Kick toward the goal | `kick_toward` | scores on an empty goal from 5 spots |
| 3 | Be the goalie | `goalie` | saves 3 of 5 shots |
| 4 | Get open for a pass | `supporter` | the pass lane is clear 70% of the time while the attacker has the ball |
| 5 | Meet a moving ball | `intercept` | reaches 4 of 5 rolling balls before they cross a line |
| 6 | Who plays what | `roles` | beats v0 over 10 games |
| 7 | Plans for situations | `PLAYS`, `pick_play` | beats v1 over 10 games |
| 8 | The whole team | `team` | a class tournament |
| 9 | Driving (own track) | `drive` | reaches 5 targets with under 5 cm overshoot |

A drill is a small Python file in `strategy/drills/`: where the robots and
the ball start (and the ball's velocity), which slots the student writes, the
opponent (none, standing still or built-in), how long it runs, the pass
check, and a hint. It runs headless like a test match, so the page can check
it in a second or two and show a replay.

## Build order

Each step keeps the built-in team identical (the fingerprint check), unless
it's a new built-in that has beaten v1.

1. **Seeing:** `world.draw`, `world.show` and the Code page's field drawing
   them. Plus `dribble=` and `max_speed=` on commands, `world.events` and
   `prepare()`. Small, no behaviour change. The kick conflict moves into the
   sim. **Done 2026-11-20**, with the same fingerprint as before:
   `core/drawing.py`; shapes and values travel with strategy_node's message
   to the web app (the Code page's **Field** split and Values card, and the
   Simulator page); events come from Brain and reach team files in their own
   process too; `max_speed` caps the controller's output in `robot_node.py`
   (and the match runner); only the kick closest to the ball fires, in
   `PymunkWorld.step`. The sticky attacker stays where it is: on a closer
   look it already reaches the roles slot through `world.sticky` and only the
   built-in `roles` reads it, so moving it would be a rename. A student's
   `roles` keeps its own in `world.memory`.
2. **Workspaces:** a team is a folder of `.py` files with `team.py` first;
   imports between them; reload on any save; errors with file and line; the
   Code page's workspace picker, file tabs, new / rename / delete / duplicate;
   single files keep working.
3. **Every team in its own process, and safe mode:** the match runner plays
   each team through a worker like live play does; the worker's audit hook,
   import list and memory cap. Needed before anyone runs someone else's code.
4. **Sharing:** download a workspace (`.zip`) or a file; upload `.zip` or
   `.py` (checked, marked as someone else's); drag and drop.
5. **Playing each other:** a workspace per side in the Play card, workspace
   against workspace in test matches, head-to-head records.

   **Steps 2-5 done 2026-11-20.** `core/loader.py` loads a workspace with its
   own importer (no `.pyc`, so every edit counts; names that would hide
   Python's modules refused); `core/safety.py` (the audit hook, the import
   list, no `input()`); `core/worker.py` gained a lockstep mode for matches
   (the same result as in-process, tested), a hello once loaded so a hang is
   told from a slow start, giving up after 3 hangs, a Windows job object
   with the memory cap, and `--check`; the match runner plays every team
   through a worker (about 5x real time instead of 8x); the web app's
   `teams.py` (workspaces, upload / download, head to head) and the Code
   page. Your old single files in `teams/` became workspaces by themselves.
   Every example in the guide is played by a unit test.
6. **More control:** `team()` for the whole team, your own roles, `goalie`
   split out, `kickoff()`, plays and `pick_play`. Built-in plays only if they
   beat v1. **Done 2026-11-20** for students (`core/slots.py`: `goalie`, the
   team's own roles, `PLAYS` with the 0.25 s switch, `team()`, `kickoff()`
   checked to our half; `world.play`; the guide's "Plans for the whole
   team", every example played by a test), with the built-in team unchanged
   (same fingerprint). Built-in plays come with strategy step 6.
7. **Skills:** the built-in attacker rewritten on `go_to_ball`,
   `kick_toward`, ...; skill slots; `time_to`, `ball.at`, `score`,
   `time_left`.
8. **Drills:** the drill format and runner, lessons 1–5, a Drills list on the
   Code page.
9. **Tournaments:** every chosen workspace against every other, a ranking
   table.
10. **Settings and Blocks:** level 0 sliders; Blockly for lessons 1–6,
    generating the same Python.
11. **Driving:** the `drive` slot and its drills, sim first.

## Open questions

- Which levels may run on the real robots, and who decides (a teacher switch
  on the Robots page?).
- The per-frame budget: 15 ms for the whole team leaves little for `team()`
  with heavy maths. Measure on the slowest laptop we'll use.
- Should plays and `team()` be one feature (a play may come with a `team`
  function) or two? This design keeps them separate so `PLAYS` stays simple
  enough for Blocks.
- Tournaments: settled for now as one machine running every workspace
  (students upload their zips to it). Over the network later, if a class
  wants it.
- Names: a typed author name in `workspace.json` is enough for a classroom;
  anything stronger (accounts) only if the platform is ever hosted.
- Safe mode for a student's own code too? Simpler to explain ("all code runs
  the same way"), but it blocks a curious student reading a data file of
  their own. Starting with: own code trusted, others' code safe.

## References

1. S. Papert. *Mindstorms: Children, Computers, and Powerful Ideas.* Basic
   Books, 1980. (Low floor, high ceiling.)
2. M. Resnick and B. Silverman. "Some Reflections on Designing Construction
   Kits for Kids." *Interaction Design and Children (IDC)*, 2005. (Wide walls.)
3. B. Browning, J. Bruce, M. Bowling and M. Veloso. "STP: Skills, Tactics and
   Plays for Multi-Robot Control in Adversarial Environments." *Journal of
   Systems and Control Engineering*, 2005.
4. M. Bowling, B. Browning and M. Veloso. "Plays as Effective Multiagent
   Plans Enabling Opponent-Adaptive Play Selection." *ICAPS*, 2004.
