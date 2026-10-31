"""The web app: one server for the browser pages and for scripts.

    uv run robocup-web                       # http://localhost:5801, opens a browser
    uv run robocup-web --with-photonvision   # and start PhotonVision too
    uv run robocup-web --host 0.0.0.0        # reachable from other machines too

Everything the pages do goes through the HTTP/WebSocket API under /api/v1, so
a script can do the same; see app/README.md. The pages themselves are
a React app (app/web), built into static/ and served from here.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ball import VIEWS, BallCalibration
from .calib import FieldCalibration
from .photonvision import ENV_DIR, PhotonVisionProcess
from .procs import CommandError
from .robots import RealRobots
from .sim import SimBridge, SimProcess
from .tracking import (
    VIDEO_PORT,
    TrackerProcess,
    TrackingFeed,
    check_vision_source,
    field_poses,
    recordings,
)

STATIC = Path(__file__).parent / "static"
DEFAULT_PORT = 5801  # next to PhotonVision's UI on 5800
#: Camera stream to the calibration page: enough to aim by, light on the CPU.
CALIB_STREAM_FPS = 15
#: Tracker output to the Tracking page's field: about the tracker's own rate.
TRACKING_WS_HZ = 30
#: Robot link status to the Robots page.
ROBOTS_WS_HZ = 5


def _mjpeg_part(jpeg: bytes) -> bytes:
    """One frame of a multipart/x-mixed-replace MJPEG stream."""
    return (b"--frame\r\nContent-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n")


class App:
    """What the routes share: one of each, for the server's lifetime."""

    def __init__(self, photonvision_dir: Path | None = None) -> None:
        self.bridge = SimBridge()
        self.sim = SimProcess()
        self.calib = FieldCalibration()
        self.photon = PhotonVisionProcess(photonvision_dir)
        self.ball = BallCalibration()
        self.tracker = TrackerProcess()
        self.feed = TrackingFeed()
        self.robots = RealRobots()


