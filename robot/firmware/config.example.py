"""Per-robot settings. Copy this file to config.py and upload it to the robot.

config.py is git-ignored so each robot keeps its own copy.
"""

# PestoLink advertises only the first 8 characters of the name, so every robot
# needs a name that is unique within those 8 characters (e.g. "XRP-01").
ROBOT_NAME = "XRP-01"

# Global motor limit, 0..1. Start low for a first test; raise it once it drives right.
MAX_EFFORT = 0.5

# True if W drives the robot backward while A / D and Q / E are right: the
# symptom of Motor L and Motor R plugged into each other's ports. Flips
# forward / backward only. (Swapping the two cables fixes it too.)
REVERSE_FORWARD = False
