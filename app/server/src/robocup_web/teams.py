"""Workspaces for the Code page: students' own code for the team's slots.

A workspace is a folder in teams/ at the repo root (not committed: it's
yours) holding Python files; its team starts in team.py, and the other files
are its own modules (strategy/core/loader.py). The page lists, creates,
duplicates, renames and deletes workspaces and their files; checks one (does
it load, which slots does it replace); downloads one as a .zip and uploads
.zip / .py files (someone else's code, which then plays in safe mode:
strategy/core/safety.py); plays a match between two teams
(strategy/eval/match.py, as a child process) and keeps head-to-head
records; and the simulator plays one live (SimProcess, --blue/--red).

Nothing here runs a team inside the server: checking loads it in a
short-lived child process with a time limit, matches and the simulator run
it in theirs, so a team that loops forever can't hang the web app.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .procs import CommandError, ManagedProcess

REPO = Path(__file__).resolve().parents[4]
TEAMS_DIR = REPO / "teams"
EXAMPLES = REPO / "strategy" / "examples"
GUIDE = REPO / "strategy" / "docs" / "guide.md"  # the Code page's Guide tab
RESULTS = TEAMS_DIR / ".results.json"  # head-to-head records
ENTRY = "team.py"
META = "workspace.json"
WS_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
MAX_FILE_BYTES = 200_000
MAX_FILES = 50
MAX_UPLOAD_BYTES = 1_000_000
CHECK_TIMEOUT_S = 20.0
OPPONENTS = ("v2", "v1", "v0", "current")
# New workspace: where its files come from.
STARTS: dict[str, dict[str, Path | str]] = {
    "template": {ENTRY: EXAMPLES / "template.py"},
    "get_open": {ENTRY: EXAMPLES / "get_open.py"},
    "blank": {ENTRY: '"""My team."""\n\nfrom strategy.api import Command, Me, World\n\n\n'
                     "def supporter(me: Me, world: World) -> Command:\n"
                     "    return me.go_to(world.ball.pos, face=world.ball.pos)\n"},
}
_LOCATION = re.compile(r"\b([A-Za-z_]\w*\.py):(\d+)")
# The strategy's own rule for module file names (strategy/core/loader.py),
# copied: the server doesn't import the strategy.
_RESERVED = frozenset(sys.stdlib_module_names) | {"numpy", "strategy", "team", "pymunk", "zmq"}


def file_problem(name: str) -> str | None:
    """Why `name` can't be one of a workspace's files, or None if it can."""
    stem, dot, ext = name.rpartition(".")
    if not dot or ext != "py" or not stem.isidentifier():
        return "a file name is letters, digits and _, starting with a letter, ending in .py"
    if len(stem) > 40:
        return "a file name is at most 40 characters"
    if stem in _RESERVED and name != ENTRY:
        return f"{name} would hide Python's own {stem} module: pick another name"
    return None


class Conflict(CommandError):
    """A name that's taken: the page asks before replacing anything."""

    def __init__(self, message: str, taken: str, suggest: str):
        super().__init__(message)
        self.taken = taken
        self.suggest = suggest


def ws_path(name: str) -> Path:
    """A workspace's folder, checked so it can't point outside teams/."""
    if not WS_NAME.fullmatch(name or ""):
        raise CommandError("a workspace name is letters, digits, _ or -, at most 40 (e.g. anna_team)")
    return TEAMS_DIR / name


def team_path(name: str) -> Path:
    """The path the simulator and the match runner play for a workspace."""
    path = ws_path(name)
    if not (path / ENTRY).is_file():
        raise CommandError(f"no workspace {name} (or it has no {ENTRY})")
    return path


def file_path(ws: str, name: str) -> Path:
    problem = file_problem(name or "")
    if problem:
        raise CommandError(problem)
    return ws_path(ws) / name


