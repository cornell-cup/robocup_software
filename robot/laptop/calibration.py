"""Experimental steady-state calibration from motion-test profiles.

This module does not send commands to a robot. Its independent-axis model
does not identify motor deadbands or predict combined translation and rotation.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean


AXIS_TESTS = {
    "forward_pos": "forward", "forward_neg": "backward",
    "left_pos": "strafe_left", "left_neg": "strafe_right",
    "turn_pos": "spin_ccw", "turn_neg": "spin_cw",
}


@dataclass
class AxisFit:
    """Mean steady rates at each measured normalized command magnitude."""

    points: tuple

    def rate(self, command):
        if not math.isfinite(command) or command < 0 or command > 1:
            raise ValueError("command must be finite and between 0 and 1")
        if not self.points or command > self.points[-1][0]:
            raise ValueError("command is outside the measured range")
        previous_command, previous_rate = 0.0, 0.0
        for current_command, current_rate in self.points:
            if command <= current_command:
                fraction = (command - previous_command) / (current_command - previous_command)
                return previous_rate + fraction * (current_rate - previous_rate)
            previous_command, previous_rate = current_command, current_rate
        raise ValueError("command is outside the measured range")

    def command_for(self, rate):
        if not math.isfinite(rate) or rate < 0:
            raise ValueError("rate must be finite and nonnegative")
        if not self.points or any(
            current[1] <= previous[1]
            for previous, current in zip(self.points, self.points[1:])
        ):
            return None
        previous_command, previous_rate = 0.0, 0.0
        for current_command, current_rate in self.points:
            if current_rate <= previous_rate:
                return None
            if rate <= current_rate:
                fraction = (rate - previous_rate) / (current_rate - previous_rate)
                return previous_command + fraction * (current_command - previous_command)
            previous_command, previous_rate = current_command, current_rate
        return None


@dataclass
class CalibrationModel:
    robot: str
    axes: dict


def fit_profile(profile):
    """Build signed-axis curves from completed, moving motion-test records."""
    grouped = {axis: defaultdict(list) for axis in AXIS_TESTS}
    test_axes = {test: axis for axis, test in AXIS_TESTS.items()}
    for record in profile.get("records", []):
        result = record.get("result") or {}
        axis = test_axes.get(record.get("test"))
        command = record.get("speed")
        rate = result.get("rate")
        if (axis is None or record.get("ended") != "reached" or not result.get("moved")
                or not isinstance(command, (int, float)) or not isinstance(rate, (int, float))
                or not math.isfinite(command) or not math.isfinite(rate)
                or not 0 < command <= 1 or rate <= 0):
            continue
        grouped[axis][command].append(rate)
    axes = {axis: AxisFit(tuple((command, mean(rates))
                               for command, rates in sorted(speeds.items())))
            for axis, speeds in grouped.items()}
    return CalibrationModel(robot=profile.get("robot", ""), axes=axes)


def fleet_scale(models, reference, margin=0.97):
    """Return a conservative common full-command scale for complete profiles."""
    if not models or not math.isfinite(margin) or not 0 < margin <= 1:
        raise ValueError("provide models and a margin between 0 and 1")
    scale = 1.0
    for model in models:
        for axis in AXIS_TESTS:
            try:
                target = reference.axes[axis].rate(1.0)
                actual = model.axes[axis].rate(1.0)
                if target <= 0 or actual <= 0:
                    raise ValueError("zero rate")
                if (reference.axes[axis].command_for(target) is None
                        or model.axes[axis].command_for(actual) is None):
                    raise ValueError("nonmonotonic response")
            except (KeyError, ValueError) as error:
                raise ValueError("complete monotonic full-speed profiles required: " + axis) from error
            scale = min(scale, actual / target)
    return scale * margin


class Compensator:
    """Map independent axis commands to a scaled reference response."""

    def __init__(self, model, reference, scale=1.0):
        if not math.isfinite(scale) or not 0 < scale <= 1:
            raise ValueError("scale must be between 0 and 1")
        self.model = model
        self.reference = reference
        self.scale = scale

    def __call__(self, forward, left, turn):
        corrected = []
        for value, prefix in ((forward, "forward"), (left, "left"), (turn, "turn")):
            if not math.isfinite(value) or abs(value) > 1:
                raise ValueError("commands must be finite and between -1 and 1")
            if value == 0:
                corrected.append(0.0)
                continue
            axis = prefix + ("_pos" if value > 0 else "_neg")
            try:
                target = self.scale * self.reference.axes[axis].rate(abs(value))
                command = self.model.axes[axis].command_for(target)
            except KeyError as error:
                raise ValueError("missing calibration axis: " + axis) from error
            if command is None:
                raise ValueError("unreachable or nonmonotonic calibration axis: " + axis)
            corrected.append(math.copysign(command, value))
        return tuple(corrected)