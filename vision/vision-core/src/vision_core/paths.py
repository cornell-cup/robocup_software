"""Where the repo is, so shared files resolve regardless of the current directory.

calib/ holds things that describe the *camera*, not any one skill: the
intrinsics, and the ball color profiles. A cwd-relative "calib/intrinsics.json"
would mean different files depending on which folder you launched from -- and the
failure is quiet, because a missing intrinsics file just falls back to a guessed
field of view.

So resolve it from this file's location instead. uv installs every workspace
member editable, so __file__ really is the checked-out source tree.
"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """The robocup_testing folder: the one holding vision-core/ and calib/.

    Fixed by layout, not found by a marker file: this file lives at
    <root>/vision-core/src/vision_core/paths.py. (It used to search upward for
    uv.lock, but the lockfile now sits one level higher, at the monorepo root.)
    """
    root = Path(__file__).resolve().parents[3]
    if not (root / "vision-core").is_dir():
        raise RuntimeError(
            f"{__file__} is not inside the robocup_testing source tree; "
            f"vision_core must be installed editable. Run 'uv sync' from the repo root."
        )
    return root


def calib_dir() -> Path:
    """The shared calibration directory. Not created until something writes."""
    return repo_root() / "calib"


def calib_path(name: str) -> Path:
    """A file inside calib/, e.g. calib_path("intrinsics.json")."""
    return calib_dir() / name
