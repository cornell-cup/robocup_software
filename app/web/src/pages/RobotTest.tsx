import { useEffect, useState } from "react";
import { Card } from "../components/Card";
import { BodyFields, type BodyForm, DEFAULT_BODY, checkBody } from "../components/BodyFields";
import { type PathLine, PathView } from "../components/PathView";
import { ProfileCard, fmt } from "../components/ProfileCard";
import { Toggle } from "../components/Toggle";
import { api } from "../lib/api";
import { motionTest, useMotionTest } from "../lib/motionTest";
import { useRobots } from "../lib/robots";
import { toasts } from "../lib/toast";
import { useTrackingFeed } from "../lib/tracking";
import type { MotionCatalog, MotionRecord, MotionReport, RunEnd, RunnerState, SavedReport } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/robotTest.css";

/** Corner calibration tags: never a robot (motion_runner.IGNORE_TAGS). */
const CORNER_TAGS = new Set([0, 1, 2, 3]);
const REPEATS = [1, 2, 3, 5];

const STATE_LABEL: Record<RunnerState, string> = {
  idle: "Ready", starting: "Getting ready", returning: "Driving back", reposition: "Move the robot",
  running: "Running", done: "Done", aborted: "Aborted", error: "Error",
};
const END_LABEL: Record<RunEnd, string> = {
  reached: "done", timeout: "timed out", tag_lost: "tag lost", edge: "edge stop", no_room: "no room", aborted: "aborted",
};

