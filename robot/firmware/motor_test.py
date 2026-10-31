"""
motor_test.py
Runs ON the XRP. Spins each wheel by itself so you can check the wiring.
LIFT THE ROBOT OFF THE TABLE FIRST, then from the laptop run:

    mpremote run firmware/motor_test.py + reset

For each wheel, in order Motor L, Motor R, Motor 4:
  1. The wheel that spins must be the one shown in the README diagram.
     If a different wheel spins, swap the motor cables.
  2. Face that wheel from outside the robot. It must spin CLOCKWISE.
     If it spins counter-clockwise, flip its entry in WHEEL_SIGNS
     (mixing.py, 1 <-> -1) and upload mixing.py again.
"""

import time

from robot import MOTORS, MOTOR_NAMES, stop_all
from mixing import WHEEL_SIGNS

TEST_EFFORT = 0.3
SPIN_S = 2.0
PAUSE_S = 1.0

try:
    for name, motor, sign in zip(MOTOR_NAMES, MOTORS, WHEEL_SIGNS):
        print("Spinning {} (should be clockwise, seen from outside)".format(name))
        motor.set_effort(sign * TEST_EFFORT)
        time.sleep(SPIN_S)
        motor.set_effort(0)
        time.sleep(PAUSE_S)
    print("Done.")
finally:
    stop_all()
