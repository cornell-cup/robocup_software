"""Run a team (a workspace or a file) in its own process, so an endless loop
or a crash in it can't stop the strategy, and so a classmate's code can run
in safe mode (core/safety.py).

The strategy node (and the match runner) keeps a TeamWorker per team. It
starts `python -m strategy.core.worker PATH COLOR` and talks to it over the
worker's stdin and stdout, one JSON line per frame:

    node -> worker   {"seq": n, "raw": world state, "kicked_off": bool,
                      "phase": str, "human": [ids], "lost": [ids],
                      "color": "blue" | "red", "game_events": [[seq, event], ...]}
    worker -> node   {"hello": true, "events": [...]} once, when the team has loaded
    worker -> node   {"seq": n, "targets": {id: [x, y, heading]}, "kicks": [ids],
                      "attackers": [ids], "dribble": [ids], "max_speed": {id: m/s},
                      "draw": [shapes], "show": {who: {name: value}},
                      "errors": [...], "events": [log lines]}

The worker plays its colour with decide() and the team's slots, exactly as
in-process (a slot that fails falls back to the built-in one, with the
error); it reloads the team when a file is saved and says what happened in
"events". Anything the team prints goes to the node's log, not into the
messages.

Live, the node waits ANSWER_TIMEOUT_S for each answer; a late or missing one
means that colour plays the built-in team for the frame, and after
RESTART_AFTER_S without an answer the worker is killed and started again. In
lockstep (the match runner) it waits for every answer, so a match plays the
same every time, and gives up on a team that hangs MAX_RESTARTS times.

A worker exits by itself when the node goes away, even mid endless loop: a
watchdog thread watches the node's process, and on Windows a job object with
a memory cap kills it with the node. `python -m strategy.core.worker --check
PATH` loads a team once and prints whether it loads (the web app's check).
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ANSWER_TIMEOUT_S = 0.020  # per frame; a frame is 1/60 s
# After a missed answer the node only glances (doesn't wait: on Windows the
# shortest wait is ~16 ms) until the worker answers again, so a stuck worker
# doesn't slow every frame; an answer that arrived meanwhile, up to
# STALE_FRAMES old, is still used.
GLANCE_S = 0.0
STALE_FRAMES = 2
SLOW_AFTER_S = 0.25  # no answer this long: say so (a reload after a save can skip a few frames)
RESTART_AFTER_S = 0.5  # no answer this long: kill and restart the worker
STARTUP_GRACE_S = 10.0  # a (re)started worker gets this long to import and load
RESTART_BACKOFF_S = 1.0  # at most one restart per this long
LOCKSTEP_TIMEOUT_S = 0.5  # lockstep: an answer later than this means it's stuck
MAX_RESTARTS = 3  # lockstep: after this many, the team plays built-in for the rest
MEMORY_LIMIT = 1 << 30  # bytes a worker may use (1 GB)

_REPO = Path(__file__).resolve().parents[2]


class TeamWorker:
    """The node's side: one worker process for one team. `color` can change
    between frames (the match runner swaps sides between games)."""

    def __init__(self, path: str | os.PathLike[str], color: str,
                 log: Callable[[str], None] = print,
                 timeout_s: float = ANSWER_TIMEOUT_S, restart_after_s: float = RESTART_AFTER_S,
                 startup_grace_s: float = STARTUP_GRACE_S, lockstep: bool = False):
        self.path = Path(path).resolve()
        self.color = color
        self.log = log
        self.timeout_s = timeout_s
        self.restart_after_s = restart_after_s
        self.startup_grace_s = startup_grace_s
        self.lockstep = lockstep
        self._proc: subprocess.Popen[str] | None = None
        self._job: Any = None  # Windows: the job object holding the memory cap
        self._answers: queue.Queue[dict[str, Any]] = queue.Queue()
        self._outbox: queue.Queue[str] | None = None
        self._seq = 0
        self._last_answer = 0.0
        self._started = 0.0
        self._last_restart = -1e9
        self.restarts = 0
        self.gave_up = False  # lockstep: hung too often; plays built-in from now on
        self.ready = False  # loaded (said hello) since the last (re)start
        self.pid: int | None = None  # the worker's own process (its hello says)
        self._missed = False  # the last frame got no answer
        # Game events (World.events) not yet confirmed by an answer, with the
        # request they first went out with. Only the newest request is ever
        # sent, so each one carries all of these until the worker has seen
        # them; the worker skips the ones it already had.
        self._unconfirmed: list[tuple[int, str]] = []
        self._start()
        atexit.register(self.close)

    # -- the process ------------------------------------------------------------

    def _start(self) -> None:
        env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "strategy.core.worker", str(self.path), self.color,
             "--node", str(os.getpid())],
            cwd=_REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", bufsize=1, env=env,
        )
        self._job = _limit_memory(self._proc, MEMORY_LIMIT)
        self._answers = queue.Queue()
        self._outbox = queue.Queue(maxsize=1)
        self._started = self._last_answer = time.monotonic()
        self.ready = False
        self._missed = False
        heard = threading.Event()  # the worker has answered the request in flight
        heard.set()
        threading.Thread(target=self._read, args=(self._proc, self._answers, heard), daemon=True).start()
        threading.Thread(target=self._write, args=(self._proc, self._outbox, heard), daemon=True).start()

    @staticmethod
    def _read(proc: subprocess.Popen[str], answers: queue.Queue[dict[str, Any]],
              heard: threading.Event) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                answers.put(json.loads(line))
            except json.JSONDecodeError:
                pass
            heard.set()

    @staticmethod
    def _write(proc: subprocess.Popen[str], outbox: queue.Queue[str], heard: threading.Event) -> None:
        # Its own thread: writing to a worker stuck in a loop blocks, and that
        # must never block the node. One request in flight at a time, always
        # the newest: a worker that's loading or slow never finds a backlog of
        # old frames to work through.
        assert proc.stdin is not None
        while True:
            heard.wait()
            line = outbox.get()
            heard.clear()
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError):
                return

    def _restart(self, why: str) -> None:
        now = time.monotonic()
        if not self.lockstep and now - self._last_restart < RESTART_BACKOFF_S:
            return
        self._last_restart = now
        self.restarts += 1
        if self.lockstep and self.restarts > MAX_RESTARTS:
            self.gave_up = True
            self.log(f"[Strategy node] {self.color} team {self.path.name}: {why}; it hung "
                     + f"{MAX_RESTARTS} times, so the built-in team plays for it from now on")
            self.close()
            return
        self.log(f"[Strategy node] {self.color} team {self.path.name}: {why}; restarting it")
        self.close()
        self._start()

    def close(self) -> None:
        """Stop the worker, all of it: on Windows a venv's python.exe is a
        launcher running the real Python as its child, and killing only the
        launcher would leave a team stuck in a loop running. Its job object
        holds both, so ending the job ends them."""
        job, self._job = self._job, None
        if job is not None:
            _end_job(job)
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    # -- one frame ----------------------------------------------------------------

    @property
    def stalled(self) -> bool:
        """It answered before but has been quiet a while: worth telling the
        student (a few missed frames during a reload aren't)."""
        return self.ready and time.monotonic() - self._last_answer > SLOW_AFTER_S

    def plan(self, raw: dict[str, Any], kicked_off: bool, phase: str,
             human: set[int], lost: set[int], events: list[str] | None = None,
             ) -> dict[str, Any] | None:
        """This frame's answer for the colour, or None (late, stuck, crashed):
        then the colour plays the built-in team this frame. `events`: Brain's
        game events this frame ("goal_blue", "kickoff", ...)."""
        assert self._outbox is not None
        self._seq += 1
        self._unconfirmed += [(self._seq, e) for e in events or []]
        if self.gave_up:
            return None
        assert self._proc is not None
        if self._proc.poll() is not None:
            self._restart(f"stopped (exit code {self._proc.returncode})")
            return None
        line = json.dumps({"seq": self._seq, "raw": raw, "kicked_off": kicked_off, "phase": phase,
                           "human": sorted(human), "lost": sorted(lost), "color": self.color,
                           "game_events": self._unconfirmed}) + "\n"
        try:  # only the newest request matters
            self._outbox.get_nowait()
        except queue.Empty:
            pass
        self._outbox.put_nowait(line)
        return self._wait_lockstep() if self.lockstep else self._wait_live()

    def _take(self, answer: dict[str, Any]) -> None:
        for event in answer.get("events", []):
            self.log(f"[Strategy node] {event}")
        self._last_answer = time.monotonic()
        self.ready = True
        if answer.get("hello"):
            self.pid = answer.get("pid")
        if "seq" in answer:
            seen = int(answer["seq"])
            self._unconfirmed = [(s, e) for s, e in self._unconfirmed if s > seen]

    def _wait_live(self) -> dict[str, Any] | None:
        newest: dict[str, Any] | None = None
        deadline = time.monotonic() + (GLANCE_S if self._missed else self.timeout_s)
        while (remaining := deadline - time.monotonic()) > 0 or not self._answers.empty():
            try:
                answer = self._answers.get(timeout=max(remaining, 0.0))
            except queue.Empty:
                break
            self._take(answer)
            newest = answer
            if answer.get("seq") == self._seq:
                break
        if newest is not None and newest.get("seq", 0) >= self._seq - STALE_FRAMES:
            self._missed = False
            return newest
        self._missed = True

        now = time.monotonic()
        silent = now - self._last_answer
        if self.ready and silent > self.restart_after_s:
            self._restart(f"no answer for {silent:.1f} s (an endless loop?)")
        elif not self.ready and now - self._started > self.startup_grace_s:
            self._restart(f"didn't load in {self.startup_grace_s:.0f} s")
        return None

    def _wait_lockstep(self) -> dict[str, Any] | None:
        """Wait for this frame's own answer: a match plays the same every time."""
        deadline = time.monotonic() + (LOCKSTEP_TIMEOUT_S if self.ready else self.startup_grace_s)
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                answer = self._answers.get(timeout=remaining)
            except queue.Empty:
                break
            self._take(answer)
            if answer.get("seq") == self._seq:
                return answer
            if answer.get("hello"):  # loaded: from now on a late answer means it's stuck
                deadline = min(deadline, time.monotonic() + LOCKSTEP_TIMEOUT_S)
        if self.ready:
            self._restart(f"no answer in {LOCKSTEP_TIMEOUT_S:.1f} s (an endless loop?)")
        else:
            self._restart(f"didn't load in {self.startup_grace_s:.0f} s")
        return None


def _limit_memory(proc: subprocess.Popen[str], max_bytes: int) -> Any:
    """Windows: put the worker in a job object with a memory cap, which also
    kills it when the node exits. Returns the job (kept open), or None."""
    if os.name != "nt":
        return None  # the worker caps itself (safety.limit_memory_unix)
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits),
                        ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimits()
        # JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x100 | 0x2000
        info.ProcessMemoryLimit = max_bytes
        ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9: extended limits
        if not ok or not k32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            k32.CloseHandle(job)
            return None
        return job
    except (OSError, AttributeError, ValueError):
        return None


