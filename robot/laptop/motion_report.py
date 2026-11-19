"""A robot's motion profile: what a session of motion tests says about it.

    report = build_report(records, robot="XRP-01", ...)

`records` are the runs as RunRecord.to_dict() gives them (what the web app
sends and saves), so a saved session can be reported on again. The report
grades each ability -- driving straight, strafing, diagonals, turning,
responsiveness, consistency -- good, fair or poor against the thresholds
below, lists what stood out in plain words, and averages each test at each
speed.

The thresholds are judgement calls for a small omni-wheel robot on a table,
not standards: change them here if they grade too hard or too soft.

Spins also measure where the tag sits on the robot (spin_fit()): the tag
circles the robot's turning centre, so a tag that isn't on that centre traces a
circle even when the robot spins perfectly in place. The fit separates the two:
the offset is reported (to enter as the robot's tag position), and only what's
left -- real wobble -- is graded.

hardware() pulls out what the robot does at full command -- top speed,
acceleration, how quickly it slows, turn rate -- the numbers the simulator's
drive model (sim/config.py) is built from. Given the same numbers for the
simulated robot (measured the same way), the report sets the two side by side
and suggests config values that would bring the sim closer.
"""

import math
import statistics

from .motion_profile import TESTS_BY_NAME

VERSION = 1

# (good below, fair below); at or above the second is poor.
#: Sideways drift per metre driven, as a fraction (0.02 = 20 mm per metre).
DRIFT_PER_M = (0.02, 0.05)
#: Heading change per metre driven, degrees.
TURN_PER_M_DEG = (3.0, 8.0)
#: Strafing speed as a fraction of forward speed: good above, fair above.
STRAFE_SPEED = (0.85, 0.65)
#: Difference between opposite directions' speeds (forward/backward,
#: left/right, clockwise/counter-clockwise), as a fraction of the faster.
ASYMMETRY = (0.10, 0.25)
#: How far a spin's centre wanders, metres.
WANDER_M = (0.02, 0.05)
#: Command to the first centimetre, seconds.
REACTION_S = (0.15, 0.30)
#: From moving to 90% of full speed, seconds.
RISE_S = (0.30, 0.60)
#: Spread (standard deviation) of where repeats end up sideways, metres.
SPREAD_M = (0.010, 0.025)
#: Speed per unit of command differing more than this between the lowest
#: and highest speed: not proportional.
NONLINEAR = 0.25
#: Coasting further than this after a stop at the top speed is worth saying.
COAST_M = 0.05
#: A tag this far from where the robot's size puts it is worth saying.
TAG_OFFSET_M = 0.01

GRADES = ("good", "fair", "poor")
#: hardware() measures at this command: full.
FULL = 1.0

STRAIGHT = ("forward", "backward")
STRAFES = ("strafe_left", "strafe_right")
DIAGONALS = ("diagonal_left", "diagonal_right")
SPINS = ("spin_ccw", "spin_cw")
#: How each straight move reads in a sentence: "veers left when ...".
DOING = {
    "forward": "driving forward", "backward": "driving backward",
    "strafe_left": "strafing left", "strafe_right": "strafing right",
    "diagonal_left": "driving diagonally forward-left",
    "diagonal_right": "driving diagonally forward-right",
}


