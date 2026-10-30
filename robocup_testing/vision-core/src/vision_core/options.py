"""The choices an interactive tool asks for, as plain data -- no window, no web page.

A tool lists its Options once. Any front end can show them: ui.form() draws
them in an OpenCV window today, and a web page can render the same list from
Option.to_json(). Values are kept in the units the code uses (metres), and each
Option says how to show them to a person (`scale` and `unit`, e.g. mm), so
nobody has to type 0.127 when the ruler says 127.

    tag = Option("tag_size", "Tag size", "Measure the black square with a ruler.",
                 unit="mm", scale=1000, step=1, minimum=10, default=0.08)
    tag.format(0.127)  -> "127"
    tag.parse("127")   -> 0.127
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    #: One or two plain sentences: what it is and how to find the right value.
    help: str = ""
    #: "number" (typed, with -/+ steps) or "choice" (one of `choices`).
    kind: str = "number"
    default: Any = None
    #: (value, label) pairs, for kind="choice".
    choices: tuple[tuple[Any, str], ...] = ()
    unit: str = ""
    #: shown value = stored value * scale (1000 shows metres as mm).
    scale: float = 1.0
    #: How far one -/+ press moves it, in shown units.
    step: float = 1.0
    #: Limits, in shown units.
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False
    #: A number that may be left empty, read as None; `blank` says what empty means.
    optional: bool = False
    blank: str = ""
    #: (other key, values): only asked when that option has one of these values.
    show_if: tuple[str, tuple[Any, ...]] | None = None

    def visible(self, values: dict[str, Any]) -> bool:
        if self.show_if is None:
            return True
        key, allowed = self.show_if
        return values.get(key) in allowed

    def label_of(self, value: Any) -> str:
        return next((lbl for v, lbl in self.choices if v == value), str(value))

    def format(self, value: Any) -> str:
        """A stored value as a person should see it (no unit)."""
        if value is None:
            return ""
        if self.kind == "choice":
            return self.label_of(value)
        shown = float(value) * self.scale
        return str(int(round(shown))) if self.integer else f"{round(shown, 6):g}"

    def parse(self, text: str) -> Any:
        """What a person typed -> the stored value. ValueError says what is wrong,
        in words fit to show them."""
        t = text.strip()
        if not t:
            if self.optional:
                return None
            raise ValueError(f"{self.label}: type a number")
        try:
            shown = float(t)
        except ValueError:
            raise ValueError(f"{self.label}: '{t}' is not a number") from None
        unit = f" {self.unit}" if self.unit else ""
        if self.minimum is not None and shown < self.minimum:
            raise ValueError(f"{self.label}: must be at least {self.minimum:g}{unit}")
        if self.maximum is not None and shown > self.maximum:
            raise ValueError(f"{self.label}: must be at most {self.maximum:g}{unit}")
        if self.integer:
            if shown != int(shown):
                raise ValueError(f"{self.label}: must be a whole number")
            return int(shown)
        return shown / self.scale

    def stepped(self, text: str, direction: int) -> str:
        """The typed text moved one step up (+1) or down (-1), kept in range."""
        try:
            shown = float(text)
        except ValueError:
            shown = (float(self.default) * self.scale) if self.default is not None else 0.0
        shown += direction * self.step
        if self.minimum is not None:
            shown = max(shown, self.minimum)
        if self.maximum is not None:
            shown = min(shown, self.maximum)
        return str(int(round(shown))) if self.integer else f"{round(shown, 6):g}"

    def cycled(self, value: Any, direction: int) -> Any:
        """The next (+1) or previous (-1) choice after `value`."""
        values = [v for v, _ in self.choices]
        if not values:
            return value
        i = values.index(value) if value in values else 0
        return values[(i + direction) % len(values)]

    def to_json(self) -> dict[str, Any]:
        """Everything a web form needs to draw this, JSON-safe."""
        return {
            "key": self.key, "label": self.label, "help": self.help, "kind": self.kind,
            "default": self.default,
            "choices": [{"value": v, "label": lbl} for v, lbl in self.choices],
            "unit": self.unit, "scale": self.scale, "step": self.step,
            "minimum": self.minimum, "maximum": self.maximum, "integer": self.integer,
            "optional": self.optional, "blank": self.blank,
            "show_if": (None if self.show_if is None
                        else {"key": self.show_if[0], "in": list(self.show_if[1])}),
        }


def initial_values(options: list[Option], given: dict[str, Any]) -> dict[str, Any]:
    """One value per option: what was given, else its default. A choice given a
    value it does not offer falls back to the default rather than showing junk."""
    out: dict[str, Any] = {}
    for o in options:
        v = given.get(o.key, o.default)
        if o.kind == "choice" and v not in [c for c, _ in o.choices]:
            v = o.default
        out[o.key] = v
    return out
