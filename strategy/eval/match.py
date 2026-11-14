"""Headless matches: one strategy against another, as fast as the CPU allows.

Runs the sim's physics (PymunkWorld), robot_node's controllers and one Brain
per team in a single process: no ZMQ, no window, no clock. Sides swap every
game, so neither strategy keeps the side that happens to suit it.

From the repo root:

    uv run python -m strategy.eval.match                      # current vs v0, 10 games of 120 s
    uv run python -m strategy.eval.match --a current --b current --games 4   # sanity: about even
    uv run python -m strategy.eval.match --games 40 --duration 300

Strategies are named in STRATEGIES: "current" is strategy_node.decide, "v0"
the frozen baseline (baseline_v0.py). Each game is seeded, so a rerun with
the same flags plays the same games. Set ROBOCUP_FIELD to play on another
field size, as for the sim.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, "sim")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import DT, FIELD_W, NUM_ROBOTS, TEAM_BLUE_SIZE  # noqa: E402
from pymunk_world import PymunkWorld  # noqa: E402
from robot_node import RobotController  # noqa: E402
from simulation_node import BALL_STUCK_DURATION, BALL_STUCK_THRESHOLD  # noqa: E402

from strategy import strategy_node  # noqa: E402
from strategy.eval import baseline_v0  # noqa: E402
from strategy.strategy_node import Brain, resolve_kick_conflict  # noqa: E402

STRATEGIES: dict[str, Callable[..., Any]] = {
    "current": strategy_node.decide,
    "v0": baseline_v0.decide,
}

BLUE_IDS = frozenset(range(TEAM_BLUE_SIZE))
RED_IDS = frozenset(range(TEAM_BLUE_SIZE, NUM_ROBOTS))


@dataclass
class GameResult:
    """One game, from strategy A's point of view."""

    seed: int
    a_color: str
    goals_a: int = 0
    goals_b: int = 0
    ticks: int = 0
    possession_a: int = 0  # ticks one of A's robots had the ball in front of it
    possession_b: int = 0
    attacking_a: int = 0  # ticks the ball was in B's half
    kicks_a: int = 0  # kicks that actually fired
    kicks_b: int = 0
    stuck_resets: int = 0  # ball sat still for BALL_STUCK_DURATION and was reset
    goal_times: list[float] = field(default_factory=list)

    @property
    def diff(self) -> int:
        return self.goals_a - self.goals_b


def play_game(
    decide_a: Callable[..., Any],
    decide_b: Callable[..., Any],
    a_is_blue: bool,
    seed: int,
    duration_s: float,
    stuck_reset: bool = True,
) -> GameResult:
    """Play one game of `duration_s` sim seconds."""
    a_color, b_color = ("blue", "red") if a_is_blue else ("red", "blue")
    brains = {
        a_color: Brain(a_color, decide_fn=decide_a, resolve_kicks=False),
        b_color: Brain(b_color, decide_fn=decide_b, resolve_kicks=False),
    }
    team_ids = {"blue": BLUE_IDS, "red": RED_IDS}
    result = GameResult(seed=seed, a_color=a_color)

    world = PymunkWorld(seed=seed)
    controllers = [RobotController(i) for i in range(NUM_ROBOTS)]
    score = {"blue": 0, "red": 0}
    goal_seq = 0
    ball_in_goal = False
    stuck_timer = 0.0
    stuck_anchor = (world.ball.position.x, world.ball.position.y)

    state = world.step()  # first observation; robots haven't moved yet
    n_ticks = int(round(duration_s / DT))
    for _ in range(n_ticks):
        raw = {
            "t": state["t"],
            "score": dict(score),
            "last_goal": {"seq": goal_seq} if goal_seq else None,
            "game": {
                "half": 1,
                "time_remaining": max(0.0, duration_s - state["t"]),
                "half_over": False,
            },
            "ball": state["ball"],
            "robots": state["robots"],
        }

        # Each brain drives only its own team.
        targets: dict[int, tuple[float, float, float]] = {}
        kicks: list[int] = []
        dribbling: set[int] = set()
        for color, brain in brains.items():
            plan = brain.tick(raw)
            mine = team_ids[color]
            targets.update({rid: t for rid, t in plan.targets.items() if rid in mine})
            kicks += [rid for rid in plan.kicks if rid in mine]
            dribbling |= {rid for rid, on in plan.dribble.items() if on and rid in mine}
            if color == "blue":
                gamestate = plan.gamestate  # blue's view; possession below is blue-relative
        kicks = resolve_kick_conflict(kicks, gamestate)

        # As robot_node: strategy targets in, wheel speeds out.
        for rid, (tx, ty, ta) in targets.items():
            controllers[rid].set_target(tx, ty, None, ta)
        wheels = {
            rid: controllers[rid].compute_wheels(state["robots"][str(rid)])
            for rid in range(NUM_ROBOTS)
        }

        state = world.step(wheels, kicks, dribbling)
        result.ticks += 1

        # Stats, from A's side.
        blue_has = gamestate.possession == "ours"
        red_has = gamestate.possession == "theirs"
        a_has, b_has = (blue_has, red_has) if a_is_blue else (red_has, blue_has)
        result.possession_a += a_has
        result.possession_b += b_has
        bx = state["ball"]["x"]
        in_red_half = bx > FIELD_W / 2.0
        result.attacking_a += in_red_half if a_is_blue else not in_red_half
        for rid in state["kicks"]:
            if rid in team_ids[a_color]:
                result.kicks_a += 1
            else:
                result.kicks_b += 1

        # Goals: edge-triggered, ball back to the centre, as simulation_node.
        scoring = state["scoring_team"]
        new_goal = scoring is not None and not ball_in_goal
        ball_in_goal = scoring is not None
        if new_goal:
            score[scoring] += 1
            goal_seq += 1
            world.reset_ball()
            if scoring == a_color:
                result.goals_a += 1
            else:
                result.goals_b += 1
            result.goal_times.append(round(state["t"], 1))
            stuck_timer = 0.0
            stuck_anchor = (world.ball.position.x, world.ball.position.y)

        # Ball-stuck reset, as simulation_node's (off by default there).
        if stuck_reset:
            by = state["ball"]["y"]
            if math.hypot(bx - stuck_anchor[0], by - stuck_anchor[1]) < BALL_STUCK_THRESHOLD:
                stuck_timer += DT
                if stuck_timer >= BALL_STUCK_DURATION:
                    world.reset_ball()
                    result.stuck_resets += 1
                    stuck_timer = 0.0
                    stuck_anchor = (world.ball.position.x, world.ball.position.y)
            else:
                stuck_timer = 0.0
                stuck_anchor = (bx, by)

    return result


