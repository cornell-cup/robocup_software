"""What a tactic tells one robot to do this frame."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Command:
    x: float  # where to go, meters
    y: float
    angle: float  # heading to face, radians counter-clockwise from +x
    # Fire the kicker this frame. Safe to send every frame: the sim only kicks
    # when the ball is in the kick zone (skills/kick.py).
    kick: bool = False

    @property
    def target(self) -> tuple[float, float, float]:
        """(x, y, heading), as strategy_node publishes it."""
        return (self.x, self.y, self.angle)
