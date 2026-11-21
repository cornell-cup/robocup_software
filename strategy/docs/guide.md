# Coding your team

You write Python that controls a team of three robots. You don't have to
write the whole team: you replace **one piece at a time**, and every piece
you leave alone keeps playing the built-in way. So your team always plays a
full game, from your first line of code on.

## How your code runs

60 times a second, for your team:

1. **`prepare(world)`** runs once, if you wrote one (for work the whole team shares).
2. **`roles(team, world)`** decides which robot plays which role: attacker, supporter or
   defender (or a play, or your own roles: see [Plans for the whole team](#plans-for-the-whole-team)).
3. For **each robot**, the function named after its role runs: `attacker(me, world)`,
   `supporter(me, world)` or `defender(me, world)`. It returns a **command**: where that
   robot should go this frame.

`me` is the robot the function is deciding for. `world` is everything your team can
see: the ball, your teammates, the opponents, the goals.

## What you can change

Write a function with one of these names in your file and it replaces the built-in one.
Delete it and the built-in one is back.

| Function | It decides | The built-in one | Good first idea |
| --- | --- | --- | --- |
| `supporter(me, world)` | Where the second robot waits | 1.5 m ahead of the ball and 1 m to the side | Stand where a pass can reach you (start from **Example**) |
| `defender(me, world)` | The goalie | On the line from our goal to the ball, stepping out as the ball comes close | Stay closer to the goal; rush out when the ball is near |
| `attacker(me, world)` | The robot going for the ball | Gets behind the ball and pushes it toward their goal; a rolling ball it meets where it can first reach it | Kick instead of pushing; aim at the open side of the goal |
| `roles(team, world)` | Who plays which role | Closest to the ball attacks, next supports, the last defends | Two defenders when we're ahead |
| `prepare(world)` | Nothing by itself: a place for shared work | Does nothing | Count goals, pick a plan for the whole team |
| Your own roles, `PLAYS` and `pick_play`, `team()`, `kickoff()` | Plans for the whole team | None | See [Plans for the whole team](#plans-for-the-whole-team) |
| `go_to_ball`, `kick_toward`, `intercept`, `pass_to`, `dribble_to` | One action with the ball | Used by the built-in attacker | A kick instead of a push: see [Skills](#skills-one-action-at-a-time) |

The easiest start: **New… → Template**. It has the roles and the skills, each calling
the built-in one, so it plays exactly like the built-in team, and the rest (goalie,
`prepare`, plays, `team()`, `kickoff()`) written out with an example each, ready to
switch on by removing the `#`. Change one line, save, and watch.

## Your first change

Make the supporter wait closer to the ball:

```python
def supporter(me, world):
    spot = world.ball.pos + (world.their_goal - world.ball.pos) * 0.2
    return me.go_to(spot, face=world.ball.pos)
```

Save it (Ctrl+S), press **Play**, and turn on **Field**. Then change `0.2` to `0.6`, save
again, and watch the supporter move. Your code reloads every time you save.

The editor helps while you type. After `me.` or `world.` it lists what's there, with what
each one does; after `def ` at the start of a line it lists the functions you can write,
with the right parameters. Mistakes get underlined before you even save: red for errors
(a typo like `wrold`, a missing `:`), yellow for warnings (a name that doesn't exist, like
`me.kick_towards`, or a function that never returns a command). Hover to read why.

## Blocks instead of typing

You can build your roles by snapping blocks together (Google Blockly) instead of typing.
**New… → Blocks** starts a workspace that way, or press **Convert to blocks** above any
`team.py`. Each role is a **when I'm the supporter** block; put moves (**go to**, **kick
toward**, **intercept**…) inside it, with places (**the ball**, **their goal**, **between
… and …**) plugged into them. **Show Python** puts the Python your blocks make beside them,
updating as you snap, and that Python is what plays: save, **Play** and **Test match**
work just the same.

**who plays which role** (in Roles) is `roles()`: the built-in way, or everyone in one role.
**play like the built-in attacker** (in Move) does what the built-in team does for that
role: the template's roles start that way, so swap it for your own blocks when you're ready.
Hover over a block to read what it does; a role's description (its docstring) shows there too.

Whatever blocks can't show yet stays as grey Python blocks, kept exactly as you wrote it.
**other Python in the file** holds what isn't a role: the file's description, imports,
helper functions, parts switched off with `#`. Each starts folded to its first line; click
to open it. Inside a role, **Python step**, **Python move** and **Python value** hold a
line the blocks can't say (a loop, an unusual command). Comments inside a role are the one
thing that doesn't come along (the page asks first). **Back to Python** goes the other way:
you keep the Python your blocks made and type from there, and **Convert to blocks** again
any time.

## Commands: what a robot can do

Every role function returns one command:

| Command | What the robot does |
| --- | --- |
| `me.go_to(point)` | Drive to `point`, facing where it's going |
| `me.go_to(point, face=other_point)` | Drive there while looking at `other_point` (the ball, say) |
| `me.go_to(point, face=0.0)` | ...or facing an angle, in radians (0 = toward +x) |
| `me.go_to(point, kick=True)` | Fire the kicker. It only kicks if the ball is right in front of the robot |
| `me.go_to(point, dribble=True)` | Dribbler on: the robot holds the ball while it drives. It can't kick while dribbling |
| `me.go_to(point, max_speed=0.5)` | Drive no faster than 0.5 m/s (full speed is about 1.2 m/s) |
| `me.face(point)` | Stay here and turn toward `point` |
| `me.stop()` | Stay here |
| `me.kick_toward(point)`, `me.pass_to(mate)`, ... | A **skill**: a whole action with the ball (see [Skills](#skills-one-action-at-a-time)) |

You can combine them: `me.go_to(spot, face=world.ball.pos, max_speed=0.6)`.

## What your code can see

All positions are in metres, as `(x, y)`. Angles are in radians.

**`me`, the robot you're deciding for**

| | |
| --- | --- |
| `me.pos` | Where it is |
| `me.vel` | How fast it's moving, as `(vx, vy)` in m/s |
| `me.angle` | Which way it faces |
| `me.has_ball` | `True` if the ball is right in front of it (it could kick or dribble now) |
| `me.id` | Its number |
| `me.distance_to(p)` | Metres from it to `p` |
| `me.angle_to(p)` | The angle from it toward `p` |

**`world`, what your team sees**

| | |
| --- | --- |
| `world.ball.pos`, `world.ball.vel`, `world.ball.speed` | The ball |
| `world.teammates` | Your robots (including `me`), each like `me` |
| `world.opponents` | Their robots, each like `me` |
| `world.our_goal`, `world.their_goal` | The middle of each goal |
| `world.field_size` | `(length, width)` of the field |
| `world.attack_angle` | The direction toward their goal |
| `world.lane_clear(a, b)` | `True` if no opponent is in the way on the straight line from `a` to `b` |
| `world.phase` | `"kickoff"` (waiting for the first touch), `"play"`, or `"halt"` (game over) |
| `world.kicked_off` | `False` from a restart until the ball first moves |
| `world.time` | Seconds since the game started |
| `world.score` | `(ours, theirs)` |
| `world.time_left` | Seconds left in this half |
| `world.ball.at(t)` | Where the ball will be in `t` seconds, slowing down as it rolls |
| `world.ball.stops_at` | Where the ball comes to rest if nothing touches it |
| `me.time_to(point)` | Seconds this robot needs to get to `point` |
| `world.memory` | A dictionary that keeps what you put in it from frame to frame |
| `world.events` | What just happened (see below) |
| `world.us.race_margin` | Seconds we reach the ball before them (below 0: they get there first) |
| `world.us.first_to_ball` | Our quickest robot to the ball: `.robot_id`, `.t` (seconds), `.point` (where); `None` if none can |
| `world.us.first_threat` | The most dangerous spot for our goal right now |

### Points and maths

Positions like `me.pos` and `world.ball.pos` are numpy arrays, so maths works on them:

```python
def supporter(me, world):
    halfway = (world.ball.pos + world.their_goal) / 2       # the middle between two points
    beside = world.ball.pos + (0.0, 1.0)                     # 1 m above the ball
    return me.go_to(halfway, face=beside)
```

A point you type yourself, like `(6.0, 4.0)`, is a plain tuple: you can pass it to
`go_to` or `draw`, but `(6.0, 4.0) + (1.0, 0.0)` doesn't add them up (Python joins
tuples). Write `np.array((6.0, 4.0))` after `import numpy as np` to do maths on it.

## See what your code is thinking

When a robot doesn't do what you meant, you need to see what your code computed. You
can't see a variable like `spot` on the field, but your code can **draw** it: you
don't draw with the mouse, you add a line of code like `world.draw.point(spot)`, and
the field shows a dot at `spot`, moving with it, as long as the game runs.

Try it, step by step:

1. **New… → Example** (the get-open supporter). It already draws.
2. Press **Play**, and make sure **Field** is on (the button next to Save). The field
   appears beside your code.
3. Watch the second robot. A **line** goes from the ball to where it's heading: green
   while no opponent blocks that pass, red when one does. A **dot** marks the spot.
   Those come from the `world.draw.line(...)` and `world.draw.point(...)` lines in the
   file.
4. Add `world.draw.circle(world.ball.pos, 0.5, color="yellow")` on the line before
   `return`, and save. A yellow ring now follows the ball.

Drawings last one frame: your code draws them again every frame, so they move with
whatever they show. Shapes show on the field (with **Field** on) and on the Simulator
page:

| | |
| --- | --- |
| `world.draw.point(p)` | A dot |
| `world.draw.line(a, b)` | A line |
| `world.draw.arrow(a, b)` | An arrow from `a` to `b` |
| `world.draw.circle(p, radius)` | A circle |
| `world.draw.text(p, "hello")` | A label |

Each one takes `color="green"`: red, blue, green, yellow, orange, purple, pink, cyan,
white, gray, black, or `"#rrggbb"`. Your team's colour if you leave it out.

```python
def supporter(me, world):
    spot = world.ball.pos + (world.their_goal - world.ball.pos) * 0.4
    clear = world.lane_clear(world.ball.pos, spot)
    world.draw.line(world.ball.pos, spot, color="green" if clear else "red")
    world.draw.point(spot)
    world.show("lane clear", clear)
    return me.go_to(spot, face=world.ball.pos)
```

`world.show("name", value)` puts a value in the **Values** card, next to the robot it's
for. `print(...)` writes to the **Log** card. Use `show` for numbers that change every
frame, and `print` for things that happen once; a `print` that runs every frame floods
the log.

## Remembering and reacting

`world.memory` is yours: a dictionary that keeps its contents between frames. Your whole
team shares it.

`world.events` lists what happened since the last frame. Usually it's empty:

| Event | When |
| --- | --- |
| `"new_game"` | The very first frame, and when the simulator restarts |
| `"goal_for"` | We scored |
| `"goal_against"` | They scored |
| `"half_time"` | A new half started |
| `"kickoff"` | The ball moved for the first time after a restart |

`prepare(world)` runs once a frame before any robot's function: the place for things the
whole team uses.

```python
def prepare(world):
    if "goal_for" in world.events:
        world.memory["ahead"] = world.memory.get("ahead", 0) + 1
    if "goal_against" in world.events:
        world.memory["ahead"] = world.memory.get("ahead", 0) - 1


def roles(team, world):
    # The closest robot to the ball attacks; when we're ahead, everyone else defends.
    by_distance = sorted(team, key=lambda robot: robot.distance_to(world.ball.pos))
    if world.memory.get("ahead", 0) > 0:
        return [(by_distance[0], "attacker")] + [(r, "defender") for r in by_distance[1:]]
    names = ["attacker", "supporter"] + ["defender"] * len(team)
    return [(robot, names[i]) for i, robot in enumerate(by_distance)]
```

`roles` has to give every robot in `team` exactly one role: `"attacker"`, `"supporter"`,
`"defender"`, `"goalie"`, or one of your own (next section). `team` can have fewer than
three robots (a robot the camera lost, or one someone is driving by hand).

## Plans for the whole team

Once single robots do what you want, plan for the team. There are three ways, from
simple to full control; use whichever fits.

### Your own roles

Any function in `team.py` can be a role. Write it like `supporter`, and give it to a
robot in `roles`:

```python
def runner(me, world):
    # Run ahead down the side, ready for a long pass.
    length, width = world.field_size
    ahead = world.ball.pos[0] + (1.5 if world.attack_angle == 0 else -1.5)
    return me.go_to((min(max(ahead, 0.5), length - 0.5), width * 0.85), face=world.ball.pos)


def roles(team, world):
    by_distance = sorted(team, key=lambda robot: robot.distance_to(world.ball.pos))
    names = ["attacker", "runner", "goalie"]
    return [(robot, names[min(i, 2)]) for i, robot in enumerate(by_distance)]
```

`goalie` is a built-in role too: it plays like the built-in defender. A role with no
function (a typo, say) is an error, and the built-in roles play that frame.

### Plays

A **play** is a plan for a situation: which roles the team needs, most important first.
List your plays in `PLAYS`, and pick one in `pick_play`:

```python
PLAYS = {
    "attack": ["attacker", "supporter", "goalie"],
    "defend": ["attacker", "goalie", "goalie"],
}


def pick_play(world):
    if world.us.race_margin < -0.3:  # they reach the ball well before us
        return "defend"
    return "attack"
```

The closest robot to the ball gets the play's first role, the next one the second, and so
on. `world.play` says which play runs, and a new pick takes over only after `pick_play`
has kept choosing it for a quarter of a second, so the team doesn't flicker between two.

### The whole team at once

`team(robots, world)` gives commands to any robots you like, all at once: the way to
time a pass with a run, or to wall off the goal with two robots side by side. Return a
dictionary `{robot: command}`; robots you leave out play their roles as usual.

```python
def team(robots, world):
    if world.ball.pos[0] > world.field_size[0] / 2:
        return {}  # ball in their half: everyone plays their role
    goal = world.our_goal
    # Ball in our half: the two robots nearest our goal make a wall in front of it.
    wall = sorted(robots, key=lambda robot: robot.distance_to(goal))[:2]
    inward = 0.5 if goal[0] < world.their_goal[0] else -0.5
    return {
        robot: robot.go_to((goal[0] + inward, goal[1] + side), face=world.ball.pos)
        for robot, side in zip(wall, (-0.25, 0.25))
    }
```

And `kickoff(robots, world)` says where robots stand before a kickoff: return
`{robot: (x, y)}`, each spot on our half of the field. Leave out the robot that should
take the kickoff: it plays its role (the attacker goes for the ball).

When you write several of these, `team()` decides first, then `kickoff()`'s spots, then
the play's roles (or `roles()`), then each robot's role function.

## Skills: one action at a time

A **skill** is one action with the ball, smaller than a whole role. Your robots use them
like commands, and the built-in attacker is made of them:

| Skill | What the robot does | The built-in attacker uses it |
| --- | --- | --- |
| `me.go_to_ball()` | Drive to the ball, facing it | |
| `me.kick_toward(point)` | Get behind the ball on the line to `point`, and send it there | To push the ball at their goal |
| `me.intercept()` | Meet a rolling ball where this robot can first reach it | When the ball rolls faster than 0.3 m/s |
| `me.pass_to(mate)` | Send the ball to a teammate, where it's going to be (kicks once lined up) | |
| `me.dribble_to(point)` | Take the ball with the dribbler on and carry it to `point` | |

Use them in any role:

```python
def attacker(me, world):
    if world.ball.speed > 0.3:
        return me.intercept()
    if me.distance_to(world.their_goal) < 3:
        return me.kick_toward(world.their_goal)
    return me.dribble_to(world.their_goal)
```

And replace any of them: write a function with the skill's name, and every robot that uses
the skill uses yours, **the built-in attacker too**. The built-in `kick_toward` pushes the
ball with the robot's body; this one kicks:

```python
import numpy as np


def kick_toward(me, world, target):
    ball = world.ball.pos
    line = np.asarray(target) - ball
    line = line / max(float(np.linalg.norm(line)), 0.01)
    behind = ball - line * 0.25  # 25 cm behind the ball, on the line to the target
    if me.distance_to(behind) > 0.08:
        return me.go_to(behind, face=target)
    return me.go_to(ball, face=target, kick=True)
```

A skill you write takes `me` and `world` first, then what the call gives it:
`kick_toward(me, world, target)`, `pass_to(me, world, mate)`, `dribble_to(me, world, target)`.
If it fails, the built-in one plays instead, and the problem shows on its line.

## More examples

A goalie that stays on its goal line, level with the ball:

```python
def defender(me, world):
    goal = world.our_goal
    y = min(max(world.ball.pos[1], goal[1] - 0.4), goal[1] + 0.4)  # within 0.4 m of the goal's middle
    inward = 0.3 if goal[0] < world.their_goal[0] else -0.3          # 0.3 m out, toward the field
    return me.go_to((goal[0] + inward, y), face=world.ball.pos)
```

An attacker that drives at the ball and kicks when it has it:

```python
def attacker(me, world):
    if me.has_ball:
        return me.go_to(world.their_goal, face=world.their_goal, kick=True)
    return me.go_to(world.ball.pos, face=world.ball.pos)
```

(Try it in a test match: it often kicks the ball the wrong way. Why? Draw where it's
facing when it kicks.)

## Your workspace: more than one file

Your team is a **workspace**: a folder of Python files, one tab each above the editor.
`team.py` is where the team starts (the functions above go there). When it gets long,
move helpers into files of their own with **+**, and import them in `team.py`:

```py
# passing.py
def open_spot(world):
    return world.ball.pos + (world.their_goal - world.ball.pos) * 0.4
```

```py
# team.py
from passing import open_spot

def supporter(me, world):
    return me.go_to(open_spot(world), face=world.ball.pos)
```

Saving any file reloads the whole team, and an error says which file and line it's on
(`passing.py:4`): click it to go there. A file's name is its module's name, so use letters, digits and `_`, and not
the name of one of Python's own modules (`random.py` would hide Python's `random`).

Keep as many workspaces as you like: **Workspace ▾ → Duplicate** keeps a version before a
big change, so you can play the old one against the new one.

## Playing a classmate

1. Your classmate: **Workspace ▾ → Download .zip**, and sends you the file.
2. You: **Upload…** (or drag the `.zip` onto the page). It becomes a workspace of its own.
3. **Play in the simulator**: your workspace as Blue, theirs as Red. Or **Test match**:
   your workspace against theirs, several games, sides swapped every game. **Head to
   head** keeps the score of every match.

Code from someone else runs on your computer, so it plays in **safe mode**: it can't
read or write your files, start programs or use the network, and it can import only
`math`, `random`, `numpy` and a few other safe modules, its own files, and the strategy's
`api`, `tactics`, `skills` and `world`. Your own workspace has no such limits, but the
same rule holds for everyone: you see the other team only on the field.

## Rules

- Each function gets **10 ms** a frame. Slower than that, or if it raises an error, or
  returns something that isn't a command, that robot plays the built-in function for that
  frame, and the problem shows under the editor with its line number. Your team never
  stops because of a bug.
- Your file runs in its own program. If it gets stuck (an endless `while` loop), your
  robots play the built-in team until you fix it and save.
- You control your robots through commands only. You can't move robots or the ball
  directly, change the rules, or see the other team's code.

## What you can't change (yet)

How a robot drives to its target (speed, turning), the physics, the referee (goals,
kickoffs), and the rule that only one robot's kick fires at a time.

## Read the built-in code

The built-in functions are written the same way yours are. Read them, copy them, change
them: `strategy/tactics/attacker.py`, `supporter.py`, `defender.py` and
`role_assignments.py`. Everything `me` and `world` offer is in `strategy/api.py`.
