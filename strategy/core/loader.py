"""Load a team: your own code, swapped into the slots.

A team is a **workspace**, a folder of Python files whose `team.py` defines
functions named after the slots it replaces, any of:

    def roles(team, world): ...      # -> [(me, role), ...] for every robot
    def attacker(me, world): ...     # -> Command, e.g. me.go_to(...)
    def supporter(me, world): ...
    def defender(me, world): ...     # also a robot playing alone
    def goalie(me, world): ...       # a role for plays and roles()
    def prepare(world): ...          # once a frame, before the robots
    def team(robots, world): ...     # -> {robot: Command}, any robots at once
    def kickoff(robots, world): ...  # -> {robot: (x, y)} before a kickoff
    PLAYS = {"attack": ["attacker", "supporter", "goalie"], ...}
    def pick_play(world): ...        # -> the name of the play to run

Any other function in team.py is a role of the team's own (`runner`,
`marker`) that roles() or a play can give a robot.

The other files are its own modules: `from passing import best_spot` in
team.py loads passing.py from the same folder. A single .py file works too,
as a workspace of one file. Anything the team doesn't define stays built-in.
strategy/examples/ has a template and examples. Play one with the match
runner (`--a teams/my_team`) or live in the sim (`--blue teams/my_team`).

TeamFile watches the workspace and reloads it when any file is saved; an edit
that doesn't load (a syntax error, say) keeps the last version that did, and
the error is reported with its file and line. Errors while playing are
handled by slots.py: that robot falls back to the built-in slot for the frame.

A workspace's workspace.json (written by the web app) says where it came
from; one uploaded from someone else plays in safe mode (core/safety.py).
Live and in matches, a team runs in its own process (core/worker.py).
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import itertools
import json
import keyword
import os
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from strategy.core.slots import DEFAULT_TEAM, ROLES, SLOT_NAMES, Team

SLOTS: tuple[str, ...] = SLOT_NAMES
ENTRY = "team.py"  # a workspace's first file
META = "workspace.json"  # where it came from (the web app writes it)
_loads = itertools.count()
# Names a workspace's own file can't have: they'd hide a module everyone needs.
RESERVED = frozenset(sys.stdlib_module_names) | {"numpy", "strategy", "team", "pymunk", "zmq"}


class TeamFileError(Exception):
    """A team that can't be used, with where and why."""


def module_name_problem(file_name: str) -> str | None:
    """Why `file_name` can't be one of a workspace's files, or None if it can."""
    stem, dot, ext = file_name.rpartition(".")
    if not dot or ext != "py" or not stem.isidentifier() or keyword.iskeyword(stem):
        return "a file name is letters, digits and _, starting with a letter, ending in .py"
    if len(stem) > 40:
        return "a file name is at most 40 characters"
    if stem in RESERVED and file_name != ENTRY:
        return f"{file_name} would hide Python's own {stem} module: pick another name"
    return None


def workspace_files(path: str | os.PathLike[str]) -> tuple[Path, Path, dict[str, Path]]:
    """(folder, first file, its own modules by name) for a workspace folder or
    a single file. A single file is a workspace of one: no other modules."""
    p = Path(path).resolve()
    if p.is_dir():
        entry = p / ENTRY
        modules = {f.stem: f for f in sorted(p.glob("*.py"))
                   if f.name != ENTRY and module_name_problem(f.name) is None}
        return p, entry, modules
    return p.parent, p, {}


def workspace_info(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The workspace's workspace.json ({} for a single file, or none)."""
    p = Path(path)
    try:
        return json.loads((p / META).read_text(encoding="utf-8")) if p.is_dir() else {}
    except (OSError, ValueError):
        return {}


def is_safe_mode(path: str | os.PathLike[str]) -> bool:
    """Plays in safe mode: it came from someone else (uploaded)."""
    return bool(workspace_info(path).get("safe"))


class _Loader(importlib.machinery.SourceFileLoader):
    """A workspace module: compiled straight from its source (no .pyc, so an
    edit always counts), with safe-mode builtins when asked."""

    def __init__(self, fullname: str, path: str, builtins_: dict[str, Any] | None):
        super().__init__(fullname, path)
        self.builtins_ = builtins_

    def get_code(self, fullname: str) -> Any:
        return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)

    def exec_module(self, module: ModuleType) -> None:
        if self.builtins_ is not None:
            module.__dict__["__builtins__"] = self.builtins_
        module.__dict__["__workspace__"] = True
        super().exec_module(module)


class _WorkspaceImporter(importlib.abc.MetaPathFinder):
    """Finds the current workspace's own modules (`import passing`), and only
    those: nothing else on the import path changes."""

    def __init__(self) -> None:
        self.modules: dict[str, Path] = {}
        self.builtins_: dict[str, Any] | None = None

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> Any:
        if path is None and name in self.modules:
            file = str(self.modules[name])
            return importlib.util.spec_from_file_location(
                name, file, loader=_Loader(name, file, self.builtins_))
        return None


_importer = _WorkspaceImporter()


