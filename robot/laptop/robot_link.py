"""One BLE connection to one robot, kept alive and fed with the latest packet."""

import asyncio

from bleak import BleakClient, BleakScanner

from .protocol import NAME_LENGTH, RX_UUID, neutral_packet

SEND_PERIOD_S = 0.05        # 20 Hz, well inside the robot's 300 ms watchdog
SCAN_TIMEOUT_S = 10.0
RETRY_DELAY_S = 2.0
# Windows' "the device is not ready" (0x800710DF): its Bluetooth radio is off.
WINERROR_RADIO_OFF = -2147020577


def describe_error(error):
    """A BLE error as one line for a person to act on."""
    if getattr(error, "winerror", None) == WINERROR_RADIO_OFF:
        return "Bluetooth is off on this computer (Settings > Bluetooth & devices)"
    return str(error) or type(error).__name__


async def nearby(timeout=5.0):
    """Scan for BLE devices: [(rssi, name)], strongest first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [(adv.rssi, adv.local_name or device.name or "(no name)")
               for device, adv in found.values()]
    return sorted(devices, reverse=True)


class RobotLink:
    """Connects to a robot by name and streams packets to it, reconnecting on loss.

    set_packet() only stores the newest packet; the send loop picks it up on
    its next tick.

    Links to several robots can share one connect_lock (an asyncio.Lock) so
    they search and connect one at a time: BLE stacks, Windows' especially,
    tend to fail a connect that overlaps another scan.
    """

    def __init__(self, name, log=print, connect_lock=None):
        self.name = name
        self.connected = False
        self.state = "starting"   # searching / connecting / connected / retrying
        self.error = None         # the last link error, for status displays
        self._log = log
        self._packet = neutral_packet()
        self._connect_lock = connect_lock

    def set_packet(self, packet):
        self._packet = packet

    async def run(self):
        """Connect, stream, and reconnect forever. Cancel the task to stop."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # BLE errors vary by platform; keep retrying
                self.error = describe_error(error)
                self._log("[{}] link error: {}".format(self.name, self.error))
            self.connected = False
            self.state = "retrying"
            await asyncio.sleep(RETRY_DELAY_S)

    async def _session(self):
        if self._connect_lock is None:
            client = await self._connect()
        else:
            async with self._connect_lock:
                client = await self._connect()
        try:
            self.connected = True
            self.state = "connected"
            self.error = None
            self._log("[{}] connected".format(self.name))
            while client.is_connected:
                await client.write_gatt_char(RX_UUID, self._packet, response=False)
                await asyncio.sleep(SEND_PERIOD_S)
            self._log("[{}] disconnected".format(self.name))
        finally:
            await self._release(client)

    async def _connect(self):
        self.state = "searching"
        self._log("[{}] searching...".format(self.name))
        wanted = self.name[:NAME_LENGTH]
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: (adv.local_name or d.name or "") == wanted,
            timeout=SCAN_TIMEOUT_S)
        if device is None:
            raise RuntimeError("not found nearby (is it powered on and running robot.py?)")
        self.state = "connecting"
        client = BleakClient(device)
        await client.connect()
        return client

    async def _release(self, client):
        """Best-effort stop, then disconnect. Never raises."""
        # The link may already be gone, in which case both calls fail; the robot's
        # own 300 ms watchdog stops it either way.
        try:
            if client.is_connected:
                await client.write_gatt_char(RX_UUID, neutral_packet(), response=False)
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass
