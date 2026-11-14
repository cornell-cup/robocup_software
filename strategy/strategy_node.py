# receives world state, runs strategy & control the game
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import TYPE_CHECKING, Any, final

import numpy as np

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill

# Add project root so `strategy.*` resolves when run as a standalone script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VISION_PORT = 9090
STRATEGY_PORT = 9091
COMMAND_PORT = 9092
MANUAL_PORT = 9093

# The one checkpoint committed to git (.gitignore keeps only first_final/final.zip).
DEFAULT_RL_CHECKPOINT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "rl", "checkpoints", "first_final", "final.zip",
)

from dataclasses import dataclass  # noqa: E402

from strategy.geometry import distance, opp_goal, our_goal  # noqa: E402
from strategy.state import (  # noqa: E402
    FIELD_LENGTH,
    FIELD_SCALE,
    GOAL_Y_MAX,
    GOAL_Y_MIN,
    GameState,
    build_game_state,
)

our_color = "blue"


@final
class ZMQBackend:
    def __init__(self):
        import zmq

        self.zmq = zmq
        self.context = zmq.Context()

        self.vision_sub = self.context.socket(zmq.SUB)
        _ = self.vision_sub.connect(f"tcp://localhost:{VISION_PORT}")
        self.vision_sub.setsockopt_string(zmq.SUBSCRIBE, "")
        self.vision_sub.setsockopt(zmq.RCVTIMEO, 100)

        self.strategy_pub = self.context.socket(zmq.PUB)
        _ = self.strategy_pub.bind(f"tcp://*:{STRATEGY_PORT}")

        # Listen to viz for the RL-kick toggle.
        self.manual_sub = self.context.socket(zmq.SUB)
        _ = self.manual_sub.connect(f"tcp://localhost:{MANUAL_PORT}")
        self.manual_sub.setsockopt_string(zmq.SUBSCRIBE, "")
        self.manual_sub.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking

        # Push kick commands straight to the sim.
        self.cmd_push = self.context.socket(zmq.PUSH)
        _ = self.cmd_push.connect(f"tcp://localhost:{COMMAND_PORT}")

        print(
            f"[Strategy node] ZMQ mode: vision←:{VISION_PORT}  "
            + f"strategy→:{STRATEGY_PORT}  manual←:{MANUAL_PORT}  cmds→:{COMMAND_PORT}"
        )

    def receive_state(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.vision_sub.recv_string())
        except self.zmq.Again:
            return None
        # Drain buffered frames so we always process the most recent state.
        while True:
            try:
                raw = json.loads(self.vision_sub.recv_string(flags=self.zmq.NOBLOCK))
            except self.zmq.Again:
                break
        return raw

    def drain_manual(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        while True:
            try:
                msgs.append(json.loads(self.manual_sub.recv_string()))
            except self.zmq.Again:
                break
        return msgs

    def send_targets(self, msg: dict[str, Any]) -> None:
        _ = self.strategy_pub.send_string(json.dumps(msg))

    def send_kick(self, robot_id: int) -> None:
        _ = self.cmd_push.send_string(
            json.dumps({"type": "kick", "robot_id": int(robot_id)})
        )

    def send_dribble(self, robot_id: int, active: bool) -> None:
        _ = self.cmd_push.send_string(
            json.dumps({"type": "dribble", "robot_id": int(robot_id), "active": bool(active)})
        )


@final
class TCPBackend:
    def __init__(self):
        import socket
        import time

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for attempt in range(10):
            try:
                self.sock.connect(("127.0.0.1", VISION_PORT))
                print(f"[StrategyNode] TCP mode: connected to localhost:{VISION_PORT}")
                break
            except ConnectionRefusedError:
                print(
                    f"[StrategyNode] Connection refused, retrying ({attempt + 1}/10)..."
                )
                time.sleep(1)
        else:
            raise ConnectionError("Could not connect to Godot after 10 attempts")

        self.sock.setblocking(False)
        self.buffer = ""

    def receive_state(self) -> dict[str, Any] | None:
        import socket

        try:
            chunk = self.sock.recv(65536).decode()
            if not chunk:
                return None
            self.buffer += chunk
        except (BlockingIOError, socket.error):
            pass

        raw = None
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    pass
        return raw

    def drain_manual(self) -> list[dict[str, Any]]:
        return []

    def send_targets(self, msg: dict[str, Any]) -> None:
        try:
            self.sock.sendall((json.dumps(msg) + "\n").encode())
        except BrokenPipeError:
            print("[Strategy node] TCP mode: connection lost")

    def send_kick(self, robot_id: int) -> None:
        pass  # TCP mode has no command channel; kicks only work in ZMQ mode.

    def send_dribble(self, robot_id: int, active: bool) -> None:
        pass  # TCP mode has no command channel; dribble only works in ZMQ mode.


def decide(
    gamestate: GameState,
    rl_skill: "RLKickSkill | None" = None,
    rl_enabled: bool = False,
    sticky_attackers: dict[str, int] | None = None,
    kicked_off: bool = True,
    human_blue_ids: set[int] | None = None,
    lost_ids: set[int] | None = None,
) -> tuple[dict[int, tuple[float, float, float]], list[int], set[int]]:
    # Placeholder for Decision Tree
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    lost = lost_ids or set()
    ball = gamestate.ball

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
        attacks_right = team_color == "blue"
        team_dir = 0.0 if attacks_right else math.pi

        # Drop the robots the AI can't drive: human-driven ones (blue only)
        # and camera robots the camera has lost. The remaining team falls into
        # whichever role-count branch fits its size (1 → goalie,
        # 2 → attacker+supporter, 3 → +defender).
        skip = (human_ids if team_color == "blue" else set()) | lost
        if skip:
            team = [r for r in team if r.id not in skip]
            if not team:
                sticky.pop(team_color, None)
                continue

        # Single-robot teams act as the lone goalie: stay on the line from
        # own goal to the ball at a small standoff. No kick is emitted.
        if len(team) == 1:
            robot = team[0]
            tx, ty, ta = _goalie_target(ball.pos, team_color)
            targets[robot.id] = (tx, ty, ta)
            sticky.pop(team_color, None)
            continue

        # Roles purely by distance to ball: closest = attacker, middle =
        # supporter, furthest = defender. Sticky hysteresis keeps the
        # attacker stable while the supporter's path runs past the ball.
        sorted_team = sorted(team, key=lambda r: distance(r.pos, ball.pos))

        # Sticky attacker with hysteresis. The supporter's path runs past the
        # ball, so per-tick "closest is attacker" flips the role mid-stride and
        # the wrong robot ends up emitting the kick. Keep the incumbent unless
        # a teammate is meaningfully closer.
        last_rid = sticky.get(team_color)
        incumbent = (
            next((r for r in sorted_team if r.id == last_rid), None)
            if last_rid is not None
            else None
        )
        if incumbent is not None and (
            distance(sorted_team[0].pos, ball.pos) + ATTACKER_HYSTERESIS
            >= distance(incumbent.pos, ball.pos)
        ):
            sorted_team = [incumbent] + [r for r in sorted_team if r.id != incumbent.id]
        sticky[team_color] = sorted_team[0].id

        for i, robot in enumerate(sorted_team):
            if i == 0:  # attacker
                attacker_rids.add(robot.id)
                # Policy was trained with robot west of ball (blue-attacks-right).
                # Mirror works cleanly only when the attacker is on its own side
                # of the ball along the attack axis; otherwise the policy drifts
                # OOD and picks the long way round. Fall back to classic there.
                if attacks_right:
                    rl_in_dist = robot.pos[0] < ball.pos[0] - 0.1
                else:
                    rl_in_dist = robot.pos[0] > ball.pos[0] + 0.1

                opp_team_list = gamestate.red if team_color == "blue" else gamestate.blue
                opp_min = min(
                    (distance(o.pos, ball.pos) for o in opp_team_list),
                    default=float("inf"),
                )
                contested = (
                    opp_min < CONTESTED_OPP_DIST and ball.speed < CONTESTED_BALL_SPEED
                )

                if contested:
                    # Skew the kick off the goal axis so the two attackers no
                    # longer pin the ball head-on. Whichever fires first sends
                    # the ball off its own diagonal and out of the standoff.
                    kick_angle = team_dir + CONTESTED_SKEW
                    kick_dir = np.array([math.cos(kick_angle), math.sin(kick_angle)])
                    pt = ball.pos - kick_dir * APPROACH_OFFSET
                    targets[robot.id] = (float(pt[0]), float(pt[1]), kick_angle)
                    # Fire the kicker every tick — kick.py's zone check gates it
                    # so it only takes effect once the attacker is positioned.
                    kicks.append(robot.id)
                elif rl_enabled and rl_skill is not None and rl_in_dist:
                    # RL: lock to team_dir. Policy was trained with the robot
                    # at that angle and its action frame assumes it.
                    tx, ty = rl_skill.target(robot, ball, attacks_right=attacks_right)
                    targets[robot.id] = (tx, ty, team_dir)
                    # Only RL emits an auto-kick. Classic relies on body push
                    # so the AI doesn't kick the ball out from under a human
                    # operator who's nudged near it.
                    kicks.append(robot.id)
                else:
                    # Classic: face the motion direction while navigating so the
                    # robot doesn't "shift" sideways with its back to the target.
                    # Once near the approach point, rotate to team_dir to line
                    # up the kick.
                    pt = _approach_behind_ball(ball.pos, opp_goal(team_color), team_color)
                    dx, dy = float(pt[0] - robot.pos[0]), float(pt[1] - robot.pos[1])
                    dist = math.hypot(dx, dy)
                    ta = team_dir if dist < ATTACKER_ALIGN_DIST else math.atan2(dy, dx)
                    targets[robot.id] = (float(pt[0]), float(pt[1]), ta)
            elif i == 1:  # supporter — sits past the ball toward opp goal
                goal = opp_goal(team_color)
                to_goal = (goal - ball.pos) / (np.linalg.norm(goal - ball.pos) + 1e-6)
                # Fixed +y perp so blue/red mirror across midfield instead of
                # point-mirroring through the ball (which sent red diagonally).
                perp = np.array([0.0, 1.0])
                if not kicked_off:
                    # Pre-kickoff: hold on own side so the two supporters don't
                    # cross paths and collide while the attackers set up.
                    pt = ball.pos - to_goal * SUPPORT_AHEAD + perp * SUPPORT_SIDE
                else:
                    pt = ball.pos + to_goal * SUPPORT_AHEAD + perp * SUPPORT_SIDE
                targets[robot.id] = (
                    float(pt[0]), float(pt[1]),
                    _face_point(robot.pos, ball.pos, team_dir),  # face the ball, not the target
                )
            else:  # defender — plays goalie on the goal-to-ball line
                tx, ty, ta = _goalie_target(ball.pos, team_color)
                targets[robot.id] = (tx, ty, ta)

    # Possession: only the attacker strictly closer to the ball emits a kick.
    # Two opposing kicks in the same tick produce equal-opposite impulses
    # that cancel, leaving the ball pinned between both robots.
    if len(kicks) > 1:
        rid_to_bot = {r.id: r for r in gamestate.blue + gamestate.red}
        closest = min(kicks, key=lambda rid: distance(rid_to_bot[rid].pos, ball.pos))
        kicks = [closest]

    return targets, kicks, attacker_rids


def _ball_in_opp_half_for(team_color: str, ball_x: float) -> bool:
    # Blue attacks +x (right half); red attacks -x (left half).
    if team_color == "blue":
        return ball_x > FIELD_LENGTH / 2.0
    return ball_x < FIELD_LENGTH / 2.0


def _face_point(
    pos: np.ndarray, toward: np.ndarray, fallback: float
) -> float:
    dx, dy = float(toward[0] - pos[0]), float(toward[1] - pos[1])
    if math.hypot(dx, dy) < 0.05:
        return fallback
    return math.atan2(dy, dx)


# Distances below are tuned on the 9 x 6 field and scaled by FIELD_SCALE, like
# the robot, ball and kick zone (sim/config.py), so on a custom --field they
# keep the same size relative to the robots. Speeds are not scaled: the
# physics isn't either.

# Offset, in meters, placed on our side of the ball so body pushes forward.
# Must keep the ball inside the kick zone (ROBOT_RADIUS … ROBOT_RADIUS+
# KICK_ZONE_DEPTH ≈ 0.143 … 0.263 on 9 x 6) once the attacker arrives,
# otherwise the kick never fires. 0.25 lands the ball cleanly inside that band.
APPROACH_OFFSET = 0.25 * FIELD_SCALE
# Per-team y bias on the approach point so the two attackers don't converge
# head-on at the same point and pin the ball. Must stay below
# KICK_ZONE_HALF_WIDTH (0.10) plus ARRIVAL_THRESH (~0.04) so the ball is still
# inside the kick zone once the attacker stops at the approach point.
APPROACH_LATERAL = 0.05 * FIELD_SCALE
# Within this distance of the approach point, the attacker rotates to the kick
# direction. Farther away, it faces the motion direction for natural driving.
ATTACKER_ALIGN_DIST = 0.6 * FIELD_SCALE
# Incumbent attacker keeps the role unless a teammate is at least this much
# closer to the ball. Bigger than typical per-tick noise and the supporter's
# transient pass-by distance.
ATTACKER_HYSTERESIS = 0.4 * FIELD_SCALE
# Contested-ball detection: an opposing forward this close to a stalled ball
# means we're in a head-on push stalemate. The attacker switches to a skewed
# approach to break it.
CONTESTED_OPP_DIST = 0.5 * FIELD_SCALE
CONTESTED_BALL_SPEED = 0.5  # m/s
# Supporter: this far past the ball toward the opponent's goal (behind it
# before kickoff), and this far to the side (+y).
SUPPORT_AHEAD = 1.5 * FIELD_SCALE
SUPPORT_SIDE = 1.0 * FIELD_SCALE
# Kick-angle skew applied when contested. Both teams skew by the same sign,
# so since team_dir differs by π the absolute kick directions diverge by
# 2·SKEW — the two attackers end up on different diagonals around the ball
# instead of pinning head-on along the goal-to-goal axis.
CONTESTED_SKEW = math.pi / 6


# Goalie's resting standoff in front of the goal. Bigger than the pure
# mouth-coverage value so lateral tracking has meaningful range when the
# ball is wide.
GOALIE_STANDOFF = 0.8 * FIELD_SCALE
# Once the ball is within RUSH_DIST of own goal, scale the standoff up
# toward RUSH_STANDOFF so the goalie commits forward to challenge.
GOALIE_RUSH_DIST = 2.5 * FIELD_SCALE
GOALIE_RUSH_STANDOFF = 1.6 * FIELD_SCALE
# Keep the goalie a hair inside the posts so it never wedges against a wall.
GOALIE_Y_MARGIN = 0.05 * FIELD_SCALE


def _goalie_target(
    ball_pos: np.ndarray, team_color: str
) -> tuple[float, float, float]:
    goal = our_goal(team_color)
    to_ball = ball_pos - goal
    n = float(np.linalg.norm(to_ball))
    if n < 1e-6:
        tx, ty = float(goal[0]), float(goal[1])
    else:
        if n < GOALIE_RUSH_DIST:
            t = 1.0 - n / GOALIE_RUSH_DIST
            standoff = GOALIE_STANDOFF + (GOALIE_RUSH_STANDOFF - GOALIE_STANDOFF) * t
        else:
            standoff = GOALIE_STANDOFF
        pt = goal + (to_ball / n) * standoff
        tx, ty = float(pt[0]), float(pt[1])

    ty = float(np.clip(ty, GOAL_Y_MIN + GOALIE_Y_MARGIN, GOAL_Y_MAX - GOALIE_Y_MARGIN))

    dx, dy = float(ball_pos[0] - tx), float(ball_pos[1] - ty)
    angle = math.atan2(dy, dx) if math.hypot(dx, dy) > 0.05 else (
        0.0 if team_color == "blue" else math.pi
    )
    return tx, ty, angle


def _approach_behind_ball(
    ball_pos: np.ndarray, opp_goal_pos: np.ndarray, team_color: str
) -> np.ndarray:
    to_goal = opp_goal_pos - ball_pos
    n = np.linalg.norm(to_goal)
    if n < 1e-6:
        return ball_pos
    dir_goal = to_goal / n
    # Blue from -y, red from +y so the two attackers don't pin head-on at the
    # ball when they meet. Stays inside KICK_ZONE_HALF_WIDTH so the kick still
    # fires once the robot settles.
    lateral_y = -APPROACH_LATERAL if team_color == "blue" else APPROACH_LATERAL
    return ball_pos - dir_goal * APPROACH_OFFSET + np.array([0.0, lateral_y])


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy node for the RoboCup game")
    _ = parser.add_argument(
        "--mode", choices=["tcp", "zmq"], default="zmq", help="Mode: tcp or zmq"
    )
    _ = parser.add_argument(
        "--color",
        choices=["blue", "red"],
        default="blue",
        help="Team color: blue or red",
    )
    _ = parser.add_argument(
        "--rl-checkpoint",
        type=str,
        default=DEFAULT_RL_CHECKPOINT,
        help="Path to the trained PPO kick checkpoint. Loaded lazily on first toggle-on.",
    )
    args = parser.parse_args()

    global our_color
    our_color = args.color

    backend = ZMQBackend() if args.mode == "zmq" else TCPBackend()

    print(f"[Strategy node] {args.mode} mode: started")
    print(f"[Strategy node] controlling BOTH teams (blue attacks right, red attacks left)")

    brain = Brain(args.color, rl_checkpoint=args.rl_checkpoint, log=print)

    frame = 0
    while True:
        raw = backend.receive_state()
        if raw is None:
            continue

        # Control messages from viz / the web app: RL kick, dribble, human ids.
        for msg in backend.drain_manual():
            brain.handle_control(msg)

        try:
            plan = brain.tick(raw)
            backend.send_targets({
                "targets": {
                    str(rid): {"x": tx, "y": ty, "angle": ta}
                    for rid, (tx, ty, ta) in plan.targets.items()
                },
                "attackers": sorted(plan.attackers),
            })
            for rid in plan.kicks:
                backend.send_kick(rid)
            for rid, on in plan.dribble.items():
                backend.send_dribble(rid, on)
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            continue

        frame += 1
        if frame % 300 == 0:
            gamestate = plan.gamestate
            print(
                f"[Strategy node] frame={frame}  phase={gamestate.game_phase}  "
                + f"possession={gamestate.possession}  "
                + f"rl_kick={'ON' if brain.rl_enabled else 'OFF'}  "
                + f"ball=({gamestate.ball.pos[0]:.1f}, {gamestate.ball.pos[1]:.1f})"
            )


# A camera robot the camera hasn't seen for this long is lost, and the team is
# planned without it. Same as the sim's VISION_ROBOT_COAST_S, after which the
# sim freezes the body.
LOST_AFTER_S = 0.5
# Ball faster than this (m/s) means the kickoff has been taken.
KICKOFF_BALL_SPEED = 0.1


@dataclass
class Plan:
    """What the strategy wants this frame."""

    targets: dict[int, tuple[float, float, float]]  # robot id -> (x, y, heading)
    kicks: list[int]
    dribble: dict[int, bool]  # dribbler on/off for each of our AI-driven robots
    attackers: set[int]  # both teams' attackers (viewers ring them)
    gamestate: GameState


class Brain:
    """decide() plus everything the strategy remembers between frames.

    strategy_node's main loop runs the strategy through this. `decide_fn`
    swaps the decision function.
    """

    def __init__(
        self,
        color: str = "blue",
        decide_fn: Any = None,
        rl_checkpoint: str = DEFAULT_RL_CHECKPOINT,
        log: Any = None,
    ):
        self.color = color
        self.decide_fn = decide_fn or decide
        self.rl_checkpoint = rl_checkpoint
        self.log = log or (lambda _msg: None)
        self.rl_skill: "RLKickSkill | None" = None
        self.rl_enabled = False
        self.dribble_attacker = False
        # Blue robot ids driven by human controllers; AI skips planning for them.
        # viz_node / the web app publish this at startup and on toggles.
        self.human_blue_ids: set[int] = set()
        self._reset_memory()

    def _reset_memory(self) -> None:
        self.sticky: dict[str, int] = {}
        # False from a restart (start, goal, half time) until the ball first
        # moves. Meanwhile supporters hold their own half so they don't barrel
        # into each other at midfield.
        self.kicked_off = False
        self._goal_seq: int | None = None
        self._half: int | None = None
        self._last_t: float | None = None
        self._stale_since: dict[int, float] = {}

    def handle_control(self, msg: dict[str, Any]) -> None:
        """A control message from the manual port (viz_node or the web app)."""
        if "rl_kick_enabled" in msg:
            want = bool(msg["rl_kick_enabled"])
            if want and self.rl_skill is None:
                self.rl_skill = _try_load_skill(self.rl_checkpoint)
            self.rl_enabled = want and self.rl_skill is not None
            self.log(f"[Strategy node] RL kick → {'ON' if self.rl_enabled else 'OFF'}")
        if "dribble_attacker" in msg:
            self.dribble_attacker = bool(msg["dribble_attacker"])
            self.log(f"[Strategy node] Dribble (attacker) → {'ON' if self.dribble_attacker else 'OFF'}")
        if "human_blue_ids" in msg:
            self.human_blue_ids = {int(x) for x in msg["human_blue_ids"]}
            self.log(f"[Strategy node] human_blue_ids → {sorted(self.human_blue_ids)}")

    def tick(self, raw: dict[str, Any]) -> Plan:
        """One world-state frame in, one plan out."""
        gamestate = build_game_state(raw, self.color)
        self._update_kickoff(gamestate)
        lost = self._update_lost(gamestate)

        targets, kicks, attackers = self.decide_fn(
            gamestate, self.rl_skill, self.rl_enabled, self.sticky, self.kicked_off,
            self.human_blue_ids, lost,
        )

        # Our dribblers: on for our attacker (when the toggle is on), off for
        # every other robot we drive, so stale state never leaks between
        # attacker changes. Human-driven robots keep their own dribble state
        # (their controller's button, routed through robot_node).
        ours = [
            r for r in gamestate.our_team
            if r.id not in lost and not (self.color == "blue" and r.id in self.human_blue_ids)
        ]
        our_ids = {r.id for r in ours}
        our_attacker = next((rid for rid in attackers if rid in our_ids), None)
        dribble = {rid: self.dribble_attacker and rid == our_attacker for rid in sorted(our_ids)}

        return Plan(targets, kicks, dribble, attackers, gamestate)

    def _update_kickoff(self, gamestate: GameState) -> None:
        if self._last_t is not None and gamestate.timestamp < self._last_t:
            self._reset_memory()  # sim time went back: the sim was restarted
        self._last_t = gamestate.timestamp

        # A goal or a new half puts the ball back on the centre spot.
        if (self._goal_seq is not None and gamestate.goal_seq != self._goal_seq) or (
            self._half is not None and gamestate.half != self._half
        ):
            self.kicked_off = False
            self.log("[Strategy node] restart: waiting for kickoff")
        self._goal_seq = gamestate.goal_seq
        self._half = gamestate.half

        if not self.kicked_off and gamestate.ball.speed > KICKOFF_BALL_SPEED:
            self.kicked_off = True
            self.log("[Strategy node] kickoff")
        if not self.kicked_off and gamestate.game_phase == "play":
            gamestate.game_phase = "kickoff"

    def _update_lost(self, gamestate: GameState) -> set[int]:
        """Camera robots the camera hasn't seen for LOST_AFTER_S."""
        lost: set[int] = set()
        now = gamestate.timestamp
        for r in gamestate.blue + gamestate.red:
            if r.from_camera and r.stale:
                since = self._stale_since.setdefault(r.id, now)
                if now - since >= LOST_AFTER_S:
                    lost.add(r.id)
            else:
                self._stale_since.pop(r.id, None)
        return lost


def _try_load_skill(path: str):
    if not os.path.exists(path):
        print(f"[Strategy node] RL checkpoint not found: {path}")
        return None
    try:
        from strategy.skills.rl_kick import RLKickSkill
        skill = RLKickSkill(path)
        print(f"[Strategy node] loaded RL kick from {path}")
        return skill
    except Exception as e:
        print(f"[Strategy node] failed to load RL kick: {e}")
        return None


if __name__ == "__main__":
    main()
