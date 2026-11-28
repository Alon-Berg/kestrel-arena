"""Show a fastchess tournament on the Live page by following its results folder.

fastchess writes a game to its PGN file when the game ends and reports nothing while it is played, so this shows what exists: standings, crosstable, the list of finished games and their moves
(with the engines' evaluations) as the pairs complete - no live board and no clocks.  Nothing is run, wrapped or changed: the PGN files are only read, the engines are not touched.

usage:  python tools/live_follow.py <folder or .pgn file> [--glob "*.pgn"] [--port 8790] [--share] [--refresh 5] [--name "5+0.1 round robin"] [--total 10100]
        then open  http://127.0.0.1:8790/   (the page reloads its data every --refresh seconds; --share listens on all interfaces: other PCs on your network can watch, read-only)
"""
import argparse
import glob
import gzip
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import chess                                                          # noqa: E402
import chess.pgn                                                      # noqa: E402
from arena import live as livemod                                     # noqa: E402

COMMENT = re.compile(r"([+-])(M\d+|\d+(?:\.\d+)?)/(\d+)\s+(\d+(?:\.\d+)?)s")          # fastchess: "{+0.26/14 0.223s}", the engine's own point of view
STAMP = "%Y-%m-%dT%H:%M:%S %z"


def parse_game(game):
    """A python-chess game (fastchess PGN) -> (summary fields, record) or None when it is not finished."""
    h = game.headers
    result = h.get("Result", "*")
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    board = game.board()
    uci, san, cp, depth, ms = [], [], [], [], []
    for node in game.mainline():
        mv = node.move
        white = board.turn == chess.WHITE
        san.append(board.san(mv)), uci.append(mv.uci())
        m = COMMENT.search(node.comment or "")
        if m:
            v = (30000 - int(m.group(2)[1:])) if m.group(2).startswith("M") else int(round(float(m.group(2)) * 100))
            v = v if m.group(1) == "+" else -v
            cp.append(v if white else -v), depth.append(int(m.group(3))), ms.append(int(round(float(m.group(4)) * 1000)))      # (white's point of view, like the page)
        else:
            cp.append(None), depth.append(None), ms.append(None)
        board.push(mv)
    book = 0
    while book < len(cp) and cp[book] is None:
        book += 1
    out = board.outcome(claim_draw=True)
    term = (h.get("Termination") or "").strip()
    reason = (out.termination.name.lower().replace("_", " ") if out and term in ("", "normal") else term) or ""
    try:
        t0, t1 = datetime.strptime(h["GameStartTime"], STAMP), datetime.strptime(h["GameEndTime"], STAMP)
        dur, ended, t0s, t1s = int((t1 - t0).total_seconds()), h["GameEndTime"], t0.timestamp(), t1.timestamp()
    except (KeyError, ValueError):
        dur, ended, t0s, t1s = None, None, None, None
    rnd = h.get("Round", "")
    rec = {"white": h.get("White", "?"), "black": h.get("Black", "?"), "round": int(rnd) if rnd.isdigit() else None, "result": result, "reason": reason,
           "start_fen": h.get("FEN") if h.get("SetUp") == "1" else None, "uci": uci, "san": san, "cp": cp, "depth": depth, "ms": ms, "book": book, "plies": len(uci), "dur": dur, "ended": ended, "arm": None}
    return rec, h.get("TimeControl"), t0s, t1s


