"""A tiny MJPEG server: publish() a picture, browsers and cv2.VideoCapture read
the latest one from /stream.mjpg."""

from __future__ import annotations

import http.server
import socketserver
import threading

import cv2
import numpy as np


class MjpegServer:
    """Pictures as a multipart MJPEG stream at http://<host>:<port>/stream.mjpg,
    the way PhotonVision serves its camera: photon-sim's stand-in camera, and
    track-combined's drawn view (--mjpeg) for the web app."""

    def __init__(self, port: int) -> None:
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?")[0] != "/stream.mjpg":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                seen = -1
                try:
                    while True:
                        with outer._cond:
                            outer._cond.wait_for(lambda s=seen: outer._seq != s, timeout=1.0)
                            jpeg, seen = outer._jpeg, outer._seq
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                                         + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *_args) -> None:
                pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._httpd = Server(("", port), Handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def publish(self, bgr: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            with self._cond:
                self._jpeg, self._seq = buf.tobytes(), self._seq + 1
                self._cond.notify_all()

    def stop(self) -> None:
        self._httpd.shutdown()
