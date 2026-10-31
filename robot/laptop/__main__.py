"""Drive XRP robots from this laptop, with gamepads, the keyboard, or the simulator.

    python -m laptop                   drive XRP-01 with the first gamepad
    python -m laptop XRP-02            drive a robot with a different name
    python -m laptop XRP-01 XRP-02     two robots, one gamepad each (first gamepad -> XRP-01)
    python -m laptop --keyboard        use the keyboard instead (Tab switches robot)
    python -m laptop --gamepad 1       start from the second gamepad plugged in
    python -m laptop XRP-01 --sim 0    XRP-01 follows sim robot 0 (see laptop/sim_follow.py)
    python -m laptop --scan            list nearby Bluetooth devices
"""

import argparse
import asyncio
import sys

from .protocol import encode_packet, neutral_packet

LOOP_PERIOD_S = 0.02


def parse_args():
    parser = argparse.ArgumentParser(prog="python -m laptop", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("robots", nargs="*", default=["XRP-01"], metavar="robot",
                        help="ROBOT_NAME from each robot's config.py (default: XRP-01)")
    parser.add_argument("--keyboard", action="store_true", help="drive with the keyboard")
    parser.add_argument("--gamepad", type=int, default=0, metavar="N",
                        help="first gamepad to use, 0 = first (default: %(default)s); "
                             "the next robot gets the next gamepad")
    parser.add_argument("--sim", type=sim_ids, metavar="IDS",
                        help="follow the simulator instead: comma-separated sim robot ids, "
                             "one per robot, e.g. --sim 0,1")
    parser.add_argument("--scan", action="store_true", help="list nearby BLE devices and exit")
    args = parser.parse_args()
    if len(set(args.robots)) != len(args.robots):
        parser.error("a robot is listed twice")
    if args.sim is not None and len(args.sim) != len(args.robots):
        parser.error("--sim needs one sim robot id per robot ({} robots, {} ids)".format(
            len(args.robots), len(args.sim)))
    if args.sim is not None and args.keyboard:
        parser.error("--sim and --keyboard both drive the robots; pick one")
    return args


def sim_ids(text):
    try:
        return [int(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("expected ids like 0 or 0,1") from None


async def scan():
    from .robot_link import nearby
    print("Scanning for 5 s...")
    for rssi, name in await nearby(5.0):
        print("{:>4} dBm  {}".format(rssi, name))


def open_inputs(args):
    """Whatever drives the robots: read() gives one (axes, buttons) per robot."""
    # Imported here so --scan and --help do not need pygame.
    if args.sim is not None:
        from .sim_follow import SimFollower
        return SimFollower(args.sim)
    if args.keyboard:
        from .keyboard import Keyboard
        return Keyboard("Drive " + ", ".join(args.robots), len(args.robots))
    from .gamepad import Gamepads
    return Gamepads(len(args.robots), args.gamepad)


async def drive(args):
    from .robot_link import RobotLink
    inputs = open_inputs(args)
    print("{} <- {}. Ctrl+C to quit.".format(", ".join(args.robots), inputs.name))

    # One connect at a time: overlapping BLE scans and connects tend to fail.
    connect_lock = asyncio.Lock()
    links = [RobotLink(name, connect_lock=connect_lock) for name in args.robots]
    tasks = [asyncio.create_task(link.run()) for link in links]
    try:
        while not inputs.quit_requested:
            for link, (axes, buttons) in zip(links, inputs.read()):
                link.set_packet(encode_packet(axes, buttons))
            inputs.show(links)
            await asyncio.sleep(LOOP_PERIOD_S)
    finally:
        for link in links:
            link.set_packet(neutral_packet())
        await asyncio.sleep(0.2)   # let the final stop packets go out
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    args = parse_args()
    try:
        if args.scan:
            asyncio.run(scan())
        else:
            asyncio.run(drive(args))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
