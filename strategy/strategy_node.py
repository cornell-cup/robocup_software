"""The strategy node: the team AI as a process in the sim pipeline.

Every frame it reads the world state from the sim (VISION_PORT), runs a Brain
(core/brain.py) on it, publishes each robot's target for robot_node
(STRATEGY_PORT), and sends kicks and dribbler on/off straight to the sim
(COMMAND_PORT). Toggles (RL kick, dribble, human drivers) arrive on
MANUAL_PORT from viz_node or the web app. sim/run_pipeline.py starts it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, final

# Run as a script: put the repo root on sys.path so `strategy` imports.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from strategy.core.brain import DEFAULT_RL_CHECKPOINT, Brain  # noqa: E402
from strategy.world.simconfig import COMMAND_PORT, MANUAL_PORT, STRATEGY_PORT, VISION_PORT  # noqa: E402


@final
class SimLink:
    """The node's ZMQ sockets to the sim, the web app and robot_node."""

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
            f"[Strategy node] vision←:{VISION_PORT}  "
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy node for the RoboCup game")
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

    backend = SimLink()

    print("[Strategy node] started")
    print("[Strategy node] controlling BOTH teams (blue attacks right, red attacks left)")

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
