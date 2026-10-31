"""Follow the simulator instead of a gamepad: a real robot takes the commands
the sim's robot_node computes for one sim robot.

    camera -> tracker -> sim world state -> strategy / robot_node -> this -> robot

robot_node publishes every robot's command in its own body frame on REAL_PORT
(see sim/robot_node.py). With the camera mirroring the real robot into the sim
(run_pipeline.py --vision), that closes the loop through the camera.

A robot only moves while the camera sees it ("tracked" in the message).
Otherwise robot_node is steering the sim's guess of where the robot is, and
the real one would drive blind.
"""

import json
import time

from .protocol import body_axes

REAL_PORT = 9094       # sim/config.py REAL_PORT
FRESH_S = 0.2          # no message for this long and the sim counts as stopped
DRIBBLER_BUTTON = 0    # packet button index, see firmware/robot.py
STOPPED = ((0.0, 0.0, 0.0, 0.0), frozenset())


def command_input(command):
    """One robot's entry from robot_node's message -> (axes, buttons)."""
    if not command or not command.get("tracked"):
        return STOPPED
    axes = body_axes(command["forward"], command["left"], command["turn"])
    buttons = frozenset([DRIBBLER_BUTTON]) if command.get("dribble") else frozenset()
    return axes, buttons


class SimFeed:
    """The newest message from robot_node, read without blocking."""

    def __init__(self, address="tcp://localhost:{}".format(REAL_PORT)):
        import zmq   # only needed here, not for gamepad or keyboard driving
        self._zmq = zmq
        self._sock = zmq.Context.instance().socket(zmq.SUB)
        self._sock.setsockopt(zmq.RCVTIMEO, 0)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt_string(zmq.SUBSCRIBE, "")
        self._sock.connect(address)
        self._robots = {}
        self._received = 0.0

    def poll(self):
        """Take in what has arrived; the newest message wins."""
        while True:
            try:
                message = json.loads(self._sock.recv_string())
            except self._zmq.Again:
                return
            self._robots = message.get("robots", {})
            self._received = time.monotonic()

    @property
    def up(self):
        return time.monotonic() - self._received < FRESH_S

    def command(self, sim_id):
        """robot_node's newest command for one sim robot, or None."""
        return self._robots.get(str(sim_id)) if self.up else None

    def input(self, sim_id):
        """(axes, buttons) for the real robot that follows sim robot sim_id."""
        return command_input(self.command(sim_id))

    def close(self):
        self._sock.close()


class SimFollower:
    """The command-line input for python -m laptop --sim: robot i follows sim
    robot sim_ids[i]. Prints a line whenever what a robot is doing changes."""

    quit_requested = False   # same interface as Keyboard

    def __init__(self, sim_ids, log=print):
        self._ids = list(sim_ids)
        self._feed = SimFeed()
        self._log = log
        self._shown = {}
        self.name = "sim robot " + ", ".join(str(i) for i in self._ids)

    def read(self):
        self._feed.poll()
        return [self._feed.input(sim_id) for sim_id in self._ids]

    def show(self, links):
        for link, sim_id in zip(links, self._ids):
            command = self._feed.command(sim_id)
            if not self._feed.up:
                what = "stopped: no commands from the sim (is run_pipeline.py running?)"
            elif not command or not command.get("tracked"):
                what = "stopped: the camera doesn't see it"
            else:
                what = "following sim robot {}".format(sim_id)
            if self._shown.get(link.name) != what:
                self._shown[link.name] = what
                self._log("[{}] {}".format(link.name, what))
