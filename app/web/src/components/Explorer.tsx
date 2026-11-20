// The Code page's file tree, VS Code style: every workspace a folder, its
// files inside. Click a file to open it; right-click (or ⋯) for actions;
// renaming and new files are typed in place.

import { useEffect, useRef, useState } from "react";
import { ENTRY, toFileName, toWorkspaceName, type Workspace } from "../lib/teams";

export type WsAction = "download" | "duplicate" | "delete";

interface Props {
  workspaces: Workspace[] | null;
  active: string; // the open workspace
  activeFile: string;
  dirty: string[]; // unsaved files of the open workspace
  expanded: string[];
  onExpand: (names: string[]) => void;
  onOpen: (ws: string, file: string) => void;
  onNewWorkspace: () => void;
  onUpload: () => void;
  onNewFile: (ws: string, name: string) => Promise<boolean>;
  onRenameFile: (ws: string, file: string, to: string) => Promise<boolean>;
  onDeleteFile: (ws: string, file: string) => void;
  onRenameWs: (ws: string, to: string) => Promise<boolean>;
  onWsAction: (ws: string, action: WsAction) => void;
}

type Editing =
  | { kind: "new-file"; ws: string; value: string }
  | { kind: "rename-file"; ws: string; file: string; value: string }
  | { kind: "rename-ws"; ws: string; value: string };

interface MenuItem {
  label: string;
  run: () => void;
  danger?: boolean;
}

