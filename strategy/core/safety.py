"""Safe mode: how a workspace from someone else runs (a classmate's upload).

Their code runs in its own process (core/worker.py) like any team, and in
that process, before it loads:

- an audit hook (PEP 578) refuses writing files, reading files outside the
  workspace and the libraries, listing other folders, starting programs,
  network sockets, ctypes and the like, each with a PermissionError on the
  line that tried;
- the workspace's modules get their own builtins, whose import allows only
  a list of modules (maths, numpy, the workspace's own files, the strategy's
  api / tactics / skills / world to read and reuse the built-in code), and
  no input() (stdin is the worker's channel to the strategy node);
- the process gets a memory cap (core/worker.py).

In CPython this is a strong fence for a classroom, not a proven sandbox: a
determined expert may find a way around it. It's meant for classmates' code
on our own laptops with the web app on localhost, not for code from
strangers on the internet (that needs Pyodide in a browser or a container).
"""

from __future__ import annotations

import builtins
import os
import sys
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Modules a workspace in safe mode may import, besides its own files.
SAFE_MODULES = frozenset({
    "math", "cmath", "random", "statistics", "numpy", "collections", "itertools",
    "functools", "operator", "dataclasses", "typing", "enum", "heapq", "bisect",
    "copy", "string", "re", "time", "fractions", "decimal", "abc",
})
# The strategy's own code a workspace may import (to read and reuse it).
SAFE_STRATEGY = ("strategy.api", "strategy.tactics", "strategy.skills", "strategy.world",
                 "strategy.core.command", "strategy.core.drawing")

# Audit events refused in safe mode (exact names, or prefixes ending in ".").
_REFUSED = (
    "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
    "os.forkpty", "os.startfile", "os.kill", "os.killpg", "os.remove", "os.unlink",
    "os.rename", "os.replace", "os.rmdir", "os.removedirs", "os.mkdir", "os.makedirs",
    "os.chmod", "os.chown", "os.chflags", "os.lchown", "os.truncate", "os.link", "os.symlink",
    "os.utime", "os.putenv", "os.unsetenv", "os.chdir", "os.setxattr", "os.removexattr",
    "shutil.", "socket.", "ctypes.", "_winapi.", "winreg.", "msvcrt.", "sys.addaudithook",
    "urllib.", "http.", "ftplib.", "smtplib.", "imaplib.", "poplib.", "telnetlib.",
    "nntplib.", "webbrowser.", "pty.", "tempfile.", "pickle.find_class", "sqlite3.",
    "os.add_dll_directory",
)
_WHAT = {
    "open": "opening files outside your workspace (or writing any)",
    "os.listdir": "looking into other folders", "os.scandir": "looking into other folders",
    "subprocess.": "starting programs", "os.system": "starting programs",
    "os.startfile": "starting programs", "socket.": "the network", "ctypes.": "ctypes",
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class SafeModeError(PermissionError):
    """Something a workspace in safe mode isn't allowed to do."""


def _norm(p: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(p))


def _inside(path: str, roots: Iterable[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _refuse(event: str) -> None:
    what = next((w for k, w in _WHAT.items() if event == k or (k.endswith(".") and event.startswith(k))),
                event)
    raise SafeModeError(f"{what} isn't allowed: a workspace from someone else runs in safe mode")


def install(workspace: Path, exempt_threads: Iterable[int] = ()) -> None:
    """Turn safe mode on for the rest of this process. Call it after the
    worker's own imports (numpy, the strategy) and before the workspace loads."""
    sys.dont_write_bytecode = True
    repo = Path(__file__).resolve().parents[2]
    # Reading is fine inside these: the workspace, Python and its packages,
    # and the strategy's own code (and sim/config.py, which it reads).
    read_roots = {_norm(workspace), _norm(repo / "strategy"), _norm(repo / "sim"),
                  _norm(sys.prefix), _norm(sys.base_prefix), _norm(sys.exec_prefix)}
    # Listing is fine there and in the import path's folders (imports list them).
    list_roots = read_roots | {_norm(p or ".") for p in sys.path}
    exempt = frozenset(exempt_threads)

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & _WRITE_FLAGS)
            if writes:
                _refuse("open")
            if isinstance(path, (str, bytes, os.PathLike)) and not _inside(_norm(os.fsdecode(path)), read_roots):
                _refuse("open")
            return
        if event in ("os.listdir", "os.scandir"):
            path = args[0] if args and args[0] is not None else "."
            if isinstance(path, (str, bytes, os.PathLike)) and not _inside(_norm(os.fsdecode(path)), list_roots):
                _refuse(event)
            return
        if any(event == r or (r.endswith(".") and event.startswith(r)) for r in _REFUSED):
            if threading.get_ident() in exempt:
                return  # the worker's own watchdog (ctypes on Windows)
            _refuse(event)

    sys.addaudithook(hook)


def safe_builtins(own_modules: Iterable[str]) -> dict[str, Any]:
    """Builtins for a workspace module in safe mode: import limited to the
    allowed modules and the workspace's own files."""
    own = frozenset(own_modules)
    real_import = builtins.__import__

    def guarded_import(name: str, globals: Any = None, locals: Any = None,
                       fromlist: Any = (), level: int = 0) -> Any:
        if level == 0 and not _allowed(name, own):
            raise ImportError(
                f"can't import {name}: a workspace from someone else runs in safe mode, which "
                + f"allows {', '.join(sorted(SAFE_MODULES))}, the strategy's api / tactics / "
                + "skills / world, and the workspace's own files", name=name)
        return real_import(name, globals, locals, fromlist, level)

    b = dict(builtins.__dict__)
    b["__import__"] = guarded_import
    b["input"] = no_input
    return b


def _allowed(name: str, own: frozenset[str]) -> bool:
    top = name.split(".")[0]
    if top in SAFE_MODULES or top in own:
        return True
    return name == "strategy" or any(name == s or name.startswith(s + ".") for s in SAFE_STRATEGY)


def no_input(*_args: Any) -> str:
    """input() in a team: there's no keyboard to read from, the worker's
    stdin is its channel to the strategy node."""
    raise RuntimeError("input() doesn't work in a team: nobody is there to type. "
                       + "Use world.memory for values, world.show() to see them")


def limit_memory_unix(max_bytes: int) -> None:
    """A memory cap on this process (Linux; macOS ignores it)."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    except (ImportError, ValueError, OSError):
        pass
