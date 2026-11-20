// A Python editor (CodeMirror 6): line numbers, highlighting, Tab indents,
// Ctrl/Cmd+S saves, and the lines with problems marked. One editor for a
// workspace's files: each file (docKey) keeps its own text and undo history.

import { indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { EditorState, StateEffect, StateField } from "@codemirror/state";
import { syntaxHighlighting } from "@codemirror/language";
import { oneDarkHighlightStyle } from "@codemirror/theme-one-dark";
import { Decoration, type DecorationSet, EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

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
  },
  { dark: true },
);

export function CodeEditor({
  docKey,
  value,
  onChange,
  onSave,
  problemLines: lines,
  jump,
}: {
  /** Which file this is: switching keeps each file's own undo history. */
  docKey: string;
  value: string;
  onChange: (text: string) => void;
  onSave: () => void;
  problemLines: number[];
  /** Move the cursor to this line; a new object each time, so the same line can be asked twice. */
  jump: { line: number } | null;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const handlers = useRef({ onChange, onSave });
  handlers.current = { onChange, onSave };
  const states = useRef(new Map<string, EditorState>()); // each file's editor state
  const shownKey = useRef(docKey);

  const newState = (doc: string) =>
    EditorState.create({
      doc,
      extensions: [
          basicSetup,
          python(),
          syntaxHighlighting(oneDarkHighlightStyle),
          look,
          problemLines,
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
