import unittest

from laptop.protocol import body_axes
from laptop.sim_follow import DRIBBLER_BUTTON, STOPPED, command_input


class CommandInputTest(unittest.TestCase):
    def test_tracked_robot_follows_the_command(self):
        command = {"forward": 0.5, "left": -0.2, "turn": 0.1, "dribble": True, "tracked": True}
        self.assertEqual(command_input(command),
                         (body_axes(0.5, -0.2, 0.1), frozenset([DRIBBLER_BUTTON])))

    def test_robot_the_camera_lost_is_stopped(self):
        command = {"forward": 0.5, "left": 0.0, "turn": 0.0, "dribble": True, "tracked": False}
        self.assertEqual(command_input(command), STOPPED)

    def test_no_command_is_stopped(self):
        self.assertEqual(command_input(None), STOPPED)


if __name__ == "__main__":
    unittest.main()
