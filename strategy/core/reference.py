"""What team code can use, as data: for the Code page's hints and lint.

    python -m strategy.core.reference      # prints it as JSON (the web app runs this once)

Read from the code itself (strategy/api.py, core/drawing.py, the slots), so
the hints can't drift from what's really there: every public method and
property of a robot (`me.`), the world (`world.`), the ball (`world.ball.`),
the analysis (`world.us.`) and the drawing (`world.draw.`), with its
signature and the first paragraph of its docstring; plus the attributes set
in __init__ (described here, since they have no docstring), and every slot
with its parameters.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from strategy.api import Me, World
from strategy.core.drawing import Drawing
from strategy.core.slots import SLOT_PARAMS
from strategy.world.analysis import TeamView
from strategy.world.state import BallState

# Attributes set in __init__ (or dataclass fields): no docstrings to read.
_ATTRIBUTES: dict[str, dict[str, str]] = {
    "world": {
        "teammates": "Your robots (including me), each like me",
        "opponents": "Their robots, each like me",
        "memory": "A dict that keeps what you put in it from frame to frame",
        "events": 'What just happened: "new_game", "goal_for", "goal_against", "half_time", "kickoff"',
        "play": "The play running now (one of your PLAYS), or None",
        "kicked_off": "False from a restart until the ball first moves",
        "color": '"blue" or "red": the team this code plays',
        "draw": "Shapes on the field: world.draw.line(a, b), .point(p), ...",
        "show": 'world.show("name", value): a value in the Values card',
    },
    "ball": {
        "pos": "Where the ball is, (x, y) in metres",
        "vel": "How fast it moves, (vx, vy) in m/s",
        "speed": "Its speed, m/s",
    },
    "us": {
        "race_margin": "Seconds we reach the ball before them (below 0: they get there first)",
        "first_to_ball": "Our quickest robot to the ball: .robot_id, .t (seconds), .point; None if none can",
        "their_first_to_ball": "Their quickest robot to the ball (like first_to_ball)",
        "first_threat": "The most dangerous spot for our goal right now",
        "first_threat_robot": "The opponent who makes it, or None (then it's the ball)",
        "second_threats": "The other opponents, nearest our goal first: [(id, position), ...]",
    },
}
_HIDDEN = {"state", "gamestate", "rl_skill", "rl_enabled", "sticky", "analysis"}


def _doc(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    return doc.split("\n\n")[0].replace("\n", " ").strip()


def _members(cls: type, owner: str) -> list[dict[str, str]]:
    out = []
    for name, value in vars(cls).items():
        if name.startswith("_") or name in _HIDDEN:
            continue
        if isinstance(value, (property,)) or type(value).__name__ == "cached_property":
            fn = value.fget if isinstance(value, property) else value.func
            out.append({"name": name, "kind": "property", "detail": "", "doc": _doc(fn)})
        elif inspect.isfunction(value):
            params = [p for p in inspect.signature(value).parameters.values() if p.name != "self"]
            sig = ", ".join(p.name if p.default is inspect.Parameter.empty else f"{p.name}=..."
                            for p in params)
            out.append({"name": name, "kind": "method", "detail": f"({sig})", "doc": _doc(value)})
    for name, doc in _ATTRIBUTES.get(owner, {}).items():
        if not any(m["name"] == name for m in out):
            out.append({"name": name, "kind": "property", "detail": "", "doc": doc})
    return sorted(out, key=lambda m: m["name"])


def reference() -> dict[str, Any]:
    """Everything the hints and the lint need, as plain data."""
    return {
        "me": _members(Me, "me"),
        "world": _members(World, "world"),
        "ball": _members(BallState, "ball"),
        "us": _members(TeamView, "us"),
        "draw": _members(Drawing, "draw"),
        "slots": [{"name": name, "params": list(params), "returns": returns}
                  for name, (params, returns) in SLOT_PARAMS.items()],
    }


if __name__ == "__main__":
    print(json.dumps(reference()))