def build_report(records, robot, tag_id=None, fake=False, started=None,
                 finished=None, state=None, speeds=(), tests=(), repeats=1,
                 sim=None, sim_config=None, body=None):
    """The profile for one session. Everything in it is plain JSON. `sim`:
    hardware() of the simulated robot, to compare against; `sim_config`: the
    sim/config.py values its suggestions start from; `body`: the robot's shape
    the runs were measured with (Body.to_dict())."""
    runs = [r for r in records if r.get("result") and r["result"].get("moved")]
    report = {
        "version": VERSION,
        "robot": robot,
        "tag_id": tag_id,
        "fake": fake,
        "started": started,
        "finished": finished,
        "state": state,
        "speeds": list(speeds),
        "tests": list(tests),
        "repeats": repeats,
        "body": body,
        "tag_offset": tag_offset(records, body),
        "abilities": [],
        "findings": [],
        "by_test": _by_test(records),
        "hardware": hardware(records),
        "vs_sim": None,
        "records": list(records),
    }
    if sim:
        report["vs_sim"] = compare_with_sim(report["hardware"], sim, sim_config or {})
    report["abilities"] = [
        _straight("straight", "Driving straight", _of(runs, STRAIGHT)),
        _strafing(runs),
        _straight("diagonal", "Diagonals", _of(runs, DIAGONALS)),
        _turning(runs, report["tag_offset"]),
        _responsiveness(runs),
        _consistency(runs),
    ]
    report["findings"] = _findings(records, runs)
    off = report["tag_offset"]
    if off and off["off_by_m"] >= TAG_OFFSET_M:
        if off["known"]:
            # The tag is where it was measured to be: the robot itself turns
            # about a point that far from its centre.
            report["findings"].insert(0, (
                "Turns about a point {} its centre, not on the spot: one side's wheels may be "
                "weaker").format(_where(-off["off_forward_m"], -off["off_left_m"])))
        else:
            report["findings"].insert(0, (
                "Spinning, the tag circles a point {} it. Either the tag sits that far from the "
                "robot's centre, or the robot turns about a point off its centre: measure where the "
                "tag is and enter it to tell which").format(_where(-off["forward_m"], -off["left_m"])))
    return report


# -- abilities --------------------------------------------------------------------


def _ability(key, label, grade, value, detail=""):
    return {"key": key, "label": label, "grade": grade, "value": value, "detail": detail}


def _none(key, label, why):
    return _ability(key, label, None, "–", why)


def _straight(key, label, runs, extra=None):
    """Drift and heading creep over straight-line runs, as a grade."""
    if not runs:
        return _none(key, label, "Not tested")
    drift = _mean([abs(r["result"]["drift_per_m"]) for r in runs if r["result"]["drift_per_m"] is not None])
    turn = _mean([abs(_turn_per_m(r)) for r in runs if _turn_per_m(r) is not None])
    grades = [_grade(drift, DRIFT_PER_M), _grade(turn, TURN_PER_M_DEG)] + (extra or [])
    value = "{} mm/m drift, {}°/m turn".format(_n(drift, 1000), _n(turn, 1, 1))
    detail = "Averaged over {} run{}".format(len(runs), "" if len(runs) == 1 else "s")
    return _ability(key, label, _worst(grades), value, detail)


def _strafing(runs):
    strafes = _of(runs, STRAFES)
    ratio = _ratio(_mean_rate(strafes), _mean_rate(_of(runs, ("forward",))))
    ability = _straight("strafing", "Strafing", strafes,
                        extra=[_grade_above(ratio, STRAFE_SPEED)] if ratio is not None else [])
    if strafes and ratio is not None:
        ability["value"] += ", {}% of forward speed".format(_n(ratio, 100))
    return ability


def _turning(runs, off=None):
    ccw, cw = _of(runs, ("spin_ccw",)), _of(runs, ("spin_cw",))
    if not ccw and not cw:
        return _none("turning", "Turning", "Not tested")
    # With the tag's position measured, turning about a point off the centre
    # counts against it too; without, it can't be told from the tag's offset.
    pivot = off["off_by_m"] if off and off["known"] else 0.0
    wander = _mean([_wobble(r) for r in ccw + cw])
    wander = None if wander is None else wander + pivot
    asym = _asymmetry(_mean_rate(ccw), _mean_rate(cw))
    grades = [_grade(wander, WANDER_M)]
    if asym is not None:
        grades.append(_grade(asym, ASYMMETRY))
    parts = []
    if ccw:
        parts.append("{}°/s ccw".format(_n(_mean_rate(ccw), 1)))
    if cw:
        parts.append("{}°/s cw".format(_n(_mean_rate(cw), 1)))
    parts.append("{} mm wobble".format(_n(wander, 1000)))
    if off and not off["known"]:
        parts.append("tag position not measured")
    return _ability("turning", "Turning", _worst(grades), ", ".join(parts),
                    "At {}".format(_speeds_text(ccw + cw)))


