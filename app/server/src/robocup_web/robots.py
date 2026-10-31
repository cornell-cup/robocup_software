"""The real XRP robots, from the browser.

Each robot added here gets a Bluetooth link (robot/laptop's Fleet, the same
code as `python -m laptop`). A robot is driven one of two ways:

- by hand: the Robots page sends drive commands over the WebSocket;
- following a sim robot: it takes the commands the sim's robot_node computes
  for that robot (robot/laptop/sim_follow.py), so strategy and the click /
  WASD controls on the Simulator page drive the real robot, closed-loop
  through the camera. It only moves while the camera sees it.

Either way the Fleet stops a robot whose commands stop coming. Only one
program can hold a robot's Bluetooth link: close `python -m laptop` for a
robot before adding it here.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from .procs import CommandError

ROBOT_DIR = Path(__file__).resolve().parents[4] / "robot"
#: How often a following robot gets the sim's newest command.
FOLLOW_HZ = 50


def _laptop():
    """robot/laptop, the robot driver. Not an installed package, so it is
    imported from the folder, as sim.py imports the sim's config."""
    if str(ROBOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROBOT_DIR))
    import laptop.fleet
    import laptop.protocol
    import laptop.robot_link
    import laptop.sim_follow

    return laptop


class RealRobots:
    def __init__(self) -> None:
        laptop = _laptop()
        self._protocol = laptop.protocol
        self._sim_follow = laptop.sim_follow
        self._describe = laptop.robot_link.describe_error
        self.fleet = laptop.fleet.Fleet(log=lambda line: print(f"robots: {line}"))
        self._follow: dict[str, int | None] = {}  # name -> sim robot id, None = by hand
        self._lock = threading.Lock()  # for _follow: the follower thread reads it
        self._feed: Any = None  # SimFeed, made and polled on the follower thread
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._follow_sim, name="robots-follow",
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.fleet.close()

    # -- the list -----------------------------------------------------------

    def add(self, name: str) -> None:
        try:
            self.fleet.add(name)
        except ValueError as e:
            raise CommandError(str(e)) from None
        with self._lock:
            self._follow.setdefault(name.strip(), None)

    def remove(self, name: str) -> None:
        self._check(name)
        with self._lock:
            self._follow.pop(name, None)
        self.fleet.remove(name)

    def follow(self, name: str, sim_robot: int | None) -> None:
        """Have a robot follow sim robot `sim_robot`, or None to drive it by hand."""
        self._check(name)
        if sim_robot is not None and not 0 <= sim_robot < 64:
            raise CommandError("sim robot must be a robot number from the Simulator page")
        with self._lock:
            if sim_robot is not None and any(
                    other != name and rid == sim_robot for other, rid in self._follow.items()):
                raise CommandError(f"another robot already follows sim robot {sim_robot}")
            self._follow[name] = sim_robot
        self.fleet.stop(name)

    def scan(self) -> list[dict[str, Any]]:
        try:
            found = self.fleet.scan()
        except Exception as e:  # BLE errors vary by platform: radio off, no adapter
            raise CommandError(f"Bluetooth scan failed: {self._describe(e)}") from None
        return [{"name": name, "rssi": rssi} for rssi, name in found]

    def status(self) -> dict[str, Any]:
        feed = self._feed
        sim_up = feed is not None and feed.up
        robots = []
        for r in self.fleet.status():
            rid = self._follow.get(r["name"])
            command = feed.command(rid) if feed is not None and rid is not None else None
            robots.append({**r, "follow": rid,
                           "tracked": None if rid is None else bool(command and command.get("tracked"))})
        return {"robots": robots, "sim_up": sim_up}

    # -- driving by hand ----------------------------------------------------

    def drive(self, cmd: dict[str, Any]) -> str:
        """One drive command from the page: name, forward, left, turn (each
        -1..1, in the robot's own frame, turn counter-clockwise), dribble.
        Returns the robot's name."""
        name = str(cmd.get("name", ""))
        self._check(name)
        if self._follow.get(name) is not None:
            raise CommandError(f"{name} follows sim robot {self._follow[name]}; "
                               f"switch it to driving by hand first")
        try:
            axes = self._protocol.body_axes(float(cmd.get("forward", 0)), float(cmd.get("left", 0)),
                                            float(cmd.get("turn", 0)))
        except (TypeError, ValueError):
            raise CommandError("forward, left and turn must be numbers") from None
        buttons = [self._sim_follow.DRIBBLER_BUTTON] if cmd.get("dribble") else []
        try:
            self.fleet.drive(name, axes, buttons)
        except KeyError:
            raise CommandError(f"{name} was just removed") from None
        return name

    def stop(self, names: set[str]) -> None:
        for name in names:
            if self._follow.get(name) is None:
                self.fleet.stop(name)

    def _check(self, name: str) -> None:
        if name not in self._follow:
            raise CommandError(f"no robot {name!r} -- add it first")

    # -- following the sim (its own thread: the ZMQ socket lives here) --------

    def _follow_sim(self) -> None:
        self._feed = feed = self._sim_follow.SimFeed()
        try:
            while not self._stop.wait(1.0 / FOLLOW_HZ):
                feed.poll()
                with self._lock:
                    following = list(self._follow.items())
                for name, rid in following:
                    if rid is None:
                        continue
                    axes, buttons = feed.input(rid)
                    try:
                        self.fleet.drive(name, axes, buttons)
                    except KeyError:
                        pass  # removed meanwhile
        finally:
            feed.close()
