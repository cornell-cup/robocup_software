"""On-screen building blocks for the interactive tools, drawn with OpenCV.

Everything a tool's window needs besides the camera picture itself:

    Window             a window that fits the screen, with clicks in image pixels
                       and arrow/Enter/Esc keys read the same on every OS
    instruction_panel  a band stacked under the camera picture: what to do now,
                       numbered steps, what the program sees, a progress bar, keys
    field_map          a small top-down map of the field with points on it
    form               a setup screen: pick each Option by clicking, no flags
    message            a result or notice, with buttons
    menu               a list of steps to pick from

None of the words live here. Tools pass in what to say (tag_tracking's
calib_guide holds the calibration texts) and what to ask (vision_core.options),
so another front end can show the same things without this module. Each screen
is a pure render_*() that returns the picture plus its clickable boxes, and a
loop around it that handles input -- render one to a PNG to see it without a
window.

OpenCV's built-in font is ASCII only; put() swaps the few non-ASCII characters
the texts might contain for ASCII ones rather than drawing '?'.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .options import Option

FONT = cv2.FONT_HERSHEY_SIMPLEX

# BGR
BG = (36, 32, 30)
CARD = (54, 49, 46)
CARD_HI = (82, 72, 64)
EDGE = (96, 90, 86)
TEXT = (240, 240, 240)
MUTED = (170, 165, 160)
ACCENT = (240, 165, 70)
COLORS = {
    "ok": (110, 205, 110),
    "warn": (50, 190, 245),
    "bad": (95, 95, 245),
    "info": ACCENT,
    "todo": MUTED,
}

#: The biggest a window may be drawn; anything larger is scaled down to fit.
#: Override for a bigger or smaller screen with ROBOCUP_UI_MAX_W / _H.
MAX_W = int(os.environ.get("ROBOCUP_UI_MAX_W", "1280"))
MAX_H = int(os.environ.get("ROBOCUP_UI_MAX_H", "820"))
#: Full-window screens (form, message, menu) are drawn this wide.
SCREEN_W = 1180

# Keys, as Window.key() returns them: letters lowercase, these for the rest.
NONE, TAB, BACKSPACE, ENTER, ESC, SPACE = -1, 9, 8, 13, 27, 32
UP, DOWN, LEFT, RIGHT = 1001, 1002, 1003, 1004
#: waitKeyEx's arrow codes differ per OS: macOS, Windows, Linux (GTK).
_ARROWS = {
    63232: UP, 2490368: UP, 65362: UP,
    63233: DOWN, 2621440: DOWN, 65364: DOWN,
    63234: LEFT, 2424832: LEFT, 65361: LEFT,
    63235: RIGHT, 2555904: RIGHT, 65363: RIGHT,
}

_ASCII = {"°": " deg", "×": "x", "—": "--", "–": "-", "·": "-",
          "→": "->", "←": "<-", "’": "'", "‘": "'", "“": '"',
          "”": '"', "…": "...", "±": "+/-"}


class Cancelled(SystemExit):
    """Esc, or the window closed. A SystemExit with code 0, so a caller that does
    not catch it ends quietly, as the tools always did on Esc; the guided flows
    catch it and go back a screen instead."""

    def __init__(self) -> None:
        super().__init__(0)


# --------------------------------------------------------------------------
# The window.
# --------------------------------------------------------------------------


class Window:
    """One OpenCV window, scaled down to fit the screen when the picture is
    bigger. Clicks come back in the coordinates of the picture that was shown,
    whatever the scaling, so callers never see it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._scale = 1.0
        self._clicks: list[tuple[float, float]] = []
        self.hover: tuple[float, float] | None = None
        self._open = False
        self._was_visible = False

    def _ensure(self) -> None:
        if not self._open:
            cv2.namedWindow(self.name, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(self.name, self._on_mouse)
            self._open = True
            self._was_visible = False

    def _on_mouse(self, event, x, y, _flags, _param) -> None:
        p = (x / self._scale, y / self._scale)
        if event == cv2.EVENT_LBUTTONDOWN:
            self._clicks.append(p)
        elif event == cv2.EVENT_MOUSEMOVE:
            self.hover = p

    def show(self, img: np.ndarray) -> None:
        self._ensure()
        h, w = img.shape[:2]
        s = min(1.0, MAX_W / w, MAX_H / h)
        self._scale = s
        if s < 1.0:
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        cv2.imshow(self.name, img)

    def key(self, delay_ms: int = 1) -> int:
        """The key pressed (letters lowercased; ENTER, ESC, arrows... as the
        constants above), or NONE. Closing the window counts as ESC."""
        k = cv2.waitKeyEx(delay_ms)
        if self._closed_by_user():
            return ESC
        if k == -1:
            return NONE
        if k in _ARROWS:
            return _ARROWS[k]
        k &= 0xFF
        if k in (10, 13):
            return ENTER
        if k == 127:
            return BACKSPACE
        if ord("A") <= k <= ord("Z"):
            k += 32
        return k

    def _closed_by_user(self) -> bool:
        # Only trusted once the window has reported itself visible: some
        # backends report nothing useful, and a false "closed" would abort.
        try:
            visible = cv2.getWindowProperty(self.name, cv2.WND_PROP_VISIBLE)
        except cv2.error:
            return self._was_visible
        if visible >= 1:
            self._was_visible = True
            return False
        return self._was_visible

    def clicks(self) -> list[tuple[float, float]]:
        """Clicks since the last call, in picture pixels."""
        out, self._clicks = self._clicks, []
        return out

    def close(self) -> None:
        if self._open:
            try:
                cv2.destroyWindow(self.name)
            except cv2.error:
                pass
            cv2.waitKey(1)
            self._open = False


# --------------------------------------------------------------------------
# Text and boxes.
# --------------------------------------------------------------------------

Box = tuple[int, int, int, int]


def ascii(s: str) -> str:
    for k, v in _ASCII.items():
        s = s.replace(k, v)
    return s.encode("ascii", "replace").decode()


def text_w(s: str, scale: float, thick: int = 1) -> int:
    return cv2.getTextSize(ascii(s), FONT, scale, thick)[0][0]


def line_h(scale: float) -> int:
    return int(round(36 * scale))


def wrap(s: str, max_w: int, scale: float, thick: int = 1) -> list[str]:
    lines: list[str] = []
    for para in s.split("\n"):
        cur = ""
        for word in para.split():
            t = f"{cur} {word}" if cur else word
            if not cur or text_w(t, scale, thick) <= max_w:
                cur = t
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def put(img, s: str, x: float, y: float, scale: float = 0.6,
        color=TEXT, thick: int = 1) -> None:
    cv2.putText(img, ascii(s), (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)


def box(img, b: Box, color, fill: bool = True, thick: int = 2) -> None:
    cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), color, -1 if fill else thick, cv2.LINE_AA)


