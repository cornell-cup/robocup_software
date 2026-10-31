"""PestoLink wire format, as read by pestolink.py on the robot.

Must stay in sync with firmware/lib/pestolink.py, which decodes these packets.

A control packet is 20 bytes:
    byte 0       0x01 (control packet; anything else makes the robot stop)
    bytes 1-4    axes 0..3, one byte each; 0..255 maps to -1..1, 127 is neutral
    bytes 5-6    button bitmask, little endian; bit n is button n
    bytes 7-19   unused

Source: https://github.com/AlfredoSystems/PestoLink-MicroPython
"""

RX_UUID = "452af57e-ad27-422c-88ae-76805ea641a9"   # the robot's write characteristic

PACKET_LENGTH = 20
CONTROL_HEADER = 0x01
AXIS_COUNT = 4
BUTTON_COUNT = 16
NEUTRAL_AXIS_BYTE = 127     # pestolink.py reads exactly this value as 0.0
NAME_LENGTH = 8             # robots advertise only the first 8 characters of their name


def encode_axis(value):
    """Map a stick value in -1..1 to one byte. Exactly 0 maps to the neutral byte."""
    if value == 0:
        return NEUTRAL_AXIS_BYTE
    value = max(-1.0, min(1.0, value))
    return round((value + 1) * 127.5)


def encode_packet(axes, pressed_buttons):
    """Build a control packet.

    axes: four floats in -1..1, in PestoLink order (left X, left Y, right X, right Y).
    pressed_buttons: iterable of button indices (0..15) that are held down.
    """
    if len(axes) != AXIS_COUNT:
        raise ValueError("expected {} axes, got {}".format(AXIS_COUNT, len(axes)))
    mask = 0
    for index in pressed_buttons:
        if not 0 <= index < BUTTON_COUNT:
            raise ValueError("button index out of range: {}".format(index))
        mask |= 1 << index

    packet = bytearray(PACKET_LENGTH)
    packet[0] = CONTROL_HEADER
    for i, value in enumerate(axes):
        packet[1 + i] = encode_axis(value)
    packet[5] = mask & 0xFF
    packet[6] = mask >> 8
    return bytes(packet)


def neutral_packet():
    """Sticks centred, nothing pressed. Sending this stops a robot."""
    return encode_packet([0.0] * AXIS_COUNT, [])


def body_axes(forward, left, turn):
    """A body-frame command as the four axes robot.py reads.

    forward, left: translation, -1..1, in the robot's own frame (front = dribbler).
    turn:          rotation, -1..1, counter-clockwise (seen from above) positive.
    The axes keep the gamepad conventions robot.py expects: strafe right,
    forward negative, rotate clockwise.
    """
    return (-left, -forward, -turn, 0.0)

