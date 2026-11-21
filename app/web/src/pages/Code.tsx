// The Code page: workspaces of Python files (your team's code), checked on
// every save, played live in the simulator (against the built-in team or a
// classmate's workspace), tested in matches, shared by download and upload.

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Card } from "../components/Card";
import type { Reference } from "../components/codeAssist";
import { CodeEditor } from "../components/CodeEditor";
import { Explorer, type WsAction } from "../components/Explorer";
import { FieldView } from "../components/FieldView";
import { Guide } from "../components/Guide";
import { Segmented } from "../components/Segmented";
import { api, ApiError } from "../lib/api";
import { useSim } from "../lib/sim";
import { usePolling } from "../lib/store";
import {
  type CheckResult,
  ENTRY,
  type HeadToHead,
  locate,
  type MatchStatus,
  readAsBase64,
  type Start,
  teams,
  toWorkspaceName,
  withoutRobot,
  type Workspace,
} from "../lib/teams";
import { toasts } from "../lib/toast";
import type { Shown, SimProcessStatus } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/code.css";

type Side = "blue" | "red";
type Doc = { text: string; saved: string };
interface Problem {
  from: "loading" | "match" | "live";
  text: string;
  file: string | null;
  line: number | null;
}
const THIS = "@this"; // the Play card's "this workspace"
const BUILT_IN_OPPONENTS = [
  { value: "v2", label: "v2: the built-in team (frozen)" },
  { value: "v1", label: "v1: before it met rolling balls" },
  { value: "current", label: "current: the built-in team now" },
  { value: "v0", label: "v0: before it pushed the ball" },
];

export function Code() {
  const sim = useSim();
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [wsName, setWsName] = useSavedState("code.ws", "");
  const [loadedWs, setLoadedWs] = useState("");
  const ws = workspaces?.find((w) => w.name === loadedWs);
  const [file, setFile] = useState(ENTRY);
  const [tabs, setTabs] = useState<string[]>([]); // the open files of the open workspace
  const [docs, setDocs] = useState<Record<string, Doc>>({});
  const [expanded, setExpanded] = useSavedState<string[]>("code.expanded", []);
  const doc = docs[file];
  const dirtyFiles = Object.entries(docs).filter(([, d]) => d.text !== d.saved).map(([f]) => f);
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [jump, setJump] = useState<{ line: number } | null>(null);
  const [panel, setPanel] = useState<null | "new" | { upload: File[] }>(null);
  const [tab, setTab] = useSavedState<"code" | "guide">("code.tab", "code");
  const [guide, setGuide] = useState<string | null>(null);
  // What team code can use, for the editor's hints (read once).
  const [reference, setReference] = useState<Reference | null>(null);
  useEffect(() => {
    teams.reference().then(setReference).catch(() => {});
  }, []);
  useEffect(() => {
    if (tab === "guide" && guide === null) teams.guide().then(setGuide).catch(toasts.error);
  }, [tab, guide]);
  // What went wrong playing the version that's saved now: a save starts over.
  const [liveErrors, setLiveErrors] = useState<string[]>([]);
  const [matchStale, setMatchStale] = useState(false);
  const [results, setResults] = useState<HeadToHead[]>([]);

  // What the simulator runs: which sides play which workspace.
  const [proc, setProc] = useState<SimProcessStatus | null>(null);
  usePolling(() => {
    api.get<{ process: SimProcessStatus }>("/sim/status").then((s) => setProc(s.process)).catch(() => {});
  }, 1000);
  const playing = (proc?.running && proc.options.teams) || {};
  const playingSides = Object.entries(playing).filter(([, n]) => n === loadedWs).map(([c]) => c as Side);

  // -- workspaces and files ------------------------------------------------------

  const refresh = useCallback(() => teams.list().then((l) => { setWorkspaces(l); return l; }), []);
  const updateWs = (info: Workspace) =>
    setWorkspaces((l) => (l ?? []).map((w) => (w.name === info.name ? info : w)));

  const runCheck = useCallback((name: string) => {
    setChecking(true);
    teams.check(name).then(setCheck).catch(toasts.error).finally(() => setChecking(false));
  }, []);

  const openWs = useCallback(async (name: string, first: string = ENTRY) => {
    try {
      const f = await teams.read(name, first);
      setDocs({ [first]: { text: f.text, saved: f.text } });
      setFile(first);
      setTabs([first]);
      setTab("code");
      setExpanded((e) => (e.includes(name) ? e : [...e, name]));
      setLoadedWs(name);
      setWsName(name);
      setCheck(null);
      setLiveErrors([]);
      runCheck(name);
      teams.results(name).then(setResults).catch(() => setResults([]));
    } catch (e) {
      toasts.error(e);
    }
  }, [runCheck, setWsName, setExpanded, setTab]);

  useEffect(() => {
    refresh().catch(toasts.error);
  }, [refresh]);
  useEffect(() => {
    if (!workspaces || loadedWs) return;
    if (workspaces.some((w) => w.name === wsName)) openWs(wsName);
    else if (workspaces.length) openWs(workspaces[0].name);
    else {
      setPanel("new");
      setTab("guide"); // no workspaces yet: a newcomer, most likely
    }
  }, [workspaces, loadedWs, wsName, openWs, setTab]);

  const switchWs = (name: string, first: string = ENTRY) => {
    if (name === loadedWs) return;
    if (dirtyFiles.length && !confirm(`Discard your unsaved changes to ${dirtyFiles.join(", ")}?`)) return;
    openWs(name, first);
  };

  const openFile = async (name: string) => {
    setTab("code");
    if (!docs[name]) {
      try {
        const f = await teams.read(loadedWs, name);
        setDocs((d) => ({ ...d, [name]: { text: f.text, saved: f.text } }));
      } catch (e) {
        toasts.error(e);
        return;
      }
    }
    setTabs((t) => (t.includes(name) ? t : [...t, name]));
    setFile(name);
  };

  /** A file clicked in the tree: in the open workspace, or open that workspace at it. */
  const openFrom = (ws: string, name: string) => (ws === loadedWs ? openFile(name) : switchWs(ws, name));

  /** Close a tab (the file stays): unsaved changes are kept or dropped first. */
  const closeTab = (name: string) => {
    const d = docs[name];
    if (d && d.text !== d.saved && !confirm(`${name} has unsaved changes. Close it and lose them?`)) return;
    const left = tabs.filter((t) => t !== name);
    setTabs(left);
    setDocs(({ [name]: _closed, ...rest }) => rest);
    if (file === name) setFile(left[left.length - 1] ?? "");
  };

  const edit = (text: string) => setDocs((d) => ({ ...d, [file]: { ...d[file], text } }));

  /** Save the changed files (all, or just one): the team reloads from them. */
  const save = useCallback(async (only?: string, quiet = false) => {
    const todo = Object.entries(docs).filter(([f, d]) => d.text !== d.saved && (!only || f === only));
    if (!loadedWs || !todo.length) return true;
    try {
      for (const [f, d] of todo) {
        await teams.save(loadedWs, f, d.text);
        setDocs((all) => ({ ...all, [f]: { ...all[f], saved: d.text } }));
      }
      setLiveErrors([]);
      setMatchStale(true);
      runCheck(loadedWs);
      if (!quiet) toasts.push("info", playingSides.length ? "Saved: the simulator picks it up in a moment" : "Saved");
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    }
  }, [docs, loadedWs, runCheck, playingSides.length]);

  const newFile = async (ws: string, name: string) => {
    try {
      updateWs(await teams.newFile(ws, name));
      if (ws === loadedWs) openFile(name);
      else switchWs(ws, name);
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    }
  };

  const renameFile = async (ws: string, name: string, to: string) => {
    if (ws === loadedWs && docs[name] && docs[name].text !== docs[name].saved && !(await save(name, true))) return false;
    try {
      updateWs(await teams.renameFile(ws, name, to));
      if (ws === loadedWs) {
        setDocs((d) => {
          const { [name]: moved, ...rest } = d;
          return moved ? { ...rest, [to]: moved } : rest;
        });
        setTabs((t) => t.map((x) => (x === name ? to : x)));
        if (file === name) setFile(to);
        runCheck(ws);
      }
      toasts.push("info", `Renamed. Change any "from ${name.replace(/\.py$/, "")} import" to "from ${to.replace(/\.py$/, "")} import"`);
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    }
  };

  const deleteFile = async (ws: string, name: string) => {
    if (!confirm(`Delete ${name} from ${ws}? This can't be undone.`)) return;
    try {
      updateWs(await teams.removeFile(ws, name));
      if (ws === loadedWs) {
        const left = tabs.filter((t) => t !== name);
        setTabs(left);
        setDocs(({ [name]: _gone, ...rest }) => rest);
        if (file === name) setFile(left[left.length - 1] ?? "");
        runCheck(ws);
      }
    } catch (e) {
      toasts.error(e);
    }
  };

  const renameWs = async (ws: string, to: string) => {
    if (ws === loadedWs && dirtyFiles.length && !(await save(undefined, true))) return false;
    try {
      const info = await teams.rename(ws, to);
      await refresh();
      setExpanded((e) => e.map((x) => (x === ws ? info.name : x)));
      if (ws === loadedWs) openWs(info.name, file || ENTRY);
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    }
  };

  const wsAction = async (name: string, action: WsAction) => {
    try {
      if (action === "download") {
        if (name === loadedWs && dirtyFiles.length && !(await save(undefined, true))) return;
        const info = workspaces?.find((w) => w.name === name);
        if (info && !info.author && info.origin === "own") {
          const author = prompt("Your name, so whoever gets this knows whose team it is (or leave it empty)");
          if (author) updateWs(await teams.setAuthor(name, author));
        }
        location.href = teams.downloadUrl(name);
        return;
      }
      if (action === "delete") {
        if (!confirm(`Delete the workspace ${name} and all its files? This can't be undone.`)) return;
        const left = await teams.remove(name);
        setWorkspaces(left);
        if (name === loadedWs) {
          setLoadedWs("");
          setDocs({});
          setTabs([]);
        }
        if (!left.length) setPanel("new");
        return;
      }
      if (name === loadedWs && dirtyFiles.length && !(await save(undefined, true))) return;
      const typed = prompt(`Name of the copy of ${name}`, `${name}_v2`);
      const to = typed ? toWorkspaceName(typed) : "";
      if (!to || to === name) return;
      const info = await teams.duplicate(name, to);
      await refresh();
      switchWs(info.name);
    } catch (e) {
      toasts.error(e);
    }
  };

  // Files dropped anywhere on the page, or picked with Upload…
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => /\.(py|zip)$/i.test(f.name));
    if (files.length) setPanel({ upload: files });
    else toasts.push("error", "Drop .py files or a workspace's .zip");
  };

  // -- playing live in the simulator -------------------------------------------------

  const [blue, setBlue] = useSavedState("code.blue", THIS);
  const [red, setRed] = useSavedState("code.red", "");
  const sideTeam = (v: string) => (v === THIS ? loadedWs : v);
  const [wantField, setWantField] = useSavedState("code.field", true);
  const bigField = wantField && !!proc?.running;
  const [starting, setStarting] = useState(false);

  const play = async () => {
    if (dirtyFiles.length && !(await save(undefined, true))) return;
    setStarting(true);
    try {
      if (proc?.running) await api.post("/sim/stop");
      // ai_on: the server turns the AI on once the sim is up (that's what plays the teams).
      await api.post("/sim/start", {
        strategy: true, ai_on: true, blue_team: sideTeam(blue) || null, red_team: sideTeam(red) || null,
      });
      setLog([]);
      setLiveErrors([]);
      logAfter.current = 0;
      toasts.push("info", `Starting the simulator: ${sideTeam(blue) || "built-in"} (blue) vs ${sideTeam(red) || "built-in"} (red)…`);
    } catch (e) {
      toasts.error(e);
    } finally {
      setStarting(false);
    }
  };

  const [log, setLog] = useState<string[]>([]);
  const logAfter = useRef(0);
  usePolling(() => {
    if (!proc?.running) return;
    api.get<{ lines: { n: number; text: string }[] }>(`/sim/log?after=${logAfter.current}`).then((r) => {
      if (!r.lines.length) return;
      logAfter.current = r.lines[r.lines.length - 1].n;
      // This workspace's messages, its errors (by its colour), and what teams print (untagged lines).
      const mine = r.lines.map((l) => l.text).filter((t) =>
        (loadedWs && t.includes(` ${loadedWs}`))
        || playingSides.some((c) => t.startsWith(`[Strategy node] ${c} `) && /\.py:\d+/.test(t))
        || (t.trim() !== "" && !t.startsWith("[") && !t.startsWith("$ ") && !t.startsWith("  [")));
      if (mine.length) setLog((old) => [...old, ...mine].slice(-200));
      const errors = mine.filter(isError);
      if (errors.length) setLiveErrors((old) => [...old, ...errors].slice(-50));
    }).catch(() => {});
  }, 1000);
  const logBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const b = logBox.current;
    if (b) b.scrollTop = b.scrollHeight;
  }, [log]);

  // -- test matches ---------------------------------------------------------------------

  const [opponent, setOpponent] = useSavedState("code.opponent", "v2");
  const [games, setGames] = useSavedState("code.games", 6);
  const [duration, setDuration] = useSavedState("code.duration", 60);
  const [match, setMatch] = useState<MatchStatus | null>(null);
  const [matchLines, setMatchLines] = useState<string[]>([]);
  const matchAfter = useRef(0);

  const runMatch = async () => {
    if (dirtyFiles.length && !(await save(undefined, true))) return;
    try {
      matchAfter.current = 0;
      setMatchLines([]);
      setMatch(await teams.matchStart(loadedWs, opponent, games, duration));
      setMatchStale(false);
    } catch (e) {
      toasts.error(e);
    }
  };
  usePolling(() => {
    if (!match?.running) return;
    teams.matchStatus(matchAfter.current).then((m) => {
      if (m.lines.length) {
        matchAfter.current = m.lines[m.lines.length - 1].n;
        setMatchLines((old) => [...old, ...m.lines.map((l) => l.text)].slice(-60));
      }
      setMatch(m);
      if (!m.running && loadedWs) teams.results(loadedWs).then(setResults).catch(() => {});
    }).catch(() => {});
  }, 1000);
  const gamesDone = matchLines.filter((l) => /^\s+\d+\s+(blue|red)\s/.test(l)).length;
  const matchFailed = match && !match.running && !match.result && match.exit_code !== null;

  // -- problems: loading, matches, live --------------------------------------------------

  const problems = useMemo<Problem[]>(() => {
    const out: Problem[] = [];
    const seen = new Set<string>();
    const add = (from: Problem["from"], raw: string) => {
      const t = withoutRobot(raw.replace(/^\[Strategy node\] (blue|red) /, ""));
      if (seen.has(t)) return;
      seen.add(t);
      out.push({ from, text: t, ...locate(t) });
    };
    if (check && !check.ok && check.error) add("loading", check.error);
    if (match?.result && match.request.a === loadedWs && !matchStale) match.result.errors.a.forEach((e) => add("match", e));
    liveErrors.forEach((l) => add("live", l));
    return out;
  }, [check, match, matchStale, liveErrors, loadedWs]);
  const problemLines = problems.flatMap((p) => (p.line && (p.file ?? ENTRY) === file ? [p.line] : []));
  const goTo = async (p: Problem) => {
    if (!p.line) return;
    const target = p.file && ws?.files.some((f) => f.name === p.file) ? p.file : ENTRY;
    await openFile(target);
    setJump({ line: p.line });
  };

  // -- page -------------------------------------------------------------------------------

  const status = !loadedWs ? null
    : dirtyFiles.length ? <span className="warn-text">unsaved: {dirtyFiles.join(", ")}</span>
    : checking ? <span className="dim">checking…</span>
    : check?.ok ? <span className="ok-text">loads · replaces {check.slots?.join(", ") || "nothing"}</span>
    : check ? <span className="err-text">doesn't load</span>
    : null;
  const others = (workspaces ?? []).filter((w) => w.name !== loadedWs);

  return (
    <div className="page code-page" onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }} onDrop={onDrop}>
      {dragging && <div className="drop-veil">Drop .py files or a workspace's .zip to upload them</div>}
      <section className="main-panel code-main">
        <div className="code-bar">
          <span className="ws-title mono" title="The open workspace: Play, Test match and the problems below are about it">
            {loadedWs || "no workspace open"}
          </span>
          {ws?.safe && (
            <span className="badge warn" title="Someone else's code: it plays in safe mode (no files, programs or network; a list of allowed imports)">
              from {ws.author || ws.from || "someone else"} · safe mode
            </span>
          )}
          <input ref={picker} type="file" accept=".py,.zip" multiple hidden onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (files.length) setPanel({ upload: files });
          }} />
          <span className="code-status">{status}</span>
          {proc?.running && (
            <button className={`btn sm${wantField ? "" : " ghost"}`} onClick={() => setWantField(!wantField)}
              title="The field beside the editor, with what your code draws (world.draw)">
              Field
            </button>
          )}
          <button className="btn sm primary" disabled={!dirtyFiles.length} onClick={() => save()} title="Saves every changed file (Ctrl+S saves the open one)">
            Save
          </button>
        </div>
        <div className="code-workbench">
          <Explorer workspaces={workspaces} active={loadedWs} activeFile={tab === "code" ? file : ""} dirty={dirtyFiles}
            expanded={expanded} onExpand={setExpanded} onOpen={openFrom}
            onNewWorkspace={() => setPanel(panel === "new" ? null : "new")} onUpload={() => picker.current?.click()}
            onNewFile={newFile} onRenameFile={renameFile} onDeleteFile={deleteFile} onRenameWs={renameWs} onWsAction={wsAction} />
          <div className="code-editor-col">
        <div className="code-tabs" role="tablist">
          {tabs.map((f) => {
            const d = docs[f];
            const on = tab === "code" && file === f;
            return (
              <div key={f} className={`file-tab${on ? " on" : ""}`}>
                <button role="tab" aria-selected={on} onClick={() => openFile(f)}
                  title={f === ENTRY ? "Where the team starts: the slot functions" : `A module: in team.py, from ${f.replace(/\.py$/, "")} import ...`}>
                  <span className="mono">{f}</span>
                </button>
                {d && d.text !== d.saved
                  ? <span className="dot-unsaved tab-dot" title="unsaved" />
                  : <button className="tab-close" title={`Close ${f} (the file stays)`} onClick={() => closeTab(f)}>×</button>}
              </div>
            );
          })}
          <span className="tabs-gap" />
          <button role="tab" aria-selected={tab === "guide"} className={`guide-tab${tab === "guide" ? " on" : ""}`}
            onClick={() => setTab("guide")}>
            Guide: what you can change
          </button>
        </div>
        <div className={`code-body${bigField ? " split" : ""}`}>
          {/* Kept mounted behind the guide, so undo history survives a look at the guide. */}
          <div className="code-pane" style={tab === "code" ? undefined : { display: "none" }}>
            {loadedWs && doc ? (
              <CodeEditor docKey={`${loadedWs}/${file}`} value={doc.text} onChange={edit} onSave={() => save(file)}
                problemLines={problemLines} jump={jump} reference={reference}
                lint={(text) => teams.lint(loadedWs, file, text)} />
            ) : (
              <div className="code-empty dim">
                {workspaces === null ? "Loading…"
                  : loadedWs ? "No file open: pick one in the tree on the left."
                  : "No workspace open. Make one with + in the tree (a template, an example or blank), or upload a classmate's."}
              </div>
            )}
          </div>
          {tab === "guide" && (
            <div className="guide-pane">
              <Guide text={guide} />
            </div>
          )}
          {bigField && (
            <div className="big-field">
              <FieldView world={sim.world} attackers={sim.attackers} drawings={sim.drawings} clickable={false} />
            </div>
          )}
        </div>
          </div>
        </div>
        <div className="problems">
          {problems.length === 0 ? (
            <span className="dim">{loadedWs ? "No problems found yet. Check runs on every save; matches and the simulator add what goes wrong while playing." : "New here? The Guide tab says what you can change."}</span>
          ) : (
            problems.map((p, i) => (
              <button key={i} className="problem" disabled={!p.line} onClick={() => goTo(p)}
                title={p.line ? `Go to ${p.file ?? ENTRY} line ${p.line}` : undefined}>
                <span className={`tag ${p.from}`}>{p.from}</span>
                {p.line && <span className="mono line">{p.file ?? ENTRY}:{p.line}</span>}
                <span className="msg">{p.text}</span>
              </button>
            ))
          )}
        </div>
      </section>

      <aside className="side">
        {panel === "new" && (
          <NewWorkspace existing={workspaces ?? []} onCancel={workspaces?.length ? () => setPanel(null) : undefined}
            onCreated={async (n) => { setPanel(null); await refresh(); openWs(n); }} />
        )}
        {panel && panel !== "new" && (
          <Upload files={panel.upload} into={loadedWs} onCancel={() => setPanel(null)}
            onDone={async (info) => { setPanel(null); await refresh(); if (info.name === loadedWs) openWs(info.name, file || ENTRY); else switchWs(info.name); }} />
        )}

        <Card title="Play in the simulator" aside={playingSides.length > 0 && <span className="badge ok">playing as {playingSides.join(" and ")}</span>}>
          <div className="stack">
            <div className="row">
              <TeamPick label="Blue" value={blue} onChange={setBlue} here={loadedWs} others={others} />
              <TeamPick label="Red" value={red} onChange={setRed} here={loadedWs} others={others} />
            </div>
            <div className="row">
              <button className="btn primary" disabled={starting || (!sideTeam(blue) && !sideTeam(red))} onClick={play}>
                {proc?.running ? "Restart" : "Play"}
              </button>
              {proc?.running && <button className="btn ghost" onClick={() => api.post("/sim/stop").catch(toasts.error)}>Stop</button>}
            </div>
            <small className="dim">
              Starts the simulator (no camera) with these teams and the AI on. Pick a classmate's workspace for the other
              side to play each other. Save while it plays and it reloads. Each team runs in its own process: if one
              crashes or loops, its robots use the built-in team until it's fixed.
            </small>
            {proc?.running && !bigField && (
              <div className="mini-field">
                <FieldView world={sim.world} attackers={sim.attackers} drawings={sim.drawings} clickable={false} />
              </div>
            )}
          </div>
        </Card>

        <Card title="Test match" aside={match?.running && <span className="badge warn">game {Math.min(gamesDone + 1, match.request.games ?? 1)} of {match.request.games}</span>}>
          <div className="stack">
            <div className="row">
              <label className="field">
                <span>{loadedWs || "This workspace"} against</span>
                <select className="input" value={opponent} onChange={(e) => setOpponent(e.target.value)}>
                  {BUILT_IN_OPPONENTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  {others.length > 0 && (
                    <optgroup label="Workspaces">
                      {others.map((w) => <option key={w.name} value={w.name}>{w.name}{w.safe ? ` (from ${w.author || w.from || "someone else"})` : ""}</option>)}
                    </optgroup>
                  )}
                </select>
              </label>
            </div>
            <div className="row">
              <label className="field"><span>Games</span><input className="input mono" type="number" min={1} max={40} value={games} onChange={(e) => setGames(Number(e.target.value))} /></label>
              <label className="field"><span>Seconds each</span><input className="input mono" type="number" min={5} max={600} step={5} value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></label>
            </div>
            <div className="row">
              <button className="btn primary" disabled={!loadedWs || !!match?.running} onClick={runMatch}>Run</button>
              {match?.running && <button className="btn ghost" onClick={() => teams.matchStop().then(setMatch).catch(toasts.error)}>Stop</button>}
            </div>
            <small className="dim">Plays without a window, about 5× faster than real time. Sides swap every game; each team runs in its own process.</small>
            {match?.result && <MatchSummary result={match.result} />}
            {matchFailed && <div className="err-text">The match stopped: {matchLines.slice(-3).join(" ") || `exit code ${match.exit_code}`}</div>}
          </div>
        </Card>

        {results.length > 0 && <HeadToHeadCard results={results} />}

        {proc?.running && <ShownValues shown={sim.shown} first={playingSides[0] ?? null} />}

        {proc?.running && (
          <Card title="Log" aside={<span className="dim">this workspace's messages and prints</span>}>
            <div className="log mono" ref={logBox}>
              {log.length ? log.map((l, i) => (
                <div key={i} className={isError(l) ? "err" : undefined}>{l}</div>
              )) : <div className="dim">Nothing yet.</div>}
            </div>
          </Card>
        )}
      </aside>
    </div>
  );
}

