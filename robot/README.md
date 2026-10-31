# XRP Omniwheel Manual Control

Drive a three-omniwheel XRP robot from a laptop, with a **gamepad** plugged into
the laptop or with the **laptop keyboard**. Steps 1–6 get one robot driving: wire
it up, upload the code, drive it. Step 7 drives several at once, lets the
simulator drive them, or does it all from the web app.

```
gamepad / keyboard --> laptop (python -m laptop) --Bluetooth--> XRP robot --> wheels
```

> **In robocup_software:** skip step 2. Run `uv sync` once at the repo root
> (it installs bleak, pygame and mpremote into the shared `.venv`), then run
> every command below from this `robot/` folder with `uv run` in front:
> `uv run mpremote ...`, `uv run python -m laptop XRP-07`.

## What you need

- An XRP robot with three omniwheels, charged batteries, and a dribbler (optional)
- A USB cable for the XRP
- A laptop with Bluetooth and Python 3.9 or newer
  (macOS: already installed; Windows: install it from python.org)
- A gamepad (Xbox, PlayStation or Switch Pro), or just use the keyboard

## 1. Wire the motors

Plug the three wheel motors into the board's **Motor L**, **Motor R** and
**Motor 4** ports (not Motor 3), like this. You are looking **down on the
robot from above, with the dribbler (front) pointing toward you**:

```
               Motor 4              <- back
                  ^
                /   \
              /       \
            /           \
     Motor L ___________ Motor R    <- front (dribbler)

                 YOU
```

The dribbler plugs into the pin set by `DRIBBLER_PIN` in `firmware/robot.py`
(pin 16).

## 2. Set up the laptop (once)

Open a terminal in this folder and run:

```sh
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r laptop/requirements.txt
```

Each time you open a new terminal, run the `activate` line again before the
commands below.

## 3. Flash the robot

1. **Plug the XRP into the laptop with USB** and turn its power switch on.
2. **Update the XRP's software (new robots, or once per term).** Open
   [xrpcode.wpi.edu](https://xrpcode.wpi.edu) in Chrome and click **Connect**.
   If it offers to update MicroPython or XRPLib, accept and wait for it to
   finish. Then **close the tab**: while it is open, the laptop cannot talk to
   the robot over USB.
3. **Name the robot.** Copy the settings file:

   ```sh
   cp firmware/config.example.py firmware/config.py    # Windows: copy firmware\config.example.py firmware\config.py
   ```

   Open `firmware/config.py` and set `ROBOT_NAME` to something unique, like
   `XRP-07`. Only the first 8 characters count, so keep it short.
4. **Upload the code:**

   ```sh
   mpremote cp firmware/lib/pestolink.py :lib/pestolink.py
   mpremote cp firmware/config.py firmware/mixing.py firmware/robot.py firmware/main.py :
   mpremote reset
   ```

   The robot now runs the drive code every time it is switched on.

Run these upload commands again whenever you change a file in `firmware/`.

## 4. Check the motors

**Lift the robot so no wheel touches anything**, then run:

```sh
mpremote run firmware/motor_test.py + reset
```

It spins one wheel at a time for 2 seconds: Motor L, then Motor R, then
Motor 4. For each one:

1. **Is it the right wheel?** It must match the diagram above. If not, swap the
   motor cables on the board.
2. **Is it spinning the right way?** Stand facing that wheel from outside the
   robot. It must spin **clockwise**. If it spins counter-clockwise, open
   `firmware/mixing.py` and flip that wheel's number in `WHEEL_SIGNS`
   (`1` becomes `-1`, `-1` becomes `1`). The order is Motor L, Motor R, Motor 4.
   Then run the upload commands again (step 3) and repeat this test.

When all three wheels are right and spin clockwise, the robot is ready.

## 5. Pair the gamepad (once per laptop)

Skip this if you use the keyboard, or plug the gamepad in with a USB-C cable.

For the **EasySMX X15** over Bluetooth:

1. On the laptop, open the Bluetooth settings (macOS: System Settings >
   Bluetooth; Windows: Settings > Bluetooth & devices > Add device > Bluetooth).
2. With the controller **off**, hold **X + HOME** together for about 3 seconds,
   until lights LED1 and LED4 flash.
3. In the laptop's Bluetooth list, pick **Xbox Wireless Controller** (or
   **EasySMX X15**) and pair.
4. LED1 and LED4 stay lit once it is connected.

After that, switching the controller on with HOME reconnects it. The X15 can
also use its 2.4 GHz USB dongle instead of Bluetooth. Other gamepads: pair them
the same way through the laptop's Bluetooth settings.

## 6. Drive

Switch the robot on (USB cable not needed) and the gamepad on, then on the laptop:

```sh
python -m laptop XRP-07              # with a gamepad (use your robot's name)
python -m laptop XRP-07 --keyboard   # with the keyboard
```

It finds the robot over Bluetooth and connects; this can take a few seconds.
Press **Ctrl+C** in the terminal (or **Esc** in the keyboard window) to stop.

**Gamepad**

| Input | Action |
|-------|--------|
| Left stick | Drive in any direction (up = forward) |
| Right stick left/right | Turn |
| A button (Cross on PlayStation) | Dribbler on while held |

