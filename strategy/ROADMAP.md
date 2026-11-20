# Strategy roadmap and student platform

Two plans that share a foundation:

1. **The strategy:** what to build next, following the design in
   [README.md](README.md#design). Reference numbers `[n]` point to its
   [References](README.md#references).
2. **The student platform:** students learn RoboCup and coding by replacing
   one piece of the team AI at a time (how the supporter gets open, how the
   attacker kicks) with their own code, in blocks or in Python, from the web
   app.

Both start with the same step: a standard interface for behaviours, so any
behaviour can be swapped for another without touching the rest.

Status (2026-11-20): design steps 1-3 are done (fixes, match runner and
tests, tactics split out of `decide()`, the time-based analysis layer), and
the attacker pushes the ball through. Step 1 below (slots) is done, and the
folder was reorganised around it: the process and `api.py` at the top, the
built-in `tactics/` written against `api.py`, the machinery in `core/` and
`world/` (see the [Files](README.md#files) list). The built-in team is
frozen as `v1`, the match runner's default opponent, and student team files
load, play and reload, each in its own process, and the web app's Code page
edits, plays and tests them (build order steps 2 and 3 below). Step 1 of
[what students can code](docs/student_code.md#build-order) is done too: code
draws on the field and shows values, gets game events and a `prepare` slot,
and can dribble and limit its speed; and steps 2-5: workspaces of several
files, every team in its own process with safe mode for someone else's,
upload and download, and students playing each other's code. Next on the
platform: `team()` and plays (step 6 there); and strategy step 2.

## 1. Strategy: next steps

In order. Each one has to match or beat the version before it in the match
runner (`python -m strategy.eval.match`).

1. **A standard interface for behaviours ("slots").** Every behaviour takes
   the robot and the world and returns a `Command`. Each role (attacker,
   supporter, defender) and the role assignment are named slots with a
   built-in default, and a team can swap any slot for another
   implementation. Behaviours should be able to say when they apply, which
   is what makes a behavior tree work [12]. Checked to play exactly like the
   code before it.
   **Done 2026-11-20:** `api.py` (`Me`, `World`), `core/slots.py` (`Team`,
   `DEFAULT_TEAM`, fallback to the built-in slot on errors), `teams=` in
   `decide()`, `Brain` and the match runner (`team_a` / `team_b`); see
   [Write your own team](README.md#write-your-own-team). Identical to the
   code before it on 20,000 random positions and whole games on both field
   sizes. Not yet: skill-level slots (go to, kick toward) and behaviours
   saying when they apply; those come with the steps that need them.
2. **Plug the analysis layer in.** `analyze()` (`world/analysis.py`) is computed
   once per frame and handed to every behaviour; today nothing uses it.
3. **Threat-based defense and the CMDragons supporter [3, 4].** Defenders
   block the first-level threat's line to our goal and the passing lanes of
   the second-level threats (both come out of `analyze()`). The supporter
   holds a fixed distance from the ball: toward our goal when an opponent is
   near it, toward theirs otherwise.
4. **Role assignment by priority and cost, in seconds [6, 7].** Roles as
   tasks with priorities; with 3 robots, score all 6 assignments, with a
   bonus for keeping the current role. Formation spots (kickoff) by SCRAM's
   rule: the assignment whose longest trip is shortest.
5. **An attacker that rates its options, as TIGERs do [5].** Generate
   candidates (shots at points in the goal, passes to teammates, dribbles),
   filter out the impossible ones, rate each separately (goal chance,
   interception risk, progress) and pick by priority. The kicker works in
   the sim, so shots and passes can start there. Pushing with the dribbler
   on should also cut the ~17 s a lone attacker takes to walk the ball into
   the goal on 9 x 6.
6. **Plays that commit, for game situations [1, 2].** Kickoff, after a goal,
   and the sim's pause / new-game controls, each a play that runs until it
   is done. That also fixes the strategy missing a new game (it waits for a
   kickoff after goals and halves, not after **New game**). In open play,
   how many robots attack follows the score and the time left.
7. **Navigation around robots.** Robots drive straight through each other
   today.
8. **Make it visible.** Publish each robot's behaviour and the active play
   in the targets message, and draw them on the web app's field.
9. **Measure as we go.** Freeze the current strategy as baseline `v1` (the
   pushing attacker changed play since `v0`), add statistics such as pass
   success (TIGERs track it [5]), and tune constants offline in the match
   runner. **`v1` frozen 2026-11-20** (`eval/baseline_v1.py`, the match
   runner's default opponent; identical to the built-in team on 20,000 random
   positions and whole games, both field sizes).
10. **Hardware, in parallel.** Measure the XRP's real drive (the
    `TODO(hardware)` values in `sim/config.py`) so the time model fits the
    real robot; kicker firmware; optionally retrain the RL kick at the real
    table's size.

## 2. Student platform

### Blocks or Python?

**Blockly that generates Python, plus a Python mode.** Not Scratch.

- Scratch is a whole application built around sprites on a stage and
  broadcast messages. That model fits a robot deciding where to go 60
  times a second poorly, Scratch is heavy to embed in our pages, and it
  leaves students no path to real code.
- [Blockly](https://developers.google.com/blockly) is the library Scratch's
  blocks are built on. It embeds in the web app's React pages, takes custom
  blocks, and generates readable Python.
- XRP students may already know this setup: XRPCode, the XRP's own editor,
  offers Blockly or MicroPython, converts blocks to Python and can show that
  Python ([XRPCode guide](https://xrpusersguide.readthedocs.io/es/latest/course/XRPCode.html)).
- The path is natural: blocks first, then "show me the Python", then edit
  the Python.
- Python is the real target: the strategy is Python, so students' code runs
  through the same interface as the built-in behaviours. Replacing a block
  means swapping one function.

### Replaceable pieces (slots)

The full design, from settings and skills to plays, the whole team and
driving, with the drills, limits and build order:
[docs/student_code.md](docs/student_code.md). In short:

Three levels, each slot with a built-in default. A student replaces one slot
and the rest of the team keeps playing with the defaults.

| Level | Slots |
| --- | --- |
| Skills | go to, kick toward, dribble to, intercept |
| Roles | attacker, supporter ("get open for a pass"), defender, goalie |
| Team | who plays which role, kickoff formation |

The same interface for everyone, built-in or student-written:

```python
@behavior("supporter")
def get_open(me, world):
    spot = world.ball.pos + (world.their_goal - world.ball.pos) * 0.4
    if not world.pass_lane_clear(world.ball.pos, spot):
        spot = spot + (0, 0.5)
    return me.go_to(spot, face=world.ball.pos)
```

`world` offers the ball, teammates, opponents, both goals and friendly
helpers over the analysis layer (who reaches the ball first, whether a pass
lane is open); a robot's command is `go_to`, `face`, `kick`, `dribble` or
`stop`.

### Where student code runs

The web app runs on the student's own computer, so the simplest safe setup
is the student's code in a separate Python process next to the strategy:

- a time budget per frame (around 5 ms);
- if the code raises or overruns, that robot falls back to the built-in
  behaviour and the error shows in the editor;
- saving reloads the code without restarting the sim;
- never with `robocup-web --host 0.0.0.0`: anyone on the network could run
  code on the machine.

If the platform is ever hosted for many students online, running Python in
the browser ([Pyodide](https://pyodide.org)) is the safer option; not needed
yet. Once students upload each other's code, a classmate's workspace runs in
safe mode (no files, programs or network; a list of allowed imports):
[Running other people's code safely](docs/student_code.md#running-other-peoples-code-safely).

### A "Code" page in the web app

- The editor, switching between blocks and Python.
- A picker for the slot your code replaces, and the team it plays for.
- The live field beside it and an error console.
- A **Test match** button: the match runner plays your version against the
  built-in team and shows the score ("your supporter vs the default: 7-3").
- Real robots run the same code through **Follow sim**, which only moves a
  robot while the camera sees it.

Lessons follow the slots: drive to the ball; kick toward the goal; get open
for a pass; write a goalie; decide who plays which role.

### Build order

From here on the platform's build order lives in
[docs/student_code.md](docs/student_code.md#build-order), which adds
workspaces (a team is a folder of Python files), sharing them by upload and
download, students playing each other, and safe mode for running a
classmate's code. Done so far:

1. The slot interface (strategy step 1). **Done.**
2. Student Python files: loaded, isolated, reloaded on save; a match-runner
   option such as `--blue mycode.py`. **Done 2026-11-20:**
   `core/loader.py`, `examples/`, `--a my_team.py` in the match runner and
   `--blue` / `--red` on the strategy node and `run_pipeline.py`; a slot that
   raises, returns junk or takes over 10 ms falls back to the built-in one,
   with the error on the file's line. Live, the file runs in its own process
   (`core/worker.py`): a frame it misses (20 ms) is played by the built-in
   team, and an endless loop or crash restarts it after 0.5 s, so the
   strategy never stops. The match runner still loads the file in its own
   process; the web app puts a time limit on the whole match.
3. The Code page with a Python editor. **Done 2026-11-20:** files in
   `teams/` (new from the template, the get-open example or blank; save with
   Ctrl+S; delete), a load check on every save, **Play** in the simulator as
   blue or red with the live field and log, **Test match** against `v1`,
   `v0`, `current` or another file, and every problem (loading, live,
   match) listed with its line and highlighted in the editor. Not yet:
   blocks, and Follow sim for real robots from the page.
4. Seeing what code does (`world.draw`, `world.show`), events, `prepare`,
   dribble and speed limits, and the students' guide in the Code page's
   Guide tab. **Done 2026-11-20.**

5. Workspaces, every team in its own process with safe mode, upload and
   download, and playing each other. **Done 2026-11-20.**

Next, in order: `team()` and plays; skills; drills; tournaments; settings
and Blocks; the driving slot.