/** A log line about something going wrong (errors, a stuck or crashed team). */
function isError(line: string): boolean {
  return /error|traceback|exception|isn't answering|endless loop|didn't load|safe mode|hung/i.test(line);
}

function TeamPick({ label, value, onChange, here, others }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  here: string;
  others: Workspace[];
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value={THIS}>{here ? `${here} (this one)` : "this workspace"}</option>
        <option value="">the built-in team</option>
        {others.map((w) => <option key={w.name} value={w.name}>{w.name}{w.safe ? ` (from ${w.author || w.from || "someone else"})` : ""}</option>)}
      </select>
    </label>
  );
}

const STARTS: { value: Start; label: string; title: string }[] = [
  { value: "template", label: "Template", title: "The roles and skills, each calling the built-in one, and the rest ready to switch on: change what you like" },
  { value: "get_open", label: "Example", title: "A supporter that gets open for a pass, and draws what it checks" },
  { value: "blank", label: "Blank", title: "One small supporter to start from" },
];

function NewWorkspace({ existing, onCreated, onCancel }: {
  existing: Workspace[];
  onCreated: (name: string) => void;
  onCancel?: () => void;
}) {
  const [typed, setTyped] = useState("my_team");
  const [start, setStart] = useState<Start>("template");
  const [busy, setBusy] = useState(false);
  const name = toWorkspaceName(typed);
  const taken = existing.some((w) => w.name === name);
  const create = async () => {
    setBusy(true);
    try {
      await teams.create(name, start);
      onCreated(name);
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="New workspace" active>
      <div className="stack">
        <label className="field">
          <span>Name</span>
          <input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name && !taken && create()} />
        </label>
        <small className="dim">{name ? (taken ? `${name} already exists` : `A folder teams/${name}/, starting with team.py`) : "Letters, digits, _ or -"}</small>
        <Segmented<Start> options={STARTS} value={start} onChange={setStart} />
        <small className="dim">{STARTS.find((s) => s.value === start)!.title}.</small>
        <div className="row">
          <button className="btn primary" disabled={!name || taken || busy} onClick={create}>Create</button>
          {onCancel && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
        </div>
      </div>
    </Card>
  );
}

/** Someone else's code in: a workspace's .zip, or .py files (a new
 *  workspace, or into the open one). It plays in safe mode. */
function Upload({ files, into, onDone, onCancel }: {
  files: File[];
  into: string;
  onDone: (info: Workspace) => void;
  onCancel: () => void;
}) {
  const zip = files.length === 1 && /\.zip$/i.test(files[0].name);
  const mixed = files.length > 1 && files.some((f) => /\.zip$/i.test(f.name));
  const [where, setWhere] = useState<"new" | "into">("new");
  const [typed, setTyped] = useState(files[0].name.replace(/\.(py|zip)$/i, ""));
  const [replace, setReplace] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const data = await Promise.all(files.map(async (f) => ({ name: f.name, data: await readAsBase64(f) })));
      const info = await teams.upload(data, where === "into" && !zip
        ? { into, replace }
        : { as: toWorkspaceName(typed) || undefined, replace });
      toasts.push("info", `${info.name}: uploaded. It plays in safe mode, as someone else's code`);
      onDone(info);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict(e.message);
        if (e.data?.suggest) setTyped(e.data.suggest);
      } else {
        toasts.error(e);
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Upload" active>
      <div className="stack">
        <div className="mono small">{files.map((f) => f.name).join(", ")}</div>
        {mixed ? (
          <div className="err-text">Upload one .zip, or .py files: not both at once.</div>
        ) : (
          <>
            {!zip && into && (
              <Segmented<"new" | "into"> value={where} onChange={setWhere} options={[
                { value: "new", label: "New workspace" },
                { value: "into", label: `Add to ${into}` },
              ]} />
            )}
            {(zip || where === "new") && (
              <label className="field">
                <span>Workspace name</span>
                <input className="input mono" value={typed} onChange={(e) => { setTyped(e.target.value); setConflict(null); }} />
              </label>
            )}
            {conflict && (
              <div className="stack">
                <div className="warn-text">{conflict}.</div>
                <label className="row small"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> Replace it</label>
              </div>
            )}
            <small className="dim">
              Code from someone else runs on this computer, so it plays in safe mode: no files, programs or network, and
              only a list of imports. {where === "into" && !zip ? `${into} counts as someone else's from now on.` : ""}
            </small>
          </>
        )}
        <div className="row">
          <button className="btn primary" disabled={busy || mixed} onClick={go}>Upload</button>
          <button className="btn ghost" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </Card>
  );
}