def read_pgn(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        while True:
            try:
                g = chess.pgn.read_game(f)
            except Exception:
                break
            if g is None:
                break
            out.append(g)
    return out


class Follower:
    def __init__(self, files_glob, name=None, total=None, quiet_after=90):
        self.glob, self.name, self.total, self.quiet_after = files_glob, name, total, quiet_after
        self.lock = threading.Lock()
        self.seen = {}                      # file -> (size, number of finished games already taken)
        self.where = {}                     # gid -> (file, index in file)
        self.games, self.recent = [], {}    # summaries in the order they were found; the moves of the latest games
        self.tc = None
        self.t0 = self.t1 = None
        self.newest = 0.0                   # the newest modification time of any file: the tournament is 'running' while files still change
        self.cache = (0.0, None)

    def scan(self):
        changed = False
        for path in sorted(glob.glob(self.glob)):
            try:
                size = os.path.getsize(path)
                self.newest = max(self.newest, os.path.getmtime(path))
            except OSError:
                continue
            old = self.seen.get(path, (-1, 0))
            if size == old[0]:
                continue
            taken = old[1]
            fresh = []
            for i, g in enumerate(read_pgn(path)):
                p = parse_game(g) if i >= taken else None
                if p:
                    fresh.append((i, p))
                elif i >= taken:
                    break                   # a game that is still being written: look again at the next scan
            with self.lock:
                for i, (rec, tc, t0, t1) in fresh:
                    gid = len(self.games) + 1
                    self.where[gid] = (path, i)
                    s = {k: rec[k] for k in ("round", "white", "black", "result", "reason", "plies", "dur", "ended", "arm")}
                    s.update({"gid": gid, "key": None, "batch": 0, "pair": 0, "g": i})
                    self.games.append(s)
                    self.recent[gid] = rec
                    self.recent.pop(gid - 300, None)
                    self.tc = self.tc or tc
                    self.t0 = t0 if self.t0 is None or (t0 and t0 < self.t0) else self.t0
                    self.t1 = t1 if self.t1 is None or (t1 and t1 > self.t1) else self.t1
                    changed = True
                self.seen[path] = (size, taken + len(fresh))
        return changed

    def record(self, gid):
        with self.lock:
            if gid in self.recent:
                return self.recent[gid]
            w = self.where.get(gid)
        if not w:
            return None
        games = read_pgn(w[0])
        p = parse_game(games[w[1]]) if w[1] < len(games) else None
        return p[0] if p else None

    def snapshot(self):
        with self.lock:
            games = list(self.games)
            recs = {gid: dict(r) for gid, r in self.recent.items() if gid > len(games) - 200}
        ids = []
        for s in games:
            for k in ("white", "black"):
                if s[k] not in ids:
                    ids.append(s[k])
        n, gpp = len(ids), max([1] + [c for _, c in self.seen.values()])
        total = self.total or max(len(games), n * (n - 1) // 2 * gpp)
        running = time.time() - self.newest < self.quiet_after
        name = self.name or os.path.basename(os.path.dirname(self.glob) or ".")
        meta = {"id": "follow", "name": name, "format": "roundrobin", "status": "running" if running else "finished", "error": False, "total": total, "done": len(games),
                "elapsed": round((self.t1 - self.t0) if self.t0 and self.t1 else 0, 1), "tc": {}, "tc_label": (self.tc or "").replace("+", "+"), "params": {}, "concurrency": None, "created": None}
        for r in recs.values():
            r.pop("dur", None), r.pop("ended", None), r.pop("plies", None)
        return {"t": "state", "seq": 0, "tournament": meta, "engines": [{"id": i, "name": i, "elo": None} for i in ids], "games": games, "active": [], "structure": None,
                "now": time.time(), "snapshot": False, "taken": time.strftime("%H:%M:%S"), "records": recs}


def make_handler(fol, refresh):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            pass

        def _send(self, status, data, ctype, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _get(self):
            try:
                path = urlparse(self.path).path
                if path in ("", "/"):
                    return self._send(302, b"", "text/plain", {"Location": "/live?src=/live/api/snapshot&refresh=%d" % refresh})
                if path in ("/live", "/live/"):
                    return self._send(200, livemod.page_html(ROOT, ""), "text/html; charset=utf-8")
                if path in livemod.SHARE_STATIC:
                    with open(os.path.join(ROOT, "web", livemod.SHARE_STATIC[path]), "rb") as f:
                        ctype = {"css": "text/css", "js": "text/javascript", "svg": "image/svg+xml"}[path.rsplit(".", 1)[1]]
                        return self._send(200, f.read(), ctype + ("; charset=utf-8" if ctype != "image/svg+xml" else ""))
                if path == "/live/api/snapshot":
                    ts, data = fol.cache
                    if data is None or time.time() - ts > max(1, refresh - 1):
                        data = json.dumps(fol.snapshot(), separators=(",", ":")).encode("utf-8")
                        fol.cache = (time.time(), data)
                    if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                        return self._send(200, gzip.compress(data, 3), "application/json; charset=utf-8", {"Content-Encoding": "gzip"})
                    return self._send(200, data, "application/json; charset=utf-8")
                m = re.match(r"^/live/api/game/(\d+)$", path)
                if m:
                    rec = fol.record(int(m.group(1)))
                    return self._send(200 if rec else 404, json.dumps(rec or {"error": "unknown game"}).encode("utf-8"), "application/json; charset=utf-8")
                self._send(404, b'{"error":"not found"}', "application/json")
            except (OSError, ValueError):
                pass

        def _deny(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= 65536:
                    self.rfile.read(n)                                     # (an unread body would make the close look like a reset to the client)
            except (ValueError, OSError):
                pass
            self._send(405, b"", "text/plain", {"Allow": "GET, HEAD", "Connection": "close"})
            self.close_connection = True

        do_GET = do_HEAD = _get
        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _deny
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="a folder with the PGN files of fastchess, or one PGN file")
    ap.add_argument("--glob", default="*.pgn", help="which files of the folder (default *.pgn)")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--share", action="store_true", help="listen on all interfaces (read-only) instead of only on this PC")
    ap.add_argument("--refresh", type=int, default=5, help="seconds between two looks at the folder / page reloads")
    ap.add_argument("--name", default=None)
    ap.add_argument("--total", type=int, default=None, help="games the tournament will have (for the progress bar; default: estimated from the engines)")
    a = ap.parse_args()
    pattern = a.source if os.path.isfile(a.source) else os.path.join(a.source, a.glob)
    fol = Follower(pattern, a.name, a.total)
    t0 = time.time()
    fol.scan()
    print("read %d games in %.1f s" % (len(fol.games), time.time() - t0), flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0" if a.share else "127.0.0.1", a.port), make_handler(fol, a.refresh))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    hosts = livemod.lan_addresses() if a.share else ["127.0.0.1"]
    print("Live page: " + "   ".join("http://%s:%d/" % (h, a.port) for h in hosts), flush=True)
    try:
        while True:
            time.sleep(a.refresh)
            if fol.scan():
                print("%s: %d games" % (time.strftime("%H:%M:%S"), len(fol.games)), flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
