"""The live broadcast (arena/live.py, web/live.html) with pretend engines (a small Python script with python-chess that plays legal moves and sends info lines): the stream of a round robin, a
knockout and an elimination with Armageddon is complete and in order, never faster than four messages a second, empty (and free of any work in the game threads) when nobody watches; the share
server serves the live page and its GET data and nothing else; the one-file broadcast holds every game; the page's own chess stepper agrees with python-chess (needs Deno, else skipped).
usage: python tests/test_live.py"""
import http.client
import json
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp(prefix="arena_live_test_")
os.environ["KESTREL_ARENA_DATA"] = os.path.join(tmp, "data")                # (never the user's data)
os.makedirs(os.environ["KESTREL_ARENA_DATA"])

import chess                                                              # noqa: E402
from arena import live as livemod                                         # noqa: E402
from arena.api import AppState, build_tournament_cfg                      # noqa: E402
from arena.server import start_server                                     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails += 1


fake = os.path.join(tmp, "fake.py")
open(fake, "w").write(textwrap.dedent('''
    import sys, time
    import chess
    board = chess.Board()
    delay = float(sys.argv[1]) if len(sys.argv) > 1 else 0.04
    for line in sys.stdin:
        c = line.split()
        if not c:
            continue
        if c[0] == "uci":
            print("id name Fake"); print("uciok", flush=True)
        elif c[0] == "isready":
            print("readyok", flush=True)
        elif c[0] == "position":
            i = c.index("moves") if "moves" in c else len(c)
            board = chess.Board() if c[1] == "startpos" else chess.Board(" ".join(c[2:i]))
            for m in c[i + 1:]:
                board.push_uci(m)
        elif c[0] == "go":
            moves = sorted(board.legal_moves, key=lambda m: m.uci())
            mv = moves[len(board.move_stack) % 3 % len(moves)].uci()
            for d in (1, 2, 3):
                print("info depth %d seldepth %d score cp %d nodes %d nps 1000 time %d pv %s" % (d, d + 1, 10 * d, 100 * d, 5 * d, mv), flush=True)
                time.sleep(delay / 4)
            time.sleep(delay)
            print("bestmove " + mv, flush=True)
        elif c[0] == "quit":
            break
'''))

st = AppState()
st.registry.settings["engines"] = [{"id": "f%d" % i, "name": "Fake %d" % i, "path": sys.executable, "args": '"%s" %s' % (fake, "0.04"), "cwd": tmp, "options": {}, "elo": 3000 - i, "enabled": True}
                                   for i in range(4)]
ids = ["f0", "f1", "f2"]
common = {"tc": {"mode": "time", "base": 20, "inc": 0.5}, "concurrency": 2, "hash": 16, "adj": {"max_plies": 14}, "openings": {"source": "none"}, "overhead_ms": None}
hub = st.live


def run(cfg_extra, watch=True, settle=1.0, limit=120):
    """Plays a tournament; returns (tournament, [(time received, message)]) of one viewer that is connected the whole time."""
    t = st.tournaments.create(build_tournament_cfg(st, dict(common, **cfg_extra)))
    q = hub.subscribe("local") if watch else None
    t.start()
    msgs, t0 = [], time.time()
    while time.time() - t0 < limit:
        if q is not None:
            try:
                data = q.get(timeout=0.2)
                if data is not None:
                    msgs.append((time.monotonic(), json.loads(data)))
            except queue.Empty:
                pass
        else:
            time.sleep(0.2)
        if t.status not in ("running", "paused", "stopping"):
            break
    end = time.time() + settle                                           # (the last events come with the next tick)
    while q is not None and time.time() < end:
        try:
            data = q.get(timeout=0.2)
            if data is not None:
                msgs.append((time.monotonic(), json.loads(data)))
        except queue.Empty:
            pass
    if q is not None:
        hub.unsubscribe(q)
    return t, msgs


