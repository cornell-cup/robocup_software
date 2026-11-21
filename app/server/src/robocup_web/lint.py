"""The Code page's lint and hints: mistakes underlined while students type.

lint() checks one file's text, without running it:

- ruff (`python -m ruff`), with the rules that catch mistakes rather than
  style: syntax errors, undefined names (a typo), unused imports and
  variables, a few real bug patterns. Messages are reworded for students.
- checks only this platform knows (team.py): a slot with the wrong
  parameters (`def supporter(me)`), a role or skill that never returns
  anything, PLAYS without a pick_play, and names that don't exist on a robot
  or the world (`me.kick_towards`: did you mean kick_toward?).

The names and slots come from reference(): strategy/core/reference.py, run
once in a child process (the server doesn't import the strategy).
"""

from __future__ import annotations

import ast
import difflib
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
# Mistakes, not style: syntax and IO errors, pyflakes, pylint's errors, and
# bugbear's mutable or called defaults and statements that do nothing.
RULES = "E9,F,PLE,B006,B008,B015,B018"
TIMEOUT_S = 10.0
_reference: dict[str, Any] | None = None
_lock = threading.Lock()

_FRIENDLY = {
    "F821": "isn't defined: a typo, or a missing import?",
    "F841": "is set but never used",
    "F401": "is imported but never used",
    "F811": "is defined twice: the second one replaces the first",
    "F632": "use == to compare values (is checks for the very same object)",
    "B006": "a list, dict or set as a default value is shared between calls: use None and make it inside",
    "B018": "this line does nothing: did you forget to return it, or to call it?",
    "B015": "this comparison does nothing: did you mean to use it in an if, or return it?",
}


def reference() -> dict[str, Any]:
    """What team code can use (strategy/core/reference.py), read once."""
    global _reference
    with _lock:
        if _reference is None:
            r = subprocess.run([sys.executable, "-m", "strategy.core.reference"], cwd=REPO,
                               capture_output=True, text=True, timeout=60, check=True)
            _reference = json.loads(r.stdout)
        return _reference


def lint(file_name: str, text: str, entry: bool) -> list[dict[str, Any]]:
    """Problems in one file of a workspace, as [{line, col, end_line,
    end_col, severity, message, code}], lines and columns from 1. `entry`:
    it's team.py (the slots live there)."""
    found = _ruff(file_name, text)
    if entry:
        found += _platform_checks(text)
    found.sort(key=lambda d: (d["line"], d["col"]))
    return found


def _ruff(file_name: str, text: str) -> list[dict[str, Any]]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--select", RULES,
             "--output-format", "json", "--stdin-filename", file_name, "-"],
            input=text, capture_output=True, text=True, timeout=TIMEOUT_S, check=False, cwd=REPO,
        )
        items = json.loads(r.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return _syntax_only(file_name, text)  # no ruff: syntax errors at least
    out = []
    for item in items:
        code = item.get("code") or "syntax"
        message = item.get("message", "")
        name = message.split("`")[1] if message.count("`") >= 2 else None
        if code in _FRIENDLY:
            message = f"`{name}` {_FRIENDLY[code]}" if name and code.startswith("F") and code != "F632" \
                else _FRIENDLY[code]
        elif code in ("syntax", "invalid-syntax"):
            message = "syntax error: " + message.removeprefix("SyntaxError: ")
        start, end = item.get("location") or {}, item.get("end_location") or {}
        out.append({
            "line": start.get("row", 1), "col": start.get("column", 1),
            "end_line": end.get("row", start.get("row", 1)), "end_col": end.get("column", start.get("column", 1)),
            "severity": "error" if code in ("syntax", "invalid-syntax", "F821", "F632") or code.startswith(("E9", "PLE"))
            else "warning",
            "message": message, "code": code,
        })
    return out


def _syntax_only(file_name: str, text: str) -> list[dict[str, Any]]:
    try:
        compile(text, file_name, "exec")
        return []
    except SyntaxError as e:
        line, col = e.lineno or 1, e.offset or 1
        return [_diag(line, col, line, col + 1, "error", f"syntax error: {e.msg}", "syntax")]


def _diag(line: int, col: int, end_line: int, end_col: int, severity: str, message: str,
          code: str) -> dict[str, Any]:
    return {"line": line, "col": col, "end_line": end_line, "end_col": end_col,
            "severity": severity, "message": message, "code": code}


def _platform_checks(text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []  # ruff says where
    ref = reference()
    slots = {s["name"]: s for s in ref["slots"]}
    out: list[dict[str, Any]] = []
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in slots:
            slot = slots[node.name]
            params = slot["params"]
            args = node.args
            positional = args.posonlyargs + args.args
            required = len(positional) - len(args.defaults)
            if args.vararg is None and not required <= len(params) <= len(positional):
                out.append(_at(node, "error",
                               f"{node.name} is called with ({', '.join(params)}): write "
                               f"def {node.name}({', '.join(params)}):", "slot-params"))
            if slot["returns"] != "nothing" and not _returns_value(node):
                out.append(_at(node, "warning",
                               f"{node.name} never returns anything: it should give back "
                               f"{slot['returns']}, or the built-in {node.name} plays instead",
                               "slot-return"))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLAYS"
                                                for t in node.targets) and "pick_play" not in defined:
            out.append(_at(node, "warning", "PLAYS needs a def pick_play(world) that picks one of them",
                           "plays"))

    names = {key: {m["name"] for m in ref[key]} for key in ("me", "world", "ball", "us", "draw")}
    what = {"me": "a robot", "world": "the world", "ball": "the ball", "us": "world.us", "draw": "world.draw"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        owner = _owner(node.value)
        if owner and node.attr not in names[owner] and not node.attr.startswith("_"):
            close = difflib.get_close_matches(node.attr, names[owner], n=1)
            hint = f": did you mean {close[0]}?" if close else ""
            out.append(_diag(node.end_lineno or node.lineno, (node.end_col_offset or 0) - len(node.attr) + 1,
                             node.end_lineno or node.lineno, (node.end_col_offset or 0) + 1, "warning",
                             f"{what[owner]} has no {node.attr}{hint}", "unknown-name"))
    return out


def _owner(value: ast.expr) -> str | None:
    """Which reference table an attribute's owner is: me, world, world.ball,
    world.us or world.draw (only these names: anything else may be anything)."""
    if isinstance(value, ast.Name) and value.id in ("me", "world"):
        return value.id
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "world" \
            and value.attr in ("ball", "us", "draw"):
        return value.attr
    return None


def _returns_value(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the function itself (not one inside it) ever returns a value."""
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Return) and node.value is not None:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _at(node: ast.stmt, severity: str, message: str, code: str) -> dict[str, Any]:
    """A problem on a statement's first line (its name, for a def)."""
    col = node.col_offset + 1
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        col += len("async def " if isinstance(node, ast.AsyncFunctionDef) else "def ")
        end = col + len(node.name)
    else:
        end = col + 5
    return _diag(node.lineno, col, node.lineno, end, severity, message, code)