def button(img, x: int, y: int, label: str, scale: float = 0.62, *, primary: bool = False,
           enabled: bool = True, selected: bool = False, min_w: int = 0) -> Box:
    """Draw a button with its top-left at (x, y); returns its box."""
    pad_x, h = int(18 * scale / 0.62), int(44 * scale / 0.62)
    w = max(min_w, text_w(label, scale, 2) + 2 * pad_x)
    b = (x, y, x + w, y + h)
    if not enabled:
        box(img, b, CARD)
        box(img, b, EDGE, fill=False, thick=1)
        color = MUTED
    elif primary or selected:
        box(img, b, ACCENT)
        color = (20, 20, 20)
    else:
        box(img, b, CARD_HI)
        box(img, b, EDGE, fill=False, thick=1)
        color = TEXT
    tw = text_w(label, scale, 2)
    put(img, label, x + (w - tw) / 2, y + h / 2 + 8 * scale / 0.62, scale, color, 2)
    return b


def hit(boxes: Sequence[tuple[Box, Any]], p: tuple[float, float]) -> Any:
    for b, action in boxes:
        if b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]:
            return action
    return None


def fit_width(img: np.ndarray, w: int) -> np.ndarray:
    if img.shape[1] == w:
        return img
    h = int(round(img.shape[0] * w / img.shape[1]))
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    s = min(w / img.shape[1], h / img.shape[0])
    size = (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------
# Instructions under the camera picture.
# --------------------------------------------------------------------------


def instruction_panel(width: int, info: dict[str, Any],
                      side: np.ndarray | None = None) -> np.ndarray:
    """A band to stack under the camera picture, from an instruction dict:
    title, steps, status, kind ("ok"/"info"/"warn"/"bad"), progress (0..1 or
    None) and keys ([(key, what it does)]). `side` (e.g. a field_map) is drawn
    on the right."""
    f = max(0.55, width / 1280)
    pad = int(18 * f)
    side_w = side.shape[1] + pad if side is not None else 0
    tw = width - 2 * pad - side_w
    t_s, s_s, k_s = 0.86 * f, 0.64 * f, 0.56 * f
    kind = info.get("kind", "info")

    title = wrap(info.get("title", ""), tw, t_s, 2)
    steps = [wrap(s, tw - int(30 * f), s_s) for s in info.get("steps", ())]
    status = wrap(info.get("status", ""), tw - int(26 * f), s_s, 2) if info.get("status") else []
    progress = info.get("progress")
    keys = info.get("keys", ())

    h = pad + len(title) * line_h(t_s) + int(6 * f)
    h += sum(len(s) for s in steps) * line_h(s_s) + len(steps) * int(4 * f)
    h += (len(status) * line_h(s_s) + int(10 * f)) if status else 0
    h += int(26 * f) if progress is not None else 0
    h += int(46 * f) if keys else 0
    h = max(h + pad, (side.shape[0] + 2 * pad) if side is not None else 0)

    img = np.full((h, width, 3), BG, np.uint8)
    cv2.rectangle(img, (0, 0), (int(6 * f), h), COLORS.get(kind, ACCENT), -1)
    y = pad + int(24 * f)
    for ln in title:
        put(img, ln, pad, y, t_s, TEXT, 2)
        y += line_h(t_s)
    y += int(6 * f)
    for i, lines in enumerate(steps):
        put(img, f"{i + 1}.", pad, y, s_s, ACCENT, 2)
        for ln in lines:
            put(img, ln, pad + int(30 * f), y, s_s, TEXT)
            y += line_h(s_s)
        y += int(4 * f)
    if status:
        y += int(6 * f)
        cv2.circle(img, (pad + int(8 * f), y - int(8 * f)), int(8 * f),
                   COLORS.get(kind, ACCENT), -1, cv2.LINE_AA)
        for ln in status:
            put(img, ln, pad + int(26 * f), y, s_s, COLORS.get(kind, ACCENT), 2)
            y += line_h(s_s)
    if progress is not None:
        y += int(2 * f)
        x1 = pad + tw
        cv2.rectangle(img, (pad, y - int(6 * f)), (x1, y + int(8 * f)), CARD, -1)
        fill = pad + int((x1 - pad) * min(max(float(progress), 0.0), 1.0))
        cv2.rectangle(img, (pad, y - int(6 * f)), (fill, y + int(8 * f)),
                      COLORS["ok"] if progress >= 1.0 else ACCENT, -1)
        y += int(26 * f)
    if keys:
        x = pad
        y += int(16 * f)
        for k, what in keys:
            kw = text_w(k, k_s, 2) + int(16 * f)
            cv2.rectangle(img, (x, y - int(22 * f)), (x + kw, y + int(8 * f)), CARD_HI, -1)
            put(img, k, x + int(8 * f), y, k_s, TEXT, 2)
            x += kw + int(8 * f)
            put(img, what, x, y, k_s, MUTED)
            x += text_w(what, k_s) + int(26 * f)
    if side is not None:
        sy = (h - side.shape[0]) // 2
        img[sy:sy + side.shape[0], width - pad - side.shape[1]:width - pad] = side
    return img


def stack(picture: np.ndarray, panel: np.ndarray) -> np.ndarray:
    """The camera picture with the panel under it. Under, not over, so nothing
    in the picture is hidden and clicks on it keep their coordinates."""
    if picture.ndim == 2:
        picture = cv2.cvtColor(picture, cv2.COLOR_GRAY2BGR)
    return np.vstack([picture, fit_width(panel, picture.shape[1])])


# --------------------------------------------------------------------------
# A small top-down map of the field.
# --------------------------------------------------------------------------

#: Point states: todo (not yet), current (do this one now), done, ok/warn/bad
#: (measured, graded), skip.
def field_map(bounds: tuple[float, float, float, float],
              points: Sequence[tuple[float, float]] = (),
              labels: Sequence[str] | None = None,
              states: Sequence[str] | None = None,
              size: tuple[int, int] = (320, 210),
              coords: bool = False) -> np.ndarray:
    """The field (x0, y0, width, height in metres; +X right, +Y up) with its
    points: the current one pulses, done ones are filled. `coords` writes each
    point's (x, y) beside it."""
    W, H = size
    img = np.full((H, W, 3), CARD, np.uint8)
    x0, y0, fw, fh = bounds
    m = 34
    s = min((W - 2 * m) / max(fw, 1e-6), (H - 2 * m) / max(fh, 1e-6))
    left = (W - fw * s) / 2
    bottom = (H + fh * s) / 2

    def px(x, y):
        return int(round(left + (x - x0) * s)), int(round(bottom - (y - y0) * s))

    cv2.rectangle(img, px(x0, y0), px(x0 + fw, y0 + fh), (90, 170, 90), 2, cv2.LINE_AA)
    # Which way x and y go, just outside the bottom-left corner, so "x" and
    # "y" in the instructions mean something.
    bl = px(x0, y0)
    cv2.arrowedLine(img, (bl[0], bl[1] + 18), (bl[0] + 40, bl[1] + 18), MUTED, 2,
                    cv2.LINE_AA, tipLength=0.3)
    put(img, "x", bl[0] + 46, bl[1] + 24, 0.5, MUTED)
    cv2.arrowedLine(img, (bl[0] - 18, bl[1]), (bl[0] - 18, bl[1] - 40), MUTED, 2,
                    cv2.LINE_AA, tipLength=0.3)
    put(img, "y", bl[0] - 24, bl[1] - 46, 0.5, MUTED)
    pulse = 0.5 + 0.5 * np.sin(time.monotonic() * 6.0)
    for i, (x, y) in enumerate(points):
        c = px(x, y)
        st = states[i] if states else "todo"
        if st == "current":
            cv2.circle(img, c, int(10 + 6 * pulse), ACCENT, 2, cv2.LINE_AA)
            cv2.circle(img, c, 7, ACCENT, -1, cv2.LINE_AA)
        elif st == "skip":
            cv2.drawMarker(img, c, MUTED, cv2.MARKER_TILTED_CROSS, 12, 2)
        elif st == "todo":
            cv2.circle(img, c, 6, MUTED, 2, cv2.LINE_AA)
        else:
            cv2.circle(img, c, 7, COLORS.get(st, COLORS["ok"]), -1, cv2.LINE_AA)
        txt = labels[i] if labels else ""
        if coords:
            txt = f"{txt} ({round(x, 3):g}, {round(y, 3):g})".strip()
        if txt:
            tx = c[0] + 10 if c[0] < W - 90 else c[0] - 10 - text_w(txt, 0.48)
            ty = c[1] - 10 if c[1] > 24 else c[1] + 22
            put(img, txt, tx, ty, 0.48, TEXT if st == "current" else MUTED)
    return img


# --------------------------------------------------------------------------
# The setup form.
# --------------------------------------------------------------------------

_LABEL_W = 330


def _pill_layout(o: Option, x: int, y: int, max_w: int, scale: float) -> list[tuple[Box, Any]]:
    out, cx, cy = [], x, y
    h = int(40 * scale / 0.58)
    for value, label in o.choices:
        w = text_w(label, scale, 2) + 28
        if cx > x and cx + w > x + max_w:
            cx, cy = x, cy + h + 8
        out.append(((cx, cy, cx + w, cy + h), value))
        cx += w + 8
    return out


def render_form(title: str, intro: Sequence[str], options: Sequence[Option],
                values: dict[str, Any], texts: dict[str, str], active: int,
                buttons: Sequence[tuple[str, str]], errors: Sequence[str],
                preview: tuple[np.ndarray | None, str] | None = None,
                editing: bool = False) -> tuple[np.ndarray, list[tuple[Box, Any]]]:
    """The form as a picture, plus clickable boxes -> ("pick", key, value),
    ("focus", key), ("step", key, +-1) or ("button", key)."""
    W = SCREEN_W
    img = np.full((1600, W, 3), BG, np.uint8)
    boxes: list[tuple[Box, Any]] = []
    pad = 30
    pw = 420 if preview is not None else 0
    right = W - pad - (pw + pad if pw else 0)
    y = pad + 30
    put(img, title, pad, y, 1.0, TEXT, 2)
    y += 22
    for para in intro:
        for ln in wrap(para, right - pad, 0.55):
            y += line_h(0.55)
            put(img, ln, pad, y, 0.55, MUTED)
        y += 8
    y += 18
    if preview is not None:
        pic, caption = preview
        px0, py0 = W - pad - pw, pad + 50
        ph = int(pw * 9 / 16)
        if pic is not None:
            thumb = fit(pic, pw, ph)
            img[py0:py0 + thumb.shape[0], px0:px0 + thumb.shape[1]] = thumb
            ph = thumb.shape[0]
        else:
            box(img, (px0, py0, px0 + pw, py0 + ph), CARD)
        cy = py0 + ph + 26
        for ln in wrap(caption, pw, 0.5):
            put(img, ln, px0, cy, 0.5, MUTED)
            cy += line_h(0.5)

    visible = [o for o in options if o.visible(values)]
    cx = pad + _LABEL_W + 20
    cw = right - cx
    for i, o in enumerate(visible):
        help_lines = wrap(o.help, _LABEL_W - 16, 0.48) if o.help else []
        label_h = 30 + len(help_lines) * line_h(0.48)
        if o.kind == "choice":
            pills = _pill_layout(o, cx, y + 10, cw, 0.58)
            ctrl_h = pills[-1][0][3] - y if pills else 40
        else:
            pills, ctrl_h = [], 54
        row_h = max(label_h, ctrl_h) + 18
        if i == active:
            box(img, (pad - 12, y - 4, right + 8, y + row_h - 4), CARD_HI)
            cv2.rectangle(img, (pad - 12, y - 4), (pad - 7, y + row_h - 4), ACCENT, -1)
        put(img, o.label, pad, y + 26, 0.64, TEXT, 2)
        hy = y + 26
        for ln in help_lines:
            hy += line_h(0.48)
            put(img, ln, pad, hy, 0.48, MUTED)
        if o.kind == "choice":
            for b, value in pills:
                selected = values.get(o.key) == value
                box(img, b, ACCENT if selected else CARD)
                box(img, b, ACCENT if selected else EDGE, fill=False, thick=1)
                lbl = o.label_of(value)
                put(img, lbl, b[0] + 14, b[1] + 27, 0.58, (20, 20, 20) if selected else TEXT, 2)
                boxes.append((b, ("pick", o.key, value)))
        else:
            by = y + 10
            minus = button(img, cx, by, "-", 0.62, min_w=44)
            tb = (minus[2] + 8, by, minus[2] + 8 + 170, by + 44)
            bad = any(e.startswith(o.label + ":") for e in errors)
            box(img, tb, (30, 28, 26))
            box(img, tb, COLORS["bad"] if bad else (ACCENT if i == active else EDGE),
                fill=False, thick=2)
            t = texts.get(o.key, "")
            if t:
                shown = t + ("|" if i == active and editing and int(time.monotonic() * 2) % 2
                             else "")
                put(img, shown, tb[0] + 12, tb[1] + 30, 0.68, TEXT, 2)
            elif o.blank:
                put(img, o.blank, tb[0] + 12, tb[1] + 30, 0.6, MUTED)
            plus = button(img, tb[2] + 8, by, "+", 0.62, min_w=44)
            if o.unit:
                put(img, o.unit, plus[2] + 12, by + 30, 0.62, MUTED)
            boxes += [(minus, ("step", o.key, -1)), (tb, ("focus", o.key)),
                      (plus, ("step", o.key, 1))]
        y += row_h + 6

    y += 10
    for e in errors[:3]:
        put(img, e, pad, y + 18, 0.55, COLORS["bad"], 2)
        y += line_h(0.55)
    y += 14
    x = pad
    for j, (key, label) in enumerate(buttons):
        quit_like = j == len(buttons) - 1 and len(buttons) > 1
        b = button(img, x, y, label, 0.66, primary=(j == 0),
                   enabled=bool(quit_like or not errors))
        boxes.append((b, ("button", key)))
        x = b[2] + 14
    hint_y = y + 44 + 32
    put(img, "Click to choose.  Up/Down: move between rows.  Left/Right or -/+: change.  "
             "Type numbers.  Enter: " + buttons[0][1] + ".  Esc: " + buttons[-1][1] + ".",
        pad, hint_y, 0.46, MUTED)
    h = max(hint_y + pad, (pad + 50 + int(pw * 9 / 16) + 120) if pw else 0)
    return img[:h].copy(), boxes


def form(window: Window, title: str, intro: Sequence[str], options: Sequence[Option],
         values: dict[str, Any],
         buttons: Sequence[tuple[str, str]] = (("start", "Start"), ("quit", "Quit")),
         preview: Callable[[dict[str, Any]], tuple[np.ndarray | None, str]] | None = None,
         ) -> tuple[str, dict[str, Any]]:
    """Ask for `options` on screen, starting from `values`. Returns (the button
    pressed, the values). Every button but the last (the way out) needs every
    visible number to be valid first; Enter presses the first, Esc the last."""
    values = dict(values)
    texts = {o.key: o.format(values.get(o.key)) for o in options if o.kind == "number"}
    active, fresh, editing = 0, True, False

    def parsed() -> tuple[dict[str, Any], list[str]]:
        out, errs = dict(values), []
        for o in options:
            if o.kind == "number" and o.visible(values):
                try:
                    out[o.key] = o.parse(texts[o.key])
                except ValueError as e:
                    errs.append(str(e))
        return out, errs

    while True:
        visible = [o for o in options if o.visible(values)]
        active = min(active, len(visible) - 1)
        current, errors = parsed()
        img, boxes = render_form(title, intro, options, values, texts, active, buttons,
                                 errors, preview(current) if preview else None, editing)
        window.show(img)
        k = window.key(30)
        pressed = None
        for p in window.clicks():
            a = hit(boxes, p)
            if a is None:
                continue
            if a[0] == "pick":
                values[a[1]] = a[2]
            elif a[0] == "focus":
                fresh, editing = True, True
            elif a[0] == "step":
                o = next(o for o in options if o.key == a[1])
                texts[o.key] = o.stepped(texts[o.key], a[2])
            elif a[0] == "button":
                pressed = a[1]
            if a[0] in ("pick", "focus", "step"):
                keys = [o.key for o in [o for o in options if o.visible(values)]]
                active = keys.index(a[1]) if a[1] in keys else active
        if k == ESC:
            pressed = buttons[-1][0]
        elif k == ENTER:
            pressed = buttons[0][0]
        elif k in (DOWN, TAB):
            active, fresh, editing = (active + 1) % len(visible), True, False
        elif k == UP:
            active, fresh, editing = (active - 1) % len(visible), True, False
        elif k in (LEFT, RIGHT, ord("-"), ord("+"), ord("=")) and visible:
            o = visible[active]
            d = 1 if k in (RIGHT, ord("+"), ord("=")) else -1
            if o.kind == "choice" and k in (LEFT, RIGHT):
                values[o.key] = o.cycled(values.get(o.key), d)
            elif o.kind == "number":
                texts[o.key] = o.stepped(texts[o.key], d)
                fresh = True
        elif visible and visible[active].kind == "number" and (
                ord("0") <= k <= ord("9") or k == ord(".")):
            o = visible[active]
            texts[o.key] = (chr(k) if fresh else texts[o.key] + chr(k))
            fresh, editing = False, True
        elif k == BACKSPACE and visible and visible[active].kind == "number":
            o = visible[active]
            texts[o.key] = "" if fresh else texts[o.key][:-1]
            fresh, editing = False, True
        if pressed is not None:
            current, errors = parsed()
            if pressed == buttons[-1][0] or not errors or len(buttons) == 1:
                return pressed, current


# --------------------------------------------------------------------------
# A message with buttons, and a menu.
# --------------------------------------------------------------------------


def render_message(title: str, lines: Sequence[str | tuple[str, str]] = (),
                   kind: str | None = None, headline: str | None = None,
                   buttons: Sequence[tuple[str, str]] = (("ok", "OK"),),
                   image: np.ndarray | None = None) -> tuple[np.ndarray, list[tuple[Box, Any]]]:
    """Lines are plain strings, or (text, kind) for a coloured bullet. A narrow
    `image` sits on the right, a wide one under the text."""
    W, pad = SCREEN_W, 30
    img = np.full((2000, W, 3), BG, np.uint8)
    beside = image is not None and image.shape[1] <= 600
    tw = W - 2 * pad - ((image.shape[1] + pad) if beside else 0)
    y = pad + 30
    put(img, title, pad, y, 1.0, TEXT, 2)
    y += 30
    if headline:
        color = COLORS.get(kind or "info", ACCENT)
        hl = wrap(headline, tw - 40, 0.8, 2)
        hb = (pad, y, pad + tw, y + 22 + len(hl) * line_h(0.8))
        box(img, hb, CARD)
        cv2.rectangle(img, (hb[0], hb[1]), (hb[0] + 8, hb[3]), color, -1)
        hy = y + 10
        for ln in hl:
            hy += line_h(0.8)
            put(img, ln, pad + 24, hy - 6, 0.8, color, 2)
        y = hb[3] + 18
    top_of_image = y
    for item in lines:
        text, k = (item, None) if isinstance(item, str) else item
        indent = 0
        if k is not None:
            cv2.circle(img, (pad + 8, y + 16), 7, COLORS.get(k, ACCENT), -1, cv2.LINE_AA)
            indent = 26
        for ln in wrap(text, tw - indent, 0.6):
            y += line_h(0.6)
            put(img, ln, pad + indent, y, 0.6, TEXT)
        y += 10
    if image is not None:
        if beside:
            x0 = W - pad - image.shape[1]
            img[top_of_image:top_of_image + image.shape[0], x0:x0 + image.shape[1]] = image
            y = max(y, top_of_image + image.shape[0])
        else:
            im = fit_width(image, W - 2 * pad)
            if im.shape[0] > 560:
                im = fit(image, W - 2 * pad, 560)
            y += 14
            img[y:y + im.shape[0], pad:pad + im.shape[1]] = im
            y += im.shape[0]
    y += 26
    boxes, x = [], pad
    for j, (key, label) in enumerate(buttons):
        b = button(img, x, y, label, 0.66, primary=(j == 0))
        boxes.append((b, key))
        x = b[2] + 14
    return img[:y + 44 + pad].copy(), boxes


def message(window: Window, title: str, lines: Sequence[str | tuple[str, str]] = (),
            kind: str | None = None, headline: str | None = None,
            buttons: Sequence[tuple[str, str]] = (("ok", "OK"),),
            image: np.ndarray | None = None) -> str:
    """Show a message until a button is chosen; returns its key. Enter is the
    first button, Esc the last."""
    img, boxes = render_message(title, lines, kind, headline, buttons, image)
    while True:
        window.show(img)
        k = window.key(40)
        for p in window.clicks():
            a = hit(boxes, p)
            if a is not None:
                return a
        if k == ENTER:
            return buttons[0][0]
        if k == ESC:
            return buttons[-1][0]


@dataclass
class MenuItem:
    key: str
    title: str
    detail: str = ""
    kind: str = "todo"  # ok | warn | bad | todo: colours the badge
    badge: str = ""
    recommended: bool = False


def render_menu(title: str, intro: Sequence[str], items: Sequence[MenuItem], active: int,
                buttons: Sequence[tuple[str, str]]) -> tuple[np.ndarray, list[tuple[Box, Any]]]:
    W, pad = SCREEN_W, 30
    img = np.full((1600, W, 3), BG, np.uint8)
    boxes: list[tuple[Box, Any]] = []
    y = pad + 30
    put(img, title, pad, y, 1.0, TEXT, 2)
    y += 16
    for para in intro:
        for ln in wrap(para, W - 2 * pad, 0.55):
            y += line_h(0.55)
            put(img, ln, pad, y, 0.55, MUTED)
        y += 6
    y += 22
    for i, it in enumerate(items):
        details = wrap(it.detail, W - 2 * pad - 300, 0.52) if it.detail else []
        h = 62 + len(details) * line_h(0.52)
        b = (pad, y, W - pad, y + h)
        box(img, b, CARD_HI if i == active else CARD)
        if it.recommended:
            box(img, b, ACCENT, fill=False, thick=2)
        cv2.circle(img, (pad + 34, y + 36), 20, ACCENT if it.recommended else EDGE, -1,
                   cv2.LINE_AA)
        put(img, str(i + 1), pad + 27, y + 44, 0.7, (20, 20, 20) if it.recommended else TEXT, 2)
        put(img, it.title, pad + 70, y + 40, 0.74, TEXT, 2)
        dy = y + 40
        for ln in details:
            dy += line_h(0.52)
            put(img, ln, pad + 70, dy, 0.52, MUTED)
        badge = it.badge + ("   <- DO THIS NEXT" if it.recommended else "")
        if badge:
            put(img, badge, W - pad - 20 - text_w(badge, 0.56, 2), y + 40, 0.56,
                COLORS.get(it.kind, MUTED), 2)
        boxes.append((b, it.key))
        y += h + 12
    y += 10
    x = pad
    for key, label in buttons:
        bb = button(img, x, y, label, 0.62)
        boxes.append((bb, key))
        x = bb[2] + 14
    put(img, "Click a step, or press its number.  Esc: " + buttons[-1][1] + ".",
        pad, y + 44 + 30, 0.46, MUTED)
    return img[:y + 44 + 30 + pad].copy(), boxes


def menu(window: Window, title: str, intro: Sequence[str], items: Sequence[MenuItem],
         buttons: Sequence[tuple[str, str]] = (("quit", "Quit"),)) -> str:
    """Pick one item (or a button); returns its key."""
    active = next((i for i, it in enumerate(items) if it.recommended), 0)
    while True:
        img, boxes = render_menu(title, intro, items, active, buttons)
        window.show(img)
        k = window.key(40)
        for p in window.clicks():
            a = hit(boxes, p)
            if a is not None:
                return a
        if k == ESC:
            return buttons[-1][0]
        if k == ENTER and items:
            return items[active].key
        if k in (DOWN, TAB):
            active = (active + 1) % len(items)
        if k == UP:
            active = (active - 1) % len(items)
        if ord("1") <= k <= ord("9") and k - ord("1") < len(items):
            return items[k - ord("1")].key


# --------------------------------------------------------------------------
# Odds and ends.
# --------------------------------------------------------------------------


class CameraPeek:
    """The live picture from whichever camera a form has selected, so "which
    number is the field camera?" is answered by looking, not by guessing. The
    camera it ends up on is handed over with take() rather than reopened."""

    def __init__(self, width: int, height: int) -> None:
        self.size = (width, height)
        self.index: int | None = None
        self.cap: cv2.VideoCapture | None = None
        self.last: np.ndarray | None = None

    def preview(self, values: dict[str, Any]) -> tuple[np.ndarray | None, str]:
        index = values.get("camera")
        if index is None:
            return None, ""
        if index != self.index:
            from .camera import open_camera

            self.release()
            self.index = index
            try:
                self.cap = open_camera(int(index), *self.size)
            except SystemExit:
                self.cap = None
        if self.cap is not None:
            ok, frame = self.cap.read()
            if ok and frame is not None:
                self.last = frame
        if self.cap is None:
            return None, (f"Camera {index} did not open. Try another number, or check the "
                          "cable (and on a Mac: System Settings > Privacy > Camera).")
        return self.last, f"Camera {index} sees this. Pick the one that shows the field."

    def take(self, index: int) -> cv2.VideoCapture | None:
        """The open camera if it is `index`, now the caller's to release."""
        if index == self.index and self.cap is not None:
            cap, self.cap, self.index = self.cap, None, None
            return cap
        self.release()
        return None

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
        self.cap, self.index, self.last = None, None, None


def open_file(path: str | Path) -> None:
    """Open a file in the system's own viewer (a PNG to print, a report)."""
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", p])
    except OSError as e:
        print(f"could not open {p}: {e}")