def _responsiveness(runs):
    if not runs:
        return _none("response", "Responsiveness", "No runs moved")
    reaction = _mean([r["result"]["reaction_s"] for r in runs if r["result"]["reaction_s"] is not None])
    rise = _mean([r["result"]["rise_s"] for r in runs if r["result"]["rise_s"] is not None])
    value = "{} ms to move, {} ms to full speed".format(_n(reaction, 1000), _n(rise, 1000))
    return _ability("response", "Responsiveness",
                    _worst([_grade(reaction, REACTION_S), _grade(rise, RISE_S)]), value,
                    "Command to the first centimetre, then to 90% of full speed")


def _consistency(runs):
    spreads = []
    for (test, _speed), group in _groups(runs).items():
        if test in SPINS or len(group) < 2:
            continue
        spreads.append(statistics.pstdev(r["result"]["drift_m"] for r in group))
    if not spreads:
        return _none("consistency", "Consistency", "Run 2 or more repeats to measure it")
    spread = _mean(spreads)
    return _ability("consistency", "Consistency", _grade(spread, SPREAD_M),
                    "{} mm spread".format(_n(spread, 1000, 1)),
                    "How far apart repeats of the same move end up sideways")


# -- findings ---------------------------------------------------------------------


def _findings(records, runs):
    out = []

    # Drift and turning, per direction, over every speed. Veering the same
    # way in every direction is one finding: one cause (a heading offset, a
    # wheel) turns every move.
    veers, turns = [], []
    for name in STRAIGHT + STRAFES + DIAGONALS:
        group = _of(runs, (name,))
        drift = _mean([r["result"]["drift_per_m"] for r in group if r["result"]["drift_per_m"] is not None])
        turn = _mean([_turn_per_m(r) for r in group if _turn_per_m(r) is not None])
        if drift is not None:
            veers.append((name, drift))
        if turn is not None and abs(turn) >= TURN_PER_M_DEG[0]:
            turns.append("Turns {} while {} ({}° per metre)".format(
                "left" if turn > 0 else "right", DOING[name], _n(abs(turn), 1, 1)))
    big = [(n, d) for n, d in veers if abs(d) >= DRIFT_PER_M[0]]
    same_way = len({d > 0 for _, d in big}) == 1
    if len(big) >= 3 and len(big) == len(veers) and same_way:
        sizes = sorted(abs(d) for _, d in big)
        out.append("Veers {} of its line in every direction ({}–{} mm per metre): something "
                   "turns every move, like a heading offset or a wheel".format(
                       "left" if big[0][1] > 0 else "right", _n(sizes[0], 1000), _n(sizes[-1], 1000)))
    else:
        for name, drift in big:
            out.append("Veers {} of its line when {} ({} mm per metre)".format(
                "left" if drift > 0 else "right", DOING[name], _n(abs(drift), 1000)))
    out.extend(turns)

    # Speeds that don't match.
    fwd = _mean_rate(_of(runs, ("forward",)))
    strafe = _mean_rate(_of(runs, STRAFES))
    ratio = _ratio(strafe, fwd)
    if ratio is not None and ratio < STRAFE_SPEED[0]:
        out.append("Strafes {}% slower than it drives forward".format(_n(1 - ratio, 100)))
    for a, b, what in (("forward", "backward", "Drives backward"),
                       ("strafe_left", "strafe_right", "Strafes right"),
                       ("spin_ccw", "spin_cw", "Turns clockwise")):
        ra, rb = _mean_rate(_of(runs, (a,))), _mean_rate(_of(runs, (b,)))
        asym = _asymmetry(ra, rb)
        if asym is not None and asym >= ASYMMETRY[0]:
            out.append("{} {}% {} than {}".format(
                what, _n(asym, 100), "slower" if rb < ra else "faster",
                {"forward": "forward", "strafe_left": "left", "spin_ccw": "counter-clockwise"}[a]))

    # Spins that wander, once the tag's offset is taken out.
    wander = max([_wobble(r) or 0.0 for r in _of(runs, SPINS)], default=0.0)
    if wander >= WANDER_M[0]:
        out.append("Wobbles when spinning: its centre moves up to {} mm".format(_n(wander, 1000)))

    # Speed not proportional to the command.
    by_speed = {}
    for r in _of(runs, ("forward",)):
        if r["result"]["rate"]:
            by_speed.setdefault(r["speed"], []).append(r["result"]["rate"] / r["speed"])
    if len(by_speed) >= 2:
        lo, hi = min(by_speed), max(by_speed)
        per_lo, per_hi = _mean(by_speed[lo]), _mean(by_speed[hi])
        if abs(per_lo - per_hi) / max(per_lo, per_hi) > NONLINEAR:
            out.append("Speed isn't proportional to the command: {} m/s at {:g}, {} m/s at {:g}".format(
                _n(per_lo * lo, 1, 2), lo, _n(per_hi * hi, 1, 2), hi))

    # Commands too small to move it.
    stuck = {}
    for (test, speed), group in _groups(records).items():
        if group and not any(r.get("result") and r["result"].get("moved") for r in group) \
                and any(r.get("ended") == "timeout" for r in group):
            stuck.setdefault(speed, []).append(TESTS_BY_NAME[test].label.lower())
    for speed in sorted(stuck):
        out.append("Doesn't move at {:g} ({}): commands that small may be in a dead band".format(
            speed, ", ".join(stuck[speed])))

    # Coasting at the top speed.
    if runs:
        top = max(r["speed"] for r in runs)
        coast = _mean([r["result"]["coast_m"] for r in runs
                       if r["speed"] == top and r["result"]["coast_m"] is not None])
        if coast is not None and coast >= COAST_M:
            out.append("Coasts {} mm after a stop at {:g}".format(_n(coast, 1000), top))

    reaction = _mean([r["result"]["reaction_s"] for r in runs if r["result"]["reaction_s"] is not None])
    if reaction is not None and reaction >= REACTION_S[1]:
        out.append("Slow to react: {} ms from command to the first centimetre".format(_n(reaction, 1000)))

    # Runs that didn't finish.
    ends = {}
    for r in records:
        if r.get("ended") not in (None, "reached", "aborted"):
            ends[r["ended"]] = ends.get(r["ended"], 0) + 1
    words = {"tag_lost": "the camera lost the tag", "edge": "stopped at the field's edge",
             "timeout": "timed out", "no_room": "skipped: no room on the field"}
    for end, n in sorted(ends.items()):
        out.append("{} run{} {}".format(n, "" if n == 1 else "s", words.get(end, end)))
    return out