def run_match(
    name_a: str,
    name_b: str,
    games: int,
    duration_s: float,
    seed: int,
    stuck_reset: bool = True,
    report: Callable[[GameResult], None] | None = None,
) -> list[GameResult]:
    """`games` games, A as blue in even games and red in odd ones."""
    decide_a, decide_b = STRATEGIES[name_a], STRATEGIES[name_b]
    results = []
    for g in range(games):
        r = play_game(decide_a, decide_b, a_is_blue=(g % 2 == 0), seed=seed + g,
                      duration_s=duration_s, stuck_reset=stuck_reset)
        results.append(r)
        if report:
            report(r)
    return results


def summarize(results: list[GameResult], name_a: str, name_b: str) -> str:
    n = len(results)
    diffs = [r.diff for r in results]
    mean = statistics.fmean(diffs)
    half_ci = 1.96 * statistics.stdev(diffs) / math.sqrt(n) if n > 1 else float("nan")
    ticks = sum(r.ticks for r in results)
    wins = sum(d > 0 for d in diffs)
    losses = sum(d < 0 for d in diffs)
    lines = [
        f"{name_a} vs {name_b}: {n} games",
        f"  goals        {sum(r.goals_a for r in results)} - {sum(r.goals_b for r in results)}"
        + f"   (W {wins} / D {n - wins - losses} / L {losses} for {name_a})",
        f"  goal diff    {mean:+.2f} per game, 95% CI +/-{half_ci:.2f}",
        f"  possession   {100 * sum(r.possession_a for r in results) / ticks:.0f}% - "
        + f"{100 * sum(r.possession_b for r in results) / ticks:.0f}%",
        f"  ball in {name_b}'s half {100 * sum(r.attacking_a for r in results) / ticks:.0f}% of the time",
        f"  kicks        {sum(r.kicks_a for r in results)} - {sum(r.kicks_b for r in results)}",
        f"  stuck resets {sum(r.stuck_resets for r in results)}",
    ]
    if half_ci == half_ci and abs(mean) <= half_ci:
        lines.append("  -> no clear difference; play more games to separate them")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless matches between two strategies")
    _ = parser.add_argument("--a", default="current", choices=sorted(STRATEGIES))
    _ = parser.add_argument("--b", default="v0", choices=sorted(STRATEGIES))
    _ = parser.add_argument("--games", type=int, default=10)
    _ = parser.add_argument("--duration", type=float, default=120.0, help="sim seconds per game")
    _ = parser.add_argument("--seed", type=int, default=0, help="seed of the first game")
    _ = parser.add_argument("--no-stuck-reset", action="store_true",
                            help="never reset a ball that sits still for 10 s")
    args = parser.parse_args()

    print(f"{args.a} (A) vs {args.b} (B), {args.games} x {args.duration:.0f} s, sides swap every game")
    print(" game  A is   score A-B   possession A/B   stuck")
    t0 = time.perf_counter()

    def report(r: GameResult) -> None:
        print(
            f" {r.seed:4d}  {r.a_color:5s}  {r.goals_a:3d} - {r.goals_b:<3d}"
            + f"   {100 * r.possession_a / r.ticks:5.1f}% / {100 * r.possession_b / r.ticks:4.1f}%"
            + f"   {r.stuck_resets:5d}"
        )

    results = run_match(args.a, args.b, args.games, args.duration, args.seed,
                        stuck_reset=not args.no_stuck_reset, report=report)
    wall = time.perf_counter() - t0
    sim_s = sum(r.ticks for r in results) * DT
    print()
    print(summarize(results, args.a, args.b))
    print(f"  ({sim_s:.0f} sim s in {wall:.0f} s wall, {sim_s / wall:.1f}x real time)")


if __name__ == "__main__":
    main()