function MatchSummary({ result: r }: { result: NonNullable<MatchStatus["result"]> }) {
  const ci = r.diff_ci95 === null ? "" : ` ± ${r.diff_ci95.toFixed(2)}`;
  const clear = r.diff_ci95 !== null && Math.abs(r.diff_per_game) > r.diff_ci95;
  const problemsA = new Set(r.errors.a.map(withoutRobot)).size;
  const problemsB = new Set(r.errors.b.map(withoutRobot)).size;
  const verdict = !clear ? "no clear difference yet: play more games to tell"
    : r.diff_per_game > 0 ? `${r.a} is better` : `${r.b} is better`;
  return (
    <div className="match-summary">
      <div className="score-row">
        <span className="mono name">{r.a}</span>
        <span className="score mono">{r.goals[0]} – {r.goals[1]}</span>
        <span className="mono name">{r.b}</span>
      </div>
      <dl className="kv">
        <dt>Games</dt><dd>{r.games}: {r.wins} won, {r.draws} drawn, {r.losses} lost</dd>
        <dt>Goal difference</dt><dd className="mono">{r.diff_per_game >= 0 ? "+" : ""}{r.diff_per_game.toFixed(2)} a game{ci}</dd>
        <dt>Possession</dt><dd className="mono">{Math.round(r.possession[0] * 100)}% – {Math.round(r.possession[1] * 100)}%</dd>
        <dt>Ball in their half</dt><dd className="mono">{Math.round(r.ball_in_b_half * 100)}%</dd>
      </dl>
      <div className={clear ? (r.diff_per_game > 0 ? "ok-text" : "err-text") : "dim"}>{verdict}</div>
      {problemsA > 0 && <div className="err-text">{problemsA} problem{problemsA > 1 ? "s" : ""} in {r.a}: see below the editor</div>}
      {problemsB > 0 && <div className="warn-text">{problemsB} problem{problemsB > 1 ? "s" : ""} in {r.b}</div>}
    </div>
  );
}

