"""Several robots at once, for programs that are not asyncio themselves (the
web app's server, the simulator follower): call it from any thread.

    fleet = Fleet()
    fleet.add("XRP-01")
    fleet.drive("XRP-01", axes, buttons)   # again at least every COMMAND_TIMEOUT_S
    fleet.close()

The BLE work runs on one asyncio loop in a background thread, one RobotLink
per robot, connecting one robot at a time. A robot whose commands stop coming
is stopped: the link resends the newest packet forever, so without this a
closed browser tab or a crashed program would leave a robot driving.
"""

import asyncio
import threading
import time

from .protocol import NAME_LENGTH, encode_packet, neutral_packet
from .robot_link import RobotLink, nearby

COMMAND_TIMEOUT_S = 0.25   # no drive() for this long and the robot is stopped
WATCHDOG_PERIOD_S = 0.05
SCAN_S = 4.0


class _Robot:
    def __init__(self, link, task):
        self.link = link
        self.task = task
        self.last_drive = 0.0   # time.monotonic() of the newest drive()
        self.moving = False     # the newest packet is not the neutral one


class Fleet:
    def __init__(self, log=print, link_factory=None):
        self._log = log
        self._link_factory = link_factory or RobotLink   # tests pass a fake RobotLink
        self._robots = {}                                # name -> _Robot, guarded by _lock
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="fleet",
                                        daemon=True)
        self._thread.start()
        # Made on the loop: on Python 3.9 an asyncio.Lock belongs to the loop
        # that is current when it is made.
        self._connect_lock = self._call(self._new_lock())
        self._watchdog = self._call(self._start_watchdog())

    # -- called from any thread ---------------------------------------------

    def add(self, name):
        """Start connecting to a robot (and keep reconnecting). No-op if added."""
        name = name.strip()
        if not name:
            raise ValueError("robot name is empty")
        if len(name) > NAME_LENGTH:
            raise ValueError("robots advertise only {} characters of their name: "
                             "use {!r}".format(NAME_LENGTH, name[:NAME_LENGTH]))
        with self._lock:
            if name in self._robots:
                return
        robot = self._call(self._start_link(name))
        with self._lock:
            self._robots[name] = robot

    def remove(self, name):
        """Stop the robot and disconnect. No-op if it isn't in the fleet."""
        with self._lock:
            robot = self._robots.pop(name, None)
        if robot is not None:
            self._call(self._stop_link(robot), timeout=5.0)

    def names(self):
        with self._lock:
            return list(self._robots)

    def drive(self, name, axes, buttons):
        """Send a robot this input (axes, buttons as for encode_packet) until the
        next drive(), or until COMMAND_TIMEOUT_S passes without one."""
        packet = encode_packet(axes, buttons)
        with self._lock:
            robot = self._robots.get(name)
            if robot is None:
                raise KeyError(name)
            robot.link.set_packet(packet)
            robot.last_drive = time.monotonic()
            robot.moving = packet != neutral_packet()

    def stop(self, name):
        with self._lock:
            robot = self._robots.get(name)
            if robot is not None:
                robot.link.set_packet(neutral_packet())
                robot.moving = False

    def status(self):
        """One dict per robot, in the order they were added."""
        now = time.monotonic()
        with self._lock:
            return [{
                "name": name,
                "state": robot.link.state,
                "connected": robot.link.connected,
                "error": robot.link.error,
                "moving": robot.moving,
                "command_age_s": round(now - robot.last_drive, 2) if robot.last_drive else None,
            } for name, robot in self._robots.items()]

    def scan(self, timeout=SCAN_S):
        """Nearby BLE devices, [(rssi, name)], strongest first. Waits for any
        connect in progress, and holds the others off while it scans."""
        return self._call(self._scan(timeout), timeout=timeout + SCAN_S + 15.0)

    def close(self):
        """Stop and disconnect every robot, then end the background thread."""
        for name in self.names():
            self.remove(name)
        self._call(self._stop_watchdog())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2.0)

    def _call(self, coro, timeout=2.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # -- on the loop ----------------------------------------------------------

    async def _new_lock(self):
        return asyncio.Lock()

    async def _start_watchdog(self):
        return asyncio.get_running_loop().create_task(self._stop_quiet_robots())

    async def _stop_watchdog(self):
        self._watchdog.cancel()
        await asyncio.gather(self._watchdog, return_exceptions=True)

    async def _start_link(self, name):
        link = self._link_factory(name, log=self._log, connect_lock=self._connect_lock)
        return _Robot(link, asyncio.get_running_loop().create_task(link.run()))

    async def _stop_link(self, robot):
        robot.link.set_packet(neutral_packet())
        robot.task.cancel()   # RobotLink sends a stop packet as it disconnects
        await asyncio.gather(robot.task, return_exceptions=True)

    async def _stop_quiet_robots(self):
        while True:
            await asyncio.sleep(WATCHDOG_PERIOD_S)
            now = time.monotonic()
            with self._lock:
                for robot in self._robots.values():
                    if robot.moving and now - robot.last_drive > COMMAND_TIMEOUT_S:
                        robot.link.set_packet(neutral_packet())
                        robot.moving = False

    async def _scan(self, timeout):
        async with self._connect_lock:
            return await nearby(timeout)
