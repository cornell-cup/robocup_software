// Shapes the server sends. The world state is simulation_node's own message.

export interface FieldInfo {
  width: number;
  height: number;
  goal_y_min: number;
  goal_y_max: number;
  robot_radius: number;
  ball_radius: number;
  team_blue_size: number;
  num_robots: number;
  is_default: boolean;
}

export interface Robot {
  x: number;
  y: number;
  angle: number;
  vx?: number;
  vy?: number;
  stale?: boolean;
  source?: string;
  tag?: number | null;
}

export interface Ball {
  x: number;
  y: number;
  vx?: number;
  vy?: number;
  stale?: boolean;
  source?: string;
}

export interface Game {
  half: number;
  time_remaining: number;
  half_over: boolean;
  phase: string;
  winner: "blue" | "red" | "draw" | null;
  ball_stuck_seq: number;
  ball_stuck_enabled: boolean;
}

export interface World {
  t: number;
  score: { blue: number; red: number };
  last_goal: { seq: number; team: string; score: { blue: number; red: number } } | null;
  game: Game;
  ball: Ball;
  robots: Record<string, Robot>;
  field?: FieldInfo;
  vision?: Record<string, any>;
}

/** What FieldView draws: a sim world, or one made from the tracker's output. */
export interface FieldScene {
  field?: FieldInfo;
  ball: Ball | null;
  robots: Record<string, Robot>;
}

export interface Controls {
  strategy: boolean;
  dribble: boolean;
  rl_kick: boolean;
}

export interface WorldMessage {
  type: "world";
  up: boolean;
  world: World | null;
  attackers: string[];
  controls: Controls;
  manual_error: string | null;
}

export type Mode = "pd" | "time" | "mpc";

/** Where a robot was last sent, drawn until it gets there. */
export interface Pin {
  robot: number;
  x: number;
  y: number;
  fromX: number | null;
  fromY: number | null;
  /** The controller's colour, as the pygame window drew its pin. */
  color: string;
}

export type SimCommand =
  | { type: "target"; robot: number; x: number; y: number; mode: Mode }
  | { type: "drive"; robot: number; vx: number; vy: number; w: number }      // field directions
  | { type: "drive"; robot: number; forward: number; left: number; w: number } // the robot's own frame
  | { type: "kick"; robot: number }
  | { type: "strategy" | "dribble" | "rl_kick" | "ball_stuck_reset"; enabled: boolean };

export interface SimProcessStatus {
  running: boolean;
  pid: number | null;
  exit_code: number | null;
  options: { strategy?: boolean; color?: string; field?: number[] | null; vision?: string | null };
}

export interface Detection {
  id: number;
  corners: number[][];
  center: number[];
}

export type CheckGrade = "PASS" | "WARN" | "FAIL" | "SKIP";

export interface CalibCheck {
  active: boolean;
  /** The last finished check, until another starts. */
  summary: {
    verdict: "PASS" | "WARN" | "FAIL";
    values: {
      pos_rms_mm: number;
      pos_max_mm: number;
      heading_max_deg: number;
      off_plane_median_mm: number;
      jitter_mm: number;
      detection_min_pct: number;
    };
    worst_point: number[];
    n_points: number;
    n_skipped: number;
    diagnosis: string[];
    report: string;
    checked_at: number;
  } | null;
  index?: number;
  total?: number;
  /** Field coordinates, metres, as the trackers report them. */
  targets?: number[][];
  /** The same spots in raw image pixels, for the overlay. */
  targets_px?: number[][] | null;
  count?: number;
  frames?: number;
  ready?: boolean;
  message?: string;
  live_mm?: number | null;
  placement?: string;
  results?: { x: number; y: number; skipped: boolean; err_mm: number | null; heading_err_deg: number | null; grade: CheckGrade }[];
}

export interface CalibStatus {
  state: "disconnected" | "connecting" | "connected" | "error";
  error: string | null;
  /** PhotonVision's cameras, when it has several and none was picked. */
  cameras: string[] | null;
  capturing: boolean;
  captured: number;
  setup: {
    tag_size: number;
    field: number[];
    layout: Record<string, number[]>;
    min_frames: number;
    max_frames: number;
    sequential: boolean;
  } | null;
  /** One tag at a time: where the capture is. */
  corners?: {
    index: number;
    total: number;
    count: number;
    per_corner: number;
    ready: boolean;
    status: "no_tag" | "several" | "at_previous" | "moving" | "capturing" | "ready" | "done" | null;
    captured: number[][];
  };
  rate: number;
  camera?: {
    name: string;
    size: number[];
    fx: number;
    source: string;
    stream: string | null;
    host: string | null;
  };
  detections: Detection[];
  result?: {
    from: "calibrated" | "saved";
    source: string;
    field: number[];
    origin: number[] | null;
    frames: number | null;
    rotation_spread_deg: number | null;
    translation_spread_mm: number | null;
    reproj_px: number | null;
    max_reproj_px: number | null;
    /** One tag at a time: how far each corner's tag lands from where it was put, mm. */
    fit_mm: (number | null)[] | null;
    camera_height_m: number;
  };
  overlay?: {
    outline: number[][][];
    centre_lines: number[][][];
    snaps: number[][];
    origin: number[];
    x_axis: number[];
    y_axis: number[];
  };
  /** The accuracy check (verify-calibration): one tag on 9 tape-measured spots. */
  check?: CalibCheck;
  saved: {
    path: string;
    exists: boolean;
    source?: string;
    field?: number[] | null;
    origin?: number[] | null;
    modified?: number;
    error?: string;
  };
  saved_to?: string;
}

export interface PhotonVisionStatus {
  folder: string;
  installed: boolean;
  jar: string | null;
  java: string | null;
  java_bundled: boolean;
  version: string | null;
  photonlibpy: string | null;
  version_mismatch: boolean;
  running: boolean;
  ours: boolean;
  exit_code: number | null;
  up: boolean;
  state: "running" | "external" | "starting" | "stopped";
  failure: string | null;
  setup_hints: string[];
  /** The right jar, or a Java of its own, is missing from the folder. */
  needs_download: boolean;
  /** Why this machine can't download PhotonVision, else null. */
  no_download: string | null;
  /** macOS: no released jar, so the download builds it from source instead. */
  from_source: boolean;
  install: {
    state: "idle" | "starting" | "working" | "done" | "error";
    step?: string | null;
    got?: number | null;
    total?: number | null;
    error?: string | null;
  };
  ui: string;
}

export interface BallStatus {
  state: "disconnected" | "connecting" | "connected" | "error";
  error: string | null;
  stream: { url: string; size: number[] } | null;
  profile: {
    name: string;
    radius_mm: number;
    note: string;
    threshold: number;
    s_min: number;
    h_spread: number;
    s_spread: number;
    sampled: boolean;
    sample_px: number;
    hue: number | null;
  };
  stats: { mask_pct?: number; blobs?: number; looks_right?: boolean };
  saved: {
    path: string;
    error?: string;
    profiles: {
      name: string;
      radius_mm: number;
      hue: number;
      threshold: number;
      s_min: number;
      created: string;
      note: string;
    }[];
  };
  saved_to?: string;
}

export interface SceneHues {
  bins: { h0: number; share: number }[];
  ball_hue: number | null;
}

/** One frame of track-combined's output (its build_state_doc). */
export interface TrackingDoc {
  t_capture: number;
  field: { width: number; height: number; x0: number; y0: number };
  tags: {
    id: number; x: number; y: number; theta_deg: number; speed: number;
    visible: boolean; lost?: boolean; inside?: boolean; age?: number;
  }[];
  ball: { x: number; y: number; z?: number; speed?: number; visible: boolean; grounded?: boolean; resting?: boolean } | null;
  stats: {
    fps?: number; latency_ms?: number; photon_latency_ms?: number;
    capture_dropped?: number; photon_skipped?: number; ball_dropouts?: number;
    /** What the tracker's keys have set (track-combined --control). */
    view?: { grid: boolean; trails: boolean; mask: boolean; mode: "floor" | "wall"; ball_min_conc: number };
  };
}

/** The tracker's newest output, as status's "live" and the tracking socket send it. */
export interface TrackingLive {
  up: boolean;
  age_s: number | null;
  n: number;
  doc: TrackingDoc | null;
}

export interface TrackingStatus {
  process: {
    running: boolean;
    pid: number | null;
    exit_code: number | null;
    options: { tag_size?: number; ball_profile?: string; recording?: string | null; synthetic_field?: boolean };
  };
  live: TrackingLive;
  /** Field calibrations in vision/calib the tracker can load. */
  field_poses: string[];
}

export interface Recording {
  name: string;
  path: string;
  size_kb: number;
  modified: number;
}

// ── real robots (Robots page) ───────────────────────────────────────────

export interface RealRobot {
  name: string;
  /** the Bluetooth link: searching, connecting, connected, retrying */
  state: string;
  connected: boolean;
  error: string | null;
  /** the newest command is not "stopped" */
  moving: boolean;
  command_age_s: number | null;
  /** the sim robot it follows, or null when driven by hand */
  follow: number | null;
  /** following: whether the camera sees it right now (it only moves then) */
  tracked: boolean | null;
}

export interface RealRobotsStatus {
  robots: RealRobot[];
  /** robot_node is publishing commands for real robots */
  sim_up: boolean;
}

export interface RobotDrive {
  type: "drive";
  name: string;
  forward: number;
  left: number;
  turn: number;
  dribble: boolean;
}
