// Workspaces for the Code page: the server's /api/v1/teams (teams.py). A
// workspace is a folder of Python files; its team starts in team.py.

import type { LintFinding, Reference } from "../components/codeAssist";
import { api, BASE } from "./api";

export interface WorkspaceFile {
  name: string;
  size: number;
  modified: number;
}

export interface Workspace {
  name: string;
  files: WorkspaceFile[];
  modified: number;
  author: string;
  /** Someone else's code (uploaded): plays in safe mode. */
  safe: boolean;
  origin: "own" | "uploaded";
  from: string;
}

export interface CheckResult {
  ok: boolean;
  slots?: string[];
  error?: string;
  file: string | null;
  line: number | null;
  printed?: string[];
  safe?: boolean;
}

export interface MatchResult {
  a: string;
  b: string;
  games: number;
  goals: [number, number];
  wins: number;
  draws: number;
  losses: number;
  diff_per_game: number;
  diff_ci95: number | null;
  possession: [number, number];
  ball_in_b_half: number;
  errors: { a: string[]; b: string[] };
  per_game: { seed: number; a_color: string; goals: [number, number] }[];
}

export interface MatchStatus {
  running: boolean;
  exit_code: number | null;
  request: { a?: string; b?: string; games?: number; duration?: number };
  result: MatchResult | null;
  lines: { n: number; text: string }[];
}

/** One past match, from a workspace's side. */
export interface HeadToHead {
  a: string;
  b: string;
  when: number;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  goals: [number, number];
}

export type Start = "template" | "get_open" | "blank";
export const ENTRY = "team.py";

const enc = encodeURIComponent;
const ws = (name: string) => `/teams/${enc(name)}`;
const file = (w: string, f: string) => `${ws(w)}/files/${enc(f)}`;

export const teams = {
  list: () => api.get<{ workspaces: Workspace[] }>("/teams").then((r) => r.workspaces),
  info: (w: string) => api.get<Workspace>(ws(w)),
  create: (name: string, start: Start) => api.post<Workspace>("/teams/new", { name, start }),
  duplicate: (w: string, name: string) => api.post<Workspace>(`${ws(w)}/duplicate`, { name }),
  rename: (w: string, name: string) => api.post<Workspace>(`${ws(w)}/rename`, { name }),
  remove: (w: string) => api.post<{ workspaces: Workspace[] }>(`${ws(w)}/delete`).then((r) => r.workspaces),
  setAuthor: (w: string, author: string) => api.post<Workspace>(`${ws(w)}/author`, { author }),
  check: (w: string) => api.post<CheckResult>(`${ws(w)}/check`),
  results: (w: string) => api.get<{ results: HeadToHead[] }>(`${ws(w)}/results`).then((r) => r.results),
  downloadUrl: (w: string) => `${BASE}${ws(w)}/download`,
  read: (w: string, f: string) => api.get<{ name: string; text: string }>(file(w, f)),
  save: (w: string, f: string, text: string) => api.post<{ modified: number }>(`${file(w, f)}/save`, { text }),
  newFile: (w: string, name: string) => api.post<Workspace>(`${ws(w)}/files/new`, { name }),
  renameFile: (w: string, f: string, name: string) => api.post<Workspace>(`${file(w, f)}/rename`, { name }),
  removeFile: (w: string, f: string) => api.post<Workspace>(`${file(w, f)}/delete`),
  upload: (files: { name: string; data: string }[], opts: { into?: string; as?: string; replace?: boolean }) =>
    api.post<Workspace>("/teams/upload", { files, ...opts }),
  guide: () => api.get<{ text: string }>("/teams/guide").then((r) => r.text),
  reference: () => api.get<Reference>("/teams/reference"),
  lint: (w: string, f: string, text: string) =>
    api.post<{ diagnostics: LintFinding[] }>(`${ws(w)}/lint`, { file: f, text }).then((r) => r.diagnostics),
  matchStart: (a: string, b: string, games: number, duration: number) =>
    api.post<MatchStatus>("/teams/match/start", { a, b, games, duration }),
  matchStatus: (after: number) => api.get<MatchStatus>(`/teams/match?after=${after}`),
  matchStop: () => api.post<MatchStatus>("/teams/match/stop"),
};

/** A workspace name from what someone typed: "Anna's Team" -> "anna_s_team". */
export function toWorkspaceName(typed: string): string {
  return typed.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}

/** A module file name from what someone typed: "Passing" -> "passing.py". */
export function toFileName(typed: string): string {
  let base = typed.trim().replace(/\.py$/i, "").toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  if (/^[0-9]/.test(base)) base = `m_${base}`;
  return base ? `${base.slice(0, 40)}.py` : "";
}

/** The file and line an error message points at: "passing.py:12: ..." or "(team.py:4)". */
export function locate(message: string): { file: string | null; line: number | null } {
  const m = message.match(/\b([A-Za-z_]\w*\.py):(\d+)/);
  return m ? { file: m[1], line: Number(m[2]) } : { file: null, line: null };
}

/** "supporter, robot 2: AttributeError: ... (f.py:4); using the built-in supporter"
 *  -> the same message for any robot, so one problem shows once. */
export function withoutRobot(message: string): string {
  return message.replace(/, robot \d+:/, ":");
}

/** A file's bytes as base64, for an upload. */
export async function readAsBase64(f: File): Promise<string> {
  const bytes = new Uint8Array(await f.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
