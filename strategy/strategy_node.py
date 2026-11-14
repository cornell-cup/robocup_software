# receives world state, runs strategy & control the game
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

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

from strategy.geometry import distance  # noqa: E402
from strategy.state import GameState, build_game_state  # noqa: E402
from strategy.tactics.attacker import attack  # noqa: E402
from strategy.tactics.defender import defend_goal  # noqa: E402
from strategy.tactics.role_assignments import (  # noqa: E402
    ATTACKER,
    SUPPORTER,
    assign_roles,
)
from strategy.tactics.supporter import support  # noqa: E402

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
    """One frame of the team AI, for both teams: who plays which role, and
    where each robot goes. Returns (targets, kicks, attackers): robot id ->
    (x, y, heading), robots firing their kicker, and each team's attacker.

    Roles come from tactics/role_assignments.py and each role's behaviour from
    tactics/attacker.py, supporter.py, defender.py. `sticky_attackers` (each
    team's attacker last frame) is updated in place.
    """
    targets: dict[int, tuple[float, float, float]] = {}
    kicks: list[int] = []
    attacker_rids: set[int] = set()
    sticky = sticky_attackers if sticky_attackers is not None else {}
    human_ids = human_blue_ids or set()
    lost = lost_ids or set()

    for team_color, team in (("blue", gamestate.blue), ("red", gamestate.red)):
        # Drop the robots the AI can't drive: human-driven ones (blue only)
        # and camera robots the camera has lost. The rest of the team gets
        # whichever roles fit its size (1: goalie, 2: attacker + supporter,
        # 3: + defender).
        skip = (human_ids if team_color == "blue" else set()) | lost
        if skip:
            team = [r for r in team if r.id not in skip]
            if not team:
                sticky.pop(team_color, None)
                continue

        for robot, role in assign_roles(team, gamestate.ball.pos, sticky, team_color):
            if role == ATTACKER:
                attacker_rids.add(robot.id)
                command = attack(robot, gamestate, team_color, rl_skill, rl_enabled)
            elif role == SUPPORTER:
                command = support(robot, gamestate.ball, team_color, kicked_off)
            else:
                command = defend_goal(gamestate.ball.pos, team_color)
            targets[robot.id] = command.target
            if command.kick:
                kicks.append(robot.id)

    # Both teams' kicks. When both attackers kick in the same tick, Brain keeps
    # only the closer one (resolve_kick_conflict).
    return targets, kicks, attacker_rids


def resolve_kick_conflict(kicks: list[int], gamestate: GameState) -> list[int]:
    """Only the kicker closest to the ball kicks. Two opposing kicks in the
    same tick produce equal-opposite impulses that cancel, leaving the ball
    pinned between both robots."""
    if len(kicks) <= 1:
        return list(kicks)
    rid_to_bot = {r.id: r for r in gamestate.blue + gamestate.red}
    return [min(kicks, key=lambda rid: distance(rid_to_bot[rid].pos, gamestate.ball.pos))]


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

    strategy_node's main loop and the match runner (eval/match.py) both run the
    strategy through this, so the two behave the same. `decide_fn` swaps the
    decision function, e.g. eval/baseline_v0.decide for the frozen baseline.
    """

    def __init__(
        self,
        color: str = "blue",
        decide_fn: Any = None,
        rl_checkpoint: str = DEFAULT_RL_CHECKPOINT,
        log: Any = None,
        resolve_kicks: bool = True,
    ):
        self.color = color
        self.decide_fn = decide_fn or decide
        # False when something else settles kicks between two brains (the
        # match runner runs one brain per team).
        self.resolve_kicks = resolve_kicks
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
        if self.resolve_kicks:
            kicks = resolve_kick_conflict(kicks, gamestate)

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
