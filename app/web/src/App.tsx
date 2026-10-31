import { useEffect, useState } from "react";
import { Logo } from "./components/Logo";
import { Toasts } from "./components/Toasts";
import { photonvision, usePhotonVision } from "./lib/photonvision";
import { useSim } from "./lib/sim";
import { Ball } from "./pages/Ball";
import { Calibration } from "./pages/Calibration";
import { Robots } from "./pages/Robots";
import { Simulator } from "./pages/Simulator";
import { Tracking } from "./pages/Tracking";

const PAGES = [
  { path: "/sim", label: "Simulator" },
  { path: "/robots", label: "Robots" },
  { path: "/tracking", label: "Tracking" },
  { path: "/calibration", label: "Field calibration" },
  { path: "/ball", label: "Ball profile" },
];

function currentRoute(): string {
  const p = location.hash.replace(/^#/, "") || "/sim";
  return PAGES.some((x) => x.path === p) ? p : "/sim";
}

export default function App() {
  const [route, setRoute] = useState(currentRoute);
  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <>
      <Header route={route} />
      <main>
        {route === "/calibration" ? <Calibration />
          : route === "/ball" ? <Ball />
          : route === "/tracking" ? <Tracking />
          : route === "/robots" ? <Robots />
          : <Simulator />}
      </main>
      <Toasts />
    </>
  );
}

/** PhotonVision in the header, on every page: the camera pages all need it.
 *  Shows its state with the one action that makes sense next. */
function PhotonVisionStatus() {
  const { status: pv, busy } = usePhotonVision();
  if (!pv) return null;
  const inst = pv.install;
  const downloading = inst.state === "starting" || inst.state === "working";

  if (pv.up) {
    return (
      <span className="stat">
        <a className="stat" href={`http://${location.hostname}:5800`} target="_blank" rel="noreferrer" title="Open PhotonVision's own UI: camera, lens calibration, AprilTag pipeline">
          <span className="dot ok" />
          PhotonVision <span className="ext-arrow" aria-hidden="true">↗</span>
        </a>
        {pv.ours && <button className="btn sm ghost stat-btn" disabled={busy} onClick={photonvision.stop}>Stop</button>}
      </span>
    );
  }
  if (downloading) {
    const pct = inst.total ? ` ${Math.round(((inst.got ?? 0) / inst.total) * 100)}%` : "";
    return (
      <span className="stat" title={inst.step ?? undefined}>
        <span className="dot warn pulse" />
        {pv.from_source ? "Building" : "Downloading"} PhotonVision{pct}
      </span>
    );
  }
  if (pv.state === "starting") {
    return (
      <span className="stat">
        <span className="dot warn pulse" />
        PhotonVision starting…
      </span>
    );
  }
  if (!pv.installed && pv.no_download) {
    return (
      <span className="stat" title={pv.no_download}>
        <span className="dot" />
        PhotonVision: no build for this computer
      </span>
    );
  }
  if (!pv.installed) {
    return (
      <span className="stat" title={pv.from_source ? `No Mac release, so this builds it from source, which takes a while, into ${pv.folder}` : `About 160 MB, into ${pv.folder}`}>
        <span className="dot" />
        PhotonVision not installed
        <button className="btn sm primary stat-btn" disabled={busy} onClick={photonvision.install}>{pv.from_source ? "Build" : "Download"}</button>
      </span>
    );
  }
  return (
    <span className="stat" title={pv.failure ?? `${pv.jar} in ${pv.folder}`}>
      <span className={`dot${pv.failure ? " red" : ""}`} />
      PhotonVision off
      <button className="btn sm stat-btn" disabled={busy} onClick={photonvision.start}>Start</button>
    </span>
  );
}

function Header({ route }: { route: string }) {
  const sim = useSim();
  return (
    <header className="top">
      <a className="brand" href="#/sim">
        <Logo size={32} />
        <span className="word">
          Cornell Cup Robotics <span>RoboCup</span>
        </span>
      </a>

      <nav>
        {PAGES.map((p) => (
          <a key={p.path} href={`#${p.path}`} className={route === p.path ? "on" : undefined}>
            {p.label}
          </a>
        ))}
      </nav>

      <div className="status">
        <PhotonVisionStatus />
        <span className="stat" title={sim.connected ? "" : "Can't reach the app server"}>
          <span className={`dot${sim.up ? " ok" : !sim.connected ? " red" : ""}`} />
          {!sim.connected ? "Server offline" : sim.up ? "Sim running" : "Sim stopped"}
        </span>
      </div>
    </header>
  );
}