export function RobotTest() {
  const session = useMotionTest();
  const { robots } = useRobots();
  const feed = useTrackingFeed();
  const [catalog, setCatalog] = useState<MotionCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [opts, setOpts] = useSavedState("robotTest.options", {
    robot: "", tests: null as string[] | null, speeds: [0.4, 0.7, 1.0], repeats: 1, returnHome: true, fake: false,
  });
  const set = <K extends keyof typeof opts>(k: K, v: (typeof opts)[K]) => setOpts((o) => ({ ...o, [k]: v }));

  // Each robot's size and tag position, in cm as typed; the tag's left blank
  // when nobody has measured it.
  const [bodies, setBodies] = useSavedState<Record<string, BodyForm>>("robotTest.bodies", {});
  const bodyKey = opts.fake ? "(fake)" : opts.robot;
  const form = bodies[bodyKey] ?? DEFAULT_BODY;
  const setForm = (k: keyof BodyForm, v: string) =>
    setBodies((b) => ({ ...b, [bodyKey]: { ...(b[bodyKey] ?? DEFAULT_BODY), [k]: v } }));
  const useTag = (rep: MotionReport) => {
    const off = rep.tag_offset;
    if (!off) return;
    const key = rep.fake ? "(fake)" : rep.robot;
    setBodies((b) => ({ ...b, [key]: { ...(b[key] ?? DEFAULT_BODY),
      tagForward: (off.forward_m * 100).toFixed(1), tagLeft: (off.left_m * 100).toFixed(1) } }));
    toasts.push("ok", `Tag position set for ${rep.fake ? "the fake robot" : rep.robot}`);
  };
  const bodyProblem = checkBody(form);

  useEffect(() => {
    api.get<MotionCatalog>("/motion-test/catalog").then(setCatalog).catch(toasts.error);
  }, []);

  // Profiles: the newest session's once it's built, saved ones to look back at.
  const [latest, setLatest] = useState<MotionReport | null>(null);
  const [saved, setSaved] = useState<SavedReport[]>([]);
  const [viewing, setViewing] = useState<string | null>(null);
  const [viewed, setViewed] = useState<MotionReport | null>(null);
  const [compare, setCompare] = useState<MotionReport | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    if (session.report_ready) motionTest.report().then(setLatest).catch(() => setLatest(null));
    motionTest.saved().then((r) => setSaved(r.reports)).catch(() => {});
  }, [session.report_ready, session.started]);
  const loadSaved = (key: string | null, then: (r: MotionReport | null) => void) => {
    if (!key) return then(null);
    const [robot, file] = key.split("/");
    motionTest.load(robot, file).then(then).catch(toasts.error);
  };
  const view = (key: string | null) => {
    setViewing(key);
    setSelected(null);
    loadSaved(key, setViewed);
  };
  const report = viewing ? viewed : latest;

  const runner = session.runner;
  const running = session.running;
  const tests = opts.tests ?? catalog?.tests.map((t) => t.name) ?? [];
  const speeds = catalog?.speeds ?? [0.4, 0.7, 1.0];
  const robot = robots.find((r) => r.name === opts.robot) ?? null;
  const robotTags = (feed.doc?.tags ?? []).filter((t) => t.visible && !CORNER_TAGS.has(t.id));

  // What has to be true to start, in the order it's worth fixing.
  const checks = opts.fake
    ? [{ ok: true, text: "Fake robot and camera: no hardware needed" }]
    : [
      { ok: !!robot?.connected, text: robot ? (robot.connected ? `${robot.name} connected` : `${robot.name} not connected yet`) : "Pick a robot (add it on the Robots page)" },
      { ok: !!robot && robot.follow === null, text: robot?.follow != null ? `Following sim robot ${robot.follow}: switch it to by hand` : "Driven by hand, not following the sim" },
      { ok: feed.up, text: feed.up ? "Camera tracking" : "Start tracking on the Tracking page" },
      { ok: robotTags.length === 1, text: robotTags.length === 1 ? `Sees tag ${robotTags[0].id}` : robotTags.length === 0 ? "No robot tag in view" : `${robotTags.length} robot tags in view: one robot at a time` },
    ];
  const runs = tests.length * opts.speeds.length * opts.repeats;
  const canStart = !running && !busy && checks.every((c) => c.ok) && runs > 0 && !bodyProblem;

  async function start() {
    setBusy(true);
    try {
      await motionTest.start({
        robot: opts.fake ? undefined : opts.robot, tests, speeds: opts.speeds,
        repeats: opts.repeats, return_home: opts.returnHome, fake: opts.fake,
        body: {
          length_cm: Number(form.length), width_cm: Number(form.width),
          tag_forward_cm: form.tagForward.trim() === "" ? null : Number(form.tagForward),
          tag_left_cm: form.tagLeft.trim() === "" ? null : Number(form.tagLeft),
        },
      });
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  async function abort() {
    try {
      await motionTest.abort();
    } catch (e) {
      toasts.error(e);
    }
  }

  const toggleIn = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  // The field: the finished runs of the test in progress faded under the live
  // one; between sessions, every run.
  const current = runner?.current ?? null;
  const shown = current ? session.records.filter((r) => r.test === current.test)
    : report && !running ? report.records.filter((r) => !selected || r.test === selected)
    : session.records;
  const lines: PathLine[] = shown.map((r) => ({ points: r.path.map(([, x, y]) => [x, y] as [number, number]) }));
  if (current) lines.push({ points: current.path, live: true });
  const testInfo = catalog?.tests.find((t) => t.name === current?.test) ?? null;
  const state = runner?.state ?? "idle";

  return (
    <div className="page robot-test-page">
      <section className="main-panel">
        <header className="rt-head">
          <h1>Robot test</h1>
          <span className={`rt-state ${state}`}>{STATE_LABEL[state]}</span>
          {runner && <span className="muted mono">{runner.done} of {runner.total}</span>}
          {running && <button className="btn danger" onClick={abort}>Abort</button>}
        </header>
        {runner && (
          <div className="rt-progress" aria-hidden="true">
            <div style={{ width: `${runner.total ? (100 * runner.done) / runner.total : 0}%` }} />
          </div>
        )}
        <div className="rt-message">
          {session.error ? <span className="error-text">{session.error}</span>
            : runner?.message ? <span className={state === "reposition" ? "warn-text" : undefined}>{runner.message}</span>
            : <span className="muted">Pick the tests on the right, put one robot near the middle of the field, and run them.</span>}
        </div>
        <PathView
          field={runner?.field ?? feed.doc?.field ?? null}
          home={runner?.home ?? null}
          start={current?.start ?? null}
          test={testInfo}
          lines={lines}
          body={runner?.body ?? null}
          pose={running ? runner?.pose ?? null : null}
          margin={runner?.margin_m}
        />
        <footer className="rt-foot">
          <span><span className="legend ideal" /> where it should go</span>
          <span><span className="legend live" /> where it went</span>
          <span><span className="legend home" /> start spot</span>
          {session.fake && <span className="muted">Fake robot</span>}
        </footer>
      </section>

      <aside className="side">
        {!running && (report || saved.length > 0) && (
          <ProfileCard report={report} compare={compare} saved={saved} viewing={viewing}
            onView={view} onCompare={(key) => loadSaved(key, setCompare)}
            selected={selected} onSelect={setSelected} onUseTag={useTag} />
        )}

        <Card title="Set up" aside={running ? <span className="muted">running…</span> : undefined}>
          <fieldset className="stack rt-setup" disabled={running}>
            <Toggle label="Fake robot (practice)" checked={opts.fake} onChange={(v) => set("fake", v)} hint="Runs the tests on a pretend robot and camera" />
            {!opts.fake && (
              <label className="field">
                <span>Robot</span>
                <select className="input" value={opts.robot} onChange={(e) => set("robot", e.target.value)}>
                  <option value="">{robots.length ? "pick one" : "none added yet"}</option>
                  {robots.map((r) => <option key={r.name} value={r.name}>{r.name}{r.connected ? "" : " (not connected)"}</option>)}
                </select>
              </label>
            )}

            <ul className="rt-checks">
              {checks.map((c) => (
                <li key={c.text}><span className={`dot ${c.ok ? "ok" : "red"}`} />{c.text}</li>
              ))}
            </ul>

            <div className="field">
              <span>Tests</span>
              <div className="rt-chips">
                {catalog?.tests.map((t) => (
                  <button key={t.name} type="button" className={`chip${tests.includes(t.name) ? " on" : ""}`}
                    onClick={() => set("tests", toggleIn(tests, t.name))}>{t.label}</button>
                ))}
              </div>
            </div>
            <div className="field">
              <span>Speeds (command, 0–1)</span>
              <div className="rt-chips row-chips">
                {speeds.map((s) => (
                  <button key={s} type="button" className={`chip${opts.speeds.includes(s) ? " on" : ""}`}
                    onClick={() => set("speeds", toggleIn(opts.speeds, s).sort((a, b) => a - b))}>{s}</button>
                ))}
              </div>
            </div>
            <div className="field">
              <span>Repeats</span>
              <div className="rt-chips row-chips">
                {REPEATS.map((n) => (
                  <button key={n} type="button" className={`chip${opts.repeats === n ? " on" : ""}`} onClick={() => set("repeats", n)}>{n}</button>
                ))}
              </div>
            </div>
            <BodyFields form={form} onChange={setForm} problem={bodyProblem} isNew={!(bodyKey in bodies)} />
            <Toggle label="Drive back between runs" checked={opts.returnHome} onChange={(v) => set("returnHome", v)} hint="Otherwise it asks you to move the robot when it has no room" />
            <button className="btn primary block" disabled={!canStart} onClick={start}>
              {running ? "Running…" : `Run ${runs} run${runs === 1 ? "" : "s"}`}
            </button>
            <small className="dim">
              One robot on the field at a time. It stops on its own if the camera loses its tag or it nears the edge.
            </small>
          </fieldset>
        </Card>

        <Card title="Runs" aside={session.records.length ? <span className="muted">{session.records.length}</span> : undefined}>
          {session.records.length === 0 ? (
            <p className="note">Each finished run shows here: how far off course it went, how fast, how quickly it reacted.</p>
          ) : (
            <table className="plain rt-runs">
              <thead>
                <tr><th>Test</th><th>Speed</th><th>Ended</th><th title="Sideways drift at the end; + is left">Drift</th><th title="Steady speed, or turn rate for spins">Rate</th><th title="Command to the first centimetre">React</th></tr>
              </thead>
              <tbody>
                {session.records.map((r, i) => <RunRow key={i} record={r} label={catalog?.tests.find((t) => t.name === r.test)?.label ?? r.test} />)}
              </tbody>
            </table>
          )}
        </Card>
      </aside>
    </div>
  );
}

function RunRow({ record: r, label }: { record: MotionRecord; label: string }) {
  const res = r.result;
  const spin = r.test.startsWith("spin");
  return (
    <tr className={r.ended === "reached" ? undefined : "off"}
      title={r.ended === "no_room" ? "The field is too small for this move from any spot: skipped" : res?.notes.join("; ") || undefined}>
      <td>{label}{r.repeat > 1 ? ` #${r.repeat}` : ""}</td>
      <td className="mono">{r.speed}</td>
      <td>{r.ended ? END_LABEL[r.ended] : "–"}</td>
      <td className="mono">{spin ? fmt(res?.max_deviation_m, 1000, "mm") : fmt(res?.drift_m, 1000, "mm")}</td>
      <td className="mono">{spin ? fmt(res?.rate, 1, "°/s") : fmt(res?.rate, 1, "m/s", 2)}</td>
      <td className="mono">{fmt(res?.reaction_s, 1000, "ms")}</td>
    </tr>
  );
}

