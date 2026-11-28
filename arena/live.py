"""Live broadcast of a running tournament (TCEC / CCC style): a read-only feed for the Live page.

Who does what
  game threads   store what they know anyway - the moves of a game, its clocks and the newest UCI info line (Tournament.livex) - with a few list / dict assignments (the info lines
                 only while somebody watches: GATE.watched).  They never wait for this module and never write to an engine.
  hub thread     exists only while somebody is connected: at most four times a second (TICK) it turns the changes into small JSON messages and puts them in the viewers' queues.
  http threads   one per viewer (server-sent events); a viewer that cannot keep up is cut off (it reconnects and gets the whole state again), it never holds the hub.
The *share* server (optional, off by default) serves the same GET data on another port on all interfaces and nothing else: no control endpoints, no token, no engine paths.
The page itself (web/live.html + js/live.js) reads either this stream or a snapshot (``window.__BROADCAST__`` in an exported file, or ``?src=<url of a snapshot json>``).
"""
import base64
import json
import mimetypes
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import chess

from .game import _fmt_pv, tc_label
from .uci import score_to_cp

TICK = 0.25                     # seconds between two messages to the viewers: never more than 4 a second, however fast the games are
PING = 10.0                     # keep-alive comment on an idle stream
MAX_VIEWERS = 40
STRUCT_EVERY = 1.0              # the bracket is sent at most once a second
META_EVERY = 5.0                # ... the clock of the tournament (elapsed) at least this often
SHARE_PORT = 8780               # first port tried by the share server
REC_KEYS = ("white", "black", "round", "result", "reason", "start_fen", "uci", "san", "cp", "depth", "ms", "book", "plies", "dur", "ended", "arm")
SHARE_STATIC = {"/live.css": "live.css", "/js/live_util.js": "js/live_util.js", "/js/board.js": "js/board.js", "/js/elo.js": "js/elo.js", "/js/live.js": "js/live.js",
                "/assets/logo.svg": "assets/logo.svg", "/assets/icon.svg": "assets/icon.svg"}


class Gate:
    """Read by the game threads: is anybody looking at the live page?  (Nobody: the info lines of the engines are not even stored.)"""
    watched = False


GATE = Gate()


# ------------------------------------------------------------------------------------------------------------------ public views (a whitelist: no paths, no options)
def _public_params(cfg):
    out = {}
    for k, v in (cfg.get("params") or {}).items():
        if isinstance(v, (bool, int, float)) or v is None or (isinstance(v, str) and k in ("hero", "seeding")):
            out[k] = v
    return out


def _meta(t):
    cfg = t.cfg
    tc = cfg.get("tc") or {}
    return {"id": t.id, "name": cfg.get("name"), "format": cfg["format"], "status": t.status, "error": bool(t.error), "total": t.total, "done": len(t.games),
            "elapsed": round(t.elapsed(), 1), "tc": {k: v for k, v in tc.items() if isinstance(v, (bool, int, float, str))}, "tc_label": tc_label(tc),
            "params": _public_params(cfg), "concurrency": cfg.get("concurrency"), "created": cfg.get("created")}


def _engines(t):
    return [{"id": eid, "name": t.cfg["engines"][eid]["name"], "elo": t.cfg["engines"][eid].get("elo")} for eid in t.cfg["engine_ids"]]


def _structure(t):
    """The bracket / schedule structure; the tournament computes it at every finished game (Tournament._emit_state), it is only computed here for one that has not done so yet."""
    sc = getattr(t, "structure_cache", None)
    if sc is None or sc[0] != len(t.games):
        try:
            sc = (len(t.games), t.sched.structure(t._games_for_sched()))
        except Exception:
            sc = (len(t.games), None)
        t.structure_cache = sc
    return sc


