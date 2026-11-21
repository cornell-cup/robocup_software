// The Code page's Blocks view (Google Blockly): snap blocks together, and
// each change gives the blocks' JSON and the Python they make
// (blocks/robotBlocks.ts). Loaded only when a file is opened as blocks.

import * as Blockly from "blockly/core";
import { useEffect, useRef, useState } from "react";
import { BUILT_IN_STYLES, generate, installBlocks, PALETTE, TOOLBOX } from "../blocks/robotBlocks";

// The app's font (styles/app.css --sans) for every word on the blocks too.
const SANS = '"Exo 2 Variable", "Exo 2", ui-sans-serif, system-ui, "Segoe UI", sans-serif';
const FONT = { family: SANS, weight: "600", size: 12 };

/** A colour moved toward another: t = 0 is `hex`, 1 is `to`. */
function mix(hex: string, to: string, t: number): string {
  const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [a, b] = [rgb(hex), rgb(to)];
  return `#${a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

function blockStyle(colour: string): Partial<Blockly.Theme.BlockStyle> {
  return {
    colourPrimary: colour,
    colourSecondary: mix(colour, "#ffffff", 0.22), // a value block's default, plugged in
    colourTertiary: mix(colour, "#000000", 0.35), // outlines
  };
}

let theme: Blockly.Theme | null = null;
function darkTheme(): Blockly.Theme {
  if (theme) return theme;
  const blockStyles: Record<string, Partial<Blockly.Theme.BlockStyle>> = {};
  for (const [name, colour] of Object.entries(PALETTE)) blockStyles[name] = blockStyle(colour);
  for (const [name, ours] of Object.entries(BUILT_IN_STYLES)) blockStyles[name] = blockStyle(PALETTE[ours]);
  const categoryStyles = Object.fromEntries(Object.entries(PALETTE).map(([name, colour]) => [name, { colour }]));
  theme = Blockly.Theme.defineTheme("robocup-dark", {
    name: "robocup-dark",
    base: Blockly.Themes.Classic,
    blockStyles: blockStyles as { [key: string]: Blockly.Theme.BlockStyle },
    categoryStyles,
    componentStyles: {
      workspaceBackgroundColour: "#0a0a0a",
      toolboxBackgroundColour: "#111111",
      toolboxForegroundColour: "#f4f4f4",
      flyoutBackgroundColour: "#161616",
      flyoutForegroundColour: "#a3a3a3",
      flyoutOpacity: 1,
      scrollbarColour: "#3a3a3a",
      scrollbarOpacity: 0.8,
      insertionMarkerColour: "#ffffff",
      insertionMarkerOpacity: 0.25,
      cursorColour: "#e0261a",
      selectedGlowColour: "#f4f4f4",
    },
    fontStyle: FONT,
    startHats: false,
  });
  return theme;
}

/** Whether any two top blocks overlap. */
function overlapping(w: Blockly.WorkspaceSvg): boolean {
  const rects = w.getTopBlocks(false).map((b) => b.getBoundingRectangle());
  return rects.some((a, i) => rects.slice(i + 1).some((b) => a.intersects(b)));
}

export default function BlocksEditor({
  docKey,
  state,
  onChange,
}: {
  /** Which file: a new one loads its blocks fresh. */
  docKey: string;
  /** The file's blocks (Blockly's JSON) to load. */
  state: object | null;
  /** Every change: the blocks' JSON and their Python. `loaded`: right after loading, not an edit. */
  onChange: (state: object, python: string, loaded: boolean) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const ws = useRef<Blockly.WorkspaceSvg | null>(null);
  const changed = useRef(onChange);
  changed.current = onChange;
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let gone = false;
    let end = () => {};
    // Blockly measures each label as it draws: with the font loaded, not a stand-in.
    document.fonts.load(`${FONT.weight} ${FONT.size}pt "Exo 2 Variable"`).catch(() => []).then(() => {
      if (gone) return;
      installBlocks();
      const w = Blockly.inject(host.current!, {
        toolbox: TOOLBOX as Blockly.utils.toolbox.ToolboxDefinition,
        theme: darkTheme(),
        renderer: "thrasos", // flat and compact, like the rest of the app
        trashcan: true,
        zoom: { controls: true, wheel: true, startScale: 1, maxScale: 2, minScale: 0.4, scaleSpeed: 1.15 },
        move: { scrollbars: true, drag: true, wheel: false },
        grid: { spacing: 24, length: 2, colour: "#262626", snap: true },
        sounds: false,
      });
      ws.current = w;
      w.addChangeListener((e: Blockly.Events.Abstract) => {
        if (e.isUiEvent) return;
        changed.current(Blockly.serialization.workspaces.save(w), generate(w), false);
      });
      const resize = new ResizeObserver(() => Blockly.svgResize(w));
      resize.observe(host.current!);
      end = () => {
        resize.disconnect();
        w.dispose();
        ws.current = null;
      };
      setReady(true);
    });
    return () => {
      gone = true;
      end();
    };
  }, []);

  // Another file (or the first): load its blocks.
  useEffect(() => {
    const w = ws.current;
    if (!w) return;
    // Blockly fires its events a moment later: off while loading, so a load isn't an edit.
    Blockly.Events.disable();
    try {
      w.clear();
      if (state) Blockly.serialization.workspaces.load(state, w);
      // Just converted (no places yet) or piled up: one tidy column, in order.
      if (overlapping(w)) w.cleanUp();
    } finally {
      Blockly.Events.enable();
    }
    w.scroll(0, 0);
    changed.current(Blockly.serialization.workspaces.save(w), generate(w), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on a file switch only
  }, [docKey, ready]);

  return <div className="blocks-host" ref={host} />;
}
