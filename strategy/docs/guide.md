# Coding your team

You write Python that controls a team of three robots. You don't have to
write the whole team: you replace **one piece at a time**, and every piece
you leave alone keeps playing the built-in way. So your team always plays a
full game, from your first line of code on.

## How your code runs

60 times a second, for your team:

1. **`prepare(world)`** runs once, if you wrote one (for work the whole team shares).
2. **`roles(team, world)`** decides which robot plays which role: attacker, supporter or defender.
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
| `attacker(me, world)` | The robot going for the ball | Gets behind the ball and pushes it toward their goal | Kick instead of pushing; aim at the open side of the goal |
| `roles(team, world)` | Who plays which role | Closest to the ball attacks, next supports, the last defends | Two defenders when we're ahead |
| `prepare(world)` | Nothing by itself: a place for shared work | Does nothing | Count goals, pick a plan for the whole team |

The easiest start: **New… → Template**. It has all of these, each calling the built-in
one, so it plays exactly like the built-in team. Change one line, save, and watch.

## Your first change

Make the supporter wait closer to the ball:

```python
def supporter(me, world):
    spot = world.ball.pos + (world.their_goal - world.ball.pos) * 0.2
    return me.go_to(spot, face=world.ball.pos)
```

Save it (Ctrl+S), press **Play**, and turn on **Field**. Then change `0.2` to `0.6`, save
again, and watch the supporter move. Your code reloads every time you save.

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

`roles` has to give every robot in `team` exactly one role, each `"attacker"`,
`"supporter"` or `"defender"`. `team` can have fewer than three robots (a robot the
camera lost, or one someone is driving by hand).

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
kickoffs), and the rule that only one robot's kick fires at a time. Coming later: smaller
pieces to replace, like "kick toward a point" and "intercept a moving ball", your own role
names, and plans for the whole team.

## Read the built-in code

The built-in functions are written the same way yours are. Read them, copy them, change
them: `strategy/tactics/attacker.py`, `supporter.py`, `defender.py` and
`role_assignments.py`. Everything `me` and `world` offer is in `strategy/api.py`.
