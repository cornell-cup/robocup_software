// The Code page's blocks (Google Blockly): roles, moves, positions, numbers
// and conditions for our robots, and the Python each one becomes.
//
// Blocks -> Python happens here (generate()). Python -> blocks happens on the
// server (app/server/src/robocup_web/blocks.py), which reads the exact Python
// these generators write, so a team can go back and forth. Anything blocks
// can't say lives in a "Python" block holding the code itself, so converting
// never loses code.

import { registerFieldMultilineInput } from "@blockly/field-multilineinput";
import * as Blockly from "blockly/core";
import "blockly/blocks";
import * as En from "blockly/msg/en";
import { Order, pythonGenerator } from "blockly/python";

// Each category's style (its colour, in the theme: BlocksEditor.tsx). Python is grey:
// it's code kept as written, not a block to snap things into.
const ROLE = "robo_role";
const MOVE = "robo_move";
const PLACE = "robo_place";
const NUMBER = "robo_number";
const CHECK = "robo_check";
const SHOW = "robo_show";
const VARIABLE = "robo_variable";
const RAW = "robo_python";
export const PALETTE: Record<string, string> = {
  [ROLE]: "#b4462b",
  [MOVE]: "#1d8058",
  [PLACE]: "#2f66c8",
  [NUMBER]: "#6f4cc2",
  [CHECK]: "#1b7a8c",
  [SHOW]: "#a33b72",
  [VARIABLE]: "#9a6b1c",
  [RAW]: "#44444c",
};
// Blockly's own blocks (if, compare, numbers, variables) in our categories' colours.
export const BUILT_IN_STYLES: Record<string, string> = {
  logic_blocks: CHECK,
  math_blocks: NUMBER,
  variable_blocks: VARIABLE,
  variable_dynamic_blocks: VARIABLE,
};

const COLORS: [string, string][] = [
  ["green", "green"], ["red", "red"], ["yellow", "yellow"], ["blue", "blue"],
  ["white", "white"], ["orange", "orange"], ["purple", "purple"],
];

export const HATS = new Set(["robo_roles", "robo_role", "py_code"]);
// Blocks that end what a robot does this frame (they return its command).
const MOVES = new Set(["robo_go_to", "robo_face", "robo_stop", "robo_go_to_ball", "robo_kick_toward",
  "robo_intercept", "robo_dribble_to", "robo_pass", "robo_built_in", "py_return"]);
// Roles with a "play like the built-in ..." block: strategy/tactics/<role>.py.
const BUILT_IN_ROLES = ["attacker", "supporter", "defender"];

let installed = false;

