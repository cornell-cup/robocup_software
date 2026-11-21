"""Python -> blocks: a team file as Google Blockly blocks, for the Code page.

to_blocks() reads Python with Python's own parser and builds the blocks the
Code page shows (their definitions and the way back, blocks -> Python, are in
app/web/src/blocks/robotBlocks.ts). It recognises the exact Python those
blocks write, so a team can go back and forth:

- a role function, def attacker(me, world) (or supporter, defender, goalie),
  becomes a "when I'm the attacker" block; inside it, moves (return
  me.go_to(...), me.kick_toward(...), ...), if / elif / else, variables,
  drawing and showing, numbers, places and conditions become their blocks;
- anything blocks can't say becomes a "Python" block holding that code:
  a helper function, PLAYS, a line inside a role. Converting back gives
  the code again, so nothing is lost but the comments inside a role
  function (counted, so the page can say so).
"""

from __future__ import annotations

import ast
import io
import itertools
import textwrap
import tokenize
from typing import Any

ROLES = ("attacker", "supporter", "defender", "goalie")
COLORS = ("green", "red", "yellow", "blue", "white", "orange", "purple")
Block = dict[str, Any]

# Python the blocks write, parsed once, to recognise it.
_PASS = ast.dump(ast.parse(
    "me.pass_to(min((m for m in world.teammates if m.id != me.id), "
    "key=lambda m: me.distance_to(m.pos), default=me))", mode="eval").body)
_NEAREST = ast.dump(ast.parse("min(world.opponents, key=lambda o: me.distance_to(o.pos)).pos", mode="eval").body)
_POINTS = {"world.ball.pos": "ball", "world.our_goal": "our_goal", "world.their_goal": "their_goal", "me.pos": "me"}
_NUMBERS = {"world.ball.speed": "ball_speed", "world.field_size[0]": "length", "world.field_size[1]": "width",
            "world.time_left": "time_left", "world.score[0]": "our_score", "world.score[1]": "their_score",
            "world.us.race_margin": "race"}
_ARITH = {ast.Add: "ADD", ast.Sub: "MINUS", ast.Mult: "MULTIPLY", ast.Div: "DIVIDE", ast.Pow: "POWER"}
_COMPARE = {ast.Eq: "EQ", ast.NotEq: "NEQ", ast.Lt: "LT", ast.LtE: "LTE", ast.Gt: "GT", ast.GtE: "GTE"}
_HEADER_IMPORTS = {"Command", "Me", "World"}
# Roles with a "play like the built-in ..." block (strategy/tactics/<role>.py).
BUILT_IN_ROLES = ("attacker", "supporter", "defender")