def replay(msgs):
    """The page's reducer in Python: every game as the viewer has built it from the stream, and the finished ones."""
    games, ended, structure = {}, {}, None

    def put(v):
        g = games.setdefault(v["gid"], {"uci": [], "san": [], "cp": [], "clocks": None})
        assert v["from"] <= len(g["uci"]), "a gap in the moves of game %d: from %d, have %d" % (v["gid"], v["from"], len(g["uci"]))
        for k in ("uci", "san", "cp"):
            del g[k][v["from"]:]
            g[k].extend(v[k])
        g["clocks"], g["ply"] = v["clocks"], v["ply"]
    for _, m in msgs:
        if m["t"] == "state":
            games = {}
            ended = {e["gid"]: None for e in m["games"]}
            structure = m.get("structure")
            for v in m["active"]:
                put(v)
        else:
            for v in m.get("g", []):
                put(v)
            for e in m.get("end", []):
                ended[e["sum"]["gid"]] = e
            structure = m.get("structure", structure)
    return games, ended, structure


def max_per_second(msgs):
    ts = [t for t, m in msgs if m["t"] != "state"]                          # (the whole state a viewer gets when it connects is a one-off)
    return max((sum(1 for u in ts if t <= u < t + 1.0) for t in ts), default=0)


# ----------------------------------------------------------------------------------------------------------------- nothing is shared or computed while nobody watches
check(hub.share_status()["on"] is False and hub.share is None and not livemod.GATE.watched and hub.thread is None, "at the start: sharing is off, nobody watches, no hub thread")
t_quiet = st.tournaments.create(build_tournament_cfg(st, dict(common, name="quiet", format="roundrobin", engines=ids, games=2)))
t_quiet.start()
seen_think, seen_lx = False, 0
t0 = time.time()
while t_quiet.status == "running" and time.time() - t0 < 60:
    for lx in list(t_quiet.livex.values()):
        seen_lx += 1
        seen_think = seen_think or lx["think"] is not None
    time.sleep(0.02)
check(t_quiet.status == "finished" and len(t_quiet.games) == 6, "a round robin runs without a viewer: %s, %d games" % (t_quiet.status, len(t_quiet.games)))
check(seen_lx > 0 and not seen_think and not t_quiet.livex and hub.thread is None, "no viewer: the info lines are not stored (%d looks at running games), the hub has no thread, nothing is left behind" % seen_lx)

# ----------------------------------------------------------------------------------------------------------------- a round robin, watched
t, msgs = run(dict(name="rr", format="roundrobin", engines=ids, games=2))
first = msgs[0][1] if msgs else {}
check(first.get("t") == "state" and first["tournament"]["format"] == "roundrobin" and [e["name"] for e in first["engines"]] == ["Fake 0", "Fake 1", "Fake 2"], "the stream starts with the whole state")
blob = json.dumps([m for _, m in msgs])
check(fake not in blob and sys.executable not in blob and tmp not in blob and "path" not in blob and "args" not in blob and "options" not in blob, "nothing of the engines' files, arguments or options is public")
games, ended, structure = replay(msgs)
recs = {gid: e["rec"] for gid, e in ended.items() if e}
check(t.status == "finished" and len(ended) == 6 and all(e and e["rec"] for e in ended.values()), "all 6 games arrive as finished games with their moves: %d" % len(ended))
check(all(len(r["uci"]) == r["plies"] == len(r["san"]) == len(r["cp"]) for r in recs.values()) and all(r["plies"] >= 1 for r in recs.values()), "every record has uci, san and cp for every ply")
bad = [gid for gid, g in games.items() if gid in recs and recs[gid]["uci"][:len(g["uci"])] != g["uci"]]
check(not bad and any(len(g["uci"]) > 3 for g in games.values()), "the moves rebuilt from the stream are the start of the game that finished (%d games followed)" % len(games))
check(any(g["clocks"] and g["clocks"][0] < 20500 and g["clocks"][0] != g["clocks"][1] for g in games.values()) or any(len(g["uci"]) > 0 for g in games.values()), "clocks arrive with the moves")
th = [v["think"] for _, m in msgs for v in ([] if m["t"] == "state" else m.get("g", [])) if v.get("think")]
check(th and all(x["pv"] and x["depth"] and x["side"] in "wb" for x in th), "what the engine on move thinks arrives (%d samples, e.g. %s)" % (len(th), th[0]["pv"] if th else ""))
check(max_per_second(msgs) <= 5, "at most 4 messages a second to a viewer: %d in the busiest second (%d messages in %.1f s)" % (max_per_second(msgs), len(msgs), msgs[-1][0] - msgs[0][0]))
check(not livemod.GATE.watched and hub.viewers() == 0, "after the last viewer left nobody is 'watching' any more")
time.sleep(0.6)
check(hub.thread is None, "... and the hub thread is gone")

