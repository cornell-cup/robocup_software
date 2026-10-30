"""Downloading PhotonVision into its folder, so nobody installs it by hand.

    uv run setup-photonvision                 # into the folder the app uses
    uv run setup-photonvision --dir D:/pv     # somewhere else

Two things go in the folder (see photonvision.py for which folder):

- the PhotonVision jar of the same version as the photonlibpy we talk to it
  with (they refuse each other otherwise), from PhotonVision's GitHub releases;
- a Java to run it, Eclipse Temurin 21 (a JRE, from Adoptium). Not whatever
  Java is installed: some (Oracle's JDK 21) bundle an MSVC runtime too old for
  PhotonVision's native code, and it will not start.

Too big for git (the jar alone is over GitHub's 100 MB file limit), hence this.
Anything already there is kept: a jar of the right version, or a Java in the
folder, is not downloaded again.
"""

from __future__ import annotations

import argparse
import os
import platform
import tarfile
import threading
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any

from .procs import CommandError

JAR_URL = ("https://github.com/PhotonVision/photonvision/releases/download/"
           "v{version}/photonvision-v{version}-{platform}.jar")
JRE_URL = "https://api.adoptium.net/v3/binary/latest/21/ga/{os}/{arch}/jre/hotspot/normal/eclipse"
CHUNK = 1 << 16


def target_platform() -> tuple[str, str, str]:
    """(PhotonVision's jar platform, Adoptium's os, Adoptium's arch) for this machine."""
    system = platform.system()
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if system == "Windows" and not arm:
        return "winx64", "windows", "x64"
    if system == "Linux":
        return ("linuxarm64", "linux", "aarch64") if arm else ("linuxx64", "linux", "x64")
    raise CommandError(f"PhotonVision publishes no build for {system} {platform.machine()}; "
                       f"run it on a Windows or Linux machine (or a coprocessor) instead")


def photonlibpy_version() -> str:
    try:
        return metadata.version("photonlibpy")
    except metadata.PackageNotFoundError:
        raise CommandError("photonlibpy is not installed -- run uv sync") from None


def local_java(folder: Path) -> Path | None:
    exe = "java.exe" if os.name == "nt" else "java"
    found = sorted(folder.glob(f"*/bin/{exe}"))
    return found[-1] if found else None


class Installer:
    """One download at a time, in the background; status() for the page."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {"state": "idle"}

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._status.update(kw)

    def start(self, folder: Path) -> None:
        if self.busy:
            raise CommandError("already downloading PhotonVision")
        target_platform()  # refuse an unsupported machine before starting
        self._status = {"state": "starting"}
        self._thread = threading.Thread(target=self.run, args=(folder,), daemon=True)
        self._thread.start()

    def run(self, folder: Path, report=None) -> None:
        """Download what is missing into `folder`. `report(status)` is called
        as it goes (the command line prints it)."""
        def step(**kw: Any) -> None:
            self._set(**kw)
            if report:
                report(self.status())

        try:
            plat, os_name, arch = target_platform()
            version = photonlibpy_version()
            folder.mkdir(parents=True, exist_ok=True)
            jar = folder / f"photonvision-v{version}-{plat}.jar"
            if jar.exists():
                step(state="working", step=f"{jar.name} is already there")
            else:
                self._download(JAR_URL.format(version=version, platform=plat), jar,
                               f"PhotonVision {version}", step)
            if local_java(folder):
                step(state="working", step="a Java is already there")
            else:
                archive = folder / ("temurin21-jre.zip" if os_name == "windows" else "temurin21-jre.tar.gz")
                self._download(JRE_URL.format(os=os_name, arch=arch), archive, "Java (Temurin 21)", step)
                step(state="working", step="unpacking Java", got=None, total=None)
                _unpack(archive, folder)
                archive.unlink()
            step(state="done", step=f"PhotonVision {version} is ready in {folder}", got=None, total=None)
        except (OSError, CommandError, zipfile.BadZipFile, tarfile.TarError) as e:
            step(state="error", step=None, error=str(e))

    def _download(self, url: str, dest: Path, label: str, step) -> None:
        part = dest.with_name(dest.name + ".part")
        step(state="working", step=f"downloading {label}", got=0, total=None, error=None)
        req = urllib.request.Request(url, headers={"User-Agent": "robocup-web"})
        with urllib.request.urlopen(req, timeout=30) as r, part.open("wb") as out:
            total = int(r.headers.get("Content-Length") or 0) or None
            got = 0
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                step(got=got, total=total)
        part.replace(dest)


def _unpack(archive: Path, folder: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(folder)
    else:
        with tarfile.open(archive) as t:
            t.extractall(folder, filter="data")


def install_main() -> None:
    from .photonvision import default_dir

    ap = argparse.ArgumentParser(description="Download PhotonVision and a Java to run it")
    ap.add_argument("--dir", type=Path, default=None,
                    help="where to put it (default: the folder robocup-web looks in)")
    args = ap.parse_args()
    folder = (args.dir or default_dir()).resolve()
    print(f"installing PhotonVision into {folder}")
    last = [""]

    def report(st: dict[str, Any]) -> None:
        line = st.get("step") or ""
        if st.get("total"):
            line += f"  {st['got'] / st['total']:.0%}"
        if line != last[0]:
            print(f"\r{line:<70}", end="" if st.get("total") else "\n", flush=True)
            last[0] = line

    installer = Installer()
    installer.run(folder, report)
    st = installer.status()
    print()
    if st["state"] == "error":
        raise SystemExit(f"failed: {st['error']}")
    print("start it with:  uv run robocup-web --with-photonvision")
