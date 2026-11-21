// A Python editor (CodeMirror 6): line numbers, highlighting, Tab indents,
// Ctrl/Cmd+S saves, the lines with problems marked, hints as you type (me.,
// world., def ...), descriptions on hover and the lint's squiggles
// (codeAssist.ts). One editor for a workspace's files: each file (docKey)
// keeps its own text and undo history.

import { indentWithTab } from "@codemirror/commands";
import { globalCompletion, localCompletionSource, pythonLanguage } from "@codemirror/lang-python";
import { LanguageSupport } from "@codemirror/language";
import type { CompletionContext } from "@codemirror/autocomplete";
import { lintGutter, linter } from "@codemirror/lint";
import { EditorState, StateEffect, StateField } from "@codemirror/state";
import { syntaxHighlighting } from "@codemirror/language";
import { oneDarkHighlightStyle } from "@codemirror/theme-one-dark";
import { Decoration, type DecorationSet, EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";
import { completions, hovers, type LintFinding, type Reference, toDiagnostics } from "./codeAssist";

const setProblemLines = StateEffect.define<number[]>();

const problemLines = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (!e.is(setProblemLines)) continue;
      const doc = tr.state.doc;
      const lines = [...new Set(e.value)].filter((n) => n >= 1 && n <= doc.lines).sort((a, b) => a - b);
      deco = Decoration.set(lines.map((n) => Decoration.line({ class: "cm-problem-line" }).range(doc.line(n).from)));
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const look = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "14px", color: "var(--text)", backgroundColor: "var(--bg-2)" },
    ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" },
    ".cm-content": { caretColor: "var(--text)" },
    ".cm-cursor": { borderLeftColor: "var(--text)" },
    ".cm-gutters": { backgroundColor: "var(--bg-2)", color: "var(--dim)", borderRight: "1px solid var(--line)" },
    ".cm-activeLine": { backgroundColor: "rgba(255, 255, 255, 0.035)" },
    ".cm-activeLineGutter": { backgroundColor: "rgba(255, 255, 255, 0.05)", color: "var(--muted)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "#264f78" },
    ".cm-selectionMatch": { backgroundColor: "rgba(38, 79, 120, 0.5)" },
    ".cm-matchingBracket": { backgroundColor: "rgba(255, 255, 255, 0.12)", outline: "none" },
    ".cm-foldPlaceholder": { backgroundColor: "var(--panel-2)", border: "none", color: "var(--muted)" },
    ".cm-tooltip": { backgroundColor: "var(--panel)", border: "1px solid var(--line-2)" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--panel-2)", color: "var(--text)" },
    ".cm-problem-line": { backgroundColor: "rgba(224, 38, 26, 0.18)" },
    ".cm-tooltip-autocomplete ul li .cm-completionDetail": { color: "var(--muted)", fontStyle: "normal", marginLeft: "6px" },
    ".cm-completionInfo": { maxWidth: "380px", padding: "6px 10px", lineHeight: "1.45" },
    ".cm-hint-tooltip": { maxWidth: "420px", padding: "6px 10px", lineHeight: "1.45", fontSize: "13px" },
    ".cm-hint-head": { fontFamily: "var(--mono)", color: "var(--text)", marginBottom: "2px" },
    ".cm-tooltip-lint": { maxWidth: "460px" },
    ".cm-diagnostic": { padding: "4px 8px", fontSize: "13px" },
    ".cm-lintRange-error": { textDecorationColor: "var(--red-2)" },
  },
  { dark: true },
);

/** Python, with the platform's hints first: where there are some (after me.,
 *  world., def ...) only those; elsewhere Python's own (the file's names,
 *  keywords and builtins). */
function pythonSupport(getRef: () => Reference | null) {
  const ours = completions(getRef);
  return new LanguageSupport(pythonLanguage, [
    pythonLanguage.data.of({ autocomplete: (ctx: CompletionContext) => ours(ctx) ?? localCompletionSource(ctx) }),
    pythonLanguage.data.of({ autocomplete: globalCompletion }),
  ]);
}

export function CodeEditor({
  docKey,
  value,
  onChange,
  onSave,
  problemLines: lines,
  jump,
  reference = null,
  lint,
  readOnly = false,
}: {
  /** Which file this is: switching keeps each file's own undo history. */
  docKey: string;
  value: string;
  onChange: (text: string) => void;
  onSave: () => void;
  problemLines: number[];
  /** Move the cursor to this line; a new object each time, so the same line can be asked twice. */
  jump: { line: number } | null;
  /** What team code can use, for the hints (from the server); null: no hints. */
  reference?: Reference | null;
  /** Checks a file's text as typed: the problems to underline. */
  lint?: (text: string) => Promise<LintFinding[]>;
  /** Show only (the Python a file's blocks make). */
  readOnly?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const handlers = useRef({ onChange, onSave, lint });
  handlers.current = { onChange, onSave, lint };
  const ref = useRef(reference);
  ref.current = reference;
  const states = useRef(new Map<string, EditorState>()); // each file's editor state
  const shownKey = useRef(docKey);

  const newState = (doc: string) =>
    EditorState.create({
      doc,
      extensions: [
          basicSetup,
          EditorState.readOnly.of(readOnly),
          pythonSupport(() => ref.current),
          syntaxHighlighting(oneDarkHighlightStyle),
          look,
          problemLines,
          hovers(() => ref.current),
          lintGutter(),
          linter(async (v) => {
            const check = handlers.current.lint;
            if (!check) return [];
            const text = v.state.doc.toString();
            try {
              return toDiagnostics(v.state.doc, await check(text));
            } catch {
              return []; // the server is away: no squiggles, nothing breaks
            }
          }, { delay: 600 }),
          keymap.of([
            { key: "Mod-s", preventDefault: true, run: () => (handlers.current.onSave(), true) },
            indentWithTab,
          ]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) handlers.current.onChange(u.state.doc.toString());
        }),
      ],
    });

  useEffect(() => {
    const v = new EditorView({ parent: host.current!, state: newState(value) });
    view.current = v;
    return () => v.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- created once; value syncs below
  }, []);

  // Another file: park this one's state, bring back (or start) that one's.
  useEffect(() => {
    const v = view.current;
    if (!v || shownKey.current === docKey) return;
    states.current.set(shownKey.current, v.state);
    const kept = states.current.get(docKey);
    v.setState(kept && kept.doc.toString() === value ? kept : newState(value));
    shownKey.current = docKey;
    v.dispatch({ effects: setProblemLines.of(lines) });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on a file switch only
  }, [docKey]);

  // The same file changed from outside (reloaded, uploaded): replace the text.
  useEffect(() => {
    const v = view.current;
    if (v && shownKey.current === docKey && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value, docKey]);

  const key = lines.join(",");
  useEffect(() => {
    view.current?.dispatch({ effects: setProblemLines.of(lines) });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- by content, not identity
  }, [key]);

  useEffect(() => {
    const v = view.current;
    if (!v || !jump) return;
    const line = v.state.doc.line(Math.min(Math.max(jump.line, 1), v.state.doc.lines));
    v.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
    v.focus();
  }, [jump]);

  return <div className="code-editor" ref={host} />;
}
