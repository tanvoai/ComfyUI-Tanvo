"""A stand-in for the Tanvo API that returns real PNG, MP4 and audio files, for the end-to-end test."""
from __future__ import annotations

import io
import json
import threading
import uuid
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

CATALOG = json.loads((Path(__file__).resolve().parent.parent / "catalog.json").read_text())
LOOK_MODEL = {"image": "nano-banana-2", "video": "seedance-2-5", "music": "suno-v6"}


def png(color=(200, 80, 40), size=(96, 64)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def mp4() -> bytes:
    import av
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as c:
        s = c.add_stream("mpeg4", rate=8)
        s.width, s.height, s.pix_fmt = 64, 64, "yuv420p"
        for i in range(8):
            frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), i * 30, dtype=np.uint8), format="rgb24")
            for p in s.encode(frame):
                c.mux(p)
        for p in s.encode():
            c.mux(p)
    return buf.getvalue()


def m4a(seconds: float = 1.0, rate: int = 44100) -> bytes:
    import av
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as c:
        s = c.add_stream("aac", rate=rate, layout="stereo")
        n = int(seconds * rate)
        t = np.linspace(0, seconds, n, endpoint=False)
        wave = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        data = np.stack([wave, wave])
        for start in range(0, n, 1024):
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data[:, start:start + 1024]), format="fltp", layout="stereo")
            frame.sample_rate = rate
            frame.pts = start
            frame.time_base = Fraction(1, rate)
            for p in s.encode(frame):
                c.mux(p)
        for p in s.encode():
            c.mux(p)
    return buf.getvalue()


class State:
    def __init__(self):
        self.submissions: List[Dict[str, Any]] = []
        self.headers: List[Dict[str, str]] = []
        self.uploads: Dict[str, bytes] = {}
        self.upload_mimes: Dict[str, str] = {}
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.files = {"out.png": ("image/png", png()), "out-2.png": ("image/png", png((10, 120, 200), (64, 64))),
                      "out.mp4": ("video/mp4", mp4()), "take.m4a": ("audio/mp4", m4a())}


def serve() -> tuple[ThreadingHTTPServer, State]:
    state = State()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, status: int, payload: Any):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _raw(self, mime: str, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        @property
        def base(self) -> str:
            return f"http://127.0.0.1:{self.server.server_address[1]}"

        def do_GET(self):
            p = self.path
            if p.startswith("/api/v1/models"):
                return self._json(200, {"models": [dict(m, prompt_max_chars=4000) for m in CATALOG["models"]]})
            if p.startswith("/api/v1/apps"):
                apps = [dict(a, description="", category=None, model=LOOK_MODEL[a["kind"]], url="",
                             looks=[dict(l, prompt=f"Look {l['id']}.", model=LOOK_MODEL[a["kind"]], options={}, url="") for l in a["looks"]])
                        for a in CATALOG["apps"]]
                return self._json(200, {"apps": apps})
            if p.startswith("/api/v1/generations/"):
                run = state.runs[p.rsplit("/", 1)[1]]
                run["polls"] += 1
                g = dict(run["g"])
                if run["polls"] >= 2:
                    g["status"] = "SUCCEEDED"
                    g["outputs"] = run["outputs"]
                return self._json(200, {"generation": g})
            if p.startswith("/files/"):
                name = p[len("/files/"):]
                if name in state.files:
                    return self._raw(*state.files[name])
                return self._raw("image/png", state.uploads[name])
            self._json(404, {"error": "not_found", "message": p})

        def do_POST(self):
            body = json.loads(self._body() or b"{}")
            state.headers.append(dict(self.headers))
            if self.path == "/api/v1/uploads":
                uid = uuid.uuid4().hex
                state.upload_mimes[uid] = body.get("mime", "")
                return self._json(200, {"upload_url": f"{self.base}/put/{uid}", "url": f"{self.base}/files/{uid}"})
            if self.path == "/api/v1/generations":
                state.submissions.append(body)
                paid = next((m for m in CATALOG["models"] if m["id"] == body["model"]), {}).get("free_tier") is not True
                if paid and not self.headers.get("Authorization"):
                    return self._json(402, {"error": "needs_api_key", "message": "This model needs an API key."})
                gid = f"g{len(state.runs) + 1}"
                kind = body["kind"]
                outputs = {
                    "image": [{"url": f"{self.base}/files/out.png", "mime": "image/png"}, {"url": f"{self.base}/files/out-2.png", "mime": "image/png"}],
                    "video": [{"url": f"{self.base}/files/out.mp4", "mime": "video/mp4"}],
                    "music": [{"url": f"{self.base}/files/take.m4a", "mime": "audio/mp4", "title": "Take A", "lyrics": "la la"},
                              {"url": f"{self.base}/files/take.m4a", "mime": "audio/mp4", "title": "Take B", "lyrics": "na na"}],
                }[kind]
                g = {"id": gid, "kind": kind, "model": body["model"], "prompt": body["prompt"], "options": body.get("options") or {},
                     "cost": 0, "status": "PROCESSING", "error": None, "createdAt": 0, "outputs": []}
                state.runs[gid] = {"g": g, "polls": 0, "outputs": outputs}
                return self._json(201, {"generation": g})
            self._json(404, {"error": "not_found", "message": self.path})

        def do_PUT(self):
            data = self._body()
            state.uploads[self.path.rsplit("/", 1)[1]] = data
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state
