"""Local HTTP server: serves the web UI, the JSON API (token protected) and a server-sent-events stream.

Only 127.0.0.1 is bound. API calls need the per-run token (header X-Arena-Token or ?t=), and the Host header must be the loopback address,
so other web pages (CSRF / DNS rebinding) cannot start engines through this server.
"""
import json
import mimetypes
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .api import COMPILED, ApiError, AppState, Resp

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


class Req:
    def __init__(self, body, q, m):
        self.body, self.q, self.m = body, q, m


class ArenaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, state: AppState):
        self.state = state
        super().__init__(addr, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "KestrelArena/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # -- plumbing ---------------------------------------------------------------------------------------------------
    @property
    def st(self) -> AppState:
        return self.server.state

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def _send(self, status, data, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_HEAD(self):
        self._dispatch("GET")

    def _dispatch(self, method):
        try:
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            u = urlparse(self.path)
            path = u.path
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            token = self.headers.get("X-Arena-Token") or q.get("t")
            if token != self.st.token:
                return self._json({"error": "forbidden"}, 403)
            if path == "/api/events":
                return self._events()
            for m, rx, fn in COMPILED:
                if m != method:
                    continue
                mt = rx.match(path)
                if not mt:
                    continue
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 16 * 1024 * 1024:
                        return self._json({"error": "request too large"}, 413)
                    raw = self.rfile.read(n) if n else b""
                    if raw:
                        try:
                            body = json.loads(raw.decode("utf-8"))
                        except ValueError:
                            return self._json({"error": "bad JSON"}, 400)
                try:
                    out = fn(self.st, Req(body, q, mt.groupdict()))
                except ApiError as e:
                    return self._json({"error": str(e)}, e.status)
                if isinstance(out, Resp):
                    hdr = {"Content-Disposition": 'attachment; filename="%s"' % out.filename} if out.filename else {}
                    return self._send(200, out.data, out.ctype, hdr)
                return self._json(out)
            self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:                                                  # never let a handler kill the server
            try:
                import traceback
                traceback.print_exc()
                self._json({"error": "internal error: %s" % e}, 500)
            except Exception:
                pass

    # -- static files -----------------------------------------------------------------------------------------------
    def _static(self, path):
        web = os.path.join(self.st.res_dir, "web")
        if path in ("/", "/index.html"):
            with open(os.path.join(web, "index.html"), "rb") as f:
                html = f.read().replace(b"__ARENA_TOKEN__", self.st.token.encode("ascii"))
            return self._send(200, html, "text/html; charset=utf-8")
        rel = unquote(path.lstrip("/"))
        if rel.startswith("web/"):
            rel = rel[4:]
        full = os.path.normpath(os.path.join(web, rel))
        if not full.startswith(os.path.normpath(web) + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8" if "charset" not in ctype else ""
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    # -- server-sent events -----------------------------------------------------------------------------------------
    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        q = self.st.bus.subscribe()
        try:
            self.wfile.write(b"retry: 1500\ndata: {\"type\":\"hello\"}\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=10)
                    data = json.dumps(ev, separators=(",", ":")).encode("utf-8")
                    self.wfile.write(b"data: " + data + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.st.bus.unsubscribe(q)


def start_server(state: AppState, port=0):
    srv = ArenaServer(("127.0.0.1", port), state)
    state.port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    t.start()
    return srv