def error_location(message: str) -> tuple[str | None, int | None]:
    """(file, line) an error message points at: "passing.py:12: ..." or "(team.py:4)"."""
    m = _LOCATION.search(message)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _free_name(base: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:36] or "team"
    name, n = base, 2
    while (TEAMS_DIR / name).exists():
        name, n = f"{base}_{n}", n + 1
    return name


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)  # whole file at once: a reload never sees half of it


def _child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class Workspaces:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.migrate()

    # -- workspaces -----------------------------------------------------------

    def migrate(self) -> None:
        """Single team files from before workspaces (teams/my_team.py) become
        workspaces (teams/my_team/team.py)."""
        if not TEAMS_DIR.exists():
            return
        shutil.rmtree(TEAMS_DIR / "__pycache__", ignore_errors=True)  # from single files' days
        for old in sorted(TEAMS_DIR.glob("*.py")):
            folder = TEAMS_DIR / _free_name(old.stem)
            folder.mkdir()
            old.replace(folder / ENTRY)
            self._write_meta(folder, {"origin": "own", "created": time.time()})

    def _meta(self, folder: Path) -> dict[str, Any]:
        try:
            return json.loads((folder / META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_meta(self, folder: Path, meta: dict[str, Any]) -> None:
        _write(folder / META, json.dumps(meta, indent=2).encode("utf-8"))

    def _info(self, folder: Path) -> dict[str, Any]:
        files = sorted(folder.glob("*.py"), key=lambda f: (f.name != ENTRY, f.name))
        meta = self._meta(folder)
        return {
            "name": folder.name,
            "files": [{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
                      for f in files],
            "modified": max([f.stat().st_mtime for f in files], default=folder.stat().st_mtime),
            "author": meta.get("author", ""),
            "safe": bool(meta.get("safe")),
            "origin": meta.get("origin", "own"),
            "from": meta.get("from", ""),
        }

    def list(self) -> list[dict[str, Any]]:
        if not TEAMS_DIR.exists():
            return []
        self.migrate()
        return [self._info(p) for p in sorted(TEAMS_DIR.iterdir())
                if p.is_dir() and WS_NAME.fullmatch(p.name) and not p.name.startswith("__")]

    def info(self, name: str) -> dict[str, Any]:
        folder = ws_path(name)
        if not folder.is_dir():
            raise CommandError(f"no workspace {name}")
        return self._info(folder)

    def create(self, name: str, start: str) -> dict[str, Any]:
        folder = ws_path(name)
        if folder.exists():
            raise Conflict(f"{name} already exists", name, _free_name(name))
        if start not in STARTS:
            raise CommandError(f"start from {', '.join(STARTS)}")
        folder.mkdir(parents=True)
        for file, source in STARTS[start].items():
            text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
            _write(folder / file, text.encode("utf-8"))
        self._write_meta(folder, {"origin": "own", "created": time.time()})
        return self._info(folder)

    def duplicate(self, name: str, new: str) -> dict[str, Any]:
        src, dst = ws_path(name), ws_path(new)
        if not src.is_dir():
            raise CommandError(f"no workspace {name}")
        if dst.exists():
            raise Conflict(f"{new} already exists", new, _free_name(new))
        dst.mkdir(parents=True)
        for f in src.glob("*.py"):
            shutil.copyfile(f, dst / f.name)
        meta = self._meta(src)  # a copy of someone else's code stays theirs: safe mode
        self._write_meta(dst, {**meta, "created": time.time()})
        return self._info(dst)

    def rename(self, name: str, new: str) -> dict[str, Any]:
        src, dst = ws_path(name), ws_path(new)
        if not src.is_dir():
            raise CommandError(f"no workspace {name}")
        if dst.exists():
            raise Conflict(f"{new} already exists", new, _free_name(new))
        src.rename(dst)
        return self._info(dst)

    def delete(self, name: str) -> None:
        folder = ws_path(name)
        if folder.is_dir():
            shutil.rmtree(folder)

    def set_author(self, name: str, author: str) -> dict[str, Any]:
        folder = ws_path(name)
        if not folder.is_dir():
            raise CommandError(f"no workspace {name}")
        meta = self._meta(folder)
        meta["author"] = author.strip()[:60]
        self._write_meta(folder, meta)
        return self._info(folder)

    # -- files ----------------------------------------------------------------

    def read_file(self, ws: str, name: str) -> dict[str, Any]:
        path = file_path(ws, name)
        if not path.is_file():
            raise CommandError(f"no file {name} in {ws}")
        return {"name": name, "text": path.read_text(encoding="utf-8"), "modified": path.stat().st_mtime}

    def save_file(self, ws: str, name: str, text: str) -> dict[str, Any]:
        path = file_path(ws, name)
        data = text.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise CommandError(f"that's over {MAX_FILE_BYTES // 1000} kB: split it into files")
        if not path.parent.is_dir():
            raise CommandError(f"no workspace {ws}")
        if not path.exists() and len(list(path.parent.glob("*.py"))) >= MAX_FILES:
            raise CommandError(f"a workspace holds at most {MAX_FILES} files")
        _write(path, data)
        return {"name": name, "modified": path.stat().st_mtime}

    def new_file(self, ws: str, name: str) -> dict[str, Any]:
        path = file_path(ws, name)
        if path.exists():
            raise CommandError(f"{name} already exists in {ws}")
        stem = name.removesuffix(".py")
        self.save_file(ws, name, f'"""{stem}: helpers for team.py (in team.py: from {stem} import ...)."""\n')
        return self._info(path.parent)

    def rename_file(self, ws: str, name: str, new: str) -> dict[str, Any]:
        src, dst = file_path(ws, name), file_path(ws, new)
        if ENTRY in (name, new):
            raise CommandError(f"{ENTRY} is where the team starts: it keeps its name")
        if not src.is_file():
            raise CommandError(f"no file {name} in {ws}")
        if dst.exists():
            raise CommandError(f"{new} already exists in {ws}")
        src.rename(dst)
        return self._info(src.parent)

    def delete_file(self, ws: str, name: str) -> dict[str, Any]:
        path = file_path(ws, name)
        if name == ENTRY:
            raise CommandError(f"{ENTRY} is where the team starts: delete the workspace instead")
        if path.exists():
            path.unlink()
        return self._info(path.parent)

    # -- checking ---------------------------------------------------------------

    def check(self, ws: str) -> dict[str, Any]:
        """Load it in a child process (in safe mode if it's someone else's):
        does it load, and which slots does it replace."""
        path = team_path(ws)
        try:
            r = subprocess.run([sys.executable, "-m", "strategy.core.worker", "--check", str(path)],
                               cwd=REPO, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S,
                               check=False, env=_child_env())
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"{ws} didn't finish loading in {CHECK_TIMEOUT_S:.0f} s "
                    + "(an endless loop outside a function?)", "file": None, "line": None}
        last = (r.stdout.strip().splitlines() or [""])[-1]
        try:
            out = json.loads(last)
        except json.JSONDecodeError:
            return {"ok": False, "error": (r.stderr or r.stdout).strip()[-500:] or "it didn't load",
                    "file": None, "line": None}
        out["file"], out["line"] = error_location(out.get("error", ""))
        # What it printed while loading (a print at the top of a file).
        out["printed"] = r.stderr.strip().splitlines()[-20:] if r.stderr.strip() else []
        return out

    # -- sharing ----------------------------------------------------------------

    def download(self, ws: str) -> bytes:
        """The workspace as a .zip: its folder, its .py files, workspace.json."""
        folder = ws_path(ws)
        if not folder.is_dir():
            raise CommandError(f"no workspace {ws}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.glob("*.py")):
                z.write(f, f"{ws}/{f.name}")
            meta = self._meta(folder)
            z.writestr(f"{ws}/{META}", json.dumps({
                "name": ws, "author": meta.get("author", ""), "exported": time.time(),
                "from": meta.get("from", ""),
            }, indent=2))
        return buf.getvalue()

    def upload(self, files: list[dict[str, str]], into: str | None, as_name: str | None,
               replace: bool) -> dict[str, Any]:
        """Someone else's code in: a .zip (a whole workspace) or .py files
        (into the workspace `into`, or a new one). Whatever arrives this way
        plays in safe mode. A taken name raises Conflict unless `replace`."""
        if not files:
            raise CommandError("nothing to upload")
        decoded: list[tuple[str, bytes]] = []
        total = 0
        for f in files:
            try:
                data = base64.b64decode(f.get("data", ""), validate=True)
            except ValueError:
                raise CommandError(f"{f.get('name')}: couldn't read the upload") from None
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise CommandError(f"that's over {MAX_UPLOAD_BYTES // 1_000_000} MB: a team is a few files")
            decoded.append((Path(str(f.get("name", ""))).name, data))

        with self._lock:
            if len(decoded) == 1 and decoded[0][0].lower().endswith(".zip"):
                return self._upload_zip(decoded[0][0], decoded[0][1], as_name, replace)
            for name, _ in decoded:
                if not name.endswith(".py"):
                    raise CommandError(f"{name}: upload .py files or one .zip")
            return self._upload_py(decoded, into, as_name, replace)

    def _upload_zip(self, zip_name: str, data: bytes, as_name: str | None, replace: bool) -> dict[str, Any]:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise CommandError(f"{zip_name} isn't a zip file") from None
        entries = [i for i in z.infolist() if not i.is_dir()]
        if len(entries) > MAX_FILES + 5:
            raise CommandError(f"{zip_name} has over {MAX_FILES} files")
        files: dict[str, bytes] = {}
        meta: dict[str, Any] = {}
        folders = set()
        for info in entries:
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if "__MACOSX" in parts or parts[-1].startswith("."):
                continue
            if ".." in parts or info.filename.startswith(("/", "\\")) or len(parts) > 2:
                raise CommandError(f"{zip_name}: {info.filename} isn't a file of one workspace")
            if len(parts) == 2:
                folders.add(parts[0])
            name = parts[-1]
            if info.file_size > MAX_FILE_BYTES:
                raise CommandError(f"{zip_name}: {name} is over {MAX_FILE_BYTES // 1000} kB")
            if name == META:
                try:
                    meta = json.loads(z.read(info).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    meta = {}
                continue
            if not name.endswith(".py"):
                continue  # README.md and the like: not needed to play
            problem = file_problem(name)
            if problem:
                raise CommandError(f"{zip_name}: {problem} ({name})")
            files[name] = z.read(info)
        if ENTRY not in files:
            raise CommandError(f"{zip_name} has no {ENTRY}: it isn't a workspace")
        base = as_name or (next(iter(folders)) if len(folders) == 1 else Path(zip_name).stem)
        return self._new_upload(base, files, meta, replace)

    def _upload_py(self, decoded: list[tuple[str, bytes]], into: str | None, as_name: str | None,
                   replace: bool) -> dict[str, Any]:
        one_new_team = len(decoded) == 1 and not into  # becomes team.py: any name will do
        for name, _ in decoded:
            problem = None if one_new_team else file_problem(name)
            if problem:
                raise CommandError(f"{problem} ({name})")
        if into:  # into an existing workspace: it now holds someone else's code
            folder = ws_path(into)
            if not folder.is_dir():
                raise CommandError(f"no workspace {into}")
            taken = [n for n, _ in decoded if (folder / n).exists()]
            if taken and not replace:
                raise Conflict(f"{', '.join(taken)} already in {into}", taken[0], "")
            if len(list(folder.glob("*.py"))) + len(decoded) - len(taken) > MAX_FILES:
                raise CommandError(f"a workspace holds at most {MAX_FILES} files")
            for name, data in decoded:
                _write(folder / name, data)
            meta = self._meta(folder)
            self._write_meta(folder, {**meta, "safe": True, "from": meta.get("from") or "uploaded files"})
            return self._info(folder)
        if one_new_team:
            name, data = decoded[0]
            return self._new_upload(as_name or name.removesuffix(".py"), {ENTRY: data},
                                    {"from": name}, replace)
        files = dict(decoded)
        if ENTRY not in files:
            raise CommandError(f"which file is the team? A workspace starts in {ENTRY}: "
                               + "upload one file, or a set with a team.py")
        return self._new_upload(as_name or "uploaded", files, {}, replace)

    def _new_upload(self, base: str, files: dict[str, bytes], meta: dict[str, Any],
                    replace: bool) -> dict[str, Any]:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:40] or "uploaded"
        folder = ws_path(name)
        if folder.exists():
            if not replace:
                raise Conflict(f"there's already a workspace {name}", name, _free_name(name))
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for file, data in files.items():
            _write(folder / file, data)
        self._write_meta(folder, {
            "origin": "uploaded", "safe": True, "created": time.time(),
            "author": str(meta.get("author", ""))[:60],
            "from": str(meta.get("from") or meta.get("name") or base)[:60],
        })
        return self._info(folder)

    # -- head to head -------------------------------------------------------------

    def results(self, ws: str) -> list[dict[str, Any]]:
        """This workspace's matches, newest first, from its side."""
        out = []
        for r in reversed(_read_results()):
            if r["a"] == ws:
                out.append(r)
            elif r["b"] == ws:
                out.append({**r, "a": r["b"], "b": r["a"], "wins": r["losses"], "losses": r["wins"],
                            "goals": [r["goals"][1], r["goals"][0]]})
        return out[:50]


def _read_results() -> list[dict[str, Any]]:
    try:
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


class MatchProcess(ManagedProcess):
    """strategy/eval/match.py: one team against another (workspaces, or the
    built-in teams v1 / v0 / current)."""

    def __init__(self) -> None:
        super().__init__("match")
        self.result: dict[str, Any] | None = None
        self.request: dict[str, Any] = {}
        self._watchdog: threading.Thread | None = None

    @staticmethod
    def _side(name: str) -> str:
        return name if name in OPPONENTS else str(team_path(name))

    def start(self, a: str, b: str, games: int, duration_s: float) -> None:
        path_a, path_b = self._side(a), self._side(b)
        if not 1 <= games <= 40:
            raise CommandError("1 to 40 games")
        if not 5 <= duration_s <= 600:
            raise CommandError("5 to 600 seconds a game")
        self.result = None
        self.request = {"a": a, "b": b, "games": games, "duration": duration_s}
        self.launch([sys.executable, "-m", "strategy.eval.match", "--a", path_a, "--b", path_b,
                     "--games", str(games), "--duration", f"{duration_s:g}", "--json"], REPO)
        # The whole match gets a time limit: a team stuck in a loop stops it.
        limit = 90 + games * duration_s
        self._watchdog = threading.Thread(target=self._time_limit, args=(limit,), daemon=True)
        self._watchdog.start()

    def _time_limit(self, limit_s: float) -> None:
        end = time.monotonic() + limit_s
        while self.running and time.monotonic() < end:
            time.sleep(0.5)
        if self.running:
            self._add_log(f"[stopped: the match took over {limit_s:.0f} s -- "
                          + "is a team stuck in a loop?]")
            self.stop()

    def on_line(self, line: str) -> None:
        if line.startswith("RESULT "):
            try:
                self.result = json.loads(line[len("RESULT "):])
            except json.JSONDecodeError:
                return
            self._record(self.result)

    def _record(self, r: dict[str, Any]) -> None:
        """Keep the result for the head-to-head records."""
        if not TEAMS_DIR.exists():
            return
        records = _read_results()
        records.append({"a": self.request.get("a"), "b": self.request.get("b"), "when": time.time(),
                        "games": r["games"], "wins": r["wins"], "draws": r["draws"],
                        "losses": r["losses"], "goals": r["goals"]})
        _write(RESULTS, json.dumps(records[-500:]).encode("utf-8"))

    def status(self, after: int = 0) -> dict[str, Any]:
        return {**self.process_status(), "request": self.request, "result": self.result,
                "lines": [{"n": n, "text": s} for n, s in self.log_since(after)
                          if not s.startswith("RESULT ")]}