# ----------------------------------------------------------------------------------------------------------------- very fast games
st.registry.settings["engines"] = [dict(e, args='"%s" 0' % fake) for e in st.registry.settings["engines"]]
t, msgs = run(dict(name="fast", format="roundrobin", engines=ids, games=4, adj={"max_plies": 60}))
check(t.status == "finished" and len(t.games) == 12 and max_per_second(msgs) <= 5, "12 games of 60 plies played within a second: the viewer gets %d messages, at most %d in a second" % (len(msgs), max_per_second(msgs)))
games, ended, structure = replay(msgs)
check(len([e for e in ended.values() if e]) == 12, "... and all 12 games arrive")
st.registry.settings["engines"] = [dict(e, args='"%s" 0.04' % fake) for e in st.registry.settings["engines"]]

# ----------------------------------------------------------------------------------------------------------------- knockout and elimination with Armageddon: structure, Armageddon flag
four = ["f0", "f1", "f2", "f3"]
t, msgs = run(dict(name="ko", format="knockout", engines=four, ko_games=2, armageddon=0, adj={"max_plies": 8}, concurrency=2), settle=1.5)
games, ended, structure = replay(msgs)
check(t.status == "finished" and structure and structure.get("rounds") and any(e and e["sum"].get("arm") for e in ended.values()), "knockout: bracket structure arrives, an Armageddon game is flagged (%d games)" % len([e for e in ended.values() if e]))
check(any(v.get("arm") for _, m in msgs for v in ([] if m["t"] == "state" else m.get("g", []))), "... also while it is running")
t, msgs = run(dict(name="el", format="elimination", engines=ids, losses=2, ko_games=2, armageddon=0, adj={"max_plies": 8}, concurrency=2), settle=1.5)
games, ended, structure = replay(msgs)
check(t.status == "finished" and structure and structure.get("kind") == "bracket" and structure.get("champion"), "elimination: the bracket structure arrives and has a champion (%d games)" % len([e for e in ended.values() if e]))

# ----------------------------------------------------------------------------------------------------------------- the app server: the page and its data without a token, everything else still needs one
srv = start_server(st, 0)
base = "http://127.0.0.1:%d" % st.port