def to_blocks(source: str) -> dict[str, Any]:
    """The blocks for a team file: {"state": Blockly's workspace JSON,
    "roles": [roles made into blocks], "python_blocks": how many Python
    blocks, "dropped_comments": comments inside role functions}."""
    tree = ast.parse(source)
    conv = _Converter(source)
    lines = source.splitlines()
    top: list[Block] = []
    # Lines that don't go into a Python block: the header imports (the blocks'
    # Python starts with them) and the module docstring (a block of its own).
    skip: set[int] = set()
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        doc = body[0]
        top.append(_code("\n".join(lines[doc.lineno - 1:doc.end_lineno])))
        skip |= set(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    for node in body:
        if _is_header_import(node):
            skip |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    def code_between(first: int, last: int) -> None:
        """Lines first..last (1-based) as one Python block, exactly as written."""
        kept = [lines[i - 1] for i in range(first, last + 1) if i not in skip]
        while kept and not kept[0].strip():
            kept.pop(0)
        while kept and not kept[-1].strip():
            kept.pop()
        if kept:
            top.append(_code(textwrap.dedent("\n".join(kept))))

    after = 1  # the first line not yet handled
    for node in body:
        block = _roles_block(node)
        if block is None and not _is_role(node):
            continue
        assert isinstance(node, ast.FunctionDef)
        code_between(after, node.lineno - 1)  # everything since the last role: one Python block
        if block is not None:
            conv.roles.append("roles")
        top.append(block if block is not None else conv.role(node))
        conv.dropped += _comments_in(source, node.lineno, node.end_lineno or node.lineno)
        after = (node.end_lineno or node.lineno) + 1
    code_between(after, len(lines))
    # No places: the Blocks view stacks them in this order, measured as drawn.
    return {
        "state": {"blocks": {"languageVersion": 0, "blocks": top},
                  "variables": [{"name": n, "id": f"var_{n}"} for n in sorted(conv.variables)]},
        "roles": conv.roles,
        "python_blocks": sum(b["type"] == "py_code" for b in top) + conv.raw,
        "dropped_comments": conv.dropped,
    }


class _Converter:
    def __init__(self, source: str):
        self.source = source
        self.variables: set[str] = set()
        self.roles: list[str] = []
        self.raw = 0  # Python blocks inside roles
        self.dropped = 0
        self._locals: set[str] = set()

    # -- a role ---------------------------------------------------------------------

    def role(self, fn: ast.FunctionDef) -> Block:
        self.roles.append(fn.name)
        body = list(fn.body)
        block: Block = {"type": "robo_role", "fields": {"ROLE": fn.name}}
        doc, body = _docstring(body)
        if doc:
            block["data"] = doc  # its tooltip shows it; the Python keeps it as the docstring
        if body and _src(body[-1]) == "return me.stop()":
            body = body[:-1]  # the blocks' Python ends every role with it
        assigned = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        self.variables |= assigned
        self._locals = assigned
        chain = self.statements(body)
        if chain:
            block["inputs"] = {"DO": {"block": chain}}
        return block

    def statements(self, body: list[ast.stmt]) -> Block | None:
        """A list of statements as a chain of blocks (each the next of the one before)."""
        blocks = [self.statement(s, last=i == len(body) - 1) for i, s in enumerate(body)]
        blocks = [b for b in blocks if b is not None]
        for a, b in itertools.pairwise(blocks):
            a["next"] = {"block": b}
        return blocks[0] if blocks else None

    def statement(self, s: ast.stmt, last: bool) -> Block | None:
        if isinstance(s, ast.Pass):
            return None
        if isinstance(s, ast.Return) and s.value is not None and last:
            return self.move(s.value)
        if isinstance(s, ast.If):
            return self.if_block(s)
        if isinstance(s, ast.Assign) and len(s.targets) == 1:
            target = s.targets[0]
            if isinstance(target, ast.Name) and target.id in self._locals:
                return {"type": "variables_set", "fields": {"VAR": {"id": f"var_{target.id}"}},
                        "inputs": {"VALUE": {"block": self.expr(s.value)[0]}}}
            if isinstance(target, ast.Subscript) and _src(target.value) == "world.memory" \
                    and isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                return {"type": "robo_remember", "fields": {"KEY": target.slice.value},
                        "inputs": {"VALUE": {"block": self.expr(s.value)[0]}}}
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            block = self.call_statement(s.value)
            if block:
                return block
        return self.raw_statement(s)

    def move(self, value: ast.expr) -> Block:
        """A move: what `return value` does, as a block."""
        if _src(value) == "me.stop()":
            return {"type": "robo_stop"}
        if ast.dump(value) == _PASS:
            return {"type": "robo_pass"}
        for role in BUILT_IN_ROLES:
            if _src(value) == f"built_in_{role}(me, world)":
                return {"type": "robo_built_in", "fields": {"ROLE": role}}
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
                and isinstance(value.func.value, ast.Name) and value.func.value.id == "me":
            name, args, kw = value.func.attr, value.args, {k.arg: k.value for k in value.keywords}
            simple = {"go_to_ball": "robo_go_to_ball", "intercept": "robo_intercept"}
            one_point = {"face": ("robo_face", "POINT"), "kick_toward": ("robo_kick_toward", "TARGET"),
                         "dribble_to": ("robo_dribble_to", "TARGET")}
            if name in simple and not args and not kw:
                return {"type": simple[name]}
            if name in one_point and len(args) == 1 and not kw:
                kind, slot = one_point[name]
                return {"type": kind, "inputs": {slot: {"block": self.need(args[0], "Point")}}}
            if name == "go_to" and len(args) == 1 and set(kw) <= {"face", "kick", "dribble"}:
                flags = [k for k in ("kick", "dribble") if k in kw]
                if all(_src(kw[k]) == "True" for k in flags) and len(flags) <= 1:
                    block: Block = {"type": "robo_go_to", "fields": {"ACTION": flags[0] if flags else "none"},
                                    "inputs": {"TARGET": {"block": self.need(args[0], "Point")}}}
                    if "face" in kw:
                        block["inputs"]["FACE"] = {"block": self.need(kw["face"], "Point")}
                    return block
        self.raw += 1
        return {"type": "py_return", "fields": {"EXPR": ast.unparse(value)}}

    def if_block(self, s: ast.If) -> Block:
        block: Block = {"type": "controls_if", "inputs": {}}
        branches, node = 0, s
        while True:
            block["inputs"][f"IF{branches}"] = {"block": self.need(node.test, "Boolean")}
            chain = self.statements(node.body)
            if chain:
                block["inputs"][f"DO{branches}"] = {"block": chain}
            branches += 1
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]  # elif
                continue
            break
        extra: dict[str, Any] = {}
        if branches > 1:
            extra["elseIfCount"] = branches - 1
        if node.orelse:
            extra["hasElse"] = True
            chain = self.statements(node.orelse)
            if chain:
                block["inputs"]["ELSE"] = {"block": chain}
        if extra:
            block["extraState"] = extra
        return block

    def call_statement(self, call: ast.Call) -> Block | None:
        f = _src(call.func)
        kw = {k.arg: k.value for k in call.keywords}
        color = kw.get("color")
        color_ok = isinstance(color, ast.Constant) and color.value in COLORS and set(kw) == {"color"}
        if f == "world.draw.point" and len(call.args) == 1 and color_ok:
            assert isinstance(color, ast.Constant)
            return {"type": "robo_draw_point", "fields": {"COLOR": color.value},
                    "inputs": {"POINT": {"block": self.need(call.args[0], "Point")}}}
        if f == "world.draw.line" and len(call.args) == 2 and color_ok:
            assert isinstance(color, ast.Constant)
            return {"type": "robo_draw_line", "fields": {"COLOR": color.value},
                    "inputs": {"FROM": {"block": self.need(call.args[0], "Point")},
                               "TO": {"block": self.need(call.args[1], "Point")}}}
        if f == "world.show" and len(call.args) == 2 and not kw and isinstance(call.args[0], ast.Constant) \
                and isinstance(call.args[0].value, str):
            return {"type": "robo_show", "fields": {"NAME": call.args[0].value},
                    "inputs": {"VALUE": {"block": self.expr(call.args[1])[0]}}}
        return None

    def raw_statement(self, s: ast.stmt) -> Block:
        self.raw += 1
        segment = ast.get_source_segment(self.source, s, padded=True) or ast.unparse(s)
        return {"type": "py_stmt", "fields": {"CODE": textwrap.dedent(segment)}}

    # -- values ---------------------------------------------------------------------------

    def need(self, node: ast.expr, kind: str) -> Block:
        """A value block that fits an input taking `kind` (a Python block if nothing else does)."""
        block, out = self.expr(node)
        return block if out in (kind, None) else self.raw_value(node)

    def raw_value(self, node: ast.expr) -> Block:
        self.raw += 1
        return {"type": "py_expr", "fields": {"EXPR": ast.unparse(node)}}

    def expr(self, node: ast.expr) -> tuple[Block, str | None]:
        """A value block and its type: "Point", "Number", "Boolean" or None (anything)."""
        s = _src(node)
        if s in _POINTS:
            return {"type": "robo_point_of", "fields": {"WHICH": _POINTS[s]}}, "Point"
        if s in _NUMBERS:
            return {"type": "robo_number_of", "fields": {"WHAT": _NUMBERS[s]}}, "Number"
        simple = {"world.ball.stops_at": ("robo_ball_stops", "Point"), "me.has_ball": ("robo_has_ball", "Boolean"),
                  "world.kicked_off": ("robo_kicked_off", "Boolean")}
        if s in simple:
            return {"type": simple[s][0]}, simple[s][1]
        if ast.dump(node) == _NEAREST:
            return {"type": "robo_nearest_opponent"}, "Point"
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return {"type": "logic_boolean", "fields": {"BOOL": "TRUE" if node.value else "FALSE"}}, "Boolean"
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return _num(node.value), "Number"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) \
                and isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
            return _num(-node.operand.value), "Number"
        if isinstance(node, ast.Name) and node.id in self._locals:
            return {"type": "variables_get", "fields": {"VAR": {"id": f"var_{node.id}"}}}, None
        found = self._call(node) or self._pattern(node) or self._logic(node)
        return found if found else (self.raw_value(node), None)

    def _call(self, node: ast.expr) -> tuple[Block, str | None] | None:
        if not isinstance(node, ast.Call) or node.keywords and _src(node.func) != "world.memory.get":
            return None
        f, args = _src(node.func), node.args
        if f == "world.ball.at" and len(args) == 1:
            return {"type": "robo_ball_in", "inputs": {"T": {"block": self.need(args[0], "Number")}}}, "Point"
        if f == "np.array" and len(args) == 1 and isinstance(args[0], ast.Tuple) and len(args[0].elts) == 2:
            x, y = args[0].elts
            return {"type": "robo_xy", "inputs": {"X": {"block": self.need(x, "Number")},
                                                  "Y": {"block": self.need(y, "Number")}}}, "Point"
        if f == "world.lane_clear" and len(args) == 2:
            return {"type": "robo_lane_clear", "inputs": {"A": {"block": self.need(args[0], "Point")},
                                                          "B": {"block": self.need(args[1], "Point")}}}, "Boolean"
        if f == "me.distance_to" and len(args) == 1:
            return {"type": "robo_distance", "inputs": {"A": {"block": self.expr(ast.parse("me.pos", mode="eval").body)[0]},
                                                        "B": {"block": self.need(args[0], "Point")}}}, "Number"
        if f == "float" and len(args) == 1:
            inner = args[0]
            if isinstance(inner, ast.Call) and _src(inner.func) == "np.linalg.norm" and len(inner.args) == 1 \
                    and isinstance(inner.args[0], ast.BinOp) and isinstance(inner.args[0].op, ast.Sub):
                return {"type": "robo_distance", "inputs": {
                    "A": {"block": self.need(inner.args[0].left, "Point")},
                    "B": {"block": self.need(inner.args[0].right, "Point")}}}, "Number"
            if isinstance(inner, ast.Subscript) and isinstance(inner.slice, ast.Constant) and inner.slice.value in (0, 1):
                return {"type": "robo_coord", "fields": {"AXIS": str(inner.slice.value)},
                        "inputs": {"POINT": {"block": self.need(inner.value, "Point")}}}, "Number"
        if f == "world.memory.get" and len(args) == 2 and not node.keywords \
                and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            return {"type": "robo_recall", "fields": {"KEY": args[0].value},
                    "inputs": {"DEFAULT": {"block": self.expr(args[1])[0]}}}, None
        return None

    def _pattern(self, node: ast.expr) -> tuple[Block, str | None] | None:
        """(A + (B - A) * F): between; (P + np.array((DX, DY))): moved by; the halves."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            right = node.right
            if isinstance(right, ast.BinOp) and isinstance(right.op, ast.Mult) and isinstance(right.left, ast.BinOp) \
                    and isinstance(right.left.op, ast.Sub) and ast.dump(right.left.right) == ast.dump(node.left):
                return {"type": "robo_between", "inputs": {
                    "F": {"block": self.need(right.right, "Number")},
                    "A": {"block": self.need(node.left, "Point")},
                    "B": {"block": self.need(right.left.left, "Point")}}}, "Point"
            if isinstance(right, ast.Call) and _src(right.func) == "np.array" and len(right.args) == 1 \
                    and isinstance(right.args[0], ast.Tuple) and len(right.args[0].elts) == 2:
                dx, dy = right.args[0].elts
                return {"type": "robo_offset", "inputs": {
                    "POINT": {"block": self.need(node.left, "Point")},
                    "DX": {"block": self.need(dx, "Number")}, "DY": {"block": self.need(dy, "Number")}}}, "Point"
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)) \
                and _src(node.comparators[0]) == "world.attack_angle == 0" and isinstance(node.left, ast.Compare) \
                and len(node.left.ops) == 1 and isinstance(node.left.ops[0], ast.Lt) \
                and _src(node.left.comparators[0]) == "world.field_size[0] / 2" \
                and isinstance(node.left.left, ast.Call) and _src(node.left.left.func) == "float" \
                and len(node.left.left.args) == 1 and isinstance(node.left.left.args[0], ast.Subscript) \
                and _src(node.left.left.args[0].slice) == "0":
            whose = "our" if isinstance(node.ops[0], ast.Eq) else "their"
            return {"type": "robo_in_half", "fields": {"WHOSE": whose},
                    "inputs": {"POINT": {"block": self.need(node.left.left.args[0].value, "Point")}}}, "Boolean"
        return None

    def _logic(self, node: ast.expr) -> tuple[Block, str | None] | None:
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            left, lt = self.expr(node.left)
            right, rt = self.expr(node.right)
            if lt in ("Number", None) and rt in ("Number", None):
                return {"type": "math_arithmetic", "fields": {"OP": _ARITH[type(node.op)]},
                        "inputs": {"A": {"block": left}, "B": {"block": right}}}, "Number"
            return None
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            return {"type": "logic_compare", "fields": {"OP": _COMPARE[type(node.ops[0])]},
                    "inputs": {"A": {"block": self.expr(node.left)[0]},
                               "B": {"block": self.expr(node.comparators[0])[0]}}}, "Boolean"
        if isinstance(node, ast.BoolOp):
            op = "AND" if isinstance(node.op, ast.And) else "OR"
            block = self.need(node.values[0], "Boolean")
            for value in node.values[1:]:
                block = {"type": "logic_operation", "fields": {"OP": op},
                         "inputs": {"A": {"block": block}, "B": {"block": self.need(value, "Boolean")}}}
            return block, "Boolean"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return {"type": "logic_negate", "inputs": {"BOOL": {"block": self.need(node.operand, "Boolean")}}}, "Boolean"
        return None


def _src(node: ast.AST) -> str:
    return ast.unparse(node)


def _num(value: float) -> Block:
    return {"type": "math_number", "fields": {"NUM": value}}


def _code(text: str) -> Block:
    """Python kept as written, folded to its first line (a click opens it) so the
    blocks are what you see first. It still runs."""
    return {"type": "py_code", "fields": {"CODE": text.rstrip()}, "collapsed": True}


def _is_role(node: ast.stmt) -> bool:
    if not isinstance(node, ast.FunctionDef) or node.name not in ROLES or node.decorator_list:
        return False
    a = node.args
    return ([p.arg for p in a.posonlyargs + a.args] == ["me", "world"] and not a.defaults
            and a.vararg is None and a.kwarg is None and not a.kwonlyargs)


def _docstring(body: list[ast.stmt]) -> tuple[str, list[ast.stmt]]:
    """A function's docstring (as written, trimmed) and the rest of its body."""
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0].value.value.strip(), body[1:]
    return "", body


