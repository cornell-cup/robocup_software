"""One-time camera intrinsic calibration: focal length, principal point, and
lens distortion, via a ChArUco board.

    uv run calibrate-camera                  # guided: choose on screen, then wave a board around
    uv run calibrate-camera --no-setup       # skip the choices screen, use the flags as given
    uv run calibrate-camera --synthetic      # self-test against a digital camera, no hardware

Saves to robocup_testing/calib/intrinsics.json -- the actual calibration math
(vision_core.charuco) lives in vision-core, since it is camera-only and has
nothing to do with AprilTags; this is just the CLI wrapper: argparse, the live
capture window, and wiring the result into vision_core.intrinsics.save() so
track.py (and any future skill) picks it up automatically.

The guided run (the default) starts with a screen to pick the camera and type
the board's measured sizes, shows plain instructions under the live picture,
and ends on a verdict with Save / Try again. The words come from calib_guide,
the drawing from vision_core.ui.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from vision_core import intrinsics as intr
from vision_core import ui
from vision_core.camera import open_camera, read_frame
from vision_core.charuco import (
    COVERAGE_GRID,
    MIN_FRAMES,
    SyntheticChArucoCamera,
    calibrate_intrinsics,
    frame_coverage,
    make_board,
)
from vision_core.options import initial_values
from vision_core.paths import repo_root

from . import calib_guide as guide

MAX_LIVE_FRAMES = 60
#: A view only counts if its corners sit at least this far (median, px) from
#: the last captured view, and at least MIN_VIEW_GAP_S after it. Without that a
#: 30 fps camera fills all MAX_LIVE_FRAMES from one pose in two seconds, and
#: the calibration never sees the tilts and frame corners it needs. Compared
#: to the last view only, not every view: against all of them, a board that
#: returns to an area already covered is rejected forever and the count stalls.
MIN_VIEW_CHANGE_PX = 25.0
MIN_VIEW_GAP_S = 0.3


def _is_new_view(view: dict[int, np.ndarray], last: dict[int, np.ndarray] | None) -> bool:
    if last is None:
        return True
    shared = view.keys() & last.keys()
    if len(shared) < 4:
        return True
    moved = np.median([np.linalg.norm(view[i] - last[i]) for i in shared])
    return moved >= MIN_VIEW_CHANGE_PX


def _run_synthetic(args):
    board, _dictionary = make_board(
        tuple(args.squares), args.square_mm / 1000.0, args.marker_mm / 1000.0
    )
    shape = (args.height, args.width)
    K_truth = intr.from_fov(args.width, args.height, 60.0)
    # k3 is 0 because calibrate_intrinsics pins it (fix_k3); a non-zero truth
    # there would just leak into the recovered k2 and muddy the comparison.
    dist_truth = np.array([0.05, -0.03, 0.001, -0.0005, 0.0])
    cam = SyntheticChArucoCamera(board, K_truth, dist_truth, shape=shape)

    print("Synthetic camera -- ground truth:")
    print(f"  K =\n{K_truth}")
    print(f"  distortion = {dist_truth}\n")

    result = calibrate_intrinsics(
        cam.frames(), board, (args.width, args.height), min_frames=args.min_frames
    )
    print(f"recovered K =\n{result.K}")
    print(f"recovered distortion = {result.dist}")
    print(f"rms reprojection error: {result.rms_reproj_px:.3f} px over {result.n_frames} views")
    fx_err_pct = 100 * abs(result.K[0, 0] - K_truth[0, 0]) / K_truth[0, 0]
    cx_err_px = abs(result.K[0, 2] - K_truth[0, 2])
    k1_err = abs(result.dist[0] - dist_truth[0])
    print(f"fx error vs ground truth: {fx_err_pct:.2f}%   cx error: {cx_err_px:.1f} px   "
          f"k1 error: {k1_err:.3f}")
    ok = fx_err_pct < 2.0 and k1_err < 0.02
    print(f"self-test: {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit(1)
    return result, args.out


#: Where the frame's corner cells are in frame_coverage's grid, by name.
_CORNER_CELLS = {"top-left": (0, 0), "top-right": (0, -1),
                 "bottom-left": (-1, 0), "bottom-right": (-1, -1)}


def _missing_corners(points: list[np.ndarray], size: tuple[int, int]) -> list[str]:
    """The corners of the picture no captured board corner has reached yet."""
    w, h = size
    hit = np.zeros((COVERAGE_GRID, COVERAGE_GRID), bool)
    for p in points:
        if 0 <= p[0] < w and 0 <= p[1] < h:
            hit[int(p[1] * COVERAGE_GRID / h), int(p[0] * COVERAGE_GRID / w)] = True
    return [name for name, cell in _CORNER_CELLS.items() if not hit[cell]]


def capture_views(read_bgr, board, min_frames: int,
                  window: ui.Window | None = None) -> tuple[list, list, tuple[int, int]]:
    """Live capture: show the picture with instructions under it and keep every
    new view of the board, until Enter once min_frames are in. Returns (grey
    frames, every captured corner, picture size). Esc raises ui.Cancelled."""
    detector = cv2.aruco.CharucoDetector(board)
    own = window is None
    window = window or ui.Window("calibrate-camera")
    frames = []
    last_view: dict[int, np.ndarray] | None = None
    last_view_t = 0.0
    coverage: list[np.ndarray] = []  # every captured corner, drawn so gaps show
    size = None
    try:
        while True:
            bgr = read_bgr()
            size = size or (bgr.shape[1], bgr.shape[0])
            grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            ch_corners, ch_ids, _mk_corners, mk_ids = detector.detectBoard(grey)
            disp = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
            n_seen = 0 if mk_ids is None else len(mk_ids)
            # OpenCV's detectBoard() can return charucoCorners/charucoIds that
            # disagree even when they look consistent from Python (matching
            # len(), no Nones) -- drawDetectedCornersCharuco has a known
            # shape-handling bug on some builds that still asserts on that.
            # So: never call it. Draw the corners ourselves with plain
            # cv2.circle, which only ever looks at a flattened point list and
            # cannot hit that bug.
            good = (
                ch_corners is not None and ch_ids is not None
                and len(ch_corners) >= 6 and len(ch_corners) == len(ch_ids)
            )
            for pt in coverage:
                cv2.circle(disp, (int(round(pt[0])), int(round(pt[1]))), 2,
                           (255, 160, 0), -1, cv2.LINE_AA)
            new_view = False
            if good:
                pts = np.asarray(ch_corners, dtype=np.float64).reshape(-1, 2)
                for pt in pts:
                    cv2.circle(disp, (int(round(pt[0])), int(round(pt[1]))), 5,
                               (0, 255, 0), -1, cv2.LINE_AA)
                view = dict(zip(np.asarray(ch_ids).ravel().tolist(), pts))
                new_view = (_is_new_view(view, last_view)
                            and time.monotonic() - last_view_t >= MIN_VIEW_GAP_S)
                if new_view and len(frames) < MAX_LIVE_FRAMES:
                    frames.append(grey)
                    last_view, last_view_t = view, time.monotonic()
                    coverage.extend(pts)
            info = guide.lens_capture_text(
                len(frames), min_frames, seen=n_seen > 0, whole=good, new_view=new_view,
                full=len(frames) >= MAX_LIVE_FRAMES, missing=_missing_corners(coverage, size))
            window.show(ui.stack(disp, ui.instruction_panel(disp.shape[1], info)))
            key = window.key()
            if key == ui.ESC:
                print("aborted")
                raise ui.Cancelled()
            if key in (ui.ENTER, ui.SPACE, ord("q")) and len(frames) >= min_frames:
                break
    finally:
        if own:
            window.close()
    return frames, coverage, size


def _run_live(args, window: ui.Window | None = None, cap=None):
    board, _dictionary = make_board(
        tuple(args.squares), args.square_mm / 1000.0, args.marker_mm / 1000.0
    )
    print("Move the board around: different distances, tilts, and the corners")
    print("of the frame, not just the centre.")
    print(f"Capturing until {args.min_frames}+ good views are seen.")
    print("Enter to finish once enough are captured, Esc to abort.\n")

    cap = cap or open_camera(args.camera, args.width, args.height)

    def read_bgr() -> np.ndarray:
        ok, bgr = read_frame(cap)
        if not ok:
            raise RuntimeError("camera stopped returning frames")
        return bgr

    try:
        frames, coverage, size = capture_views(read_bgr, board, args.min_frames, window)
    finally:
        cap.release()
        if window is None:
            cv2.destroyAllWindows()
    if size != (args.width, args.height):
        # Calibrate, and save, at the size the camera actually delivers -- it
        # may not honour --width/--height, and a K keyed to the wrong
        # resolution is silently wrong everywhere it loads.
        print(f"note: asked for {args.width}x{args.height}, camera gives "
              f"{size[0]}x{size[1]} -- calibrating at {size[0]}x{size[1]} "
              f"(other commands load it at whatever size the camera gives, "
              f"scaled if the aspect ratio matches)")

    args.width, args.height = size  # saved under these
    result = calibrate_intrinsics(frames, board, size, min_frames=args.min_frames)
    result.coverage = frame_coverage(coverage, size)
    print(f"\nK =\n{result.K}")
    print(f"distortion = {result.dist}")
    print(f"rms reprojection error: {result.rms_reproj_px:.3f} px over {result.n_frames} views")
    print(f"horizontal field of view: {intr.hfov_of(result.K, size[0]):.1f} deg")
    verdict = _verdict(result)
    print(f"{verdict.headline}")
    for line in verdict.lines:
        print(f"  {line}")
    return result, (args.out or True)  # live mode always saves; True means "default path"


def _verdict(result) -> guide.Verdict:
    cov = result.coverage or {}
    return guide.lens_verdict(result.rms_reproj_px, result.n_frames,
                              cov.get("coverage_pct"), cov.get("corner_cells_reached"))


def _save(result, args, out) -> Path:
    metrics = {
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
        "image_size": [int(args.width), int(args.height)],
        "rms_reproj_px": round(result.rms_reproj_px, 4),
        "n_views": result.n_frames,
        "hfov_deg": round(intr.hfov_of(result.K, args.width), 2),
        **(result.coverage or {}),
    }
    return intr.save(
        result.K, args.width, args.height, result.source, dist=result.dist,
        path=Path(out) if isinstance(out, str) else None, metrics=metrics,
    )


def write_board(path: str | Path, squares, square_mm: float, marker_mm: float) -> Path:
    board, _dictionary = make_board(tuple(squares), square_mm / 1000.0, marker_mm / 1000.0)
    sx, sy = squares
    img = board.generateImage((1600, int(round(1600 * sy / sx))), marginSize=40)
    cv2.imwrite(str(path), img)
    return Path(path)


def guided(window: ui.Window | None = None, args=None) -> bool:
    """The whole step on screen: choices, capture, verdict, save. Returns True
    if a calibration was saved. Pass a window to share one (the `calibrate`
    menu does); Back/Esc returns to the caller."""
    args = args or build_parser().parse_args([])
    own = window is None
    window = window or ui.Window("calibrate-camera")
    peek = ui.CameraPeek(args.width, args.height)
    camera = guide.remembered("camera", args.camera, 0)
    options = guide.lens_options(camera, args.square_mm, args.marker_mm)
    values = initial_values(options, {"camera": camera, "square_mm": args.square_mm,
                                      "marker_mm": args.marker_mm})
    try:
        while True:
            pressed, values = ui.form(
                window, guide.LENS_TITLE, guide.LENS_INTRO, options, values,
                buttons=(("start", "Start"), ("board", "Make the board to print"),
                         ("back", "Quit" if own else "Back")),
                preview=peek.preview)
            if pressed == "back":
                return False
            args.camera = int(values["camera"])
            args.square_mm, args.marker_mm = values["square_mm"], values["marker_mm"]
            guide.save_choices({"camera": args.camera})
            if pressed == "board":
                path = write_board(repo_root() / "board.png", args.squares,
                                   args.square_mm, args.marker_mm)
                ui.open_file(path)
                ui.message(window, "Print the board", guide.board_print_lines(path))
                continue
            try:
                result, _ = _run_live(args, window, peek.take(args.camera))
            except ui.Cancelled:
                continue
            except SystemExit as e:  # open_camera: that camera would not open
                ui.message(window, "The camera didn't open", [str(e.code),
                           "Pick another camera and try again."], kind="bad")
                continue
            except (RuntimeError, cv2.error) as e:
                ui.message(window, "That didn't work", [str(e), "Try again."], kind="bad")
                continue
            v = _verdict(result)
            choice = ui.message(window, "Lens result", v.lines, kind=v.kind, headline=v.headline,
                                buttons=(("save", "Save"), ("again", "Try again"),
                                         ("discard", "Don't save")))
            if choice == "again":
                continue
            if choice == "save":
                path = _save(result, args, args.out or True)
                print(f"\nsaved {path}")
                ui.message(window, "Saved", [f"Saved to {path}.", guide.LENS_NEXT], kind="ok",
                           headline="Lens calibration saved.")
                return True
            return False
    finally:
        peek.release()
        if own:
            window.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--synthetic", action="store_true",
        help="self-test against a digital ChArUco board, no camera needed",
    )
    ap.add_argument("--no-setup", action="store_true",
                    help="skip the on-screen choices and use the flags as given")
    ap.add_argument("--camera", type=int, default=None,
                    help="camera index (default: the one chosen last time, else 0)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--min-frames", type=int, default=MIN_FRAMES)
    ap.add_argument(
        "--squares", type=int, nargs=2, metavar=("X", "Y"), default=[5, 7],
        help="ChArUco grid size in squares (default 5 7)",
    )
    ap.add_argument("--square-mm", type=float, default=35.0)
    ap.add_argument("--marker-mm", type=float, default=26.0)
    ap.add_argument(
        "--out", type=str, default=None,
        help="where to save (default robocup_testing/calib/intrinsics.json; "
        "--synthetic saves nowhere unless you pass this)",
    )
    ap.add_argument(
        "--save-board-png", metavar="PATH", help="write the board as a PNG, to print it"
    )
    return ap


def main() -> None:
    args = build_parser().parse_args()

    if args.save_board_png:
        write_board(args.save_board_png, args.squares, args.square_mm, args.marker_mm)
        print(f"wrote {args.save_board_png}")
        return

    if not args.synthetic and not args.no_setup:
        guided(args=args)
        return

    if args.synthetic:
        result, out = _run_synthetic(args)
    else:
        args.camera = guide.remembered("camera", args.camera, 0)
        result, out = _run_live(args)

    if not out:
        print("\n(--synthetic run, nothing saved -- pass --out to save anyway)")
        return
    print(f"\nsaved {_save(result, args, out)}")


if __name__ == "__main__":
    main()
