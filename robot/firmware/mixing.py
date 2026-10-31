"""
mixing.py
Omniwheel maths for robot.py: turns "drive this way" into one effort per wheel.
Pure Python with no hardware imports, so it also runs in the laptop unit tests.

Wheel layout, looking down on the robot from above with the dribbler toward
you (see the README):

              Motor 4            back
               /    \\
              /      \\
        Motor L ---- Motor R      front (dribbler)

Entry i of every list below describes MOTORS[i] in robot.py
(wheel 0 = Motor L, wheel 1 = Motor R, wheel 2 = Motor 4).
"""

import math

# Where each wheel sits around the robot, in degrees counter-clockwise from
# straight ahead (looking down from above). Seen from above with the front at
# the bottom, the robot's right is on your left, so Motor L sits front-right.
WHEEL_ANGLES_DEG = [300, 60, 180]

# Positive effort must turn every wheel the same way: clockwise when you face
# that wheel from outside the robot. If motor_test.py shows a wheel spinning
# counter-clockwise, flip its entry (1 <-> -1).
# Checked on the robot 2026-10-26: only Motor L needed flipping.
WHEEL_SIGNS = [-1, 1, 1]

# How strongly rotation mixes into each wheel relative to translation. At 1.0 a
# full turn drives every wheel at full effort, as full strafe does Motor 4; 0.6
# turned at 0.24 effort on the slow speed, where these motors barely move.
ROTATION_GAIN = 1.0

WHEEL_ANGLES = [math.radians(angle) for angle in WHEEL_ANGLES_DEG]


def compute_wheel_efforts(vx, vy, omega):
    """Convert a body velocity into one effort per wheel.

    vx:    strafe, right is positive                (-1..1)
    vy:    forward is positive                      (-1..1)
    omega: rotation, counter-clockwise is positive  (-1..1)
    If any wheel would exceed 1.0, all wheels are scaled down together so the
    direction of travel is preserved. WHEEL_SIGNS is applied last.
    """
    # Positive effort pushes a wheel counter-clockwise around the robot centre,
    # i.e. along (-cos a, -sin a) in (right, forward) coordinates.
    efforts = [
        -math.cos(angle) * vx - math.sin(angle) * vy + ROTATION_GAIN * omega
        for angle in WHEEL_ANGLES
    ]
    largest = max(abs(effort) for effort in efforts)
    if largest > 1.0:
        efforts = [effort / largest for effort in efforts]
    return [sign * effort for sign, effort in zip(WHEEL_SIGNS, efforts)]
