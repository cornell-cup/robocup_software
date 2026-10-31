import unittest

from laptop import protocol


def decode_packet(packet):
    """Decode like pestolink.py on the robot: 127 is exactly 0, else b / 127.5 - 1."""
    axes = [0.0 if b == 127 else b / 127.5 - 1 for b in packet[1:5]]
    mask = (packet[6] << 8) | packet[5]
    return axes, {i for i in range(16) if (mask >> i) & 1}


class EncodeAxisTest(unittest.TestCase):
    def test_zero_is_exactly_neutral(self):
        self.assertEqual(protocol.encode_axis(0.0), 127)

    def test_extremes(self):
        self.assertEqual(protocol.encode_axis(-1.0), 0)
        self.assertEqual(protocol.encode_axis(1.0), 255)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(protocol.encode_axis(5.0), 255)
        self.assertEqual(protocol.encode_axis(-5.0), 0)


class PacketTest(unittest.TestCase):
    def test_layout(self):
        packet = protocol.encode_packet([1.0, -1.0, 0.0, 0.0], {0, 9})
        self.assertEqual(len(packet), protocol.PACKET_LENGTH)
        self.assertEqual(packet[0], 0x01)
        self.assertEqual(list(packet[1:5]), [255, 0, 127, 127])
        self.assertEqual(packet[5], 0b00000001)
        self.assertEqual(packet[6], 0b00000010)

    def test_round_trip(self):
        axes, buttons = decode_packet(
            protocol.encode_packet([0.5, -0.5, 0.0, 1.0], {2, 15}))
        self.assertEqual(buttons, {2, 15})
        for got, want in zip(axes, [0.5, -0.5, 0.0, 1.0]):
            self.assertAlmostEqual(got, want, places=2)

    def test_neutral_packet_matches_robot_default(self):
        # pestolink.py resets to [1,127,127,127,127,0,...] on an unknown packet.
        self.assertEqual(list(protocol.neutral_packet()),
                         [1, 127, 127, 127, 127] + [0] * 15)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            protocol.encode_packet([0.0, 0.0], [])
        with self.assertRaises(ValueError):
            protocol.encode_packet([0.0] * 4, [16])


class BodyAxesTest(unittest.TestCase):
    def robot_sees(self, forward, left, turn):
        """What robot.py hands to compute_wheel_efforts: (vx right, vy forward, omega ccw)."""
        axes, _ = decode_packet(protocol.encode_packet(protocol.body_axes(forward, left, turn), []))
        return axes[0], -axes[1], -axes[2]

    def test_each_axis_reaches_the_robot_with_its_sign(self):
        for (forward, left, turn), want in [
                ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),    # forward -> vy
                ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),   # left -> negative strafe
                ((0.0, 0.0, 1.0), (0.0, 0.0, 1.0))]:   # ccw -> omega
            for got, expected in zip(self.robot_sees(forward, left, turn), want):
                self.assertAlmostEqual(got, expected, places=2)

    def test_zero_is_the_neutral_packet(self):
        self.assertEqual(protocol.encode_packet(protocol.body_axes(0.0, 0.0, 0.0), []),
                         protocol.neutral_packet())


if __name__ == "__main__":
    unittest.main()
