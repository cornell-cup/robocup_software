"""Read a gamepad (Xbox, PlayStation, Switch Pro, ...) with pygame.

Uses SDL's game-controller layer, so every known pad reports the same sticks
and buttons regardless of brand or OS. Unknown pads fall back to raw joystick
numbers (axes 0-3, button 0).

With several robots, each gets its own gamepad (Gamepads).

    Left stick     translate (up = forward)
    Right stick X  rotate (right = clockwise)
    A button       dribbler while held (Cross on PlayStation)
"""

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Button A is the one printed "A", also on Switch pads where it sits on the right.
os.environ.setdefault("SDL_GAMECONTROLLER_USE_BUTTON_LABELS", "1")
import pygame  # noqa: E402
from pygame._sdl2 import controller  # noqa: E402

DEADZONE = 0.10        # stick values below this count as 0 (stick drift)
DRIBBLER_BUTTON = 0    # packet button index, see firmware/robot.py

CONTROLLER_AXES = (pygame.CONTROLLER_AXIS_LEFTX, pygame.CONTROLLER_AXIS_LEFTY,
                   pygame.CONTROLLER_AXIS_RIGHTX, pygame.CONTROLLER_AXIS_RIGHTY)


def _deadzone(value):
    return 0.0 if abs(value) < DEADZONE else value


def shape(axes):
    """Deadzone on every axis; cube the left stick for finer slow driving."""
    strafe, forward, rotate, spare = (_deadzone(value) for value in axes)
    return (strafe ** 3, forward ** 3, rotate, spare)


class Gamepad:
    quit_requested = False   # same interface as Keyboard

    def __init__(self, index=0):
        pygame.init()
        controller.init()
        count = pygame.joystick.get_count()
        if index >= count:
            raise SystemExit(
                "Gamepad {} not found ({} connected). Plug it in, press its "
                "Home/Guide button, and try again.".format(index, count))
        if controller.is_controller(index):
            self._pad = controller.Controller(index)
            self.name = self._pad.name
        else:
            self._pad = pygame.joystick.Joystick(index)
            self.name = self._pad.get_name() + " (unknown model, raw mapping)"

    def read(self):
        """Return (axes, buttons): four floats in -1..1 and the held button indices."""
        pygame.event.pump()
        if isinstance(self._pad, controller.Controller):
            axes = tuple(self._pad.get_axis(a) / 32768 for a in CONTROLLER_AXES)
            dribbler = self._pad.get_button(pygame.CONTROLLER_BUTTON_A)
        else:
            count = self._pad.get_numaxes()
            axes = tuple(self._pad.get_axis(a) if a < count else 0.0 for a in range(4))
            dribbler = self._pad.get_numbuttons() > 0 and self._pad.get_button(0)
        buttons = frozenset([DRIBBLER_BUTTON]) if dribbler else frozenset()
        return shape(axes), buttons

    def show(self, links):
        """Nothing to draw; a gamepad has no window."""


class Gamepads:
    """One gamepad per robot: robot i gets gamepad first + i."""

    quit_requested = False

    def __init__(self, count, first=0):
        self._pads = [Gamepad(first + i) for i in range(count)]
        self.name = ", ".join(pad.name for pad in self._pads)

    def read(self):
        """[(axes, buttons)], one per robot."""
        return [pad.read() for pad in self._pads]

    def show(self, links):
        """Nothing to draw; a gamepad has no window."""
