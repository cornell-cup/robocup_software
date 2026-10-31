"""Starting and stopping PhotonVision from the web app, so nobody has to type
`java -jar ...` in the right folder with the right Java.

PhotonVision lives in its own folder: the jar, and a Java to run it with (a
Java whose bundled MSVC runtime is new enough -- some, Oracle's JDK 21 among
them, ship one too old and PhotonVision refuses to start). Its settings are
saved in a photonvision_config folder beside the jar, so it is always started
from that folder.

Which folder: --photonvision-dir, else $ROBOCUP_PHOTONVISION_DIR, else a
`photonvision` folder next to the repo.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import time
from importlib import metadata
from pathlib import Path
from typing import Any

from .procs import CommandError, ManagedProcess
from .pv_install import (Installer, builds_from_source, local_java, no_download_reason,
                         photonlibpy_version)
from .sim import REPO

UI_PORT = 5800
ENV_DIR = "ROBOCUP_PHOTONVISION_DIR"
#: Lines in PhotonVision's output that mean it will not come up, and why.
KNOWN_FAILURES = {
    "Invalid MSVC Runtime": "this Java's bundled MSVC runtime is too old for PhotonVision -- "
                            "put a Temurin 21 JRE in the PhotonVision folder",
    "Address already in use": "another PhotonVision (or something else) already uses its ports",
}
#: Setup PhotonVision still needs, read off its log: (line that reports the
#: setting, pattern meaning it is not done yet, what to do). The newest line of
#: each kind wins, so fixing it in PhotonVision's UI clears the hint.
SETUP_CHECKS = [
    ("Starting NT Client", "server team is 0",
     "its NetworkTables server address is not set: in its UI, Settings > Networking, "
     "set it to 127.0.0.1 (this machine)"),
    ("active VisionModules", "Started 0 active VisionModules",
     "no camera is enabled: in its UI, open Cameras and activate the camera"),
]
#: Later lines that settle one of those checks: activating a camera in its UI
#: logs only the request, never a fresh "active VisionModules" count.
SETUP_SETTLED_BY = {"/api/utils/activateMatchedCamera": "active VisionModules"}


def default_dir() -> Path:
    env = os.environ.get(ENV_DIR)
    return Path(env) if env else REPO.parent / "photonvision"


def _version(jar: Path) -> str | None:
    m = re.search(r"photonvision-v?([\d.]+[\w.-]*?)(?:-(?:win|linux|mac)\w*)?\.jar$", jar.name)
    return m.group(1) if m else None


def _ui_up(host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, UI_PORT), timeout=0.25):
            return True
    except OSError:
        return False


class PhotonVisionProcess(ManagedProcess):
    def __init__(self, folder: Path | None = None) -> None:
        super().__init__("PhotonVision", log_lines=300)
        self.folder = Path(folder) if folder else default_dir()
        self._failure: str | None = None
        self._setup_lines: dict[str, str] = {}
        self._up_cache = (0.0, False)
        self.installer = Installer()

    # -- finding it ---------------------------------------------------------

    def _jar(self) -> Path | None:
        """The jar matching photonlibpy's version if there is one, else the newest."""
        jars = sorted(self.folder.glob("photonvision-*.jar"), key=lambda p: p.stat().st_mtime)
        if os.name == "nt":
            jars = [j for j in jars if "win" in j.name] or jars
        try:
            wanted = photonlibpy_version()
            jars = [j for j in jars if _version(j) == wanted] or jars
        except CommandError:
            pass
        return jars[-1] if jars else None

    def _java(self) -> Path | None:
        """A Java inside the PhotonVision folder first (the one known to work
        with it), else whatever `java` is on PATH."""
        local = local_java(self.folder)
        if local:
            return local
        found = shutil.which("java")
        return Path(found) if found else None

    # -- state --------------------------------------------------------------

    def ui_up(self) -> bool:
        t, up = self._up_cache
        if time.monotonic() - t > 1.0:
            up = _ui_up()
            self._up_cache = (time.monotonic(), up)
        return up

    def status(self) -> dict[str, Any]:
        jar, java = self._jar(), self._java()
        up = self.ui_up()
        try:
            lib = metadata.version("photonlibpy")
        except metadata.PackageNotFoundError:
            lib = None
        version = _version(jar) if jar else None
        proc = self.process_status()
        return {
            "folder": str(self.folder),
            "installed": jar is not None and java is not None,
            "jar": jar.name if jar else None,
            "java": str(java) if java else None,
            "java_bundled": bool(java and self.folder in java.parents),
            "version": version,
            "photonlibpy": lib,
            "version_mismatch": bool(version and lib and version != lib),
            "running": proc["running"],
            "ours": proc["pid"] is not None,
            "exit_code": proc["exit_code"],
            # PhotonVision answering on its UI port, whoever started it
            "up": up,
            # running: ours and answering; external: answering, started elsewhere
            "state": ("running" if up and self.running else "external" if up
                      else "starting" if self.running else "stopped"),
            "failure": self._failure,
            "setup_hints": [hint for key, bad, hint in SETUP_CHECKS
                            if bad in self._setup_lines.get(key, "")],
            # Download offered when the right jar or a Java of its own is missing.
            "needs_download": not (version and lib and version == lib and local_java(self.folder)),
            # Set on a machine with no PhotonVision build: nothing to offer.
            "no_download": no_download_reason(),
            # macOS: the "download" builds it from source, and takes far longer.
            "from_source": builds_from_source(),
            "install": self.installer.status(),
            "ui": f"http://localhost:{UI_PORT}",
        }

    # -- control ------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            raise CommandError("PhotonVision is already starting or running")
        if self.ui_up():
            raise CommandError(f"PhotonVision is already running (something answers on port {UI_PORT})")
        jar, java = self._jar(), self._java()
        if jar is None:
            raise CommandError(f"no photonvision-*.jar in {self.folder} -- download it there, or "
                               f"point --photonvision-dir / {ENV_DIR} at its folder")
        if java is None:
            raise CommandError(f"no Java found: put a JRE in {self.folder} or install Java")
        self._failure = None
        self._setup_lines = {}
        # Started from its own folder: its settings live in photonvision_config there.
        self.launch([str(java), "-jar", jar.name], self.folder)

    def install(self) -> None:
        """Download the missing pieces into the folder, in the background."""
        if self.running:
            raise CommandError("stop PhotonVision before downloading it again")
        self.installer.start(self.folder)

    def on_line(self, line: str) -> None:
        for needle, why in KNOWN_FAILURES.items():
            if needle in line and self._failure is None:
                self._failure = why
        for key, _bad, _hint in SETUP_CHECKS:
            if key in line:
                self._setup_lines[key] = line
        for needle, key in SETUP_SETTLED_BY.items():
            if needle in line:
                self._setup_lines[key] = line

    def stop(self, timeout: float = 10.0) -> None:
        super().stop(timeout)
        self._up_cache = (0.0, False)
