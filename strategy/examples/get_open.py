"""Example team file: a supporter that gets open for a pass.

The other slots (who plays which role, the attacker, the defender) stay
built-in. Play it against the frozen team v1, from the repo root:

    uv run python -m strategy.eval.match --a strategy/examples/get_open.py

or live in the sim (save the file while it runs and it reloads):

    cd sim
    uv run python run_pipeline.py --blue ../strategy/examples/get_open.py
"""

from strategy.api import Command, Me, World


def supporter(me: Me, world: World) -> Command:
    ball = world.ball.pos
    length, width = world.field_size

    # A spot 40% of the way from the ball to their goal...
    spot = ball + (world.their_goal - ball) * 0.4
    # ...shifted toward the side of the field with more room.
    side = 1.0 if ball[1] < width / 2 else -1.0
    spot = spot + (0.0, side * width / 6)

    # If an opponent stands in the way of a pass from the ball to that spot,
    # try the same spot on the other side.
    clear = world.lane_clear(ball, spot)
    # Draw the pass lane we checked, green if it's open, red if not: run it
    # and watch the field on the Code page.
    world.draw.line(ball, spot, color="green" if clear else "red")
    if not clear:
        spot = spot - (0.0, 2 * side * width / 6)

    world.draw.point(spot)
    world.show("lane clear", clear)
    return me.go_to(spot, face=ball)
