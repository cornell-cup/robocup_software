"""One command for the whole camera calibration, for someone who has never done it.

    uv run calibrate

Opens a menu of the three steps -- camera lens, find the field, check the
accuracy -- each showing whether it is done and how well, with the one to do
next highlighted. Picking a step runs that tool's guided flow in the same
window (calibrate_camera.guided, calibrate_field.guided, verify.guided), and
comes back here when it is finished. The separate commands still work on their
own; this only puts them in order.
"""

from __future__ import annotations

import argparse

from vision_core import ui

from . import calib_guide as guide
from . import calibrate_camera, calibrate_field, verify

FLOWS = {
    "lens": calibrate_camera.guided,
    "field": calibrate_field.guided,
    "check": verify.guided,
}


def menu_items() -> list[ui.MenuItem]:
    items = []
    for s in guide.status():
        v = s["verdict"]
        if v is None:
            kind, badge, detail = "todo", "NOT DONE", s["what"]
        else:
            kind = "warn" if s["note"] else v.kind
            badge = {"ok": "DONE", "warn": "CHECK", "bad": "REDO"}.get(kind, "")
            detail = v.headline + (f"  {s['note']}" if s["note"] else "")
        items.append(ui.MenuItem(s["key"], s["title"], detail, kind, badge, s["next"]))
    return items


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    window = ui.Window("calibrate")
    try:
        while True:
            key = ui.menu(window, guide.MENU_TITLE, guide.MENU_INTRO, menu_items(),
                          buttons=(("quit", "Quit"),))
            if key == "quit":
                return
            FLOWS[key](window)
    finally:
        window.close()


if __name__ == "__main__":
    main()
