"""Load a team from a Python file: your own code, swapped into the slots.

A team file defines functions named after the slots it replaces, any of:

    def roles(team, world): ...      # -> [(me, role), ...] for every robot
    def attacker(me, world): ...     # -> Command, e.g. me.go_to(...)
    def supporter(me, world): ...
    def defender(me, world): ...     # also a robot playing alone

Anything it doesn't define stays built-in. strategy/examples/ has a template
and an example. Play one with the match runner (`--a mycode.py`) or live in
the sim (`strategy_node.py --blue mycode.py`).

TeamFile watches the file and reloads it when it's saved; an edit that
doesn't load (a syntax error, say) keeps the last version that did, and the
error is reported. Errors while playing are handled by slots.py: that robot
falls back to the built-in slot for the frame.

The file runs in this process, as trusted code on your own computer: an
endless loop in it stops the strategy. Never load files from people you don't
trust.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import os
import traceback
from pathlib import Path

from strategy.core.slots import DEFAULT_TEAM, Team

SLOTS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(Team))
_loads = itertools.count()


class TeamFileError(Exception):
    """A team file that can't be used, with where and why."""


def load_team(path: str | os.PathLike[str]) -> Team:
    """The built-in team with the slots `path` defines swapped in."""
    file = Path(path).resolve()
    if not file.is_file():
        raise TeamFileError(f"no team file at {file}")
    # A fresh module name each time, so a reload never sees the old module.
    spec = importlib.util.spec_from_file_location(f"_team_file_{next(_loads)}", file)
    if spec is None or spec.loader is None:
        raise TeamFileError(f"{file.name}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as e:
        raise TeamFileError(f"{file.name}:{e.lineno}: syntax error: {e.msg}") from None
    except Exception as e:  # noqa: BLE001 -- the file may raise anything while loading
        raise TeamFileError(f"{_where(e, file)}: {type(e).__name__}: {e}") from None

    slots = {name: getattr(module, name) for name in SLOTS if callable(getattr(module, name, None))}
    if not slots:
        raise TeamFileError(f"{file.name} defines none of the slots ({', '.join(SLOTS)})")
    return DEFAULT_TEAM.replace(**slots)


def replaced_slots(team: Team) -> list[str]:
    """The slots a team doesn't take from the built-in team."""
    return [name for name in SLOTS if getattr(team, name) is not getattr(DEFAULT_TEAM, name)]


class TeamFile:
    """A team file, reloaded when it changes on disk.

    `team` is the last version that loaded (None until one does), `error`
    what went wrong with the newest one (None when it loaded)."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path).resolve()
        self.team: Team | None = None
        self.error: str | None = None
        self._stamp: tuple[float, int] | None = None
        self.reload()

    def reload(self) -> None:
        self._stamp = self._read_stamp()
        try:
            self.team = load_team(self.path)
            self.error = None
        except TeamFileError as e:
            self.error = str(e)  # keep the last team that loaded

    def reload_if_changed(self) -> bool:
        """Reload if the file changed since the last load; True if it did."""
        if self._read_stamp() == self._stamp:
            return False
        self.reload()
        return True

    def _read_stamp(self) -> tuple[float, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime, st.st_size)


def _where(e: BaseException, file: Path) -> str:
    """file:line of the deepest frame of `e` inside `file`."""
    frames = [f for f in traceback.extract_tb(e.__traceback__) if Path(f.filename).resolve() == file]
    return f"{file.name}:{frames[-1].lineno}" if frames else file.name
