import { useEffect, useState } from "react";
import { Logo } from "./components/Logo";
import { Toasts } from "./components/Toasts";
import { usePhotonVision } from "./lib/photonvision";
import { useSim } from "./lib/sim";
import { Ball } from "./pages/Ball";
import { Calibration } from "./pages/Calibration";
import { Simulator } from "./pages/Simulator";
import { Tracking } from "./pages/Tracking";

const PAGES = [
  { path: "/sim", label: "Simulator" },
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
          : <Simulator />}
      </main>
      <Toasts />
    </>
  );
}

function Header({ route }: { route: string }) {
  const sim = useSim();
  const { status: pv } = usePhotonVision();
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
        {pv &&
          (pv.up ? (
            <a className="stat" href={`http://${location.hostname}:5800`} target="_blank" rel="noreferrer" title="Open PhotonVision's own UI">
              <span className="dot ok" />
              PhotonVision
            </a>
          ) : (
            <a className="stat" href="#/calibration" title="Start it from Field calibration">
              <span className={`dot${pv.state === "starting" ? " warn pulse" : ""}`} />
              PhotonVision {pv.state === "starting" ? "starting" : "off"}
            </a>
          ))}
        <span className="stat" title={sim.connected ? "" : "Can't reach the app server"}>
          <span className={`dot${sim.up ? " ok" : !sim.connected ? " red" : ""}`} />
          {!sim.connected ? "Server offline" : sim.up ? "Sim running" : "Sim stopped"}
        </span>
      </div>
    </header>
  );
}
