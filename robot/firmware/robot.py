"""
robot.py
Runs ON the XRP. Receives drive commands from the laptop over Bluetooth (BLE)
and drives the three omniwheels and the dribbler.

    laptop (python -m laptop) --BLE--> pestolink.py --> this file --> motors

Needs on the robot: lib/pestolink.py, config.py, mixing.py (see the README).
The robot stops whenever the laptop disconnects or goes quiet for 300 ms.
"""

import time
from machine import Pin

from XRPLib.defaults import left_motor, right_motor, motor_four
from pestolink import PestoLinkAgent

import config
from config import ROBOT_NAME, MAX_EFFORT
from mixing import compute_wheel_efforts

LOOP_PERIOD_S = 0.02
COMMAND_TIMEOUT_MS = 300   # stop if no packet arrives for this long
# Forward / backward flipped for this robot (see config.example.py). Optional
# in config.py, so older config files still work.
REVERSE_FORWARD = getattr(config, "REVERSE_FORWARD", False)

# Packet layout, matching laptop/protocol.py.
AXIS_STRAFE = 0            # right is positive
AXIS_FORWARD = 1           # forward is negative (gamepad convention)
AXIS_ROTATE = 2            # right (clockwise) is positive
BUTTON_DRIBBLER = 0

# Dribbler output is active-low: 0 = on, 1 = off.
DRIBBLER_PIN = 16
DRIBBLER_ON = 0
DRIBBLER_OFF = 1

# Same order as the lists in mixing.py: wheel 0, 1, 2.
MOTORS = [left_motor, right_motor, motor_four]
MOTOR_NAMES = ["Motor L", "Motor R", "Motor 4"]

dribbler_pin = Pin(DRIBBLER_PIN, Pin.OUT)
dribbler_pin.value(DRIBBLER_OFF)


class WatchdogPestoLink(PestoLinkAgent):
    """PestoLinkAgent that remembers when the last packet arrived, so the robot
    can stop if the laptop program freezes while the BLE link stays open."""

    def __init__(self, name):
        self.last_packet_ms = time.ticks_ms()
        super().__init__(name)

    def on_write(self, value):
        super().on_write(value)
        self.last_packet_ms = time.ticks_ms()

    def has_fresh_command(self):
        age_ms = time.ticks_diff(time.ticks_ms(), self.last_packet_ms)
        return age_ms <= COMMAND_TIMEOUT_MS


def drive(vx, vy, omega):
    if REVERSE_FORWARD:
        vy = -vy
    efforts = compute_wheel_efforts(vx, vy, omega)
    for motor, effort in zip(MOTORS, efforts):
        motor.set_effort(effort * MAX_EFFORT)


def set_dribbler(enabled):
    dribbler_pin.value(DRIBBLER_ON if enabled else DRIBBLER_OFF)


def stop_all():
    """Stop the drive motors and the dribbler."""
    for motor in MOTORS:
        motor.set_effort(0.0)
    set_dribbler(False)


def main():
    link = WatchdogPestoLink(ROBOT_NAME)
    print("Advertising as '{}'. Waiting for the laptop...".format(ROBOT_NAME))

    while True:
        if link.is_connected() and link.has_fresh_command():
            drive(link.get_axis(AXIS_STRAFE),
                  -link.get_axis(AXIS_FORWARD),
                  -link.get_axis(AXIS_ROTATE))
            set_dribbler(link.get_button(BUTTON_DRIBBLER))
        else:
            stop_all()
        time.sleep(LOOP_PERIOD_S)


if __name__ == "__main__":
    try:
        main()
    finally:
        stop_all()