def get(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


code, body, hd = get(base + "/live")
check(code == 200 and b"live.js" in body and b"__ARENA_TOKEN__" not in body and st.token.encode() in body, "app server: /live is the page, with the token of this run in it")
code, body, hd = get(base + "/live/api/state")
state = json.loads(body) if code == 200 else {}
check(code == 200 and state["tournament"]["id"] == t.id and len(state["games"]) == len(t.games), "app server: /live/api/state without a token (%d games)" % len(state.get("games", [])))
code, body, hd = get(base + "/live/api/game/%d" % t.games[0]["gid"])
check(code == 200 and len(json.loads(body)["uci"]) == t.games[0]["plies"], "app server: a finished game's moves")
check(get(base + "/live/api/game/99999")[0] == 404 and get(base + "/live/api/nothing")[0] == 404, "app server: unknown game / path -> 404")
check(get(base + "/api/tournaments")[0] == 403 and get(base + "/api/live/share")[0] == 403 and get(base + "/api/live/export")[0] == 403, "app server: the control and export endpoints still need the token")
check(get(base + "/live", headers={"Host": "evil.example"})[0] == 403, "app server: another Host header is refused")
code, body, hd = get(base + "/api/live/share", headers={"X-Arena-Token": st.token})
check(code == 200 and json.loads(body)["on"] is False, "sharing is off by default")
code, body, hd = get(base + "/api/live/export", headers={"X-Arena-Token": st.token})
check(code == 200 and "attachment" in (hd.get("Content-Disposition") or "") and b"__BROADCAST__" in body, "the export endpoint gives the one-file broadcast as a download")

# ----------------------------------------------------------------------------------------------------------------- the one-file broadcast
html = livemod.build_broadcast_html(st.res_dir, hub.snapshot())
m = re.search(r"window\.__BROADCAST__=(.*?);</script>", html, re.S)
data = json.loads(m.group(1)) if m else {}
check(m and data["tournament"]["id"] == t.id and len(data["games"]) == len(t.games) and len(data["records"]) == len(t.games), "the broadcast file holds the state and the moves of all %d games" % len(t.games))
check('src="/js/' not in html and 'href="/live.css"' not in html and "<style>" in html and html.count("</script>") == html.count("<script"), "... with style and scripts inside (%d KB)" % (len(html) // 1024))
check(fake not in html and sys.executable not in html and tmp not in html and st.token not in html, "... and nothing private in it")
check(all(len(r["uci"]) == len(r["san"]) == len(r["cp"]) for r in data["records"].values()), "... every record complete")

# ----------------------------------------------------------------------------------------------------------------- the share server (GET only, the live page and its data)
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
status = hub.share_start(port, host="127.0.0.1")                       # (loopback: the test must not make Windows ask about the firewall; all interfaces is the default)
check(status["on"] and status["port"] == port and status["urls"] == ["http://127.0.0.1:%d/" % port], "sharing on: %s" % status["urls"])
sb = "http://127.0.0.1:%d" % port
code, body, hd = get(sb + "/")
check(code == 200 and b"live.js" in body and st.token.encode() not in body and b'content=""' in body, "share server: / is the live page, without the token")
for p in ("/live/api/state", "/js/live.js", "/js/live_util.js", "/js/board.js", "/js/elo.js", "/live.css", "/assets/logo.svg"):
    check(get(sb + p)[0] == 200, "share server: GET %s" % p)
for p in ("/api/tournaments", "/api/bootstrap", "/api/events", "/api/live/share", "/api/live/export", "/live/api/snapshot", "/js/app.js", "/js/tournament.js", "/app.css", "/data/settings.json", "/arena/api.py", "/js/../app.css", "/%2e%2e/main.py", "/web/js/app.js"):
    check(get(sb + p)[0] == 404, "share server: %s is not served" % p)
for method in ("POST", "PUT", "DELETE"):
    check(get(sb + "/live/api/state", method=method, data=b"{}")[0] == 405 and get(sb + "/api/tournament/x/stop", method=method, data=b"{}")[0] == 405, "share server: %s -> 405" % method)
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
conn.request("GET", "/live/api/stream")
resp = conn.getresponse()
line = b""
while not line.startswith(b"data:"):
    line = resp.fp.readline()
check(resp.status == 200 and json.loads(line[5:])["t"] == "state" and hub.viewers("share") == 1 and livemod.GATE.watched, "share server: the stream starts with the state; one viewer counts and the page is 'watched'")
hub.share_stop()
time.sleep(0.5)
check(hub.viewers() == 0 and not livemod.GATE.watched and hub.share is None, "sharing off: the viewer is dropped, nothing watches")
try:
    urllib.request.urlopen(sb + "/", timeout=2)
    check(False, "sharing off: the port answers no more")
except (urllib.error.URLError, OSError):
    check(True, "sharing off: the port answers no more")
conn.close()

# ----------------------------------------------------------------------------------------------------------------- the page's chess stepper against python-chess (Deno)
deno = shutil.which("deno")
if deno:
    rnd = random.Random(7)
    cases = []

    def case(fen, moves=None, plies=200):
        b = chess.Board(fen) if fen else chess.Board()
        start = b.fen() if fen else None
        uci, pl = [], [b.board_fen()]
        for u in (moves or []):
            b.push_uci(u); uci.append(u); pl.append(b.board_fen())
        while moves is None and not b.is_game_over() and len(uci) < plies:
            mv = rnd.choice(list(b.legal_moves)); uci.append(mv.uci()); b.push(mv); pl.append(b.board_fen())
        cases.append({"start_fen": start, "uci": uci, "pl": pl})
    for _ in range(40):
        case(None)
    case("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", ["e1g1", "e8c8", "a1b1"])
    case("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", ["e1c1", "e8g8"])
    case("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", ["e5d6", "e8d8"])
    case("4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1", ["d4e3"])
    case("8/P6k/8/8/8/8/p7/7K w - - 0 1", ["a7a8q", "a2a1n", "a8a1"])
    case("8/P6k/8/8/8/8/p7/7K w - - 0 1", ["a7a8n", "a2a1b"])
    cf = os.path.join(tmp, "cases.json")
    json.dump(cases, open(cf, "w"))
    p = subprocess.run([deno, "run", "--allow-read", os.path.join(ROOT, "tests", "live_core_check.js"), os.path.join(ROOT, "web", "js", "live_util.js"), cf], capture_output=True, text=True, timeout=120)
    check(p.returncode == 0 and p.stdout.strip().startswith("OK"), "the page's stepper agrees with python-chess on %d games (castling, en passant, promotions): %s" % (len(cases), (p.stdout + p.stderr).strip()[:160]))
else:
    print("skip the page's chess stepper test: Deno is not installed")

# ----------------------------------------------------------------------------------------------------------------- following a fastchess results folder (tools/live_follow.py)
import gzip                                                               # noqa: E402
import importlib.util                                                     # noqa: E402
from http.server import ThreadingHTTPServer                               # noqa: E402
import chess.pgn                                                          # noqa: E402
spec = importlib.util.spec_from_file_location("live_follow", os.path.join(ROOT, "tools", "live_follow.py"))
lf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lf)
fcdir = os.path.join(tmp, "fastchess")
os.makedirs(fcdir)


def fc_game(white, black, rnd, result, plies=12):
    """A game in fastchess's PGN style: every engine move has {score/depth time} from that engine's own point of view; the first 4 plies are book."""
    b, g = chess.Board(), chess.pgn.Game()
    g.headers.update({"Event": "Fastchess Tournament", "Round": str(rnd), "White": white, "Black": black, "Result": result, "TimeControl": "5+0.1", "Termination": "normal",
                      "GameStartTime": "2026-11-21T21:27:02 +0200", "GameEndTime": "2026-11-21T21:27:20 +0200"})
    node = g
    for i in range(plies):
        moves = sorted(b.legal_moves, key=lambda m: m.uci())
        mv = moves[(i * 7) % len(moves)]
        node = node.add_variation(mv)
        if i >= 4:
            node.comment = ("+M5/20 0.100s" if i == 6 else "-M5/20 0.100s" if i == 7 else "%+.2f/14 0.%03ds" % (0.26 if i % 2 == 0 else -0.17, 100 + i))
        b.push(mv)
    return str(g) + "\n\n"


path = os.path.join(fcdir, "t_A_vs_B.pgn")
open(path, "w").write(fc_game("A", "B", 1, "1-0") + fc_game("B", "A", 1, "1/2-1/2"))
rec, tc, t0, t1 = lf.parse_game(lf.read_pgn(path)[0])
check(rec["result"] == "1-0" and tc == "5+0.1" and len(rec["uci"]) == len(rec["san"]) == len(rec["cp"]) == rec["plies"] == 12 and rec["book"] == 4 and t1 - t0 == 18, "fastchess PGN: moves, book length, time control and duration are read")
check(rec["cp"][4] == 26 and rec["cp"][5] == 17 and rec["depth"][4] == 14 and rec["ms"][4] == 104 and rec["cp"][:4] == [None] * 4, "... scores are made white's point of view (black's own -0.17 is +17), with depth and time")
check(rec["cp"][6] == 29995 and rec["cp"][7] == 29995, "... a mate for the engine on move is +/-M and the same sign convention holds for black")
fol = lf.Follower(os.path.join(fcdir, "*.pgn"), name="fc test")
fol.scan()
snap = fol.snapshot()
check(len(snap["games"]) == 2 and [e["id"] for e in snap["engines"]] == ["A", "B"] and snap["tournament"]["status"] == "running" and snap["tournament"]["total"] == 2 and snap["snapshot"] is False
      and set(snap["records"]) == {1, 2}, "the folder's two games are in the state; the page gets them with their moves")
open(path, "a").write(fc_game("A", "B", 2, "0-1") + '[Event "x"]\n[Result "*"]\n\n1. e4 {+0.2/10 0.1s} e5')           # (a game that fastchess is still writing)
check(fol.scan() and len(fol.games) == 3 and fol.games[0]["gid"] == 1 and fol.games[2]["result"] == "0-1" and not fol.scan(), "a game added later gets the next number, the unfinished one waits, the old numbers stay")
s2 = socket.socket(); s2.bind(("127.0.0.1", 0)); fport = s2.getsockname()[1]; s2.close()
fsrv = ThreadingHTTPServer(("127.0.0.1", fport), lf.make_handler(fol, 5))
fsrv.daemon_threads = True
threading.Thread(target=fsrv.serve_forever, daemon=True).start()
fb = "http://127.0.0.1:%d" % fport
code, body, hd = get(fb + "/live/api/snapshot", headers={"Accept-Encoding": "gzip"})
js = json.loads(gzip.decompress(body)) if code == 200 and hd.get("Content-Encoding") == "gzip" else {}
check(js.get("tournament", {}).get("name") == "fc test" and len(js["games"]) == 3, "followed folder: the snapshot (compressed) is served")
code, body, hd = get(fb + "/live/api/game/2")
check(code == 200 and json.loads(body)["result"] == "1/2-1/2" and get(fb + "/live/api/game/77")[0] == 404, "followed folder: a game's moves are read from its file on demand")
check(get(fb + "/live")[0] == 200 and get(fb + "/js/live.js")[0] == 200 and get(fb + "/js/app.js")[0] == 404 and get(fb + "/api/tournaments")[0] == 404, "followed folder: the live page and its files only")
check(get(fb + "/live/api/snapshot", method="POST", data=b"{}")[0] == 405 and get(fb + "/", method="GET")[0] in (200, 302), "followed folder: read-only")
fsrv.shutdown()

# ----------------------------------------------------------------------------------------------------------------- the website: static page + publishing (tools/make_live_site.py, tools/live_publish.py)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live_publish as lp                                                 # noqa: E402
import make_live_site as mls                                              # noqa: E402
page = os.path.join(tmp, "site_page")
mls.build(page)
idx = open(os.path.join(page, "index.html"), encoding="utf-8").read()
check('src="/' not in idx and 'href="/' not in idx and '"src": "data/current.json"' in idx and '"home": "../"' in idx and "js/live.js" in idx, "website page: relative paths, names its data file")
check(all(os.path.exists(os.path.join(page, f)) for f in ("live.css", "js/live.js", "js/live_util.js", "js/board.js", "js/elo.js", "assets/logo.svg", "assets/icon.svg"))
      and json.load(open(os.path.join(page, "data", "current.json"))) == {"t": "state", "tournament": None}, "website page: all files, and an empty data file")
check("'assets/logo.svg'" in open(os.path.join(page, "js", "live.js"), encoding="utf-8").read() and "'/assets/logo.svg'" not in open(os.path.join(page, "js", "live.js"), encoding="utf-8").read(), "website page: the logo has a relative path")
open(os.path.join(page, "data", "current.json"), "w").write('{"keep":1}')
mls.build(page)
check(open(os.path.join(page, "data", "current.json")).read() == '{"keep":1}', "website page: rebuilding the page keeps the published data")

data_dir = os.environ["KESTREL_ARENA_DATA"]
snap = lp.make_snapshot(data_dir, t.id, records=3)
check(snap["snapshot"] is True and snap["tournament"]["id"] == t.id and len(snap["games"]) == len(t.games) and len(snap["records"]) == 3 and sorted(snap["records"]) == sorted(g["gid"] for g in t.games)[-3:]
      and snap["active"] == [] and snap["tournament"]["status"] == "finished" and re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d \(UTC[+-]\d\d:\d\d\)$", snap["taken"]), "publish: the saved tournament as a snapshot, moves of the latest 3 games only")
blob = json.dumps(snap)
check(fake not in blob and sys.executable not in blob and tmp not in blob and "path" not in blob, "publish: nothing private in it")
rdir = os.path.join(data_dir, "tournaments", "t_running_copy")
shutil.copytree(os.path.join(data_dir, "tournaments", t.id), rdir)
cj = json.load(open(os.path.join(rdir, "config.json")))
cj["status"] = "running"
json.dump(cj, open(os.path.join(rdir, "config.json"), "w"))
os.remove(os.path.join(rdir, "games.jsonl")) if False else None
check(lp.make_snapshot(data_dir, "t_running_copy")["tournament"]["status"] == "running", "publish: a tournament whose games file is still growing is 'in progress'")
old = time.time() - 3600
os.utime(os.path.join(rdir, "games.jsonl"), (old, old))
check(lp.make_snapshot(data_dir, "t_running_copy")["tournament"]["status"] == "stopped", "publish: ... and one that stopped growing an hour ago is 'stopped'")


def sh(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    assert r.returncode == 0, "git %s: %s" % (" ".join(args), r.stderr[:200])
    return r.stdout.strip()


origin, seed, site = os.path.join(tmp, "origin.git"), os.path.join(tmp, "seed"), os.path.join(tmp, "site_clone")
sh("init", "-q", "--bare", "-b", "main", origin)
sh("init", "-q", "-b", "main", seed)
os.makedirs(os.path.join(seed, "docs"))
open(os.path.join(seed, "docs", "index.html"), "w").write("<html>home</html>")
sh("-c", "user.name=Seed", "-c", "user.email=seed@example.com", "add", "-A", cwd=seed)
sh("-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "home", cwd=seed)
sh("remote", "add", "origin", origin, cwd=seed)
sh("push", "-q", "origin", "main", cwd=seed)
lp.ensure_clone(site, url=origin)
check(os.path.exists(os.path.join(site, "docs", "index.html")) and sh("config", "user.email", cwd=site) == "alonbergman2014@gmail.com", "publish: the private working copy of the site is cloned with the Kestrel identity")
check(lp.publish(site, snap) is True and json.loads(sh("--git-dir", origin, "show", "main:docs/live/data/current.json"))["tournament"]["id"] == t.id, "publish: pushed - the site repository has the data file")
check(sh("--git-dir", origin, "log", "-1", "--format=%an|%ae|%s").startswith("Alon-Berg|alonbergman2014@gmail.com|Live: ") and sh("--git-dir", origin, "rev-list", "--count", "main") == "2", "publish: one commit, by the Kestrel identity")
check(lp.publish(site, snap) is False and sh("--git-dir", origin, "rev-list", "--count", "main") == "2", "publish: nothing new -> no commit")
snap2 = dict(snap, taken="2999-01-01 00:00 (UTC+00:00)")
check(lp.publish(site, snap2, push=False) is True and sh("--git-dir", origin, "rev-list", "--count", "main") == "2", "publish: --no-push commits locally but pushes nothing")
cli = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "live_publish.py"), "--data", data_dir, "--site", site, "--tournament", t.id, "--no-push"], capture_output=True, text=True, timeout=120)
check(cli.returncode == 0 and "games" in cli.stdout, "publish: the command line runs: %s" % cli.stdout.strip()[:100])

srv.shutdown()
st.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "%d FAILED" % fails)
sys.exit(1 if fails else 0)