def _end_job(job: Any) -> None:
    """End every process in a worker's job object, and close it."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.TerminateJobObject(job, 1)
        k32.CloseHandle(job)
    except (OSError, AttributeError, ValueError):
        pass


# -- the worker's side ---------------------------------------------------------------


def _exit_with_parent(node: int | None = None) -> None:
    """Exit when the node is gone. The worker's main thread may be stuck in
    the team's endless loop, so stdin closing alone wouldn't be seen; this
    thread still runs (Python switches threads every few ms). `node`: the
    node's process id (its parent may be a venv launcher in between)."""
    parent = node or os.getppid()
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
        if not handle:
            return  # can't watch it: stdin closing still ends a worker that isn't stuck
        while kernel32.WaitForSingleObject(handle, 500) != 0:  # 0: the node exited
            pass
    else:
        while _alive(parent):
            time.sleep(0.5)
    os._exit(0)


def _alive(pid: int) -> bool:
    """POSIX only: on Windows os.kill(pid, 0) would end the process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prepare(path: str) -> tuple[bool, Callable[..., Any]]:
    """The process before the team's code loads: the strategy imported, and
    safe mode on if the workspace came from someone else. (safe?, decide)"""
    sys.dont_write_bytecode = True
    from strategy.core import safety
    from strategy.core.loader import is_safe_mode, workspace_files
    from strategy.core.team import decide

    safety.limit_memory_unix(MEMORY_LIMIT)
    safe = is_safe_mode(path)
    if safe:
        import numpy  # noqa: F401 -- before safe mode: its import opens files and DLLs

        watchdog = [t.ident for t in threading.enumerate() if t.name == "watchdog" and t.ident]
        safety.install(workspace_files(path)[0], exempt_threads=watchdog)
    return safe, decide


def check(path: str) -> dict[str, Any]:
    """Load the team once: does it load, which slots it replaces (the web
    app's check, in a process of its own)."""
    protocol = sys.stdout
    sys.stdout = sys.stderr
    safe, _ = _prepare(path)
    from strategy.core.loader import TeamFileError, load_team, replaced_slots

    try:
        team = load_team(path, safe)
        out: dict[str, Any] = {"ok": True, "slots": replaced_slots(team), "safe": safe}
    except TeamFileError as e:
        out = {"ok": False, "error": str(e), "safe": safe}
    protocol.write(json.dumps(out) + "\n")
    protocol.flush()
    return out


def _newest_requests() -> Iterator[str]:
    """The node's requests, newest first: if several are waiting (the worker
    was loading, or a frame ran long), the older ones are skipped, so the
    worker never works through a backlog of frames nobody wants any more.
    Nothing is lost: each request repeats the game events not yet confirmed.
    (In lockstep the node waits for each answer, so there is never a backlog.)"""
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in sys.stdin:
            lines.put(line)
        lines.put(None)  # stdin closed: the node is gone

    threading.Thread(target=read, daemon=True, name="stdin").start()
    while True:
        line = lines.get()
        while line is not None and not lines.empty():
            newer = lines.get_nowait()
            if newer is None:
                break
            line = newer
        if line is None:
            return
        yield line


def _warm_up(decide: Callable[..., Any], build_game_state: Callable[..., Any], color: str) -> None:
    """One practice frame of the built-in team before saying hello: the first
    frame imports and warms up numpy and the strategy, slow enough to look
    like a stuck worker. The team's own code doesn't run here."""
    from strategy.world.state import (
        FIELD_LENGTH,
        FIELD_WIDTH,
        TEAM_BLUE_SIZE,
        TEAM_RED_SIZE,
    )

    robots = {}
    for i in range(TEAM_BLUE_SIZE + TEAM_RED_SIZE):
        x = FIELD_LENGTH * (0.2 + 0.3 * (i % 3))
        y = FIELD_WIDTH * (0.25 if i < TEAM_BLUE_SIZE else 0.75)
        robots[str(i)] = {"x": x, "y": y, "angle": 0.0, "vx": 0.0, "vy": 0.0, "omega": 0.0}
    raw = {"t": 0.0, "robots": robots,
           "ball": {"x": FIELD_LENGTH / 2, "y": FIELD_WIDTH / 2, "vx": 1.0, "vy": 0.3}}
    try:
        decide(build_game_state(raw, color), None, False, {}, True, colors=(color,))
    except Exception:  # noqa: BLE001, S110 -- only a warm-up: real frames report real problems
        pass


def main(argv: list[str]) -> None:
    if len(argv) >= 3 and argv[1] == "--check":
        check(argv[2])
        return
    path, color = argv[1], argv[2]
    node = int(argv[argv.index("--node") + 1]) if "--node" in argv else None
    threading.Thread(target=_exit_with_parent, args=(node,), daemon=True, name="watchdog").start()
    protocol = sys.stdout
    sys.stdout = sys.stderr  # the team's print()s go to the node's log

    safe, decide = _prepare(path)
    from strategy.core.loader import TeamFile, replaced_slots
    from strategy.core.team import Extras
    from strategy.world.state import TEAM_BLUE_SIZE, TEAM_RED_SIZE, build_game_state

    team_file = TeamFile(path, safe)

    def describe() -> str:
        name = team_file.path.name
        mode = " (safe mode: it's from someone else)" if safe else ""
        if team_file.error:
            keeps = "keeping the last version that loaded" if team_file.team else "playing the built-in team"
            return f"{color} team {name}: {team_file.error} ({keeps})"
        assert team_file.team is not None
        return (f"{color} plays {name}: {', '.join(replaced_slots(team_file.team))} replaced, "
                + f"the rest built-in{mode}")

    _warm_up(decide, build_game_state, color)
    protocol.write(json.dumps({"hello": True, "pid": os.getpid(), "events": [describe()]}) + "\n")
    protocol.flush()
    events: list[str] = []
    memory: dict[str, dict[str, Any]] = {}
    sticky: dict[str, int] = {}
    play_state: dict[str, dict[str, Any]] = {}
    events_seen = 0  # the newest request whose game events were played
    for frames, line in enumerate(_newest_requests(), start=1):
        request = json.loads(line)
        if request.get("color", color) != color:  # the match runner swapped sides
            color = request["color"]
            memory.clear()
            sticky.clear()
            play_state.clear()
            events.append(describe())
        ours = set(range(TEAM_BLUE_SIZE)) if color == "blue" else \
            set(range(TEAM_BLUE_SIZE, TEAM_BLUE_SIZE + TEAM_RED_SIZE))
        if frames % 30 == 0 and team_file.reload_if_changed():  # twice a second at 60 Hz
            events.append(describe())
        gamestate = build_game_state(request["raw"], color)
        gamestate.game_phase = request.get("phase", gamestate.game_phase)
        errors: list[str] = []
        game_events = [e for s, e in request.get("game_events", []) if s > events_seen]
        events_seen = max([events_seen] + [s for s, _ in request.get("game_events", [])])
        if "new_game" in game_events:
            memory.clear()  # as Brain does on a sim restart
            sticky.clear()
            play_state.clear()
        extras = Extras()
        teams = {color: team_file.team} if team_file.team is not None else {}
        targets, kicks, attackers = decide(
            gamestate, None, False, sticky, bool(request["kicked_off"]),
            set(request["human"]), set(request["lost"]),
            teams=teams, memory=memory, errors=errors, events=game_events, extras=extras,
            colors=(color,), play_state=play_state,
        )
        answer = {
            "seq": request["seq"],
            "targets": {str(rid): list(t) for rid, t in targets.items() if rid in ours},
            "kicks": [rid for rid in kicks if rid in ours],
            "attackers": sorted(a for a in attackers if a in ours),
            "dribble": sorted(r for r in extras.dribble if r in ours),
            "max_speed": {str(r): s for r, s in extras.max_speed.items() if r in ours},
            "draw": extras.drawings.get(color, []),
            "show": extras.shown.get(color, {}),
            "errors": errors,
            "events": events,  # log lines: what the team plays, load errors
        }
        events = []
        protocol.write(json.dumps(answer) + "\n")
        protocol.flush()


if __name__ == "__main__":
    main(sys.argv)