def _think_view(lx, fr):
    th = lx.get("think")                                    # (ply, side, info line, counter) of the newest info line of the search that is running
    if not th or th[0] != fr["ply"]:
        return None
    _, side, inf, n = th
    cp = score_to_cp(inf)
    if cp is not None and side == chess.BLACK:
        cp = -cp                                            # white's point of view, like everything else on the page
    try:
        pv = _fmt_pv(chess.Board(fr["fen"]), inf.get("pv") or [], 10)
    except Exception:
        pv = ""
    return {"n": n, "side": "b" if side == chess.BLACK else "w", "depth": inf.get("depth"), "sd": inf.get("seldepth"), "cp": cp, "nodes": inf.get("nodes"),
            "nps": inf.get("nps"), "time": inf.get("time"), "pv": pv}


def _game_view(gid, live, lx, frm):
    """A running game as the page wants it: the moves from ply `frm` on (0 = the whole game with its names), clocks, the details of the last two moves and what the engine
    on move is thinking.  Returns (view, signature); the signature changes whenever there is something new to send."""
    fr = lx["frame"]
    ply, book = fr["ply"], lx["book"]
    rows = lx["h"][:max(0, ply - book)]                     # (uci, san, cp, depth, ms, nodes, nps, pv) of the moves the engines played; appended before the frame is replaced
    frm = max(0, min(frm, ply))
    uci, san, cp, depth, ms = [], [], [], [], []
    for i in range(frm, ply):
        if i < book:
            uci.append(lx["book_uci"][i]), san.append(lx["book_san"][i]), cp.append(None), depth.append(None), ms.append(None)
        else:
            r = rows[i - book]
            uci.append(r[0]), san.append(r[1]), cp.append(r[2]), depth.append(r[3]), ms.append(r[4])
    info = [[i, rows[i - book][2], rows[i - book][3], rows[i - book][5], rows[i - book][6], rows[i - book][4], rows[i - book][7]] for i in range(max(book, ply - 2), ply)]
    th = _think_view(lx, fr)
    v = {"gid": gid, "from": frm, "ply": ply, "uci": uci, "san": san, "cp": cp, "depth": depth, "ms": ms, "info": info, "clocks": fr["clocks"], "turn": fr["fen"].split()[1],
         "since": int((time.monotonic() - fr["t"]) * 1000), "check": fr["check"], "wait": bool(fr.get("wait")), "think": th}
    if frm == 0:
        v.update({"white": live["white"], "black": live["black"], "round": live["round"], "key": live.get("key"), "start_fen": lx["start_fen"], "book": book, "arm": lx["arm"]})
    return v, (ply, th["n"] if th else None, bool(fr.get("wait")))


def _read_rec(t, gid):
    off = t.offsets.get(gid)
    if off is None:
        return None
    try:
        with open(t._jsonl, "rb") as f:
            f.seek(off[0])
            rec = json.loads(f.read(off[1]).decode("utf-8"))
    except (OSError, ValueError):
        return None
    return {k: rec.get(k) for k in REC_KEYS}


