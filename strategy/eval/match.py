"""Headless matches: one strategy against another, as fast as the CPU allows.

Runs the sim's physics (PymunkWorld), robot_node's controllers and one Brain
per team in a single process: no ZMQ, no window, no clock. Sides swap every
game, so neither strategy keeps the side that happens to suit it.

From the repo root:

    uv run python -m strategy.eval.match                      # current vs v2, 10 games of 120 s
    uv run python -m strategy.eval.match --a current --b current --games 4   # sanity: about even
    uv run python -m strategy.eval.match --games 40 --duration 300
    uv run python -m strategy.eval.match --a strategy/examples/get_open.py   # a team file vs v2
    uv run python -m strategy.eval.match --a teams/anna --b teams/ben         # two workspaces

Strategies are named in STRATEGIES: "current" is core/team.py's decide;
"v2", the default opponent, the built-in team after strategy step 2 (the
attacker meeting rolling balls, baseline_v2.py); "v1" and "v0" the earlier
frozen baselines (baseline_v1.py, the team earlier on 2026-11-20;
baseline_v0.py, before the attacker pushed the ball). A
side can also be a team, a workspace folder or a .py file (core/loader.py):
the built-in team with the slots it defines swapped in. A team runs in its
own process (core/worker.py, in lockstep: every frame waits for its answer),
so one that loops or crashes can't stop the match, two teams never share
anything, and one from someone else plays in safe mode. Each game is seeded,
so a rerun with the same flags plays the same games. Set ROBOCUP_FIELD to play on another
field size, as for the sim.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from strategy.core.brain import Brain
from strategy.core.loader import ENTRY, TeamFileError
from strategy.core.slots import Team
from strategy.core.team import decide
from strategy.core.worker import TeamWorker
from strategy.eval import baseline_v0, baseline_v1, baseline_v2
from strategy.world.simconfig import DT, FIELD_W, NUM_ROBOTS, TEAM_BLUE_SIZE

# isort: split
# sim/ modules: importable once strategy.world.simconfig (above) puts sim/ on sys.path
from pymunk_world import PymunkWorld, scorer
from robot_node import RobotController
from simulation_node import BALL_STUCK_DURATION, BALL_STUCK_THRESHOLD

STRATEGIES: dict[str, Callable[..., Any]] = {
    "current": decide,
    "v2": baseline_v2.decide,
    "v1": baseline_v1.decide,
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
    # Replaced slots that failed (each once): those robots fell back to the built-in slot.
    errors_a: list[str] = field(default_factory=list)
    errors_b: list[str] = field(default_factory=list)

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
    team_a: Team | TeamWorker | None = None,
    team_b: Team | TeamWorker | None = None,
) -> GameResult:
    """Play one game of `duration_s` sim seconds. `team_a` / `team_b` give a
    side its own slots (core/slots.py) instead of the built-in ones: a Team
    played in this process (your own code, from Python), or a TeamWorker
    (a team in its own process, in lockstep; run_match makes these)."""
    a_color, b_color = ("blue", "red") if a_is_blue else ("red", "blue")
    brains = {}
    for color, decide_fn, team in ((a_color, decide_a, team_a), (b_color, decide_b, team_b)):
        brains[color] = Brain(color, decide_fn=decide_fn,
                              teams={color: team} if isinstance(team, Team) else None)
        if isinstance(team, TeamWorker):
            team.color = color
            brains[color].workers[color] = team  # not set_worker(): the match closes it
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
    n_ticks = round(duration_s / DT)
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
        max_speed: dict[int, float] = {}
        kicks: list[int] = []
        dribbling: set[int] = set()
        for color, brain in brains.items():
            plan = brain.tick(raw)
            seen = result.errors_a if color == a_color else result.errors_b
            seen += [e for e in plan.errors if e not in seen]
            mine = team_ids[color]
            targets.update({rid: t for rid, t in plan.targets.items() if rid in mine})
            max_speed.update({rid: s for rid, s in plan.max_speed.items() if rid in mine})
            kicks += [rid for rid in plan.kicks if rid in mine]
            dribbling |= {rid for rid, on in plan.dribble.items() if on and rid in mine}
            if color == "blue":
                gamestate = plan.gamestate  # blue's view; possession below is blue-relative

        # As robot_node: strategy targets in, wheel speeds out.
        for rid, (tx, ty, ta) in targets.items():
            controllers[rid].set_target(tx, ty, None, ta, max_speed.get(rid))
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
        # state["scoring_team"] is whose goal the ball is in; the other team scored.
        goal = state["scoring_team"]
        scoring = scorer(goal) if goal is not None else None
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


def resolve(side: str) -> tuple[Callable[..., Any], Path | None, str]:
    """A side given on the command line: a name in STRATEGIES, or a team (a
    workspace folder or a .py file). Returns (decide function, the team's
    path or None for a built-in strategy, a short label)."""
    if side in STRATEGIES:
        return STRATEGIES[side], None, side
    path = Path(side)
    if path.is_dir():
        if not (path / ENTRY).is_file():
            raise TeamFileError(f"{path.name} has no {ENTRY}: a workspace's team starts there")
        return decide, path, path.name
    if side.endswith(".py"):
        if not path.is_file():
            raise TeamFileError(f"no team file at {side}")
        return decide, path, path.name
    raise ValueError(f"{side!r} is neither a strategy ({', '.join(STRATEGIES)}) "
                     + "nor a team (a workspace folder or a .py file)")


def run_match(
    name_a: str,
    name_b: str,
    games: int,
    duration_s: float,
    seed: int,
    stuck_reset: bool = True,
    report: Callable[[GameResult], None] | None = None,
) -> list[GameResult]:
    """`games` games, A as blue in even games and red in odd ones. A side is
    a name in STRATEGIES or a team's path (each team in its own process for
    the whole match). What a team's process reports (it doesn't load, it
    hung) counts with that side's errors."""
    (decide_a, path_a, _), (decide_b, path_b, _) = resolve(name_a), resolve(name_b)
    notes: dict[str, list[str]] = {"a": [], "b": []}

    def worker(path: Path | None, side: str) -> TeamWorker | None:
        if path is None:
            return None

        def log(line: str) -> None:
            line = line.removeprefix("[Strategy node] ")
            if not line.startswith(("blue plays ", "red plays ")):  # not the "what it plays" note
                notes[side].append(line)
        return TeamWorker(path, "blue", log=log, lockstep=True)

    team_a, team_b = worker(path_a, "a"), worker(path_b, "b")
    results = []
    try:
        for g in range(games):
            r = play_game(decide_a, decide_b, a_is_blue=(g % 2 == 0), seed=seed + g,
                          duration_s=duration_s, stuck_reset=stuck_reset, team_a=team_a, team_b=team_b)
            for side, errs in (("a", r.errors_a), ("b", r.errors_b)):
                errs += [n for n in notes[side] if n not in errs]
                notes[side].clear()
            results.append(r)
            if report:
                report(r)
    finally:
        for w in (team_a, team_b):
            if w is not None:
                w.close()
    return results


