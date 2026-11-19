import { Card } from "./Card";
import type { Ability, ByTest, MotionReport, SavedReport, VsSimRow } from "../lib/types";

/** A robot's motion profile: each ability graded, what stood out, and each
 *  test averaged. Optionally against an earlier profile (`compare`). */
export function ProfileCard({
  report, compare, saved, viewing, onView, onCompare, selected, onSelect,
}: {
  report: MotionReport | null;
  compare: MotionReport | null;
  /** saved profiles to pick from, newest first */
  saved: SavedReport[];
  /** the saved profile on show, or null for the newest session's */
  viewing: string | null;
  onView: (key: string | null) => void;
  onCompare: (key: string | null) => void;
  /** the test whose paths the field shows, or null for all */
  selected: string | null;
  onSelect: (test: string | null) => void;
}) {
  const keyOf = (r: SavedReport) => `${r.robot}/${r.file}`;
  const title = report ? `Profile: ${report.robot}${report.fake ? " (fake)" : ""}` : "Profile";
  return (
    <Card title={title} aside={report?.started ? <span className="muted">{when(report.started)}</span> : undefined}>
      <div className="stack">
        {saved.length > 0 && (
          <div className="row">
            <label className="field">
              <span>Show</span>
              <select className="input" value={viewing ?? ""} onChange={(e) => onView(e.target.value || null)}>
                <option value="">Newest session</option>
                {saved.map((r) => <option key={keyOf(r)} value={keyOf(r)}>{label(r)}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Compare with</span>
              <select className="input" value={compare ? compareKey(compare, saved) : ""} onChange={(e) => onCompare(e.target.value || null)}>
                <option value="">Nothing</option>
                {saved.map((r) => <option key={keyOf(r)} value={keyOf(r)}>{label(r)}</option>)}
              </select>
            </label>
          </div>
        )}

        {!report ? (
          <p className="note">Run some tests: the profile grades how the robot drives straight, strafes, turns and reacts, and says what stood out.</p>
        ) : (
          <>
            <ul className="abilities">
              {report.abilities.map((a) => (
                <AbilityRow key={a.key} ability={a} was={compare?.abilities.find((b) => b.key === a.key) ?? null} />
              ))}
            </ul>

            <div>
              <h3 className="rt-sub">What stood out</h3>
              {report.findings.length === 0 ? (
                <p className="note">Nothing: it went where it was told, at the speed it was told.</p>
              ) : (
                <ul className="findings">{report.findings.map((f) => <li key={f}>{f}</li>)}</ul>
              )}
            </div>

            <VsSim report={report} />

            <div>
              <h3 className="rt-sub">
                Each test <span className="dim">· click one to show only its paths</span>
              </h3>
              <table className="plain rt-runs by-test">
                <thead>
                  <tr><th>Test</th><th>Speed</th><th title="Reached its distance / runs">Done</th><th title="Sideways at the end, averaged; + is left">Drift</th><th title="Steady speed, or turn rate for spins">Rate</th><th title="Command to the first centimetre">React</th></tr>
                </thead>
                <tbody>
                  {report.by_test.map((t) => (
                    <TestRow key={`${t.test}@${t.speed}`} row={t} on={selected === t.test}
                      onClick={() => onSelect(selected === t.test ? null : t.test)} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

/** The real robot beside the simulated one, and what to change in sim/config.py. */
function VsSim({ report }: { report: MotionReport }) {
  const hw = report.hardware;
  const rows = report.vs_sim;
  const measured = hw && Object.entries(hw).some(([k, v]) => k !== "camera_latency_ms" && v !== null);
  if (!measured) {
    return <p className="note">Run some tests at speed 1 to measure top speed, acceleration and turn rate against the simulator.</p>;
  }
  const suggestions = rows?.filter((r) => r.suggest) ?? [];
  return (
    <div>
      <h3 className="rt-sub">Real robot vs the sim <span className="dim">· at full command</span></h3>
      <table className="plain rt-runs vs-sim">
        <thead><tr><th></th><th>Real</th><th>Sim</th><th>Real ÷ sim</th></tr></thead>
        <tbody>
          {(rows ?? []).map((r) => <SimRow key={r.key} row={r} />)}
        </tbody>
      </table>
      {suggestions.length > 0 && (
        <>
          <p className="note">To make the sim's robot move more like this one, in <code>sim/config.py</code>:</p>
          <ul className="findings mono small">{suggestions.map((r) => <li key={r.key}>{r.suggest}</li>)}</ul>
          <p className="note">
            The sim slows its robot smoothly (<code>LINEAR_DAMP</code>); a robot that brakes hard when its command stops
            won't fit that well. Change one value at a time and test again.
          </p>
        </>
      )}
      {hw.camera_latency_ms !== null && hw.camera_latency_ms >= 1 && (
        <p className="note">
          The camera's pictures were {hw.camera_latency_ms.toFixed(0)} ms old when they arrived. The timings above use
          when each picture was taken, so that isn't counted against the robot.
        </p>
      )}
    </div>
  );
}

function SimRow({ row: r }: { row: VsSimRow }) {
  const v = (x: number | null) => (x === null ? "–" : `${x.toFixed(r.digits)} ${r.unit}`);
  const off = r.ratio !== null && (r.ratio < 0.8 || r.ratio > 1.25);
  return (
    <tr>
      <td>{r.label}</td>
      <td className="mono">{v(r.real)}</td>
      <td className="mono">{v(r.sim)}</td>
      <td className={`mono${off ? " warn-text" : ""}`}>{r.ratio === null ? "–" : `× ${r.ratio.toFixed(2)}`}</td>
    </tr>
  );
}

function AbilityRow({ ability: a, was }: { ability: Ability; was: Ability | null }) {
  return (
    <li title={a.detail}>
      <span className="ab-label">{a.label}</span>
      <span className={`grade ${a.grade ?? "none"}`}>{a.grade ?? "not tested"}</span>
      <span className="ab-value mono">{a.value}</span>
      {was && (
        <span className="ab-was dim">
          was <span className={`grade sm ${was.grade ?? "none"}`}>{was.grade ?? "–"}</span> {was.value}
        </span>
      )}
    </li>
  );
}

function TestRow({ row: t, on, onClick }: { row: ByTest; on: boolean; onClick: () => void }) {
  const spin = t.test.startsWith("spin");
  return (
    <tr className={`clickable${on ? " on" : ""}${t.reached < t.runs ? " off" : ""}`} onClick={onClick}>
      <td>{t.label}</td>
      <td className="mono">{t.speed}</td>
      <td className="mono">{t.reached}/{t.runs}</td>
      <td className="mono">{spin ? "–" : fmt(t.drift_m, 1000, "mm")}{t.drift_sd_m != null && !spin ? ` ±${(t.drift_sd_m * 1000).toFixed(0)}` : ""}</td>
      <td className="mono">{spin ? fmt(t.rate, 1, "°/s") : fmt(t.rate, 1, "m/s", 2)}</td>
      <td className="mono">{fmt(t.reaction_s, 1000, "ms")}</td>
    </tr>
  );
}

function compareKey(r: MotionReport, saved: SavedReport[]) {
  const hit = saved.find((s) => s.robot === r.robot && s.started === r.started);
  return hit ? `${hit.robot}/${hit.file}` : "";
}

function label(r: SavedReport) {
  return `${r.robot} · ${r.started ? when(r.started) : r.file} · ${r.runs} runs`;
}

function when(t: number) {
  return new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** A measurement for a table cell: scaled, with its unit, or "–". */
export function fmt(v: number | null | undefined, scale: number, unit: string, digits = 0) {
  return v === null || v === undefined ? "–" : `${(v * scale).toFixed(digits)} ${unit}`;
}
