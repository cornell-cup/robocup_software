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

macOS is the exception: PhotonVision doesn't support it or release a jar for
it, only builds one in CI, so here the jar is built from the release's source
the same way. The build fetches what it needs into the folder, all but Apple's
compiler, and deletes it after. A release's source does not build for macOS as
it is; patch_source() and add_mac_natives() make the few changes it needs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
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
#: Building a jar on macOS: the versions PhotonVision's CI builds its jars with.
SOURCE_URL = "https://github.com/PhotonVision/photonvision.git"
JDK_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/{os}/{arch}/jdk/hotspot/normal/eclipse"
NODE_DIST = "https://nodejs.org/dist/latest-v22.x/"
PNPM = "pnpm@10"
CHUNK = 1 << 16


def target_platform() -> tuple[str, str, str]:
    """(PhotonVision's jar platform, Adoptium's os, Adoptium's arch) for this machine."""
    system = platform.system()
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if system == "Windows" and not arm:
        return "winx64", "windows", "x64"
    if system == "Linux":
        return ("linuxarm64", "linux", "aarch64") if arm else ("linuxx64", "linux", "x64")
    if system == "Darwin":
        return ("macarm64", "mac", "aarch64") if arm else ("macx64", "mac", "x64")
    raise CommandError(f"PhotonVision publishes no build for {system} {platform.machine()}; "
                       f"run it on a Windows or Linux machine (or a coprocessor) instead")


def no_download_reason() -> str | None:
    """Why PhotonVision can't be downloaded for this machine, None if it can."""
    try:
        target_platform()
    except CommandError as e:
        return str(e)
    return None


def builds_from_source() -> bool:
    """No released jar for this machine, so "downloading" means building it."""
    return platform.system() == "Darwin"


def check_build_tools() -> None:
    """The one thing a build can't fetch. git and clang exist as stubs without
    Apple's tools, so only xcode-select can tell."""
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode:
        raise CommandError("building PhotonVision needs Apple's compiler: run "
                           "xcode-select --install, then try again")


def node_url(plat: str) -> str:
    arch = "arm64" if plat == "macarm64" else "x64"
    req = urllib.request.Request(NODE_DIST + "SHASUMS256.txt", headers={"User-Agent": "robocup-web"})
    with urllib.request.urlopen(req, timeout=30) as r:
        m = re.search(rf"node-v[\d.]+-darwin-{arch}\.tar\.gz", r.read().decode())
    if not m:
        raise CommandError(f"no Node 22 for {arch} at {NODE_DIST}")
    return NODE_DIST + m.group()


def photonlibpy_version() -> str:
    try:
        return metadata.version("photonlibpy")
    except metadata.PackageNotFoundError:
        raise CommandError("photonlibpy is not installed -- run uv sync") from None


def local_java(folder: Path) -> Path | None:
    exe = "java.exe" if os.name == "nt" else "java"
    # On macOS a Java unpacks as a bundle, with bin/ under Contents/Home.
    found = sorted([*folder.glob(f"*/bin/{exe}"), *folder.glob(f"*/Contents/Home/bin/{exe}")])
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
            elif builds_from_source():
                self._build(version, plat, os_name, arch, jar, step)
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

    def _build(self, version: str, plat: str, os_name: str, arch: str, jar: Path, step) -> None:
        """Build `jar` from PhotonVision's source at `version`, the way its CI
        does. All of it happens in a build folder, left behind only on failure,
        for its log."""
        check_build_tools()
        work = jar.parent / "build"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        log = work / "build.log"
        jdk = self._tool(JDK_URL.format(os=os_name, arch=arch), work, "jdk", "Java 17 to build with", step)
        jdk = jdk / "Contents" / "Home"
        node = self._tool(node_url(plat), work, "node", "Node to build with", step)
        pnpm = work / "pnpm"
        path = [pnpm / "node_modules" / ".bin", node / "bin", jdk / "bin"]
        env = {**os.environ, "JAVA_HOME": str(jdk), "GRADLE_USER_HOME": str(work / "gradle"),
               "PATH": os.pathsep.join([*map(str, path), os.environ.get("PATH", "")])}
        src = work / "photonvision"
        self._run(["git", "clone", "--depth", "1", "--branch", f"v{version}", SOURCE_URL, str(src)],
                  work, env, log, f"getting PhotonVision {version}'s source", step)
        self._run([str(node / "bin" / "npm"), "install", "--prefix", str(pnpm), PNPM],
                  work, env, log, "installing pnpm", step)
        with log.open("a") as out:
            for change in patch_source(src):
                out.write(f"patched: {change}\n")
        self._run(["./gradlew", "--no-daemon", "--console=plain", "photon-targeting:jar",
                   "photon-server:shadowJar", *MAC_NATIVE_TASKS, f"-PArchOverride={plat}"],
                  src, env, log, f"building PhotonVision {version}", step)
        built = sorted((src / "photon-server" / "build" / "libs").glob("photonvision-*.jar"))
        if not built:
            raise CommandError(f"the build made no jar; see {log}")
        step(step="adding PhotonVision's own Mac libraries to the jar")
        add_mac_natives(built[-1], src)
        built[-1].replace(jar)
        shutil.rmtree(work, ignore_errors=True)

    def _tool(self, url: str, work: Path, name: str, label: str, step) -> Path:
        """Download and unpack a tar.gz into `work`; the folder inside it."""
        home = work / name
        home.mkdir()
        archive = work / f"{name}.tar.gz"
        self._download(url, archive, label, step)
        step(step=f"unpacking {label}", got=None, total=None)
        _unpack(archive, home)
        archive.unlink()
        return next(home.iterdir())

    def _run(self, cmd: list[str], cwd: Path, env: dict[str, str], log: Path, label: str, step) -> None:
        step(step=label, got=None, total=None)
        with log.open("a") as out, subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                                    stderr=subprocess.STDOUT, text=True,
                                                    errors="replace") as p:
            for line in p.stdout:
                out.write(line)
                if line.startswith("> Task "):
                    step(step=f"{label} ({line[7:].strip()})")
        if p.returncode:
            raise CommandError(f"{label} failed; see {log}")


# --------------------------------------------------------------------------
# What a release's source needs to build and run on macOS.
# --------------------------------------------------------------------------

#: Development builds a release pins and PhotonVision's Maven server has since
#: deleted, so a build fails resolving them: the release each one is a commit
#: or two past. These are the Rockchip/Rubik Pi NPU drivers (Java side only on
#: a Mac -- their native halves are Linux ARM) and mrcal (which has a Mac build).
_DEV_PINS = re.compile(r'((?:rknn|rubik|mrcal)Version\s*=\s*")dev-(v[\d.]+)-\d+-g[0-9a-f]+(")')

#: Gradle tasks for photon-targeting's own C++ libraries on macOS. shadowJar
#: only packs a native library whose platform name matches the build's, and
#: on a Mac nothing does, so these are built on their own and added by
#: add_mac_natives().
MAC_NATIVE_TASKS = ("photon-targeting:photontargetingOsxuniversalReleaseSharedLibrary",
                    "photon-targeting:photontargetingJNIOsxuniversalReleaseSharedLibrary")


def patch_source(src: Path) -> list[str]:
    """Make a release's source build and start on macOS. Returns what changed.
    Each change is a no-op on source that no longer needs it."""
    changes = []

    def edit(rel: str, fn, what: str) -> None:
        f = src / rel
        if not f.exists():
            return
        old = f.read_text()
        new = fn(old)
        if new != old:
            f.write_text(new)
            changes.append(what)

    edit("build.gradle", lambda t: _DEV_PINS.sub(r"\1\2\3", t),
         "NPU/mrcal libraries pinned to their releases (the dev builds are gone)")
    # The release Rubik driver's create() takes the model path only; the dev
    # build added a model version. Rubik Pi hardware only, never run on a Mac.
    edit("photon-core/src/main/java/org/photonvision/vision/objects/RubikObjectDetector.java",
         lambda t: re.sub(r"RubikJNI\.create\(\s*([^,()]+\(\)\.toString\(\)),\s*"
                          r"model\.properties\.version\(\)\.ordinal\(\)\)",
                          r"RubikJNI.create(\1)", t),
         "Rubik detector call matched to the release driver")
    # Marked unsupported, so Main refuses to start; nothing else stops it.
    edit("photon-targeting/src/main/java/org/photonvision/common/hardware/Platform.java",
         lambda t: t.replace('MACOS("Mac OS", Platform::getUnknownModel, false, OSType.MACOS, false)',
                             'MACOS("Mac OS", Platform::getUnknownModel, false, OSType.MACOS, true)'),
         "macOS allowed to start")
    return changes


def add_mac_natives(jar: Path, src: Path) -> None:
    """Put photon-targeting's Mac libraries into the jar, and into the
    ResourceInformation.json index the loader extracts from (it loads only
    what is listed there, checked by MD5)."""
    libs = [src / "photon-targeting/build/libs/photontargeting/shared/osxuniversal/release"
            / "libphotontargeting.dylib",
            src / "photon-targeting/build/libs/photontargetingJNI/shared/osxuniversal/release"
            / "libphotontargetingJNI.dylib"]
    missing = [str(p) for p in libs if not p.exists()]
    if missing:
        raise CommandError(f"the build made no Mac library {', '.join(missing)}")
    names = {f"osx/universal/shared/{p.name}": p for p in libs}
    tmp = jar.with_name(jar.name + ".tmp")
    with zipfile.ZipFile(jar) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        index = json.loads(zin.read("ResourceInformation.json"))
        hashes = index["platforms"]["osx"]["architectures"]["universal"]["fileHashes"]
        for name, p in names.items():
            hashes["/" + name] = hashlib.md5(p.read_bytes()).hexdigest()
        # The extraction folder is named after this hash: a new one, so a
        # cache of an older build without these libraries is not reused.
        index["hash"] = hashlib.md5(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        for item in zin.infolist():
            if item.filename == "ResourceInformation.json" or item.filename in names:
                continue
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("ResourceInformation.json", json.dumps(index))
        for name, p in names.items():
            zout.write(p, name)
    tmp.replace(jar)


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
