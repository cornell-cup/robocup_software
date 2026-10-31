"""Laptop side: drive XRP robots over Bluetooth (BLE). Run with `python -m laptop`.

    protocol    builds the packets the robot's pestolink.py expects
    robot_link  keeps a BLE connection to the robot and streams packets to it
    fleet       several robot_links at once, callable from any thread (the web app)
    gamepad     reads gamepads, one per robot
    keyboard    reads the keyboard (small window) when no gamepad is around
    sim_follow  takes the commands the simulator computes for a robot (--sim)
    __main__    command line
"""
