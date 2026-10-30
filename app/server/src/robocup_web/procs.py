"""Programs the web app starts for you: the simulator, the tracker, PhotonVision.

Each runs as a child process. Its output is kept (the last few hundred lines)
for the page to show, and stop() ends the whole process tree -- the simulator
and the tracker start children of their own, which a plain terminate() would
leave running.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any


class CommandError(ValueError):
    """A request the app cannot carry out: shown to whoever sent it."""


class ManagedProcess:
    def __init__(self, name: str, log_lines: int = 400) -> None:
        self.name = name
        self._proc: subprocess.Popen[str] | None = None
        self._log: deque[tuple[int, str]] = deque(maxlen=log_lines)
        self._log_n = 0
        self._log_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def launch(self, cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        if self.running:
            raise CommandError(f"{self.name} is already running")
        # Unbuffered, so lines arrive as they are printed; UTF-8, since the
        # tools print arrows and dashes and Windows would read them as cp1252.
        env = dict(env or os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        kw: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
            else {"start_new_session": True}
        )
        self._proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kw)
        self._add_log(f"$ {' '.join(cmd)}")
        threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()

    def on_line(self, line: str) -> None:
        """Called for each line of output, for subclasses that watch it."""

    def _read(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            self._add_log(line)
            self.on_line(line)
        self._add_log(f"[{self.name} exited with code {proc.wait()}]")

    def _add_log(self, line: str) -> None:
        with self._log_lock:
            self._log_n += 1
            self._log.append((self._log_n, line))

    def log_since(self, after: int) -> list[tuple[int, str]]:
        with self._log_lock:
            return [(n, s) for n, s in self._log if n > after]

    def stop(self, timeout: float = 5.0) -> None:
        """Ends the process and everything it started. Only ever one this
        app launched; a copy started some other way is left alone."""
        p = self._proc
        if p is None or p.poll() is not None:
            return
        # Windows has no process groups to signal, so taskkill walks the tree.
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()

    def process_status(self) -> dict[str, Any]:
        p = self._proc
        return {
            "running": self.running,
            "pid": p.pid if p is not None else None,
            "exit_code": None if p is None else p.poll(),
        }