def _dump(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


# ------------------------------------------------------------------------------------------------------------------ the hub
class LiveHub:
    def __init__(self, st):
        self.st = st
        self.lock = threading.RLock()
        self.subs = {}                      # queue -> "local" | "share"
        self.seq = 0
        self.tid = None
        self.sent = {}                      # gid -> signature of the last message about that game
        self.done_sent = 0
        self.struct_sent = (None, 0.0)
        self.meta_sent = (None, 0.0)
        self.thread = None
        self.share = None
        self._cache = (0.0, None, None)

    def cur(self):
        return self.st.tournaments.current

    # ----------------------------------------------------------------------------------------------------- full state / snapshot
    def state(self, t, track=False):
        """Everything the page needs to start (the stream sends this first).  track=True: the hub's own bookkeeping of what has been sent starts from this state."""
        if t is None:
            return {"t": "state", "seq": self.seq, "tournament": None}
        with t.lock:
            games = list(t.games)
            items = [(gid, dict(live), t.livex.get(gid)) for gid, live in t.active.items()]
        if track:
            self.sent = {}
        act = []
        for gid, live, lx in items:
            if lx is None:
                continue
            v, sig = _game_view(gid, live, lx, 0)
            act.append(v)
            if track:
                self.sent[gid] = sig
        sc = _structure(t)
        if track:
            self.tid, self.done_sent, self.struct_sent = t.id, len(games), (sc, time.monotonic())
        return {"t": "state", "seq": self.seq, "tournament": _meta(t), "engines": _engines(t), "games": games, "active": act, "structure": sc[1], "now": time.time()}

    def state_bytes(self):
        """The state as JSON for the polling endpoint; built at most once a second."""
        t = self.cur()
        key = (t.id if t else None, len(t.games) if t else 0, t.status if t else None)
        with self.lock:
            ts, k, data = self._cache
            now = time.monotonic()
            if data is None or k != key or now - ts >= 1.0:
                data = _dump(self.state(t))
                self._cache = (now, key, data)
            return data

    def record(self, gid):
        t = self.cur()
        return _read_rec(t, gid) if t else None

    def snapshot(self):
        """The whole broadcast in one object: the state plus the moves of every finished game (the page can run from it without a server)."""
        t = self.cur()
        s = self.state(t)
        if t is None:
            return s
        recs = {}
        try:
            with open(t._jsonl, "rb") as f:
                for line in f:
                    try:
                        r = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue                                   # (the line that is being written right now)
                    recs[r["gid"]] = {k: r.get(k) for k in REC_KEYS if k not in ("plies", "dur", "ended")}
        except OSError:
            pass
        s.update({"snapshot": True, "records": recs, "taken": time.strftime("%Y-%m-%d %H:%M:%S")})
        return s

    # ------------------------------------------------------------------------------------------------------------------ viewers
    def subscribe(self, tag="local"):
        with self.lock:
            if len(self.subs) >= MAX_VIEWERS:
                return None
            t = self.cur()
            self.seq += 1
            q = queue.Queue(maxsize=400)
            q.put_nowait(_dump(self.state(t, track=not self.subs)))        # the first viewer starts the bookkeeping, the others get the same data without disturbing it (every message is idempotent)
            self.subs[q] = tag
            GATE.watched = True
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name="live-hub", daemon=True)
                self.thread.start()
            return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.pop(q, None)
            GATE.watched = bool(self.subs)

    def _drop(self, q):
        self.subs.pop(q, None)
        GATE.watched = bool(self.subs)
        with q.mutex:
            q.queue.clear()
        try:
            q.put_nowait(None)                                             # the stream handler ends the connection
        except queue.Full:
            pass

    def close_tag(self, tag):
        with self.lock:
            for q, tg in list(self.subs.items()):
                if tg == tag:
                    self._drop(q)

    def viewers(self, tag=None):
        with self.lock:
            return sum(1 for tg in self.subs.values() if tag is None or tg == tag)

    def _broadcast(self, obj):
        self.seq += 1
        obj["seq"] = self.seq
        data = _dump(obj)
        for q in list(self.subs):
            try:
                q.put_nowait(data)
            except queue.Full:
                self._drop(q)

    # ------------------------------------------------------------------------------------------------------------------ the loop
    def _run(self):
        ev = threading.Event()
        while True:
            with self.lock:
                if not self.subs:
                    self.thread = None
                    return
            try:
                self._tick()
            except Exception:
                traceback.print_exc(file=sys.stderr) if sys.stderr else None
            nxt = time.monotonic() + TICK                                  # (never sooner than TICK after this tick: Event.wait may return early on Windows' coarse timer)
            while True:
                left = nxt - time.monotonic()
                if left <= 0:
                    break
                ev.wait(left)

    def _tick(self):
        t = self.cur()
        tid = t.id if t else None
        with self.lock:
            if not self.subs:
                return
            if tid != self.tid:                                            # another tournament was started / opened: everybody starts again from its full state
                self.tid, self.sent, self.done_sent = tid, {}, 0
                self._broadcast(self.state(t, track=True))
                return
            if t is None:
                return
            with t.lock:
                new = t.games[self.done_sent:]
                items = [(gid, dict(live), t.livex.get(gid)) for gid, live in t.active.items()]
                meta = {"status": t.status, "done": len(t.games), "total": t.total, "error": bool(t.error)}
            now = time.monotonic()
            msg = {}
            seen, gs = set(), []
            for gid, live, lx in items:
                if lx is None:
                    continue
                seen.add(gid)
                fr = lx["frame"]
                th = lx.get("think")
                sig = (fr["ply"], th[3] if th and th[0] == fr["ply"] else None, bool(fr.get("wait")))
                old = self.sent.get(gid)
                if old == sig:
                    continue
                v, sig = _game_view(gid, live, lx, 0 if old is None else old[0])
                self.sent[gid] = sig
                gs.append(v)
            gone = [gid for gid in self.sent if gid not in seen]
            for gid in gone:
                del self.sent[gid]
            if new:
                self.done_sent += len(new)
                msg["end"] = [{"sum": s, "rec": _read_rec(t, s["gid"]) if i >= len(new) - 20 else None} for i, s in enumerate(new)]
            if gs:
                msg["g"] = gs
            if gone:
                msg["gone"] = gone
            sc = _structure(t)
            if sc[1] is not None and sc is not self.struct_sent[0] and now - self.struct_sent[1] >= STRUCT_EVERY:
                msg["structure"] = sc[1]
                self.struct_sent = (sc, now)
            if msg or meta != self.meta_sent[0] or now - self.meta_sent[1] >= META_EVERY:
                msg["meta"] = dict(meta, elapsed=round(t.elapsed(), 1))
                self.meta_sent = (meta, now)
            if msg:
                msg["t"] = "tick"
                self._broadcast(msg)

    # ------------------------------------------------------------------------------------------------------------------ sharing on the local network
    def share_status(self):
        s = self.share
        if not s:
            return {"on": False, "port": None, "urls": [], "viewers": 0}
        host, port = s.server_address[0], s.server_address[1]
        return {"on": True, "port": port, "urls": ["http://%s:%d/" % (ip, port) for ip in (lan_addresses() if host in ("0.0.0.0", "::") else [host])], "viewers": self.viewers("share")}

    def share_start(self, port=None, host=None):
        """host: the address to listen on - all interfaces unless the env var KESTREL_ARENA_SHARE_HOST (a test aid, e.g. 127.0.0.1) or the argument says otherwise."""
        with self.lock:
            if self.share:
                return self.share_status()
            host = host or os.environ.get("KESTREL_ARENA_SHARE_HOST") or "0.0.0.0"
            ports = [int(port)] if port else list(range(SHARE_PORT, SHARE_PORT + 20)) + [0]
            err = None
            for p in ports:
                try:
                    srv = ShareServer((host, p), self.st)
                    break
                except OSError as e:
                    err = e
            else:
                raise OSError(str(err))
            threading.Thread(target=srv.serve_forever, name="live-share", daemon=True).start()
            self.share = srv
        return self.share_status()

    def share_stop(self):
        with self.lock:
            srv, self.share = self.share, None
        if srv:
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass
        self.close_tag("share")
        return self.share_status()

    def shutdown(self):
        self.share_stop()
        with self.lock:
            for q in list(self.subs):
                self._drop(q)