With several gamepads plugged in, pick one with `--gamepad 1` (second),
`--gamepad 2` (third) and so on.

**Keyboard** (keys only work while the small window is selected)

| Key | Action |
|-----|--------|
| W / S (or arrow keys) | Forward / backward |
| A / D | Slide left / right |
| Q / E | Turn left / right |
| Space | Dribbler on while held |
| 1 / 2 / 3 | Speed: slow / medium / fast |
| Esc | Quit |

For safety, the robot stops by itself if the laptop disconnects or stops sending
for 0.3 seconds.

## 7. Several robots, the simulator, the web app

**Several robots from one laptop.** List them all. Each one gets its own
gamepad, in order (the first robot gets the first gamepad, or the one
`--gamepad N` picks). With `--keyboard` the keys drive one robot at a time:
**Tab** switches to the next, and the others stay stopped.

```sh
python -m laptop XRP-01 XRP-02              # two gamepads, one per robot
python -m laptop XRP-01 XRP-02 --keyboard   # one keyboard, Tab switches robot
```

The laptop connects to them one at a time, so a robot that is off delays the
others by up to 10 seconds per attempt.

**Let the simulator drive (closed loop through the camera).** With the camera
tracker running and the sim mirroring it (`run_pipeline.py --vision
tcp://localhost:5556`, see the repo's main README), a robot can take the
commands the sim computes for its sim robot: strategy, or a click in the sim
window.

```sh
python -m laptop XRP-01 --sim 0          # XRP-01 follows sim robot 0
python -m laptop XRP-01 XRP-02 --sim 0,1
```

Give the robot's tag that sim robot (`--tag-map TAG:0` on the sim), and make the
tag's heading point at the dribbler (the tracker's `--heading-offset`). The
robot **only moves while the camera sees it**, and stops when the sim stops.

**From the browser.** The web app's **Robots** page (`uv run robocup-web`, then
Robots) does all of this with buttons: add robots, drive each one with the
keyboard or a gamepad, or have it follow a sim robot. Only one program can hold
a robot's Bluetooth link: quit `python -m laptop` before using the page, and
the other way round.

## Troubleshooting

- **`python -m laptop` says "not found nearby":** check the robot is switched on
  and the name matches `ROBOT_NAME` in `firmware/config.py`. Run
  `python -m laptop --scan` to list what the laptop can see.
- **Robot switches off when you unplug USB:** the batteries are off or flat.
  The robot can drive on USB power alone (handy for a first test on the desk),
  but needs batteries to drive untethered.
- **Wheels hum but the robot only moves at full stick:** low power. Charge the
  batteries, or raise `MAX_EFFORT` (below).
- **`mpremote` says the port is busy or cannot connect:** close the XRP editor
  tab (and any other program using the robot), then try again.
- **"Gamepad 0 not found":** switch the gamepad on with its HOME button and
  check it shows as connected in the laptop's Bluetooth settings. If it does
  not, pair it again ([step 5](#5-pair-the-gamepad-once-per-laptop)).
- **Robot drives the wrong way or spins when it should go straight:** redo
  [step 4](#4-check-the-motors).
- **W / S drive it backward but A / D and Q / E are right:** Motor L and
  Motor R are plugged into each other's ports. Swap the two cables, or set
  `REVERSE_FORWARD = True` in `firmware/config.py` and upload `config.py` and
  `robot.py` again.
- **Too fast or too slow:** change `MAX_EFFORT` in `firmware/config.py`
  (0 to 1, starts at 0.5) and upload again.

## For developers

| Path | Runs on | Purpose |
|------|---------|---------|
| `firmware/robot.py` | XRP | Receives commands over Bluetooth, drives the wheels and dribbler, stops on timeout |
| `firmware/mixing.py` | XRP | Wheel positions, `WHEEL_SIGNS`, and the omniwheel maths |
| `firmware/config.example.py` | XRP | Template for the per-robot `config.py` (git-ignored) |
| `firmware/main.py` | XRP | Starts `robot.py` at power-up |
| `firmware/motor_test.py` | XRP | Spins each wheel to check wiring |
| `firmware/lib/pestolink.py` | XRP | Bluetooth receiver from [PestoLink-MicroPython](https://github.com/AlfredoSystems/PestoLink-MicroPython) (MIT) |
| `laptop/` | Laptop | Reads the gamepad/keyboard and sends commands to the robot |
| `laptop/fleet.py` | Laptop | Links to several robots, callable from any thread; stops a robot whose commands stop (used by the web app) |
| `laptop/sim_follow.py` | Laptop | Reads the sim's robot_node commands (port 9094) for `--sim` and the web app |
| `tests/` | Laptop | Unit tests, no hardware needed |

The laptop sends PestoLink packets (`laptop/protocol.py`), so the robot could
also be driven from the [pestol.ink](https://pestol.ink) browser app.

Run the tests with `python -m unittest discover -t . -s tests`.

To use a different wheel layout, change `WHEEL_ANGLES_DEG` in
`firmware/mixing.py` (each wheel's position in degrees, counter-clockwise from
straight ahead, seen from above) and redo step 4.