export function Explorer(p: Props) {
  const [editing, setEditing] = useState<Editing | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const open = (ws: string) => p.expanded.includes(ws);
  const toggle = (ws: string) =>
    p.onExpand(open(ws) ? p.expanded.filter((w) => w !== ws) : [...p.expanded, ws]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("click", close);
    window.addEventListener("keydown", esc);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", esc);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  const showMenu = (e: React.MouseEvent, items: MenuItem[]) => {
    e.preventDefault();
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ x: e.type === "contextmenu" ? e.clientX : r.right - 4, y: e.type === "contextmenu" ? e.clientY : r.bottom, items });
  };

  const wsItems = (w: Workspace): MenuItem[] => [
    { label: "New file", run: () => { if (!open(w.name)) toggle(w.name); setEditing({ kind: "new-file", ws: w.name, value: "" }); } },
    { label: "Rename", run: () => setEditing({ kind: "rename-ws", ws: w.name, value: w.name }) },
    { label: "Duplicate (keep a version)", run: () => p.onWsAction(w.name, "duplicate") },
    { label: "Download .zip (to share)", run: () => p.onWsAction(w.name, "download") },
    { label: "Delete", run: () => p.onWsAction(w.name, "delete"), danger: true },
  ];
  const fileItems = (w: Workspace, f: string): MenuItem[] =>
    f === ENTRY
      ? [{ label: "Open", run: () => p.onOpen(w.name, f) }]
      : [
          { label: "Open", run: () => p.onOpen(w.name, f) },
          { label: "Rename", run: () => setEditing({ kind: "rename-file", ws: w.name, file: f, value: f.replace(/\.py$/, "") }) },
          { label: "Delete", run: () => p.onDeleteFile(w.name, f), danger: true },
        ];

  const commit = async () => {
    if (!editing) return;
    let ok = true;
    if (editing.kind === "new-file") {
      const name = toFileName(editing.value);
      ok = name ? await p.onNewFile(editing.ws, name) : true;
    } else if (editing.kind === "rename-file") {
      const to = toFileName(editing.value);
      ok = to && to !== editing.file ? await p.onRenameFile(editing.ws, editing.file, to) : true;
    } else {
      const to = toWorkspaceName(editing.value);
      ok = to && to !== editing.ws ? await p.onRenameWs(editing.ws, to) : true;
    }
    if (ok) setEditing(null);
  };

  const input = (value: string, hint: string) => (
    <InlineInput value={value} hint={hint} onChange={(v) => setEditing((e) => (e ? { ...e, value: v } : e))}
      onCommit={commit} onCancel={() => setEditing(null)} />
  );

  return (
    <nav className="explorer" aria-label="Workspaces and files">
      <div className="explorer-head">
        <span>Workspaces</span>
        <button title="New workspace" onClick={p.onNewWorkspace}>+</button>
        <button title="Upload .py files or a workspace's .zip" onClick={p.onUpload}>⇪</button>
      </div>
      <div className="explorer-tree">
        {p.workspaces === null && <div className="explorer-note">Loading…</div>}
        {p.workspaces?.length === 0 && <div className="explorer-note">No workspaces yet: + makes one.</div>}
        {p.workspaces?.map((w) => {
          const isActive = w.name === p.active;
          return (
            <div key={w.name} className="tree-ws">
              {editing?.kind === "rename-ws" && editing.ws === w.name ? (
                <div className="tree-row ws editing">{input(editing.value, "workspace name")}</div>
              ) : (
                <div className={`tree-row ws${isActive ? " active" : ""}`} onClick={() => toggle(w.name)}
                  onContextMenu={(e) => showMenu(e, wsItems(w))}
                  title={w.safe ? `From ${w.author || w.from || "someone else"}: plays in safe mode` : isActive ? "The open workspace: Play, Test match and the problems below are about it" : w.name}>
                  <span className="chev">{open(w.name) ? "▾" : "▸"}</span>
                  <span className="name">{w.name}</span>
                  {w.safe && <span className="tag-safe">safe</span>}
                  <button className="more" title="Actions" onClick={(e) => showMenu(e, wsItems(w))}>⋯</button>
                </div>
              )}
              {open(w.name) && (
                <div className="tree-files">
                  {w.files.map((f) => {
                    const on = isActive && f.name === p.activeFile;
                    const unsaved = isActive && p.dirty.includes(f.name);
                    if (editing?.kind === "rename-file" && editing.ws === w.name && editing.file === f.name) {
                      return <div key={f.name} className="tree-row file editing">{input(editing.value, "file name")}</div>;
                    }
                    return (
                      <div key={f.name} className={`tree-row file${on ? " on" : ""}`} onClick={() => p.onOpen(w.name, f.name)}
                        onContextMenu={(e) => showMenu(e, fileItems(w, f.name))}
                        title={f.name === ENTRY ? "Where the team starts: the slot functions. It keeps this name, so the team can be found" : `A module: in team.py, from ${f.name.replace(/\.py$/, "")} import ...`}>
                        <span className="py">py</span>
                        <span className="name mono">{f.name}</span>
                        {f.name === ENTRY && <span className="tag-start">start</span>}
                        {unsaved && <span className="dot-unsaved" title="unsaved" />}
                        {f.name !== ENTRY && <button className="more" title="Actions" onClick={(e) => showMenu(e, fileItems(w, f.name))}>⋯</button>}
                      </div>
                    );
                  })}
                  {editing?.kind === "new-file" && editing.ws === w.name ? (
                    <div className="tree-row file editing">{input(editing.value, "new file, e.g. passing")}</div>
                  ) : (
                    <div className="tree-row file add" onClick={() => setEditing({ kind: "new-file", ws: w.name, value: "" })}>
                      <span className="py">+</span><span className="name">new file</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {menu && (
        <div className="context-menu" style={{ left: menu.x, top: menu.y }} onClick={(e) => e.stopPropagation()}>
          {menu.items.map((it) => (
            <button key={it.label} className={it.danger ? "danger" : undefined} onClick={() => { setMenu(null); it.run(); }}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </nav>
  );
}

function InlineInput({ value, hint, onChange, onCommit, onCancel }: {
  value: string;
  hint: string;
  onChange: (v: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <input ref={ref} className="tree-input mono" value={value} placeholder={hint} spellCheck={false}
      onChange={(e) => onChange(e.target.value)} onBlur={onCancel}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit();
        } else if (e.key === "Escape") {
          onCancel();
        }
      }} />
  );
}