def lan_addresses():
    """The IPv4 addresses of this PC on the local network, the one of the default route first (nothing is sent: connect() on a UDP socket only picks the interface), then the host name."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(ai[4][0])
    except OSError:
        pass
    out = []
    for ip in ips:
        if ip and not ip.startswith(("127.", "169.254.", "0.")) and ip not in out:
            out.append(ip)
    try:
        host = socket.gethostname()
        if host and host not in out:
            out.append(host)
    except OSError:
        pass
    return out or ["127.0.0.1"]


# ------------------------------------------------------------------------------------------------------------------ GET handling shared by the app server and the share server
def page_html(res_dir, token):
    with open(os.path.join(res_dir, "web", "live.html"), "rb") as f:
        return f.read().replace(b"__ARENA_TOKEN__", token.encode("ascii"))


def serve_stream(h, hub, tag):
    q = hub.subscribe(tag)
    if q is None:
        h._json({"error": "too many viewers"}, 503)
        return True
    try:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True
        h.wfile.write(b"retry: 2000\n\n")
        h.wfile.flush()
        while True:
            try:
                data = q.get(timeout=PING)
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
                h.wfile.flush()
                continue
            if data is None:
                break
            h.wfile.write(b"data: " + data + b"\n\n")
            h.wfile.flush()
    except OSError:
        pass
    finally:
        hub.unsubscribe(q)
    return True


def handle_get(h, path, q, st, share=False):
    """GET of the live page and its data; returns False for a path that is not a live path.  `h` offers _send(status, bytes, ctype) and _json(obj, status)."""
    hub = st.live
    if path in ("/live", "/live/") or (share and path in ("/", "/index.html")):
        h._send(200, page_html(st.res_dir, "" if share else st.token), "text/html; charset=utf-8")
        return True
    if share and path in SHARE_STATIC:
        with open(os.path.join(st.res_dir, "web", SHARE_STATIC[path]), "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        h._send(200, data, ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        return True
    if not path.startswith("/live/api/"):
        return False
    rest = path[len("/live/api/"):]
    if rest == "state":
        h._send(200, hub.state_bytes(), "application/json; charset=utf-8")
    elif rest == "stream":
        return serve_stream(h, hub, "share" if share else "local")
    elif re.match(r"^game/\d+$", rest):
        rec = hub.record(int(rest[5:]))
        h._json(rec, 200) if rec is not None else h._json({"error": "unknown game"}, 404)
    elif rest == "snapshot" and not share:
        h._send(200, _dump(hub.snapshot()), "application/json; charset=utf-8")
    else:
        h._json({"error": "not found"}, 404)
    return True


class ShareHandler(BaseHTTPRequestHandler):
    server_version = "KestrelArenaLive/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")

    def _get(self):
        try:
            u = urlparse(self.path)
            if not handle_get(self, u.path, {k: v[0] for k, v in parse_qs(u.query).items()}, self.server.st, share=True):
                self._json({"error": "not found"}, 404)
        except OSError:
            pass
        except Exception:
            try:
                self._json({"error": "internal error"}, 500)
            except Exception:
                pass

    def _deny(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 65536:
                self.rfile.read(n)                                         # (an unread body would make the close look like a reset to the client)
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        except OSError:
            pass
        self.close_connection = True

    do_GET = do_HEAD = _get
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _deny


class ShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False                 # (on Windows this flag would let a second program take the same port)

    def __init__(self, addr, st):
        self.st = st
        super().__init__(addr, ShareHandler)


# ------------------------------------------------------------------------------------------------------------------ one-file broadcast
def build_broadcast_html(res_dir, snapshot):
    """The Live page with its style, scripts, logo and the snapshot in ONE html file (open it anywhere, host it on any web server)."""
    web = os.path.join(res_dir, "web")

    def rd(rel):
        with open(os.path.join(web, rel), "r", encoding="utf-8") as f:
            return f.read()

    html = rd("live.html").replace("__ARENA_TOKEN__", "")
    html = re.sub(r'<link rel="stylesheet" href="/live.css">', lambda m: "<style>\n" + rd("live.css") + "\n</style>", html)
    html = re.sub(r'<script src="/(js/[\w.]+)"></script>', lambda m: "<script>\n" + rd(m.group(1)).replace("</script", "<\\/script") + "\n</script>", html)
    for rel in ("assets/logo.svg", "assets/icon.svg"):                  # (the page and its script both name the logo with a root path: both become the picture itself)
        try:
            with open(os.path.join(web, rel), "rb") as f:
                uri = "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode("ascii")
            html = html.replace("/" + rel, uri)
        except OSError:
            pass
    data = json.dumps(snapshot, separators=(",", ":")).replace("<", "\\u003c")
    return html.replace("<!--BROADCAST-->", "<script>window.__BROADCAST__=" + data + ";</script>")
