"""Drive the robot from the laptop keyboard, for when no gamepad is available.

Opens a small pygame window; keys only count while that window has focus, and
the robot is stopped whenever it loses focus. With several robots the keys
drive one at a time; Tab picks the next, and the others stay stopped.

    W / S         forward / backward       (arrow keys also work)
    A / D         strafe left / right
    Q / E         rotate counter-clockwise / clockwise
    Space         dribbler while held
    1 / 2 / 3     speed: slow / medium / fast
    Tab           next robot
    Esc           quit
"""

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

SPEEDS = {pygame.K_1: 0.4, pygame.K_2: 0.7, pygame.K_3: 1.0}
DEFAULT_SPEED = 0.7
DRIBBLER_BUTTON = 0    # packet button index, see firmware/robot.py
WINDOW_WIDTH = 460
LINE_HEIGHT = 34
STOPPED = ((0.0, 0.0, 0.0, 0.0), frozenset())


def _axis(keys, positive, negative):
    return float(any(keys[k] for k in positive)) - float(any(keys[k] for k in negative))


class Keyboard:
    def __init__(self, title, count=1):
        self.name = "keyboard"
        pygame.init()
        # One status line per robot, plus the speed, dribbler and help lines.
        self._screen = pygame.display.set_mode((WINDOW_WIDTH, 20 + LINE_HEIGHT * (count + 5)))
        pygame.display.set_caption(title)
        self._font = pygame.font.Font(None, 30)
        self.count = count
        self.selected = 0
        self.speed = DEFAULT_SPEED
        self.quit_requested = False

    def read(self):
        """[(axes, buttons)], one per robot: the keys for the selected robot,
        stopped for the rest."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit_requested = True
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.quit_requested = True
                elif event.key == pygame.K_TAB:
                    self.selected = (self.selected + 1) % self.count
                elif event.key in SPEEDS:
                    self.speed = SPEEDS[event.key]
        inputs = [STOPPED] * self.count
        if pygame.key.get_focused():
            inputs[self.selected] = self._keys()
        return inputs

    def _keys(self):
        keys = pygame.key.get_pressed()
        strafe = _axis(keys, (pygame.K_d, pygame.K_RIGHT), (pygame.K_a, pygame.K_LEFT))
        forward = _axis(keys, (pygame.K_w, pygame.K_UP), (pygame.K_s, pygame.K_DOWN))
        rotate = _axis(keys, (pygame.K_e,), (pygame.K_q,))
        # Gamepad convention: forward is negative on the Y axis.
        axes = (strafe * self.speed, -forward * self.speed, rotate * self.speed, 0.0)
        buttons = frozenset([DRIBBLER_BUTTON]) if keys[pygame.K_SPACE] else frozenset()
        return axes, buttons

    def show(self, links):
        """Redraw the window: each robot's link status, speed and dribbler state."""
        self._screen.fill((24, 26, 31))
        focused = pygame.key.get_focused()
        dribbling = focused and pygame.key.get_pressed()[pygame.K_SPACE]
        lines = []
        for i, link in enumerate(links):
            marker = "> " if i == self.selected and self.count > 1 else ""
            lines.append(("{}{}: {}".format(marker, link.name,
                                            "connected" if link.connected else "connecting..."),
                          (110, 220, 130) if link.connected else (240, 190, 90)))
        lines += [
            ("Speed {:.0%}   (keys 1 / 2 / 3)".format(self.speed), (220, 220, 220)),
            ("Dribbler: {}".format("ON" if dribbling else "off"), (220, 220, 220)),
            ("" if focused else "Click this window: keys only work with focus",
             (240, 120, 120)),
            ("W/S fwd/back   A/D strafe   Q/E rotate", (140, 140, 150)),
            ("Space dribbler   {}Esc quit".format("Tab next robot   " if self.count > 1 else ""),
             (140, 140, 150)),
        ]
        for row, (text, color) in enumerate(lines):
            self._screen.blit(self._font.render(text, True, color),
                              (20, 20 + row * LINE_HEIGHT))
        pygame.display.flip()