# -- where the tag sits ------------------------------------------------------------


def spin_fit(path):
    """Fit one spin's path ([t, x, y, heading deg] points) as a fixed turning
    centre with the tag at a fixed offset from it: x = cx + cos(h) f - sin(h) l,
    y = cy + sin(h) f + cos(h) l. Returns (cx, cy, f, l, wobble): the offset
    in the robot's frame, and the furthest any point is from the fit -- how
    much the centre really moved. None if the spin is too short to tell."""
    if len(path) < 6:
        return None
    hs = [math.radians(p[3]) for p in path]
    if max(hs) - min(hs) < math.radians(60) and _turned(hs) < math.radians(60):
        return None
    rows, rhs = [], []
    for (_, x, y, _), h in zip(path, hs):
        c, s = math.cos(h), math.sin(h)
        rows += [[1.0, 0.0, c, -s], [0.0, 1.0, s, c]]
        rhs += [x, y]
    sol = _least_squares(rows, rhs)
    if sol is None:
        return None
    cx, cy, f, l = sol
    wobble = max(math.hypot(x - (cx + math.cos(h) * f - math.sin(h) * l),
                            y - (cy + math.sin(h) * f + math.cos(h) * l))
                 for (_, x, y, _), h in zip(path, hs))
    return cx, cy, f, l, wobble


def tag_offset(records, body=None):
    """Where the spins put the tag: the offset the runs still show (paths are
    already moved by the body's tag position), added to it, so `forward_m`,
    `left_m` is the whole offset to enter. None without a spin to measure."""
    fits = [spin_fit(r["path"]) for r in records if r["test"] in SPINS and r.get("path")]
    fits = [f for f in fits if f is not None]
    if not fits:
        return None
    df = statistics.fmean(f[2] for f in fits)
    dl = statistics.fmean(f[3] for f in fits)
    body = body or {}
    return {
        "forward_m": body.get("tag_forward_m", 0.0) + df,
        "left_m": body.get("tag_left_m", 0.0) + dl,
        # what the spins show beyond the tag position the runs were measured with
        "off_forward_m": df,
        "off_left_m": dl,
        "off_by_m": math.hypot(df, dl),
        # whether that tag position was measured: if so, what's left is the robot
        "known": bool(body.get("tag_known")),
        "runs": len(fits),
    }