def summary(results: list[GameResult], name_a: str, name_b: str) -> dict[str, Any]:
    """The match in numbers, from A's side (what --json prints)."""
    n = len(results)
    diffs = [r.diff for r in results]
    ticks = sum(r.ticks for r in results) or 1
    half_ci = 1.96 * statistics.stdev(diffs) / math.sqrt(n) if n > 1 else None
    errors = {}
    for side, attr in (("a", "errors_a"), ("b", "errors_b")):
        errs = [e.split(" ", 1)[1] for r in results for e in getattr(r, attr)]  # drop the colour
        errors[side] = list(dict.fromkeys(errs))
    return {
        "a": name_a, "b": name_b, "games": n,
        "goals": [sum(r.goals_a for r in results), sum(r.goals_b for r in results)],
        "wins": sum(d > 0 for d in diffs), "draws": sum(d == 0 for d in diffs),
        "losses": sum(d < 0 for d in diffs),
        "diff_per_game": statistics.fmean(diffs) if n else 0.0, "diff_ci95": half_ci,
        "possession": [sum(r.possession_a for r in results) / ticks, sum(r.possession_b for r in results) / ticks],
        "ball_in_b_half": sum(r.attacking_a for r in results) / ticks,
        "errors": errors,
        "per_game": [{"seed": r.seed, "a_color": r.a_color, "goals": [r.goals_a, r.goals_b]} for r in results],
    }


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
    sides = f"{', '.join(STRATEGIES)}, or a team (a workspace folder or a .py file)"
    _ = parser.add_argument("--a", default="current", help=f"side A: {sides} (default current)")
    _ = parser.add_argument("--b", default="v2", help=f"side B: {sides} (default v2)")
    _ = parser.add_argument("--games", type=int, default=10)
    _ = parser.add_argument("--duration", type=float, default=120.0, help="sim seconds per game")
    _ = parser.add_argument("--seed", type=int, default=0, help="seed of the first game")
    _ = parser.add_argument("--no-stuck-reset", action="store_true",
                            help="never reset a ball that sits still for 10 s")
    _ = parser.add_argument("--json", action="store_true",
                            help="also print the result as one line: RESULT {json} (for the web app)")
    args = parser.parse_args()
    try:
        label_a, label_b = resolve(args.a)[2], resolve(args.b)[2]
    except (TeamFileError, ValueError) as e:
        sys.exit(f"error: {e}")

    print(f"{label_a} (A) vs {label_b} (B), {args.games} x {args.duration:.0f} s, sides swap every game")
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
    print(summarize(results, label_a, label_b))
    print(f"  ({sim_s:.0f} sim s in {wall:.0f} s wall, {sim_s / wall:.1f}x real time)")
    for label, errs in ((label_a, [e for r in results for e in r.errors_a]),
                        (label_b, [e for r in results for e in r.errors_b])):
        unique = list(dict.fromkeys(e.split(" ", 1)[1] for e in errs))  # drop the team colour
        if unique:
            print()
            print(f"Errors in {label}'s slots (those robots used the built-in slot instead):")
            for e in unique[:10]:
                print(f"  {e}")
            if len(unique) > 10:
                print(f"  ... and {len(unique) - 10} more")

    if args.json:
        print("RESULT " + json.dumps(summary(results, label_a, label_b)))

if __name__ == "__main__":
    main()