/** Define the blocks and their Python, once. */
export function installBlocks(): void {
  if (installed) return;
  installed = true;
  Blockly.setLocale(En as unknown as { [key: string]: string });
  registerFieldMultilineInput();
  pythonGenerator.INDENT = "    ";
  // Blockly writes a block's comment as # lines above its code; a role's comment is
  // its docstring already (and the converter reads it back from there), so not twice.
  pythonGenerator.scrub_ = function (block, code, thisOnly) {
    const next = block.nextConnection?.targetBlock();
    return code + (next && !thisOnly ? this.blockToCode(next) : "");
  };
  // A folded Python block shows its first line of code, not the start of its label.
  Blockly.Extensions.register("py_summary", function (this: Blockly.Block) {
    this.toString = () => {
      const lines = String(this.getFieldValue("CODE")).split("\n").map((l) => l.trim());
      const first = (lines.find((l) => l && !/^("""|''')$/.test(l)) ?? "").replace(/^("""|''')|("""|''')$/g, "");
      return `Python · ${first.length > 48 ? `${first.slice(0, 47)}…` : first}`;
    };
  });

  // A role's description (its docstring, kept in the block's data) shows in its tooltip.
  Blockly.Extensions.register("doc_tooltip", function (this: Blockly.Block) {
    const base = String(this.tooltip);
    this.setTooltip(() => (this.data ? `${this.data.replace(/\s*\n\s*/g, " ")}\n\n${base}` : base));
  });

  Blockly.common.defineBlocksWithJsonArray([
    // -- who plays which role (the roles slot)
    {
      type: "robo_roles",
      message0: "who plays which role: %1",
      args0: [{ type: "field_dropdown", name: "WAY", options: [["the built-in way", "built_in"],
        ["everyone attacks", "attacker"], ["everyone supports", "supporter"], ["everyone defends", "defender"]] }],
      style: ROLE,
      extensions: ["doc_tooltip"],
      tooltip: "Every frame, which robot plays which role. The built-in way: the robot closest to the ball "
        + "attacks, the next supports, the rest defend; a robot playing alone defends.",
    },
    // -- a role: what one robot does, 60 times a second
    {
      type: "robo_role",
      message0: "when I'm the %1",
      args0: [{ type: "field_dropdown", name: "ROLE", options: [["attacker", "attacker"], ["supporter", "supporter"], ["defender", "defender"], ["goalie", "goalie"]] }],
      message1: "%1",
      args1: [{ type: "input_statement", name: "DO" }],
      style: ROLE,
      extensions: ["doc_tooltip"],
      tooltip: "What a robot playing this role does, 60 times a second. End with a move: a block from Move. Without one, it stops.",
    },
    // -- moves: each ends what the robot does this frame
    move("robo_go_to", "go to %1 facing %2 %3", [point("TARGET"), point("FACE"),
      { type: "field_dropdown", name: "ACTION", options: [["without kicking", "none"], ["and kick", "kick"], ["with the dribbler on", "dribble"]] }],
      "Drive to a point. Facing: where to look (leave it empty to face where you drive)."),
    move("robo_face", "turn to face %1", [point("POINT")], "Stay here and turn toward a point."),
    move("robo_stop", "stop", [], "Stay where you are."),
    move("robo_go_to_ball", "go to the ball", [], "Drive to the ball, facing it."),
    move("robo_kick_toward", "send the ball toward %1", [point("TARGET")], "Get behind the ball on the line to a point, and push it there."),
    move("robo_intercept", "meet the rolling ball", [], "Go where you can first reach a rolling ball, and play it on toward their goal."),
    move("robo_dribble_to", "dribble the ball to %1", [point("TARGET")], "Take the ball with the dribbler on and carry it to a point."),
    move("robo_pass", "pass to my nearest teammate", [], "Kick the ball to the teammate nearest you, where they're going to be."),
    move("robo_built_in", "play like the built-in %1", [{ type: "field_dropdown", name: "ROLE", options: BUILT_IN_ROLES.map((r) => [r, r]) }],
      "Do what the built-in team's robot in this role does (strategy/tactics/). Start here, then swap it for your own blocks."),
    move("py_return", "Python move: return %1", [{ type: "field_input", name: "EXPR", text: "me.stop()" }],
      "A move written in Python, for what the Move blocks can't say: any Python that gives back a command."),
    // -- things to do on the way (they don't end the frame)
    step("robo_draw_point", "draw a dot at %1 in %2", [point("POINT"), { type: "field_dropdown", name: "COLOR", options: COLORS }], SHOW, "Draw a dot on the field (turn on Field to see it)."),
    step("robo_draw_line", "draw a line from %1 to %2 in %3", [point("FROM"), point("TO"), { type: "field_dropdown", name: "COLOR", options: COLORS }], SHOW, "Draw a line on the field."),
    step("robo_show", "show %1 = %2", [{ type: "field_input", name: "NAME", text: "value" }, { type: "input_value", name: "VALUE" }], SHOW, "Show a value in the Values card."),
    step("robo_remember", "remember %1 = %2", [{ type: "field_input", name: "KEY", text: "note" }, { type: "input_value", name: "VALUE" }], SHOW, "Keep a value for the next frames (world.memory)."),
    step("py_stmt", "Python step %1", [{ type: "field_multilinetext", name: "CODE", text: "pass" }], RAW,
      "Python lines that run here, for what the blocks can't say yet (a loop, a calculation). Kept exactly as written."),
    // -- places on the field
    value("robo_point_of", "%1", [{ type: "field_dropdown", name: "WHICH", options: [["the ball", "ball"], ["our goal", "our_goal"], ["their goal", "their_goal"], ["me", "me"]] }], "Point", PLACE, "A place on the field."),
    value("robo_ball_in", "the ball in %1 s", [number("T")], "Point", PLACE, "Where the ball will be in some seconds, slowing down as it rolls."),
    value("robo_ball_stops", "where the ball stops", [], "Point", PLACE, "Where the ball comes to rest if nothing touches it."),
    value("robo_xy", "point x %1 y %2", [number("X"), number("Y")], "Point", PLACE, "A point: x along the field, y across it, in metres."),
    value("robo_between", "%1 of the way from %2 to %3", [number("F"), point("A"), point("B")], "Point", PLACE, "A point part of the way between two points (0.5 is halfway)."),
    value("robo_offset", "%1 moved by x %2 y %3", [point("POINT"), number("DX"), number("DY")], "Point", PLACE, "A point moved some metres."),
    value("robo_nearest_opponent", "the nearest opponent", [], "Point", PLACE, "Where the opponent nearest you is."),
    // -- numbers
    value("robo_number_of", "%1", [{ type: "field_dropdown", name: "WHAT", options: [["ball speed", "ball_speed"], ["field length", "length"], ["field width", "width"], ["time left", "time_left"], ["our goals", "our_score"], ["their goals", "their_score"], ["seconds sooner we reach the ball", "race"]] }], "Number", NUMBER, "A number about the game."),
    value("robo_distance", "distance from %1 to %2", [point("A"), point("B")], "Number", NUMBER, "Metres between two points."),
    value("robo_coord", "%1 of %2", [{ type: "field_dropdown", name: "AXIS", options: [["x", "0"], ["y", "1"]] }, point("POINT")], "Number", NUMBER, "A point's x (along the field) or y (across it)."),
    value("robo_recall", "remembered %1 or else %2", [{ type: "field_input", name: "KEY", text: "note" }, { type: "input_value", name: "DEFAULT" }], null, NUMBER, "A value you remembered, or the second one if there's none yet."),
    // -- conditions
    value("robo_has_ball", "I have the ball", [], "Boolean", CHECK, "True when the ball is right in front of me."),
    value("robo_lane_clear", "no opponent between %1 and %2", [point("A"), point("B")], "Boolean", CHECK, "True when no opponent is on the straight line between two points."),
    value("robo_in_half", "%1 is in %2 half", [point("POINT"), { type: "field_dropdown", name: "WHOSE", options: [["our", "our"], ["their", "their"]] }], "Boolean", CHECK, "Which half of the field a point is in."),
    value("robo_kicked_off", "the kickoff has happened", [], "Boolean", CHECK, "False from a restart until the ball first moves."),
    value("py_expr", "Python value %1", [{ type: "field_input", name: "EXPR", text: "0" }], null, RAW,
      "A value written in Python, for what the other blocks can't say."),
    // -- whole Python lines at the top level: anything blocks can't say
    {
      type: "py_code",
      message0: "other Python in the file %1",
      args0: [{ type: "field_multilinetext", name: "CODE", text: "# your Python" }],
      style: RAW,
      extensions: ["py_summary"],
      tooltip: "Code in your file that isn't a role: the file's description, imports, helper functions, "
        + "parts switched off with #. It's kept exactly as written and still runs. A folded one opens with a click.",
    },
  ]);

  const g = pythonGenerator;
  const val = (b: Blockly.Block, name: string, order: Order, fallback: string) => g.valueToCode(b, name, order) || fallback;
  const pt = (b: Blockly.Block, name: string) => val(b, name, Order.ADDITIVE, "world.ball.pos");
  const num = (b: Blockly.Block, name: string) => val(b, name, Order.MULTIPLICATIVE, "0");
  const color = (b: Blockly.Block) => b.getFieldValue("COLOR");
  const text = (b: Blockly.Block, name: string) => JSON.stringify(String(b.getFieldValue(name)));

  // A function's docstring: kept in the block's data (older blocks: its comment).
  const docLine = (b: Blockly.Block) => {
    const doc = b.data || b.getCommentText();
    return doc ? `${g.INDENT}"""${doc.replace(/"""/g, "'''")}"""\n` : "";
  };
  g.forBlock.robo_roles = (b) => {
    const way = b.getFieldValue("WAY");
    const ret = way === "built_in" ? "built_in_roles(team, world)" : `[(robot, "${way}") for robot in team]`;
    return `def roles(team: list[Me], world: World) -> list[tuple[Me, str]]:\n${docLine(b)}${g.INDENT}return ${ret}\n`;
  };
  g.forBlock.robo_role = (b) => {
    const role = b.getFieldValue("ROLE");
    // A role whose last block isn't a move stops there (a robot always gets a command).
    let last = b.getInputTargetBlock("DO");
    while (last?.getNextBlock()) last = last.getNextBlock();
    const end = last && MOVES.has(last.type) ? "" : `${g.INDENT}return me.stop()\n`;
    return `def ${role}(me: Me, world: World) -> Command:\n${docLine(b)}${g.statementToCode(b, "DO")}${end}`;
  };
  g.forBlock.robo_go_to = (b) => {
    const face = g.valueToCode(b, "FACE", Order.NONE);
    const action = b.getFieldValue("ACTION");
    const extra = action === "kick" ? ", kick=True" : action === "dribble" ? ", dribble=True" : "";
    return `return me.go_to(${val(b, "TARGET", Order.NONE, "me.pos")}${face ? `, face=${face}` : ""}${extra})\n`;
  };
  g.forBlock.robo_face = (b) => `return me.face(${val(b, "POINT", Order.NONE, "world.ball.pos")})\n`;
  g.forBlock.robo_stop = () => "return me.stop()\n";
  g.forBlock.robo_go_to_ball = () => "return me.go_to_ball()\n";
  g.forBlock.robo_kick_toward = (b) => `return me.kick_toward(${val(b, "TARGET", Order.NONE, "world.their_goal")})\n`;
  g.forBlock.robo_intercept = () => "return me.intercept()\n";
  g.forBlock.robo_dribble_to = (b) => `return me.dribble_to(${val(b, "TARGET", Order.NONE, "world.their_goal")})\n`;
  g.forBlock.robo_pass = () =>
    "return me.pass_to(min((m for m in world.teammates if m.id != me.id), key=lambda m: me.distance_to(m.pos), default=me))\n";
  g.forBlock.robo_built_in = (b) => `return built_in_${b.getFieldValue("ROLE")}(me, world)\n`;
  g.forBlock.py_return = (b) => `return ${b.getFieldValue("EXPR")}\n`;
  g.forBlock.robo_draw_point = (b) => `world.draw.point(${val(b, "POINT", Order.NONE, "world.ball.pos")}, color="${color(b)}")\n`;
  g.forBlock.robo_draw_line = (b) =>
    `world.draw.line(${val(b, "FROM", Order.NONE, "me.pos")}, ${val(b, "TO", Order.NONE, "world.ball.pos")}, color="${color(b)}")\n`;
  g.forBlock.robo_show = (b) => `world.show(${text(b, "NAME")}, ${val(b, "VALUE", Order.NONE, "None")})\n`;
  g.forBlock.robo_remember = (b) => `world.memory[${text(b, "KEY")}] = ${val(b, "VALUE", Order.NONE, "None")}\n`;
  g.forBlock.py_stmt = (b) => `${b.getFieldValue("CODE")}\n`;
  g.forBlock.py_code = (b) => `${b.getFieldValue("CODE")}\n`;

  const POINTS: Record<string, string> = { ball: "world.ball.pos", our_goal: "world.our_goal", their_goal: "world.their_goal", me: "me.pos" };
  g.forBlock.robo_point_of = (b) => [POINTS[b.getFieldValue("WHICH")], Order.MEMBER];
  g.forBlock.robo_ball_in = (b) => [`world.ball.at(${val(b, "T", Order.NONE, "1")})`, Order.FUNCTION_CALL];
  g.forBlock.robo_ball_stops = () => ["world.ball.stops_at", Order.MEMBER];
  g.forBlock.robo_xy = (b) => [`np.array((${val(b, "X", Order.NONE, "0")}, ${val(b, "Y", Order.NONE, "0")}))`, Order.FUNCTION_CALL];
  g.forBlock.robo_between = (b) => {
    const a = pt(b, "A");
    return [`(${a} + (${pt(b, "B")} - ${a}) * ${num(b, "F")})`, Order.ATOMIC];
  };
  g.forBlock.robo_offset = (b) =>
    [`(${pt(b, "POINT")} + np.array((${val(b, "DX", Order.NONE, "0")}, ${val(b, "DY", Order.NONE, "0")})))`, Order.ATOMIC];
  g.forBlock.robo_nearest_opponent = () => ["min(world.opponents, key=lambda o: me.distance_to(o.pos)).pos", Order.MEMBER];

  const NUMBERS: Record<string, string> = {
    ball_speed: "world.ball.speed", length: "world.field_size[0]", width: "world.field_size[1]",
    time_left: "world.time_left", our_score: "world.score[0]", their_score: "world.score[1]", race: "world.us.race_margin",
  };
  g.forBlock.robo_number_of = (b) => [NUMBERS[b.getFieldValue("WHAT")], Order.MEMBER];
  g.forBlock.robo_distance = (b) => [`float(np.linalg.norm(${pt(b, "A")} - ${pt(b, "B")}))`, Order.FUNCTION_CALL];
  g.forBlock.robo_coord = (b) => [`float(${val(b, "POINT", Order.MEMBER, "world.ball.pos")}[${b.getFieldValue("AXIS")}])`, Order.FUNCTION_CALL];
  g.forBlock.robo_recall = (b) => [`world.memory.get(${text(b, "KEY")}, ${val(b, "DEFAULT", Order.NONE, "None")})`, Order.FUNCTION_CALL];
  g.forBlock.robo_has_ball = () => ["me.has_ball", Order.MEMBER];
  g.forBlock.robo_lane_clear = (b) =>
    [`world.lane_clear(${val(b, "A", Order.NONE, "world.ball.pos")}, ${val(b, "B", Order.NONE, "world.their_goal")})`, Order.FUNCTION_CALL];
  g.forBlock.robo_in_half = (b) => {
    const op = b.getFieldValue("WHOSE") === "our" ? "==" : "!=";
    return [`((float(${val(b, "POINT", Order.MEMBER, "world.ball.pos")}[0]) < world.field_size[0] / 2) ${op} (world.attack_angle == 0))`, Order.ATOMIC];
  };
  g.forBlock.robo_kicked_off = () => ["world.kicked_off", Order.MEMBER];
  g.forBlock.py_expr = (b) => [String(b.getFieldValue("EXPR")), Order.NONE];
}

/** The imports at the top: numpy and the API always, a built-in role when the code uses it. */
function header(body: string): string {
  const roles = BUILT_IN_ROLES.filter((r) => new RegExp(`\\bbuilt_in_${r}\\b`).test(body));
  return "import numpy as np  # noqa: F401\n\nfrom strategy.api import Command, Me, World  # noqa: F401\n"
    + roles.map((r) => `from strategy.tactics.${r} import ${r} as built_in_${r}\n`).join("")
    + (/\bbuilt_in_roles\b/.test(body) ? "from strategy.tactics.role_assignments import roles as built_in_roles\n" : "");
}

/** The Python a workspace of blocks makes: the header, then each top block
 *  (roles and Python blocks) in order from top to bottom. */
export function generate(ws: Blockly.Workspace): string {
  installBlocks();
  pythonGenerator.init(ws);
  const parts: string[] = [];
  for (const block of ws.getTopBlocks(true)) {
    if (!HATS.has(block.type)) continue; // loose blocks don't run: they're not in a role
    const code = pythonGenerator.blockToCode(block, true);
    if (typeof code === "string" && code.trim()) parts.push(code.trimEnd());
  }
  // A team's own docstring (a Python block holding only that, at the very top) stays first.
  let docstring = '"""My team, made with blocks."""';
  if (parts.length && /^("""[\s\S]*"""|'''[\s\S]*''')$/.test(parts[0].trim())) docstring = parts.shift()!.trim();
  // init() declares every variable at the top (x = None); a variable here lives in
  // its role (a local), so not that: it would also pile up on each round trip.
  delete (pythonGenerator as unknown as { definitions_: Record<string, string> }).definitions_.variables;
  const body = pythonGenerator.finish(parts.join("\n\n\n")).trim();
  return `${docstring}\n\n${header(body)}\n\n${body}\n`;
}

// Defaults already plugged in when a block comes out of the toolbox.
const NUM = (n: number) => ({ shadow: { type: "math_number", fields: { NUM: n } } });
const BALL = { shadow: { type: "robo_point_of", fields: { WHICH: "ball" } } };
const GOAL = { shadow: { type: "robo_point_of", fields: { WHICH: "their_goal" } } };
const SHADOWS: Record<string, object> = {
  robo_go_to: { TARGET: BALL, FACE: BALL },
  robo_face: { POINT: BALL },
  robo_kick_toward: { TARGET: GOAL },
  robo_dribble_to: { TARGET: GOAL },
  robo_ball_in: { T: NUM(1) },
  robo_xy: { X: NUM(4.5), Y: NUM(3) },
  robo_between: { F: NUM(0.4), A: BALL, B: GOAL },
  robo_offset: { POINT: BALL, DX: NUM(0), DY: NUM(1) },
  robo_distance: { A: { shadow: { type: "robo_point_of", fields: { WHICH: "me" } } }, B: BALL },
  robo_coord: { POINT: BALL },
  robo_recall: { DEFAULT: NUM(0) },
  robo_lane_clear: { A: BALL, B: GOAL },
  robo_in_half: { POINT: BALL },
  robo_draw_point: { POINT: BALL },
  robo_draw_line: { FROM: { shadow: { type: "robo_point_of", fields: { WHICH: "me" } } }, TO: BALL },
  robo_show: { VALUE: { shadow: { type: "robo_number_of", fields: { WHAT: "ball_speed" } } } },
  robo_remember: { VALUE: NUM(0) },
  math_arithmetic: { A: NUM(1), B: NUM(1) },
  logic_compare: { A: NUM(0), B: NUM(0) },
};

/** The toolbox: blocks by category. */
export const TOOLBOX = {
  kind: "categoryToolbox",
  contents: [
    cat("Roles", ROLE, ["robo_roles", "robo_role"]),
    cat("Move", MOVE, ["robo_built_in", "robo_go_to", "robo_go_to_ball", "robo_kick_toward", "robo_intercept", "robo_dribble_to", "robo_pass", "robo_face", "robo_stop"]),
    cat("Places", PLACE, ["robo_point_of", "robo_ball_in", "robo_ball_stops", "robo_between", "robo_offset", "robo_xy", "robo_nearest_opponent"]),
    cat("Numbers", NUMBER, ["math_number", "math_arithmetic", "robo_number_of", "robo_distance", "robo_coord", "robo_recall"]),
    cat("Conditions", CHECK, ["controls_if", "logic_compare", "logic_operation", "logic_negate", "logic_boolean", "robo_has_ball", "robo_lane_clear", "robo_in_half", "robo_kicked_off"]),
    cat("Show", SHOW, ["robo_draw_point", "robo_draw_line", "robo_show", "robo_remember"]),
    { kind: "category", name: "Variables", categorystyle: VARIABLE, custom: "VARIABLE" },
    cat("Python", RAW, ["py_stmt", "py_return", "py_expr", "py_code"]),
  ],
};

// -- block-definition helpers --------------------------------------------------------------

function point(name: string) {
  return { type: "input_value", name, check: "Point" };
}
function number(name: string) {
  return { type: "input_value", name, check: "Number" };
}
function move(type: string, message: string, args: object[], tooltip: string) {
  // A move ends the frame for this robot: nothing goes under it.
  return { type, message0: message, args0: args, previousStatement: null, style: MOVE, inputsInline: true, tooltip };
}
function step(type: string, message: string, args: object[], style: string, tooltip: string) {
  return { type, message0: message, args0: args, previousStatement: null, nextStatement: null, style, inputsInline: true, tooltip };
}
function value(type: string, message: string, args: object[], output: string | null, style: string, tooltip: string) {
  return { type, message0: message, args0: args, output, style, inputsInline: true, tooltip };
}
function cat(name: string, style: string, types: string[]) {
  return {
    kind: "category", name, categorystyle: style,
    contents: types.map((type) => ({ kind: "block", type, ...(SHADOWS[type] ? { inputs: SHADOWS[type] } : {}) })),
  };
}
