"""What a team's code draws on the field, and the values it shows, each frame.

    world.draw.line(world.ball.pos, spot, color="green")
    world.show("margin", world.us.race_margin)

The shapes and values travel with the plan (strategy_node -> the web app) and
are drawn on the field next to the robots, so you see what the code was
thinking: where it wanted to go, which pass lane it checked. Both start empty
every frame. Positions are field metres, like everything else.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

# Colour names a shape can use, or any "#rrggbb".
COLORS = {
    "red": "#ff5a5f", "blue": "#3d8bff", "green": "#3fb950", "yellow": "#e3b341",
    "orange": "#f0883e", "purple": "#bc8cff", "pink": "#f778ba", "cyan": "#39c5cf",
    "white": "#f4f4f4", "gray": "#8b949e", "grey": "#8b949e", "black": "#000000",
}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
MAX_SHAPES = 100  # per team and frame: more are dropped (a loop drawing too much)
MAX_SHOWN = 30  # values per team and frame
MAX_TEXT = 40  # characters of a label or a shown value


def _point(p: Any, what: str) -> list[float]:
    try:
        a = np.asarray(p, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        raise TypeError(f"{what} must be a point (x, y), got {p!r}") from None
    if a.size < 2 or not (math.isfinite(a[0]) and math.isfinite(a[1])):
        raise TypeError(f"{what} must be a point (x, y), got {p!r}")
    return [round(float(a[0]), 3), round(float(a[1]), 3)]


def _number(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise TypeError(f"{what} must be a number, got {v!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} must be a finite number, got {v!r}")
    return f


def _text(v: Any) -> str:
    """A value as a short label: numbers rounded, points as (x, y)."""
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.3g}" if abs(float(v)) < 1e4 else f"{float(v):.0f}"
    if isinstance(v, (tuple, list, np.ndarray)) and 1 <= len(v) <= 3:
        try:
            return "(" + ", ".join(f"{float(x):.2f}" for x in v) + ")"
        except (TypeError, ValueError):
            pass
    s = v if isinstance(v, str) else repr(v)
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


class Drawing:
    """`world.draw`: shapes on the field for this frame."""

    def __init__(self, default_color: str):
        self.shapes: list[dict[str, Any]] = []
        self._default = COLORS.get(default_color, COLORS["white"])
        self.robot: int | None = None  # whose slot is running (set by decide())

    def _color(self, color: str | None) -> str:
        if color is None:
            return self._default
        if isinstance(color, str):
            if color.lower() in COLORS:
                return COLORS[color.lower()]
            if _HEX.fullmatch(color):
                return color.lower()
        raise ValueError(f"color must be one of {', '.join(sorted(set(COLORS) - {'grey'}))} "
                         + f"or \"#rrggbb\", got {color!r}")

    def _add(self, shape: dict[str, Any], color: str | None) -> None:
        shape["color"] = self._color(color)  # checked first: a bad colour is an error, even past the cap
        if len(self.shapes) >= MAX_SHAPES:
            return
        if self.robot is not None:
            shape["robot"] = self.robot
        self.shapes.append(shape)

    def point(self, p: Any, color: str | None = None) -> None:
        """A dot at p."""
        self._add({"kind": "point", "at": _point(p, "point")}, color)

    def line(self, a: Any, b: Any, color: str | None = None) -> None:
        """A line from a to b."""
        self._add({"kind": "line", "from": _point(a, "line start"), "to": _point(b, "line end")}, color)

    def arrow(self, a: Any, b: Any, color: str | None = None) -> None:
        """An arrow from a to b: a direction, a kick, a pass."""
        self._add({"kind": "arrow", "from": _point(a, "arrow start"), "to": _point(b, "arrow end")}, color)

    def circle(self, center: Any, radius: float, color: str | None = None) -> None:
        """A circle around center, radius in metres."""
        r = _number(radius, "radius")
        if r <= 0:
            raise ValueError(f"radius must be positive, got {radius!r}")
        self._add({"kind": "circle", "at": _point(center, "circle centre"), "radius": round(r, 3)}, color)

    def text(self, p: Any, text: Any, color: str | None = None) -> None:
        """A short label at p."""
        self._add({"kind": "text", "at": _point(p, "text position"), "text": _text(text)}, color)


class Shown:
    """`world.show(name, value)`: values in a table beside the field."""

    def __init__(self) -> None:
        # "robot 2" / "team" -> name -> the value as text
        self.values: dict[str, dict[str, str]] = {}
        self._count = 0
        self.robot: int | None = None  # whose slot is running (set by decide())

    def __call__(self, name: Any, value: Any) -> None:
        who = "team" if self.robot is None else f"robot {self.robot}"
        row = self.values.setdefault(who, {})
        key = _text(name)
        if key not in row:
            if self._count >= MAX_SHOWN:
                return
            self._count += 1
        row[key] = _text(value)
