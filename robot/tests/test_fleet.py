import asyncio
import time
import unittest

from laptop import fleet as fleet_module
from laptop.fleet import Fleet
from laptop.protocol import encode_packet, neutral_packet

FORWARD = encode_packet([0.0, -1.0, 0.0, 0.0], [])


class FakeLink:
    """Stands in for RobotLink: no Bluetooth, just remembers the packet."""

    def __init__(self, name, log=print, connect_lock=None):
        self.name = name
        self.connected = True
        self.state = "connected"
        self.error = None
        self.packet = neutral_packet()
        self.cancelled = False

    def set_packet(self, packet):
        self.packet = packet

    async def run(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FleetTest(unittest.TestCase):
    def setUp(self):
        self.links = {}

        def factory(name, **kwargs):
            self.links[name] = FakeLink(name, **kwargs)
            return self.links[name]

        self.fleet = Fleet(log=lambda line: None, link_factory=factory)
        self.addCleanup(self.fleet.close)

    def test_drive_reaches_the_link(self):
        self.fleet.add("XRP-01")
        self.fleet.add("XRP-02")
        self.fleet.drive("XRP-02", [0.0, -1.0, 0.0, 0.0], [])
        self.assertEqual(self.links["XRP-02"].packet, FORWARD)
        self.assertEqual(self.links["XRP-01"].packet, neutral_packet())

    def test_a_robot_whose_commands_stop_is_stopped(self):
        self.fleet.add("XRP-01")
        self.fleet.drive("XRP-01", [0.0, -1.0, 0.0, 0.0], [])
        time.sleep(fleet_module.COMMAND_TIMEOUT_S + 3 * fleet_module.WATCHDOG_PERIOD_S)
        self.assertEqual(self.links["XRP-01"].packet, neutral_packet())
        self.assertFalse(self.fleet.status()[0]["moving"])

    def test_steady_commands_keep_it_moving(self):
        self.fleet.add("XRP-01")
        end = time.monotonic() + 2 * fleet_module.COMMAND_TIMEOUT_S
        while time.monotonic() < end:
            self.fleet.drive("XRP-01", [0.0, -1.0, 0.0, 0.0], [])
            time.sleep(0.05)
        self.assertEqual(self.links["XRP-01"].packet, FORWARD)

    def test_remove_cancels_the_link(self):
        self.fleet.add("XRP-01")
        self.fleet.remove("XRP-01")
        self.assertTrue(self.links["XRP-01"].cancelled)
        self.assertEqual(self.fleet.names(), [])
        with self.assertRaises(KeyError):
            self.fleet.drive("XRP-01", [0.0] * 4, [])

    def test_adding_twice_keeps_one_link(self):
        self.fleet.add("XRP-01")
        first = self.links["XRP-01"]
        self.fleet.add("XRP-01")
        self.assertIs(self.links["XRP-01"], first)
        self.assertEqual(self.fleet.names(), ["XRP-01"])

    def test_names_must_fit_the_advertised_eight_characters(self):
        with self.assertRaises(ValueError):
            self.fleet.add("XRP-Omni-01")
        with self.assertRaises(ValueError):
            self.fleet.add("   ")


if __name__ == "__main__":
    unittest.main()
