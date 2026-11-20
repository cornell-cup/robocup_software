# receives world state, runs strategy & control the game
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, final

# Add project root so `strategy.*` resolves when run as a standalone script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VISION_PORT = 9090
STRATEGY_PORT = 9091
COMMAND_PORT = 9092
MANUAL_PORT = 9093

from strategy.brain import DEFAULT_RL_CHECKPOINT, Brain  # noqa: E402

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


if __name__ == "__main__":
    main()
