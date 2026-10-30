import type { ReactNode } from "react";

export function Card({
  title,
  step,
  aside,
  active = false,
  done = false,
  children,
}: {
  title: string;
  step?: number;
  aside?: ReactNode;
  active?: boolean;
  done?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={`card${active ? " active" : ""}`}>
      <header>
        <h2>
          {step !== undefined && <span className="step">{step}.</span>}
          {title}
          {done && <span className="done-mark">done</span>}
        </h2>
        {aside && <div className="aside">{aside}</div>}
      </header>
      <div className="body">{children}</div>
    </section>
  );
}
