// The students' guide (strategy/docs/guide.md, from the server), rendered from
// Markdown, with a Copy button on every code example.

import { marked } from "marked";
import { useEffect, useMemo, useRef } from "react";

export function Guide({ text }: { text: string | null }) {
  const box = useRef<HTMLDivElement>(null);
  // The guide is this repo's own file, not user input: rendering its HTML is safe.
  const html = useMemo(() => (text ? (marked.parse(text, { async: false }) as string) : ""), [text]);

  useEffect(() => {
    const root = box.current;
    if (!root) return;
    for (const pre of Array.from(root.querySelectorAll("pre"))) {
      if (pre.querySelector(".copy")) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn sm ghost copy";
      button.textContent = "Copy";
      button.onclick = () => {
        const code = pre.querySelector("code")?.textContent ?? "";
        navigator.clipboard.writeText(code).then(
          () => {
            button.textContent = "Copied";
            setTimeout(() => (button.textContent = "Copy"), 1500);
          },
          () => (button.textContent = "Select and copy by hand"),
        );
      };
      pre.appendChild(button);
    }
  }, [html]);

  if (text === null) return <div className="guide dim">Loading the guide…</div>;
  return <div className="guide" ref={box} dangerouslySetInnerHTML={{ __html: html }} />;
}