def load_team(path: str | os.PathLike[str], safe: bool | None = None) -> Team:
    """The built-in team with the slots the workspace (or file) at `path`
    defines swapped in. `safe`: load it in safe mode (None: as its
    workspace.json says). Safe mode also needs core/safety.install() for the
    process; the worker does both.

    One workspace at a time per process: a later load_team() takes over the
    import of module names (an earlier team keeps the modules it imported)."""
    folder, entry, modules = workspace_files(path)
    if not entry.is_file():
        raise TeamFileError(f"{folder.name} has no {ENTRY}: a workspace's team starts there"
                            if Path(path).is_dir() else f"no team file at {entry}")
    if safe is None:
        safe = is_safe_mode(path)

    # Forget the modules an earlier load imported, so edits to them count.
    for name, mod in list(sys.modules.items()):
        if getattr(mod, "__workspace__", False):
            del sys.modules[name]
    importlib.invalidate_caches()
    if safe:
        from strategy.core.safety import safe_builtins

        builtins_: dict[str, Any] | None = safe_builtins(modules)
    else:
        from strategy.core.safety import no_input

        builtins_ = {**__builtins__, "input": no_input} if isinstance(__builtins__, dict) \
            else {**vars(__builtins__), "input": no_input}
    _importer.modules = modules
    _importer.builtins_ = builtins_
    if _importer not in sys.meta_path:
        sys.meta_path.insert(0, _importer)

    # The first file under a fresh name each time, so a reload never sees the old one.
    name = f"_team_{next(_loads)}"
    spec = importlib.util.spec_from_file_location(name, entry, loader=_Loader(name, str(entry), builtins_))
    if spec is None or spec.loader is None:
        raise TeamFileError(f"{entry.name}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    files = {entry, *modules.values()}
    try:
        spec.loader.exec_module(module)
    except SyntaxError as e:
        where = Path(e.filename).name if e.filename else entry.name
        raise TeamFileError(f"{where}:{e.lineno}: syntax error: {e.msg}") from None
    except Exception as e:  # noqa: BLE001 -- the file may raise anything while loading
        raise TeamFileError(f"{_where(e, files, entry)}: {type(e).__name__}: {e}") from None

    slots = {n: getattr(module, n) for n in SLOTS if callable(getattr(module, n, None))}
    plays = _plays(module, entry)
    if not slots and not plays:
        raise TeamFileError(f"{entry.name} defines none of the slots ({', '.join(SLOTS)})")
    if plays and "pick_play" not in slots:
        raise TeamFileError(f"{entry.name}: PLAYS needs a def pick_play(world) that picks one")
    # The team's own roles: its other functions (a helper that's never named
    # as a role is never called as one).
    custom = {n: f for n, f in vars(module).items()
              if inspect.isfunction(f) and not n.startswith("_") and n not in SLOTS}
    for play, roles in plays.items():
        for role in roles:
            if role not in ROLES and role not in custom:
                raise TeamFileError(f"{entry.name}: the play {play!r} has the role {role!r}, "
                                    + f"but there's no def {role}(me, world) for it")
    return DEFAULT_TEAM.replace(**slots).with_roles(plays, custom)


def _plays(module: ModuleType, entry: Path) -> dict[str, tuple[str, ...]]:
    """The team's PLAYS, checked: a play's name -> its roles, most important first."""
    plays = getattr(module, "PLAYS", None)
    if plays is None:
        return {}
    if not isinstance(plays, dict) or not plays:
        raise TeamFileError(f"{entry.name}: PLAYS must be a dict like "
                            + '{"attack": ["attacker", "supporter", "goalie"]}')
    out = {}
    for name, roles in plays.items():
        if not isinstance(name, str) or not isinstance(roles, (list, tuple)) or not roles                 or not all(isinstance(r, str) for r in roles):
            raise TeamFileError(f"{entry.name}: PLAYS[{name!r}] must be a list of role names, "
                                + 'most important first, like ["attacker", "supporter", "goalie"]')
        out[name] = tuple(roles)
    return out


def replaced_slots(team: Team) -> list[str]:
    """The slots a team doesn't take from the built-in team (and its plays)."""
    out = [name for name in SLOTS if getattr(team, name) is not getattr(DEFAULT_TEAM, name)]
    if team.plays:
        out.append(f"PLAYS ({', '.join(team.plays)})")
    return out


class TeamFile:
    """A team (a workspace or a file), reloaded when any of its files changes.

    `team` is the last version that loaded (None until one does), `error`
    what went wrong with the newest one (None when it loaded)."""

    def __init__(self, path: str | os.PathLike[str], safe: bool | None = None):
        self.path = Path(path).resolve()
        self.safe = safe
        self.team: Team | None = None
        self.error: str | None = None
        self._stamp: tuple[Any, ...] | None = None
        self.reload()

    def reload(self) -> None:
        self._stamp = self._read_stamp()
        try:
            self.team = load_team(self.path, self.safe)
            self.error = None
        except TeamFileError as e:
            self.error = str(e)  # keep the last team that loaded

    def reload_if_changed(self) -> bool:
        """Reload if a file changed since the last load; True if it did."""
        if self._read_stamp() == self._stamp:
            return False
        self.reload()
        return True

    def _read_stamp(self) -> tuple[Any, ...]:
        files = sorted(self.path.glob("*.py")) if self.path.is_dir() else [self.path]
        stamp = []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            stamp.append((f.name, st.st_mtime_ns, st.st_size))
        return tuple(stamp)


def _where(e: BaseException, files: set[Path], entry: Path) -> str:
    """file:line of the deepest frame of `e` in the workspace's files."""
    names = {str(f) for f in files}
    frames = [f for f in traceback.extract_tb(e.__traceback__) if str(Path(f.filename).resolve()) in names]
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else entry.name
