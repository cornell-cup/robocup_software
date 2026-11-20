"""The simulator's settings (sim/config.py), for the strategy.

sim/ isn't a package: its modules import each other by bare name (`config`,
`pymunk_world`, `robot_node`). This module is the one place the strategy puts
sim/ on sys.path. Take settings from here, never `from config import ...`:

    from strategy.world.simconfig import FIELD_W, ROBOT_RADIUS

Importing it also makes the other sim modules importable (`import
pymunk_world`), which the match runner and the RL env use.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIM_DIR = os.path.join(_REPO, "sim")
if SIM_DIR not in sys.path:
    sys.path.insert(0, SIM_DIR)

from config import *  # noqa: E402, F403
