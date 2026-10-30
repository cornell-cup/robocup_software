"""What the calibration tools say and ask, in plain words, for someone who has
never calibrated a camera -- kept apart from any window code.

Three steps, always in this order:

    lens   calibrate-camera     how the camera's lens bends the picture
    field  calibrate-field      where the field is in the picture
    check  verify-calibration   tape-measured proof that positions come out right

The OpenCV windows (calibrate_camera, calibrate_field, verify, and the
`calibrate` menu) show what is here: the setup Options (vision_core.options),
the live instruction dicts the *_text() functions build, and the Verdicts.
Nothing here imports cv2 or draws, so another front end -- the web app on the
photonvision-web-app branch, say -- can serve exactly the same choices and
words: Option.to_json() for its forms, the dicts as they are for its
instruction cards, Verdict.to_json() for results, status() for an overview.

An instruction dict:

    title     one line: what to do now
    steps     short sentences, in order
    status    what the program sees right now
    kind      "ok" | "info" | "warn" | "bad" -- colours the status
    progress  0..1, or None
    keys      [(key, what it does), ...]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from vision_core.options import Option
from vision_core.paths import calib_dir, calib_path


@dataclass
class Verdict:
    kind: str  # ok | warn | bad
    headline: str
    lines: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "headline": self.headline, "lines": list(self.lines)}


ESC_BACK = ("Esc", "go back")


# --------------------------------------------------------------------------
# Remembered choices: the camera number, the tag size... so nobody types them
# twice. Per machine (camera numbers differ), so this file is gitignored.
# --------------------------------------------------------------------------

CHOICES_FILE = "last_choices.json"


def load_choices() -> dict[str, Any]:
    try:
        return json.loads(calib_path(CHOICES_FILE).read_text())
    except (OSError, ValueError):
        return {}


def save_choices(values: dict[str, Any]) -> None:
    """Merge `values` into the remembered choices (JSON-safe ones only)."""
    d = load_choices()
    d.update({k: v for k, v in values.items() if isinstance(v, (int, float, str, type(None)))})
    try:
        calib_dir().mkdir(parents=True, exist_ok=True)
        calib_path(CHOICES_FILE).write_text(json.dumps(d, indent=2))
    except OSError:
        pass


def remembered(key: str, given: Any, fallback: Any) -> Any:
    """A flag the user passed, else what they chose last time, else `fallback`."""
    if given is not None:
        return given
    v = load_choices().get(key)
    return fallback if v is None else v


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def saved_field_info(path: Path | None = None) -> dict[str, Any]:
    """What the saved field calibration says: field size, tag size, mode, origin."""
    d = _read_json(path or calib_path("field_pose.json")) or {}
    m = d.get("metrics") or {}
    f = d.get("field")
    return {
        "exists": bool(d),
        "field": (float(f["width"]), float(f["height"])) if f else None,
        "tag_size": m.get("tag_size_m"),
        "mode": m.get("mode"),
        "origin": d.get("origin"),
    }


def camera_option(default: int) -> Option:
    choices = [(i, f"Camera {i}") for i in range(4)]
    if default not in range(4):
        choices.append((default, f"Camera {default}"))
    return Option(
        "camera", "Which camera?",
        "Pick the one whose picture (right) shows the field. Camera 0 is usually the "
        "laptop's own.",
        kind="choice", default=default, choices=tuple(choices))


def tag_size_option(default: float) -> Option:
    return Option(
        "tag_size", "Tag size",
        "Measure the tag's black square, edge to edge, with a ruler. Not the white border.",
        unit="mm", scale=1000.0, step=1.0, minimum=10.0, maximum=1000.0, default=default)


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %H:%M")
    except ValueError:
        return iso


# --------------------------------------------------------------------------
# Step 1: the lens.
# --------------------------------------------------------------------------

LENS_TITLE = "Step 1 of 3: Camera lens"
LENS_INTRO = [
    "Every lens bends the picture a little, most of all near the edges. This step "
    "measures how, so positions near the edges of the picture come out right. Only "
    "needed once per camera.",
    "You need the printed checkerboard, taped FLAT to something stiff (cardboard, a "
    "clipboard). No board yet? Press 'Make the board to print'.",
    "Measure your printed board with a ruler and type the two sizes below. Takes about "
    "2 minutes.",
]
LENS_NEXT = "Next: step 2, find the field."

#: Reprojection error, px: under GOOD is good, over MAX means redo.
LENS_GOOD_RMS, LENS_MAX_RMS = 0.5, 1.0
#: Share of the picture (%) the board must have reached.
LENS_MIN_COVERAGE = 70.0


def lens_options(camera: int, square_mm: float, marker_mm: float) -> list[Option]:
    return [
        camera_option(camera),
        Option("square_mm", "Checker square size",
               "One whole black square of the printed board, edge to edge.",
               unit="mm", step=0.5, minimum=5.0, maximum=300.0, default=square_mm),
        Option("marker_mm", "Pattern size",
               "The black pattern printed inside a white square, edge to edge. Smaller "
               "than the checker square.",
               unit="mm", step=0.5, minimum=3.0, maximum=300.0, default=marker_mm),
    ]


def board_print_lines(path: Path) -> list[str]:
    return [
        f"The board was saved to {path} and should have opened.",
        "Print it on plain paper. Any size is fine -- you will measure it after.",
        "Tape it completely flat onto cardboard or a clipboard. A bent board gives a bad "
        "result.",
        "Then measure one black square and one inner pattern with a ruler and type them in.",
    ]


def lens_capture_text(n: int, need: int, *, seen: bool, whole: bool, new_view: bool,
                      full: bool, missing: list[str]) -> dict[str, Any]:
    """While the board is being waved: n pictures taken, need for a result;
    seen/whole: board found at all / found well; new_view: this frame was taken;
    missing: parts of the picture the board has not been to yet."""
    steps = [
        "Hold the board facing the camera and move it SLOWLY. It takes a picture by itself.",
        "Visit every part of the picture, especially all 4 corners. Orange dots show "
        "where the board has been.",
        "Tilt it a bit differently each time, and try it near and far.",
    ]
    if n >= need:
        if missing and not full:
            status, kind = (f"Enough to finish. For a better result, also visit the "
                            f"{_and(missing)} of the picture.", "ok")
        else:
            status, kind = "Enough pictures. Press ENTER to finish.", "ok"
    elif not seen:
        status, kind = "Can't see the board -- bring it into the picture.", "bad"
    elif not whole:
        status, kind = ("Only part of the board is found -- hold it flatter to the camera, "
                        "closer, or with more light.", "warn")
    elif new_view:
        status, kind = "Picture taken! Now move it somewhere new.", "ok"
    else:
        status, kind = "Got it -- now move it to a new spot or tilt it.", "info"
    keys = [("ENTER", "finish")] if n >= need else []
    return {
        "title": f"Wave the board around -- pictures taken: {n} of {need}",
        "steps": steps, "status": status, "kind": kind,
        "progress": min(n / max(need, 1), 1.0),
        "keys": keys + [ESC_BACK],
    }


def lens_verdict(rms_px: float, n_views: int, coverage_pct: float | None,
                 corners: int | None) -> Verdict:
    lines = [f"Fit error {rms_px:.2f} px over {n_views} pictures (under {LENS_MAX_RMS:g} "
             f"is needed, under {LENS_GOOD_RMS:g} is good)."]
    if coverage_pct is not None:
        lines.append(f"The board reached {coverage_pct:.0f}% of the picture and "
                     f"{corners}/4 of its corners (aim for {LENS_MIN_COVERAGE:.0f}% and 4/4).")
    if rms_px > LENS_MAX_RMS:
        return Verdict("bad", "Redo this: the fit is poor.", lines + [
            "Usually a blurry picture or a board that isn't flat. Move slower, add light, "
            "and tape the board flatter."])
    if coverage_pct is not None and (coverage_pct < LENS_MIN_COVERAGE or (corners or 0) < 4):
        return Verdict("warn", "Usable, but the board missed part of the picture.", lines + [
            "Positions near the parts it missed can be off. Try again and take the board "
            "into every corner."])
    if rms_px > LENS_GOOD_RMS:
        return Verdict("warn", "Usable, could be sharper.", lines + [
            "Fine to keep. Trying again with slower moves and more light may improve it."])
    return Verdict("ok", "Good lens calibration.", lines)


# --------------------------------------------------------------------------
# Step 2: the field.
# --------------------------------------------------------------------------

FIELD_TITLE = "Step 2 of 3: Find the field"
FIELD_INTRO = [
    "Shows the program where the field is, using an AprilTag on each corner. Redo it "
    "whenever the camera is moved or bumped.",
    "You need: a printed AprilTag, a ruler, and a tape measure for the field. The camera "
    "must stay still the whole time.",
]
FIELD_NEXT = "Next: step 3, check the accuracy with a tape measure."

LETTERS = "ABCDEFGHIJKLMNOP"
#: One tag: corner fit error, px.
FIELD_GOOD_PX, FIELD_MAX_PX = 1.0, 3.0
#: Four tags: how much the repeated solves disagree.
FIELD_GOOD_DEG, FIELD_MAX_DEG = 0.3, 1.0
FIELD_GOOD_MM, FIELD_MAX_MM = 5.0, 15.0


def field_options(camera: int, method: str, w: float, h: float, tag_size: float,
                  origin: str, has_saved_origin: bool) -> list[Option]:
    calibrating = ("task", ("calibrate",))
    origins = [("corner", "Corner A"), ("center", "Middle of the field"),
               ("click", "Click to choose")]
    if has_saved_origin:
        origins.insert(0, ("keep", "Keep the saved one"))
    return [
        Option("task", "What do you want to do?",
               "Only move (0,0) keeps the saved field and changes where (0,0) is.",
               kind="choice", default="calibrate",
               choices=(("calibrate", "Find the field"), ("origin", "Only move (0,0)"))),
        camera_option(camera),
        Option("method", "How will you show the corners?",
               "One tag: you put a single tag on each corner in turn. Four tags: tags 0, 1, "
               "2 and 3 sit on the corners the whole time.",
               kind="choice", default=method,
               choices=(("one", "One tag, corner by corner"), ("four", "Four tags at once")),
               show_if=calibrating),
        Option("field_w", "Field length (x)",
               "Corner A to corner B, measured with a tape measure.",
               unit="m", step=0.01, minimum=0.1, maximum=30.0, default=w, show_if=calibrating),
        Option("field_h", "Field width (y)",
               "Corner A to corner D, the other edge.",
               unit="m", step=0.01, minimum=0.1, maximum=30.0, default=h, show_if=calibrating),
        replace(tag_size_option(tag_size), show_if=calibrating),
        Option("origin", "Where should (0,0) be?",
               "Every position is measured from here. Corner A is simplest.",
               kind="choice", default=origin, choices=tuple(origins)),
    ]


def corner_sentence(i: int, xy: tuple[float, float], w: float, h: float) -> str:
    """Where corner i of the default layout is, in words."""
    x, y = xy
    if (x, y) == (0.0, 0.0):
        return "Put the tag on corner A. This is where the field starts."
    if (x, y) == (w, 0.0):
        return f"Put the tag on corner B: {w:g} m from A along the first edge (the x direction)."
    if (x, y) == (w, h):
        return "Put the tag on corner C: diagonally across from A."
    if (x, y) == (0.0, h):
        return f"Put the tag on corner D: {h:g} m from A along the other edge (the y direction)."
    return f"Put the tag at {x:g} m along x and {y:g} m along y from corner A."


def field_one_tag_text(i: int, n: int, xy: tuple[float, float], w: float, h: float,
                       count: int, need: int, visible: int, hint: str | None) -> dict[str, Any]:
    ready = count >= need
    if hint is not None:
        status, kind = hint[0].upper() + hint[1:] + ".", "warn"
    elif visible > 1:
        status, kind = (f"{visible} tags in the picture -- hide all but one.", "bad")
    elif visible == 0:
        status, kind = "No tag in the picture yet.", "bad"
    elif ready:
        status, kind = "Got it! Press ENTER for the next corner.", "ok"
    else:
        status, kind = "Hold still... measuring.", "info"
    return {
        "title": f"Corner {LETTERS[i]} ({i + 1} of {n})",
        "steps": [
            corner_sentence(i, xy, w, h),
            "Centre the tag exactly on the corner, lying flat. Then let go and keep your "
            "hand out of the picture.",
        ],
        "status": status, "kind": kind,
        "progress": count / max(need, 1),
        "keys": ([("ENTER", "next corner")] if ready else [])
        + [("R", "redo this corner"), ESC_BACK],
    }


def field_four_tags_text(need_ids: list[int], seen_ids: list[int], count: int,
                         need: int) -> dict[str, Any]:
    missing = [i for i in need_ids if i not in seen_ids]
    ready = count >= need
    if ready:
        status, kind = "Enough! Press ENTER to finish.", "ok"
    elif missing:
        status, kind = (f"Can't see tag{'s' if len(missing) > 1 else ''} "
                        f"{_and([str(i) for i in missing])} -- check it is in the picture "
                        "and not covered.", "bad")
    else:
        status, kind = "All four tags seen. Hold still...", "info"
    return {
        "title": f"Show all four corner tags -- {count} of {need} good pictures",
        "steps": [
            "Tags " + ", ".join(f"{t} on corner {LETTERS[k]}" for k, t in enumerate(need_ids))
            + " (see the map).",
            "Keep the camera still and your hands out of the picture.",
        ],
        "status": status, "kind": kind, "progress": count / max(need, 1),
        "keys": ([("ENTER", "finish")] if ready else []) + [ESC_BACK],
    }


def origin_text(where: str) -> dict[str, Any]:
    return {
        "title": "Click where (0,0) should be",
        "steps": [
            "Click on the picture. Clicks snap to the corners, the middle of each edge and "
            "the centre.",
            "The arrows show which way x and y go from there.",
        ],
        "status": f"(0,0) is now: {where}", "kind": "info",
        "keys": [("ENTER", "use this"), ("C", "centre"), ("D", "corner A"),
                 ("Esc", "use corner A")],
    }


def field_verdict(metrics: dict[str, Any]) -> Verdict:
    if metrics.get("mode") == "sequential":
        rms, mx = metrics.get("reproj_rms_px", 0.0), metrics.get("reproj_max_px", 0.0)
        lines = [f"The four corners fit together to within {rms:.2f} px (worst {mx:.2f} px). "
                 f"Under {FIELD_GOOD_PX:g} is good, over {FIELD_MAX_PX:g} means redo."]
        if mx > FIELD_MAX_PX:
            return Verdict("bad", "Redo this: the corners don't fit together.", lines + [
                "A tag was probably not centred on its corner, or the field size is typed "
                "wrong. Re-measure the field and try again."])
        if mx > FIELD_GOOD_PX:
            return Verdict("warn", "Usable, but not exact.", lines + [
                "Check the tag sits exactly on each corner. Step 3 will show how much it "
                "matters."])
        return Verdict("ok", "Good field calibration.", lines)
    rot, mm = metrics.get("rotation_spread_deg", 0.0), metrics.get("translation_spread_mm", 0.0)
    lines = [f"Repeated measurements agree to within {rot:.2f} deg and {mm:.1f} mm "
             f"over {metrics.get('n_frames', 0)} pictures."]
    if rot > FIELD_MAX_DEG or mm > FIELD_MAX_MM:
        return Verdict("bad", "Redo this: the measurements jump around.", lines + [
            "Something moved while measuring -- the camera, or a tag. Fix them in place and "
            "try again."])
    if rot > FIELD_GOOD_DEG or mm > FIELD_GOOD_MM:
        return Verdict("warn", "Usable, a little shaky.", lines + [
            "More light and a steadier camera help."])
    return Verdict("ok", "Good field calibration.", lines)


# --------------------------------------------------------------------------
# Step 3: the check.
# --------------------------------------------------------------------------

VERIFY_TITLE = "Step 3 of 3: Check the accuracy"
VERIFY_INTRO = [
    "Proves the calibration with a tape measure: you put a tag on 9 marked spots, and "
    "it tells you how far off each one reads, in millimetres.",
    "You need: a tape measure, some masking tape for the marks, and one AprilTag. Takes "
    "about 10 minutes.",
]
EDGE_INSET_M = 0.05


def verify_options(camera: int, tag_size: float, inset: float,
                   tag_id: int | None) -> list[Option]:
    insets = [(0.0, "On the edges"), (EDGE_INSET_M, f"{EDGE_INSET_M * 100:g} cm inside")]
    if inset not in (0.0, EDGE_INSET_M):
        insets.append((inset, f"{inset * 100:g} cm inside"))
    return [
        camera_option(camera),
        tag_size_option(tag_size),
        Option("inset", "Where do the edge marks go?",
               "If a wall stops the tag sitting centred on the field's edge, put the edge "
               "and corner marks a little inside instead.",
               kind="choice", default=inset, choices=tuple(insets)),
        Option("tag_id", "Tag number",
               "Leave empty if only one tag will be in the picture. If others stay in view, "
               "type the number of the one you will move.",
               integer=True, optional=True, blank="any", step=1, minimum=0, maximum=586,
               default=tag_id),
    ]


def marks_lines(points: list[tuple[float, float]],
                inset: float) -> list[str | tuple[str, str]]:
    where = ("on the corners, the middle of each edge and the centre" if not inset else
             f"{inset * 100:g} cm in from the corners and edges, and the centre")
    rows: dict[float, list[str]] = {}
    for i, (x, y) in enumerate(points):
        rows.setdefault(round(y, 3), []).append(f"spot {i + 1} at x = {_m(x)}")
    return [
        f"Use the tape measure to mark these {len(points)} spots with tape, {where}. "
        "Measure from (0,0): first along x, then along y (see the map).",
        *[(f"y = {_m(y)}:  " + ",  ".join(spots), "info") for y, spots in rows.items()],
        "All numbers are metres. Measure carefully: the check is only as good as your marks.",
    ]


def _m(v: float) -> str:
    """Metres to the millimetre a tape measure reads."""
    return f"{round(v, 3):g}"


def verify_point_text(i: int, n: int, xy: tuple[float, float], placement: str, count: int,
                      need: int, hint: str | None, visible: int, live_mm: float | None,
                      ) -> dict[str, Any]:
    ready = count >= need
    if hint is not None:
        status, kind = hint[0].upper() + hint[1:] + ".", "warn"
    elif visible > 1:
        status, kind = (f"{visible} tags in the picture -- hide the others, or set 'Tag "
                        "number' on the first screen.", "bad")
    elif visible == 0:
        status, kind = "No tag in the picture yet.", "bad"
    elif ready:
        status, kind = (f"Measured: {live_mm:.0f} mm off. Press ENTER for the next spot."
                        if live_mm is not None else "Measured. Press ENTER for the next spot.",
                        "ok")
    else:
        status, kind = (f"Hold still... measuring (reads {live_mm:.0f} mm off right now)"
                        if live_mm is not None else "Hold still... measuring", "info")
    return {
        "title": f"Spot {i + 1} of {n}: x = {_m(xy[0])}, y = {_m(xy[1])}",
        "steps": [
            f"Put the tag's centre on mark {i + 1} (the pink cross), {placement}.",
            "Let go and keep your hand out of the picture.",
        ],
        "status": status, "kind": kind, "progress": count / max(need, 1),
        "keys": ([("ENTER", "next spot")] if ready else [])
        + [("R", "redo"), ("S", "skip this spot"), ESC_BACK],
    }


def verify_verdict(summary: dict[str, Any]) -> Verdict:
    v = summary["values"]
    lines = [
        f"On average positions are {v['pos_rms_mm']:.0f} mm off; the worst spot is "
        f"{v['pos_max_mm']:.0f} mm off. Headings are within {v['heading_max_deg']:.1f} deg.",
        "Good is under 10 mm on average and 20 mm at worst.",
    ]
    if summary.get("n_skipped"):
        lines.append(f"{summary['n_skipped']} spot(s) were skipped, so the check is incomplete.")
    lines += list(summary.get("diagnosis", ()))
    verdict = summary["verdict"]
    if verdict == "PASS":
        return Verdict("ok", "PASS -- the calibration is accurate.", lines)
    if verdict == "WARN":
        return Verdict("warn", "WARN -- usable, but could be better.", lines)
    return Verdict("bad", "FAIL -- positions are too far off. Redo the calibration.", lines)


# --------------------------------------------------------------------------
# Where things stand: for the menu (or a web overview).
# --------------------------------------------------------------------------


def status() -> list[dict[str, Any]]:
    """The three steps, each: key, title, verdict (or None if never done), note
    (a reason to redo it, or ""), and whether it is the one to do next."""
    lens_p, field_p = calib_path("intrinsics.json"), calib_path("field_pose.json")
    hist_p = calib_dir() / "verification" / "history.jsonl"

    lens = _read_json(lens_p)
    if lens is None:
        lens_v = None
    elif not str(lens.get("source", "")).startswith("charuco"):
        lens_v = Verdict("warn", "Only a guess so far -- not measured.")
    elif lens.get("metrics"):
        m = lens["metrics"]
        lens_v = lens_verdict(m.get("rms_reproj_px", 0.0), m.get("n_views", 0),
                              m.get("coverage_pct"), m.get("corner_cells_reached"))
        lens_v.headline += f" ({_date(m.get('calibrated_at'))})"
    else:
        lens_v = Verdict("warn", f"Done, but how well is unknown ({lens.get('source', '')}). "
                         "Redo it to measure.")

    fld = _read_json(field_p)
    field_note = ""
    if fld is None:
        field_v = None
    elif fld.get("metrics"):
        field_v = field_verdict(fld["metrics"])
        field_v.headline += f" ({_date(fld['metrics'].get('calibrated_at'))})"
    else:
        field_v = Verdict("warn", "Done, but how well is unknown. Redo it to measure.")
    if fld is not None and lens is not None and lens_p.stat().st_mtime > field_p.stat().st_mtime + 1:
        field_note = "The lens was redone after this -- redo this step too."

    check_v, check_note = None, ""
    if hist_p.exists():
        lines = [ln for ln in hist_p.read_text().splitlines() if ln.strip()]
        if lines:
            last = json.loads(lines[-1])
            kind = {"PASS": "ok", "WARN": "warn"}.get(last.get("verdict"), "bad")
            check_v = Verdict(kind, f"{last.get('verdict')}: {last.get('pos_rms_mm', 0):.0f} mm "
                              f"off on average, {last.get('pos_max_mm', 0):.0f} mm worst "
                              f"({_date(last.get('checked_at'))})")
            try:
                checked = datetime.fromisoformat(last["checked_at"]).timestamp()
                if fld is not None and field_p.stat().st_mtime > checked + 1:
                    check_note = "The calibration changed after this check -- check again."
            except (KeyError, ValueError):
                pass

    steps = [
        {"key": "lens", "title": "Camera lens", "verdict": lens_v, "note": "",
         "what": "Once per camera. Wave a printed checkerboard in front of it."},
        {"key": "field", "title": "Find the field", "verdict": field_v, "note": field_note,
         "what": "Whenever the camera moves. Put a tag on each corner of the field."},
        {"key": "check", "title": "Check the accuracy", "verdict": check_v, "note": check_note,
         "what": "After steps 1 and 2. Put a tag on 9 tape-measured spots."},
    ]
    nxt = next((s["key"] for s in steps
                if s["verdict"] is None or s["note"] or s["verdict"].kind != "ok"), None)
    for s in steps:
        s["next"] = s["key"] == nxt
    return steps


MENU_TITLE = "Camera calibration"
MENU_INTRO = [
    "Three steps, in order. Each one tells you what to do on screen. The step to do next "
    "is highlighted.",
]


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