def _where(forward, left):
    """'12 mm forward and 4 mm right from', for a sentence ending in what
    it's measured from."""
    parts = []
    if abs(forward) >= 0.001:
        parts.append("{} mm {}".format(_n(abs(forward), 1000), "forward" if forward > 0 else "back"))
    if abs(left) >= 0.001:
        parts.append("{} mm {}".format(_n(abs(left), 1000), "left" if left > 0 else "right"))
    return " and ".join(parts) + " from" if parts else "at"


def _wobble(run):
    """How far a spin's centre really moved: its fit's leftover, or the raw
    furthest-from-start if it can't be fitted."""
    fit = spin_fit(run.get("path") or [])
    return fit[4] if fit else run["result"]["max_deviation_m"]


def _turned(hs):
    total = 0.0
    for a, b in zip(hs, hs[1:]):
        total += abs((b - a + math.pi) % (2 * math.pi) - math.pi)
    return total


def _least_squares(rows, rhs):
    """Solve rows . x = rhs in the least-squares sense (normal equations,
    Gaussian elimination). None if they don't pin x down."""
    n = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) for j in range(n)] + [sum(r[i] * v for r, v in zip(rows, rhs))]
         for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda i: abs(a[i][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        for i in range(n):
            if i != col:
                k = a[i][col] / a[col][col]
                a[i] = [x - k * y for x, y in zip(a[i], a[col])]
    return [a[i][n] / a[i][i] for i in range(n)]


# -- the robot against the simulator ---------------------------------------------


def hardware(records):
    """What it does at full command (FULL), averaged over the runs that
    measured each: speeds in m/s, turning in deg/s, damping per second (how
    quickly it slows on its own: speed over stopping distance, the sim's
    LINEAR_DAMP). None for anything not measured; camera_latency_ms from every
    run. A 0.5 m run can end before the robot levels off, so top_speed is the
    speed it reached, not necessarily its limit: compare it with another
    robot measured the same way."""
    runs = [r for r in records if r.get("result") and r["result"].get("moved")
            and r["speed"] == FULL]
    straight = _of(runs, STRAIGHT)
    spins = _of(runs, SPINS)
    top = _mean_rate(straight)
    stop = _mean([r["result"]["coast_m"] for r in straight])
    lat = _mean([r.get("camera_latency_s") for r in records])
    return {
        "top_speed": top,
        "accel": _mean([r["result"]["accel"] for r in straight]),
        "stop_m": stop,
        "damping": top / stop if top and stop and stop > 0.005 else None,
        "strafe_speed": _mean_rate(_of(runs, STRAFES)),
        "turn_rate": _mean_rate(spins),
        "turn_accel": _mean([r["result"]["accel"] for r in spins]),
        "camera_latency_ms": None if lat is None else lat * 1000.0,
    }


#: The rows of the comparison: key, label, unit, digits.
VS_SIM = (
    ("top_speed", "Speed over 0.5 m", "m/s", 2),
    ("accel", "Speeding up", "m/s²", 1),
    ("damping", "Slowing down", "/s", 1),
    ("strafe_speed", "Strafe speed", "m/s", 2),
    ("turn_rate", "Turn rate", "°/s", 0),
    ("turn_accel", "Turn speeding up", "°/s²", 0),
)


def compare_with_sim(real, sim, config):
    """Real against simulated, row by row, with what to change in
    sim/config.py. In the sim's model a robot pushes with MOTOR_MAX_FORCE and
    is slowed by LINEAR_DAMP (ANGULAR_DAMP turning), so: acceleration scales
    with MOTOR_MAX_FORCE, slowing down is LINEAR_DAMP, and the top turn rate
    is the turn acceleration over ANGULAR_DAMP."""
    rows = []
    for key, label, unit, digits in VS_SIM:
        r, m = real.get(key), sim.get(key)
        rows.append({"key": key, "label": label, "unit": unit, "digits": digits,
                     "real": r, "sim": m, "ratio": r / m if r is not None and m else None,
                     "suggest": None})
    by = {row["key"]: row for row in rows}

    force = config.get("MOTOR_MAX_FORCE")
    scale = by["accel"]["ratio"]
    if force and scale:
        by["accel"]["suggest"] = "MOTOR_MAX_FORCE ≈ {:.2f} (now {:.2f})".format(force * scale, force)
    damp = config.get("LINEAR_DAMP")
    if damp and real.get("damping"):
        by["damping"]["suggest"] = "LINEAR_DAMP ≈ {:.1f} (now {:g})".format(real["damping"], damp)
    ang = config.get("ANGULAR_DAMP")
    if ang and real.get("turn_rate") and sim.get("turn_rate") and scale:
        # With the new force the sim's turn acceleration scales by `scale`;
        # ANGULAR_DAMP then sets the top rate.
        new = ang * sim["turn_rate"] * scale / real["turn_rate"]
        by["turn_rate"]["suggest"] = "ANGULAR_DAMP ≈ {:.1f} (now {:g})".format(new, ang)
    return rows


# -- per test ---------------------------------------------------------------------


def _by_test(records):
    out = []
    for (test, speed), group in _groups(records).items():
        moved = [r for r in group if r.get("result") and r["result"].get("moved")]

        def avg(field, rs=moved):
            return _mean([r["result"][field] for r in rs if r["result"][field] is not None])

        def sd(field, rs=moved):
            values = [r["result"][field] for r in rs if r["result"][field] is not None]
            return statistics.pstdev(values) if len(values) >= 2 else None

        out.append({
            "test": test,
            "label": TESTS_BY_NAME[test].label,
            "speed": speed,
            "runs": len(group),
            "reached": sum(1 for r in group if r.get("ended") == "reached"),
            "moved": len(moved),
            "drift_m": avg("drift_m"),
            "drift_sd_m": sd("drift_m"),
            "drift_per_m": avg("drift_per_m"),
            "heading_change_deg": avg("heading_change_deg"),
            "rate": avg("rate"),
            "rate_sd": sd("rate"),
            "reaction_s": avg("reaction_s"),
            "rise_s": avg("rise_s"),
            "max_deviation_m": avg("max_deviation_m"),
            "coast_m": avg("coast_m"),
        })
    return out


# -- helpers ----------------------------------------------------------------------


def _of(runs, names):
    return [r for r in runs if r["test"] in names]


def _groups(records):
    """(test, speed) -> runs, in the order they first ran."""
    groups = {}
    for r in records:
        if r["test"] in TESTS_BY_NAME:
            groups.setdefault((r["test"], r["speed"]), []).append(r)
    return groups


def _turn_per_m(run):
    res = run["result"]
    if res["heading_change_deg"] is None or not res["along_m"] or res["along_m"] <= 0.01:
        return None
    return res["heading_change_deg"] / res["along_m"]


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def _mean_rate(runs):
    return _mean([r["result"]["rate"] for r in runs])


def _ratio(a, b):
    return a / b if a is not None and b else None


def _asymmetry(a, b):
    """How much slower the slower of two rates is, as a fraction of the faster."""
    if a is None or b is None or max(a, b) <= 0:
        return None
    return abs(a - b) / max(a, b)


def _grade(value, limits):
    """good / fair / poor for a value where smaller is better; None for no value."""
    if value is None:
        return None
    return "good" if value < limits[0] else "fair" if value < limits[1] else "poor"


def _grade_above(value, limits):
    """The same, where bigger is better."""
    if value is None:
        return None
    return "good" if value >= limits[0] else "fair" if value >= limits[1] else "poor"


def _worst(grades):
    known = [g for g in grades if g is not None]
    return max(known, key=GRADES.index) if known else None


def _n(value, scale, digits=0):
    """A number for a sentence, or '–'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    v = value * scale
    return "{:.{}f}".format(v, digits) if digits else str(int(round(v)))


def _speeds_text(runs):
    speeds = ["{:g}".format(s) for s in sorted({r["speed"] for r in runs})]
    if not speeds:
        return "–"
    if len(speeds) == 1:
        return "speed " + speeds[0]
    return "speeds " + ", ".join(speeds[:-1]) + " and " + speeds[-1]
