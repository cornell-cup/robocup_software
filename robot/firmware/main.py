# Runs ON the XRP at power-up: start robot.py.
import robot

try:
    robot.main()
finally:
    robot.stop_all()