def _roles_block(node: ast.stmt) -> Block | None:
    """roles(team, world) as the "who plays which role" block, when it's one the
    block can say: the built-in way, or everyone in one role."""
    if not isinstance(node, ast.FunctionDef) or node.name != "roles" or node.decorator_list:
        return None
    a = node.args
    if [p.arg for p in a.posonlyargs + a.args] != ["team", "world"] or a.defaults or a.vararg or a.kwarg \
            or a.kwonlyargs:
        return None
    doc, body = _docstring(list(node.body))
    if len(body) != 1:
        return None
    code = _src(body[0])
    way = "built_in" if code == "return built_in_roles(team, world)" else next(
        (r for r in BUILT_IN_ROLES if code == f"return [(robot, '{r}') for robot in team]"), None)
    if way is None:
        return None
    block: Block = {"type": "robo_roles", "fields": {"WAY": way}}
    if doc:
        block["data"] = doc
    return block


def _is_header_import(node: ast.stmt) -> bool:
    """An import the blocks' Python writes itself: numpy, strategy.api, and a built-in
    role (from strategy.tactics.attacker import attacker as built_in_attacker),
    which it adds whenever the code uses it."""
    if isinstance(node, ast.Import):
        return [(n.name, n.asname) for n in node.names] == [("numpy", "np")]
    if not isinstance(node, ast.ImportFrom) or node.level != 0:
        return False
    if node.module == "strategy.api":
        return {n.name for n in node.names} <= _HEADER_IMPORTS and all(n.asname is None for n in node.names)
    if [(n.name, n.asname) for n in node.names] == [("roles", "built_in_roles")]:
        return node.module == "strategy.tactics.role_assignments"
    return any(node.module == f"strategy.tactics.{r}" for r in BUILT_IN_ROLES) and len(node.names) == 1 \
        and node.module.rsplit(".", 1)[1] == node.names[0].name and node.names[0].asname == f"built_in_{node.names[0].name}"


def _comments_in(source: str, first: int, last: int) -> int:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return sum(1 for t in tokens if t.type == tokenize.COMMENT and first <= t.start[0] <= last)
    except (tokenize.TokenError, SyntaxError):
        return 0
