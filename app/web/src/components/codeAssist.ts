// The Code page editor's help while typing: hints (me., world., def ...),
// descriptions on hover, and the lint's squiggles. What there is to hint
// comes from the server (strategy/core/reference.py), read from the code.

import { type Completion, type CompletionContext, type CompletionResult, snippetCompletion } from "@codemirror/autocomplete";
import type { Diagnostic } from "@codemirror/lint";
import type { Text } from "@codemirror/state";
import { type EditorView, hoverTooltip } from "@codemirror/view";

export interface RefMember {
  name: string;
  kind: "method" | "property";
  detail: string; // "(target, face=...)" for a method
  doc: string;
}
export interface Reference {
  me: RefMember[];
  world: RefMember[];
  ball: RefMember[];
  us: RefMember[];
  draw: RefMember[];
  slots: { name: string; params: string[]; returns: string }[];
}
/** A lint finding from the server (lines and columns from 1). */
export interface LintFinding {
  line: number;
  col: number;
  end_line: number;
  end_col: number;
  severity: "error" | "warning" | "info";
  message: string;
  code: string;
}
type Table = Exclude<keyof Reference, "slots">;

// Names that usually hold one of our robots, besides `me`.
const ROBOT_NAMES = /^(me|robot|robots?\[\w+\]|mate|teammate|other|taker|keeper|r|m|closest|nearest|attacker_robot|best)$/;

/** Which table an expression before a dot is: me, world, world.ball, world.us, world.draw. */
function tableFor(chain: string): Table | null {
  if (chain === "world") return "world";
  if (chain === "world.ball") return "ball";
  if (chain === "world.us") return "us";
  if (chain === "world.draw") return "draw";
  if (ROBOT_NAMES.test(chain)) return "me";
  return null;
}

/** A method's call with its required parameters as placeholders: kick_toward(${target}). */
function callSnippet(m: RefMember): string {
  const params = m.detail.replace(/^\(|\)$/g, "").split(",").map((p) => p.trim()).filter((p) => p && !p.endsWith("=..."));
  return `${m.name}(${params.map((p) => `\${${p}}`).join(", ")})`;
}

function memberOption(m: RefMember): Completion {
  // boost: ahead of the plain words the editor also suggests
  const base = { label: m.name, detail: m.detail || undefined, info: m.doc || undefined, type: m.kind, boost: 50 };
  return m.kind === "method" ? snippetCompletion(callSnippet(m), base) : base;
}

/** The hints: after me. / world. / world.ball. / world.us. / world.draw., and after "def " at the start of a line. */
export function completions(getRef: () => Reference | null) {
  return (ctx: CompletionContext): CompletionResult | null => {
    const ref = getRef();
    if (!ref) return null;
    const member = ctx.matchBefore(/[A-Za-z_][\w.[\]]*\.\w*$/);
    if (member) {
      const dot = member.text.lastIndexOf(".");
      const table = tableFor(member.text.slice(0, dot));
      if (table) {
        return { from: member.from + dot + 1, options: ref[table].map(memberOption), validFor: /^\w*$/ };
      }
    }
    const def = ctx.matchBefore(/^def\s+\w*$/);
    if (def) {
      const from = def.from + def.text.search(/\w*$/);
      return {
        from,
        validFor: /^\w*$/,
        options: ref.slots.map((s) => snippetCompletion(`${s.name}(${s.params.join(", ")}):\n    \${}`, {
          label: s.name,
          detail: `(${s.params.join(", ")})`,
          info: `A slot: replaces the built-in ${s.name}. Gives back ${s.returns}.`,
          type: "function",
          boost: 50,
        })),
      };
    }
    const top = ctx.matchBefore(/^[A-Z]\w*$/);
    if (top && "PLAYS".startsWith(top.text)) {
      return {
        from: top.from,
        options: [snippetCompletion('PLAYS = {\n    "${attack}": ["attacker", "supporter", "goalie"],\n}', {
          label: "PLAYS", type: "variable", info: "Your plays: a name, and its roles most important first. Needs a def pick_play(world).",
        })],
      };
    }
    return null;
  };
}

/** A description on hover over me.xxx, world.xxx, world.ball.xxx, ... */
export function hovers(getRef: () => Reference | null) {
  return hoverTooltip((view: EditorView, pos: number) => {
    const ref = getRef();
    if (!ref) return null;
    const line = view.state.doc.lineAt(pos);
    const text = line.text;
    let start = pos - line.from;
    let end = start;
    while (start > 0 && /\w/.test(text[start - 1])) start--;
    while (end < text.length && /\w/.test(text[end])) end++;
    if (start === end || text[start - 1] !== ".") return null;
    let chainStart = start - 1;
    while (chainStart > 0 && /[\w.[\]]/.test(text[chainStart - 1])) chainStart--;
    const table = tableFor(text.slice(chainStart, start - 1));
    const member = table && ref[table].find((m) => m.name === text.slice(start, end));
    if (!member) return null;
    return {
      pos: line.from + start,
      end: line.from + end,
      above: true,
      create: () => {
        const dom = document.createElement("div");
        dom.className = "cm-hint-tooltip";
        const head = document.createElement("div");
        head.className = "cm-hint-head";
        head.textContent = `${member.name}${member.detail}`;
        dom.append(head);
        if (member.doc) {
          const doc = document.createElement("div");
          doc.textContent = member.doc;
          dom.append(doc);
        }
        return { dom };
      },
    };
  });
}

/** The server's findings as the editor's diagnostics. */
export function toDiagnostics(doc: Text, found: LintFinding[]): Diagnostic[] {
  const at = (line: number, col: number) => {
    const l = doc.line(Math.min(Math.max(line, 1), doc.lines));
    return Math.min(l.from + Math.max(col - 1, 0), l.to);
  };
  return found.map((f) => {
    const from = at(f.line, f.col);
    let to = at(f.end_line, f.end_col);
    if (to <= from) to = Math.min(from + 1, doc.line(Math.min(Math.max(f.line, 1), doc.lines)).to);
    return { from, to: Math.max(to, from), severity: f.severity, message: f.message, source: f.code };
  });
}