/** Past matches of this workspace, added up per opponent. */
function HeadToHeadCard({ results }: { results: HeadToHead[] }) {
  const rows = new Map<string, { games: number; wins: number; draws: number; losses: number; gf: number; ga: number }>();
  for (const r of results) {
    const row = rows.get(r.b) ?? { games: 0, wins: 0, draws: 0, losses: 0, gf: 0, ga: 0 };
    row.games += r.games;
    row.wins += r.wins;
    row.draws += r.draws;
    row.losses += r.losses;
    row.gf += r.goals[0];
    row.ga += r.goals[1];
    rows.set(r.b, row);
  }
  return (
    <Card title="Head to head" aside={<span className="dim">every test match so far</span>}>
      <table className="h2h">
        <thead><tr><th>Against</th><th>Games</th><th>W</th><th>D</th><th>L</th><th>Goals</th></tr></thead>
        <tbody>
          {[...rows.entries()].map(([b, r]) => (
            <tr key={b}>
              <td className="mono">{b}</td><td>{r.games}</td><td className="ok-text">{r.wins}</td><td>{r.draws}</td>
              <td className="err-text">{r.losses}</td><td className="mono">{r.gf}–{r.ga}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** world.show(name, value): each team's values this frame, per robot. */
function ShownValues({ shown, first }: { shown: Shown; first: Side | null }) {
  const colors = Object.keys(shown).sort((a, b) => Number(b === first) - Number(a === first));
  return (
    <Card title="Values" aside={<span className="dim mono">world.show(name, value)</span>}>
      {colors.length === 0 ? (
        <div className="dim">Nothing shown yet. In a slot: <code className="mono">world.show("gap", me.distance_to(world.ball.pos))</code></div>
      ) : (
        <div className="shown">
          {colors.map((c) => Object.entries(shown[c]).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })).map(([who, values]) => (
            <div key={`${c}-${who}`} className="shown-group">
              <div className={`shown-who ${c}`}>{c} · {who}</div>
              <dl className="kv">
                {Object.entries(values).map(([name, value]) => (
                  <Fragment key={name}><dt className="mono">{name}</dt><dd>{value}</dd></Fragment>
                ))}
              </dl>
            </div>
          )))}
        </div>
      )}
    </Card>
  );
}
