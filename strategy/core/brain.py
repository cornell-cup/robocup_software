"""Brain: what the strategy remembers between frames, around decide().

The sticky attacker, the kickoff (waiting for the first touch after a
restart), camera robots that are lost, each team's notes, and the RL kick /
dribbler / human-driver toggles. The strategy node and the match runner both
run the strategy through a Brain, so they behave the same.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from strategy.core.slots import Team
from strategy.core.team import decide, resolve_kick_conflict
from strategy.world.state import GameState, build_game_state

if TYPE_CHECKING:
    from strategy.skills.rl_kick import RLKickSkill

# The one checkpoint committed to git (.gitignore keeps only first_final/final.zip).
DEFAULT_RL_CHECKPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # strategy/
    "rl", "checkpoints", "first_final", "final.zip",
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
    errors: list[str] = field(default_factory=list)  # replaced slots that failed this frame


class Brain:
    """decide() plus everything the strategy remembers between frames.

    strategy_node's main loop and the match runner (eval/match.py) both run the
    strategy through this, so the two behave the same. `decide_fn` swaps the
    decision function, e.g. eval/baseline_v0.decide for the frozen baseline.
    `teams` gives a colour its own Team (slots.py) instead of the built-in one.
    """

    def __init__(
        self,
        color: str = "blue",
        decide_fn: Any = None,
        rl_checkpoint: str = DEFAULT_RL_CHECKPOINT,
        log: Any = None,
        resolve_kicks: bool = True,
        teams: dict[str, Team] | None = None,
    ):
        self.color = color
        self.decide_fn = decide_fn or decide
        self.teams: dict[str, Team] = dict(teams or {})
        if self.teams and not getattr(self.decide_fn, "takes_teams", False):
            raise ValueError("this decide function has no slots to replace (teams)")
        self._reported: set[str] = set()  # slot errors already logged
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
        self.memory: dict[str, dict[str, Any]] = {}  # each team's World.memory

    def set_team(self, color: str, team: Team | None) -> None:
        """Give a colour its own Team, or None for the built-in one; e.g.
        after a team file is saved. Errors in the new code are reported
        afresh, even ones the old code had."""
        if team is None:
            self.teams.pop(color, None)
        else:
            if not getattr(self.decide_fn, "takes_teams", False):
                raise ValueError("this decide function has no slots to replace (teams)")
            self.teams[color] = team
        self._reported.clear()

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

        errors: list[str] = []
        slots = ({"teams": self.teams, "memory": self.memory, "errors": errors}
                 if getattr(self.decide_fn, "takes_teams", False) else {})
        targets, kicks, attackers = self.decide_fn(
            gamestate, self.rl_skill, self.rl_enabled, self.sticky, self.kicked_off,
            self.human_blue_ids, lost, **slots,
        )
        for e in errors:
            if e not in self._reported:  # once each, not 60 times a second
                self._reported.add(e)
                self.log(f"[Strategy node] {e}")
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

        return Plan(targets, kicks, dribble, attackers, gamestate, errors)

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