def create_app(photonvision_dir: Path | None = None, start_photonvision: bool = False) -> FastAPI:
    state = App(photonvision_dir)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.bridge.start()
        state.feed.start()
        pump = asyncio.create_task(state.bridge.pump())
        if start_photonvision:
            try:
                state.photon.start()
                print(f"starting PhotonVision from {state.photon.folder}")
            except CommandError as e:
                print(f"PhotonVision not started: {e}")
        try:
            yield
        finally:
            pump.cancel()
            # the process stops only end what this server started
            state.sim.stop()
            await asyncio.to_thread(state.tracker.stop)
            await asyncio.to_thread(state.calib.disconnect)
            await asyncio.to_thread(state.ball.disconnect)
            await asyncio.to_thread(state.photon.stop)
            await asyncio.to_thread(state.robots.close)
            state.bridge.close()
            state.feed.close()

    app = FastAPI(title="RoboCup Control", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.exception_handler(CommandError)
    async def _command_error(_req: Request, exc: CommandError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    # -- general ------------------------------------------------------------

    @app.get("/api/v1/info")
    def info() -> dict[str, Any]:
        return {"name": "RoboCup Control", "version": __version__, "api": 1}

    # -- simulator ----------------------------------------------------------

    @app.get("/api/v1/sim/status")
    def sim_status() -> dict[str, Any]:
        snap = state.bridge.snapshot()
        return {"process": state.sim.status(), "up": snap["up"], "controls": snap["controls"],
                "manual_error": snap["manual_error"]}

    @app.get("/api/v1/sim/world")
    def sim_world() -> dict[str, Any]:
        return state.bridge.snapshot()

    @app.post("/api/v1/sim/start")
    def sim_start(opts: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        opts = dict(opts or {})
        opts["vision"] = check_vision_source(str(opts.get("vision") or "").strip())
        state.sim.start(opts)
        return state.sim.status()

    @app.post("/api/v1/sim/stop")
    def sim_stop() -> dict[str, Any]:
        state.sim.stop()
        return state.sim.status()

    @app.get("/api/v1/sim/log")
    def sim_log(after: int = 0) -> dict[str, Any]:
        lines = state.sim.log_since(after)
        return {"lines": [{"n": n, "text": s} for n, s in lines]}

    # async, like the socket below and the pump: the manual-command ZMQ socket
    # is only ever used from the event loop's thread (ZMQ sockets are not
    # thread-safe, and plain `def` routes run on a worker pool).
    @app.post("/api/v1/sim/command")
    async def sim_command(cmd: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.bridge.command(cmd)
        return {"ok": True}

    @app.websocket("/api/v1/sim/ws")
    async def sim_ws(ws: WebSocket) -> None:
        """World state out, PUSH_HZ times a second; commands in, same JSON as
        POST /sim/command. A robot driven over this socket is stopped when the
        socket closes."""
        await ws.accept()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        state.bridge.clients.add(q)
        driven: set[int] = set()

        async def send() -> None:
            await ws.send_text(json.dumps(state.bridge.snapshot()))
            while True:
                await ws.send_text(await q.get())

        sender = asyncio.create_task(send())
        try:
            while True:
                cmd = json.loads(await ws.receive_text())
                try:
                    state.bridge.command(cmd)
                    if cmd.get("type") == "drive":
                        driven.add(int(cmd.get("robot", 0)))
                except CommandError as e:
                    await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            sender.cancel()
            state.bridge.clients.discard(q)
            state.bridge.stop_driving(driven)

    # -- real robots --------------------------------------------------------

    @app.get("/api/v1/robots/status")
    def robots_status() -> dict[str, Any]:
        return state.robots.status()

    @app.post("/api/v1/robots/add")
    def robots_add(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.robots.add(str(opts.get("name", "")))
        return state.robots.status()

    @app.post("/api/v1/robots/remove")
    def robots_remove(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.robots.remove(str(opts.get("name", "")))
        return state.robots.status()

    @app.post("/api/v1/robots/follow")
    def robots_follow(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        rid = opts.get("sim_robot")
        try:
            rid = None if rid is None else int(rid)
        except (TypeError, ValueError):
            raise CommandError("sim_robot must be a robot number, or null to drive by hand") from None
        state.robots.follow(str(opts.get("name", "")), rid)
        return state.robots.status()

    @app.post("/api/v1/robots/scan")
    async def robots_scan() -> dict[str, Any]:
        return {"devices": await asyncio.to_thread(state.robots.scan)}

    @app.websocket("/api/v1/robots/ws")
    async def robots_ws(ws: WebSocket) -> None:
        """Robot status out, ROBOTS_WS_HZ times a second; drive commands in
        ({"type": "drive", "name", "forward", "left", "turn", "dribble"}).
        A robot driven over this socket is stopped when the socket closes."""
        await ws.accept()
        driven: set[str] = set()

        async def send() -> None:
            while True:
                await ws.send_text(json.dumps({"type": "status", **state.robots.status()}))
                await asyncio.sleep(1.0 / ROBOTS_WS_HZ)

        sender = asyncio.create_task(send())
        try:
            while True:
                cmd = json.loads(await ws.receive_text())
                try:
                    if cmd.get("type") == "drive":
                        driven.add(state.robots.drive(cmd))
                    else:
                        raise CommandError(f"unknown command type {cmd.get('type')!r}")
                except CommandError as e:
                    await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
        except (WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            sender.cancel()
            state.robots.stop(driven)

    # -- PhotonVision -------------------------------------------------------

    @app.get("/api/v1/photonvision/status")
    def photon_status() -> dict[str, Any]:
        return state.photon.status()

    @app.post("/api/v1/photonvision/start")
    def photon_start() -> dict[str, Any]:
        state.photon.start()
        return state.photon.status()

    @app.post("/api/v1/photonvision/install")
    def photon_install() -> dict[str, Any]:
        state.photon.install()
        return state.photon.status()

    @app.post("/api/v1/photonvision/stop")
    async def photon_stop() -> dict[str, Any]:
        # The calibration page's connection goes first: it would only time out.
        await asyncio.to_thread(state.calib.disconnect)
        await asyncio.to_thread(state.photon.stop)
        return state.photon.status()

    @app.get("/api/v1/photonvision/log")
    def photon_log(after: int = 0) -> dict[str, Any]:
        return {"lines": [{"n": n, "text": s} for n, s in state.photon.log_since(after)]}

    # -- field calibration --------------------------------------------------

    @app.get("/api/v1/calib/status")
    def calib_status() -> dict[str, Any]:
        return state.calib.status()

    @app.post("/api/v1/calib/connect")
    def calib_connect(opts: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        if state.tracker.running:
            raise CommandError("the tracker is using PhotonVision's connection -- stop tracking "
                               "first (Tracking page), then calibrate")
        state.calib.connect(opts or {})
        return state.calib.status()

    @app.post("/api/v1/calib/disconnect")
    async def calib_disconnect() -> dict[str, Any]:
        await asyncio.to_thread(state.calib.disconnect)
        return state.calib.status()

    @app.post("/api/v1/calib/capture/start")
    def calib_capture(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.calib.start_capture(opts)
        return state.calib.status()

    @app.post("/api/v1/calib/capture/stop")
    def calib_capture_stop() -> dict[str, Any]:
        state.calib.stop_capture()
        return state.calib.status()

    @app.post("/api/v1/calib/corner")
    def calib_corner(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.calib.corner(str(opts.get("action")))
        return state.calib.status()

    @app.post("/api/v1/calib/solve")
    def calib_solve() -> dict[str, Any]:
        state.calib.solve()
        return state.calib.status()

    @app.post("/api/v1/calib/load-saved")
    def calib_load_saved() -> dict[str, Any]:
        state.calib.load_saved()
        return state.calib.status()

    @app.post("/api/v1/calib/origin")
    def calib_origin(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.calib.set_origin(opts)
        return state.calib.status()

    @app.post("/api/v1/calib/save")
    def calib_save() -> dict[str, Any]:
        path = state.calib.save()
        return {**state.calib.status(), "saved_to": str(path)}

    @app.get("/api/v1/calib/stream.mjpg")
    async def calib_stream(request: Request) -> StreamingResponse:
        async def frames():
            while not await request.is_disconnected():
                jpeg = await asyncio.to_thread(state.calib.frame_jpeg)
                if jpeg is None:
                    return
                yield _mjpeg_part(jpeg)
                await asyncio.sleep(1.0 / CALIB_STREAM_FPS)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    # -- ball colour profiles -----------------------------------------------

    @app.get("/api/v1/ball/status")
    def ball_status() -> dict[str, Any]:
        return state.ball.status()

    @app.post("/api/v1/ball/connect")
    def ball_connect(opts: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
        state.ball.connect((opts or {}).get("stream"))
        return state.ball.status()

    @app.post("/api/v1/ball/disconnect")
    def ball_disconnect() -> dict[str, Any]:
        state.ball.disconnect()
        return state.ball.status()

    @app.post("/api/v1/ball/new")
    def ball_new(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.ball.new(opts)
        return state.ball.status()

    @app.post("/api/v1/ball/load")
    def ball_load(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.ball.load(str(opts.get("name", "")))
        return state.ball.status()

    @app.post("/api/v1/ball/sample")
    def ball_sample(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.ball.sample(opts)
        return state.ball.status()

    @app.post("/api/v1/ball/clear")
    def ball_clear() -> dict[str, Any]:
        state.ball.clear_sample()
        return state.ball.status()

    @app.post("/api/v1/ball/tune")
    def ball_tune(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        state.ball.tune(opts)
        return state.ball.status()

    @app.post("/api/v1/ball/save")
    def ball_save() -> dict[str, Any]:
        path = state.ball.save()
        return {**state.ball.status(), "saved_to": str(path)}

    @app.get("/api/v1/ball/scene-hues")
    def ball_scene_hues() -> dict[str, Any]:
        return state.ball.scene_hues()

    @app.get("/api/v1/ball/stream.mjpg")
    async def ball_stream(request: Request, view: str = "overlay") -> StreamingResponse:
        if view not in VIEWS:
            raise CommandError(f"view must be one of {', '.join(VIEWS)}")

        async def frames():
            while not await request.is_disconnected():
                jpeg = await asyncio.to_thread(state.ball.render, view)
                if jpeg is None:
                    return
                yield _mjpeg_part(jpeg)
                await asyncio.sleep(1.0 / CALIB_STREAM_FPS)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    # -- live tracking ------------------------------------------------------

    @app.get("/api/v1/tracking/status")
    def tracking_status() -> dict[str, Any]:
        return {"process": state.tracker.status(), "live": state.feed.snapshot(),
                "field_poses": field_poses()}

    @app.websocket("/api/v1/tracking/ws")
    async def tracking_ws(ws: WebSocket) -> None:
        """The tracker's output as it comes, at most TRACKING_WS_HZ times a
        second; the same {"up", "age_s", "n", "doc"} as status's "live"."""
        await ws.accept()
        last_n, last_up, last_sent = -1, None, 0.0
        try:
            while True:
                snap = state.feed.snapshot()
                now = time.monotonic()
                # new output, or just "it stopped", or a keep-alive
                if snap["n"] != last_n or snap["up"] != last_up or now - last_sent > 1.0:
                    await ws.send_text(json.dumps(snap))
                    last_n, last_up, last_sent = snap["n"], snap["up"], now
                await asyncio.sleep(1.0 / TRACKING_WS_HZ)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.post("/api/v1/tracking/control")
    async def tracking_control(cmd: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        await asyncio.to_thread(state.tracker.control, cmd)
        return {"ok": True}

    @app.post("/api/v1/tracking/start")
    async def tracking_start(opts: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        # The tracker runs PhotonVision's NetworkTables server itself; the
        # calibration page's connection would hold it.
        await asyncio.to_thread(state.calib.disconnect)
        await asyncio.to_thread(state.tracker.start, opts)
        return tracking_status()

    @app.post("/api/v1/tracking/stop")
    async def tracking_stop() -> dict[str, Any]:
        await asyncio.to_thread(state.tracker.stop)
        return tracking_status()

    @app.get("/api/v1/tracking/log")
    def tracking_log(after: int = 0) -> dict[str, Any]:
        return {"lines": [{"n": n, "text": s} for n, s in state.tracker.log_since(after)]}

    @app.get("/api/v1/tracking/stream.mjpg")
    async def tracking_stream(request: Request) -> StreamingResponse:
        """The tracker's own drawn view (track-combined --mjpeg), passed through."""
        try:
            upstream = await asyncio.to_thread(
                urllib.request.urlopen, f"http://127.0.0.1:{VIDEO_PORT}/stream.mjpg", timeout=5)
        except OSError:
            raise CommandError("the tracker isn't serving its view (is it running?)") from None

        async def relay():
            try:
                while not await request.is_disconnected():
                    chunk = await asyncio.to_thread(upstream.read1, 1 << 16)
                    if not chunk:
                        return
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(relay(), media_type=upstream.headers.get(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"),
            headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/recordings")
    def list_recordings() -> dict[str, Any]:
        return {"recordings": recordings()}

    # -- the pages ----------------------------------------------------------

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def page(path: str) -> FileResponse:
            f = (STATIC / path).resolve()
            if path and f.is_file() and STATIC.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(STATIC / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_build() -> JSONResponse:
            return JSONResponse({"error": "the pages are not built: cd app/web && "
                                          "npm install && npm run build"}, status_code=503)

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="RoboCup web app: simulator and field calibration")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default only this machine; 0.0.0.0 for the "
                         "network -- there is no login yet, so anyone who can reach it can "
                         "drive the robots)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--with-photonvision", action="store_true",
                    help="start PhotonVision too (stopped again when this exits)")
    ap.add_argument("--photonvision-dir", type=Path, default=None, metavar="DIR",
                    help=f"folder with PhotonVision's jar and a Java for it (default ${ENV_DIR}, "
                         f"else a 'photonvision' folder next to the repo)")
    args = ap.parse_args()

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"RoboCup Control on {url}  (API docs: {url}/api/docs)")
    if args.host == "0.0.0.0":
        print("warning: listening on the network with no login -- anyone who can reach this "
              "machine can drive the robots")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(args.photonvision_dir, args.with_photonvision),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
