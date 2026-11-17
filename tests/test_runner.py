"""The tournament runner and its helpers with pretend engines (a small Python script with python-chess that plays legal moves): a game that is dropped by an internal error is played again and never
skipped, an engine whose file went missing does not lose its games one by one, Continue right after Stop, the time of a pause, a deleted tournament, the settings of the elimination format in the API,
and the opening files (EPD, plain FEN lists, PGN).
usage: python tests/test_runner.py"""
import os
import sys
import tempfile
import textwrap
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena import game as gamemod                                  # noqa: E402
from arena import openings                                         # noqa: E402
from arena.api import AppState, ApiError, build_tournament_cfg     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="arena_runner_test_")
fake = os.path.join(tmp, "fake.py")
open(fake, "w").write(textwrap.dedent('''
    import sys
    import chess
    board = chess.Board()
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
            mv = sorted(board.legal_moves, key=lambda m: m.uci())[len(board.move_stack) % 3 % len(list(board.legal_moves))].uci()
            print("info depth 1 score cp 0 pv " + mv); print("bestmove " + mv, flush=True)
        elif c[0] == "quit":
            break
'''))

st = AppState()
st.tournaments.root = os.path.join(tmp, "tournaments")
os.makedirs(st.tournaments.root)
st.registry.settings["engines"] = [{"id": "f%d" % i, "name": "Fake %d" % i, "path": sys.executable, "args": '"%s"' % fake, "cwd": tmp, "options": {}, "elo": 3000 - i, "enabled": True} for i in range(4)]
ids = ["f0", "f1", "f2"]
common = {"tc": {"mode": "movetime", "ms": 5}, "concurrency": 2, "hash": 16, "adj": {"max_plies": 24}, "openings": {"source": "none"}, "overhead_ms": None}


def wait(t, secs=120):
    t0 = time.time()
    while t.status in ("running", "paused", "stopping") and time.time() - t0 < secs:
        time.sleep(0.05)


# ----------------------------------------------------------------------------------------------------------------- the API accepts the new format
cfg = build_tournament_cfg(st, dict(common, name="e", format="elimination", engines=ids, losses=3, ko_games=2))
check(cfg["format"] == "elimination" and cfg["params"]["losses"] == 3 and cfg["params"]["bracket"] == 2, "the API stores an elimination with 3 losses as a bracket with the final ladder")
check(build_tournament_cfg(st, dict(common, format="elimination", engines=ids, losses=99))["params"]["losses"] == 10
      and build_tournament_cfg(st, dict(common, format="elimination", engines=ids, losses=0))["params"]["losses"] == 1
      and build_tournament_cfg(st, dict(common, format="elimination", engines=ids))["params"]["losses"] == 2, "losses are limited to 1..10, the default is 2")
try:
    build_tournament_cfg(st, dict(common, format="doubleelim", engines=ids))
    check(False, "an unknown format is refused")
except ApiError as e:
    check("Unknown format" in str(e), "an unknown format is refused: %s" % e)

# ----------------------------------------------------------------------------------------------------------------- a game dropped by an internal error is played again
orig = gamemod.play_game
state = {"calls": 0}


def flaky(spec, w, b, **kw):
    state["calls"] += 1
    if state["calls"] == 2:
        raise RuntimeError("boom (a bug in the game loop)")
    return orig(spec, w, b, **kw)


gamemod.play_game = flaky
t = st.tournaments.create(build_tournament_cfg(st, dict(common, name="flaky", format="roundrobin", engines=ids, games=2)))
t.start()
wait(t)
keys = sorted(g["key"] for g in t.games)
check(t.status == "finished" and len(t.games) == 6 and len(set(keys)) == 6 and t.error is None, "a game that failed once is played again: %s, %d games, error %s" % (t.status, len(t.games), (t.error or "none")[-60:]))

state["calls"] = 0


def broken(spec, w, b, **kw):
    if {spec["white"], spec["black"]} == {"f0", "f1"}:
        raise RuntimeError("always broken")
    return orig(spec, w, b, **kw)


gamemod.play_game = broken
t = st.tournaments.create(build_tournament_cfg(st, dict(common, name="broken", format="roundrobin", engines=ids, games=2)))
t.start()
wait(t)
check(t.status == "error" and "could not be played" in (t.error or "") and "always broken" in (t.error or "") and len(t.games) == 4,
      "a game that always fails stops the tournament with a message and is not skipped: %s, %d games, '%s'" % (t.status, len(t.games), (t.error or "")[:110]))
gamemod.play_game = orig
t.start()                                                                  # Continue: the failing game works now
wait(t)
check(t.status == "finished" and len(t.games) == 6 and t.error is None, "...Continue plays the missing games and clears the error: %s, %d games" % (t.status, len(t.games)))

# ----------------------------------------------------------------------------------------------------------------- an elimination, stopped in the middle and resumed from disk (stub games: the better seed wins, a bit of time per game)
def stub_game(spec, w, b, on_update=None, should_stop=None):
    time.sleep(0.05)
    if should_stop and should_stop():
        return {"white": spec["white"], "black": spec["black"], "round": spec.get("round"), "result": "*", "reason": "aborted", "start_fen": None, "uci": [], "san": [], "cp": [], "depth": [], "ms": [], "nodes": [], "book": 0, "plies": 0, "dur": 0.0, "ended": "x"}
    better_white = int(spec["white"][1:]) < int(spec["black"][1:])
    return {"white": spec["white"], "black": spec["black"], "round": spec.get("round"), "result": "1-0" if better_white else "0-1", "reason": "checkmate", "start_fen": None, "uci": [], "san": [], "cp": [], "depth": [],
            "ms": [], "nodes": [], "book": 0, "plies": 0, "dur": 0.0, "ended": "2026-01-01T00:00:00"}


gamemod.play_game = stub_game
six = ["f0", "f1", "f2", "f3"]
st.registry.settings["engines"] += [{"id": "f%d" % i, "name": "Fake %d" % i, "path": sys.executable, "args": '"%s"' % fake, "cwd": tmp, "options": {}, "elo": 3000 - i, "enabled": True} for i in (4, 5)]
six = ["f%d" % i for i in range(6)]
cfg = build_tournament_cfg(st, dict(common, name="elim resume", format="elimination", engines=six, losses=2, ko_games=2, concurrency=2))
t = st.tournaments.create(cfg)
t.start()
t0 = time.time()
while len(t.games) < 4 and time.time() - t0 < 30:
    time.sleep(0.02)
t.stop()
wait(t)
n_stop = len(t.games)
st2 = AppState()
st2.registry.settings["engines"] = st.registry.settings["engines"]
st2.tournaments.root = st.tournaments.root
t2 = st2.tournaments.load(t.id)
check(t.status == "stopped" and len(t2.games) == n_stop and t2.snapshot()["structure"]["kind"] == "bracket", "an elimination stopped after %d games is loaded again with its bracket" % n_stop)
t2.start()
wait(t2)
sx = t2.snapshot()["structure"]
matches = sum(1 for bk in sx["brackets"] for rnd in bk["rounds"] for m in rnd["matches"] if m["kind"] == "real") + len(sx["final"])
check(t2.status == "finished" and sx["champion"] == "f0" and sx["loss"]["f0"] == 0 and all(sx["loss"]["f%d" % i] == 2 for i in range(1, 6)) and matches == 10 and len(t2.games) == 20
      and len({g["key"] for g in t2.games}) == 20, "...and resumed to the end: champion %s with %d losses, %d matches, %d games" % (sx["champion"], sx["loss"].get(sx["champion"], -1), matches, len(t2.games)))
listed = sum(len(m["g"]) for bk in sx["brackets"] for rnd in bk["rounds"] for m in rnd["matches"] if m["kind"] == "real") + sum(len(f["g"]) for f in sx["final"])
check(listed == len(t2.games) == 20, "...every game of the tournament is linked to its match in the drawing (%d of %d)" % (listed, len(t2.games)))
gamemod.play_game = orig
st2.shutdown()

# ----------------------------------------------------------------------------------------------------------------- an engine whose file is gone
cfg = build_tournament_cfg(st, dict(common, name="gone", format="roundrobin", engines=ids, games=2))
t = st.tournaments.create(cfg)
t.cfg["engines"]["f2"]["path"] = os.path.join(tmp, "moved", "engine.exe")        # the file went away after the tournament was made
t.start()
wait(t)
check(t.status == "stopped" and "Fake 2" in (t.error or "") and "missing" in (t.error or "") and not any("failed to start" in (g["reason"] or "") for g in t.games),
      "a missing engine file stops the tournament with a message instead of recording lost games: %s, '%s'" % (t.status, (t.error or "")[:100]))
check(all("f2" not in (g["white"], g["black"]) for g in t.games), "...and no game of that engine was recorded (%d games of the others)" % len(t.games))

# ----------------------------------------------------------------------------------------------------------------- Stop and Continue at once, pause time
slow = dict(common, tc={"mode": "movetime", "ms": 20}, adj={"max_plies": 120})
t = st.tournaments.create(build_tournament_cfg(st, dict(slow, name="stopgo", format="roundrobin", engines=ids + ["f3"], games=2, rounds=2)))
t.start()
t0 = time.time()
while len(t.games) < 2 and time.time() - t0 < 60:
    time.sleep(0.02)
for _ in range(3):
    t.stop()
    t.start()                                                              # without waiting for the 'stopped' state: the new run must wait for the old one's clean-up
    time.sleep(0.3)
wait(t, 180)
crashed = [g for g in t.games if g["reason"] in ("engine crashed", "engine error", "engine not responding", "no move", "illegal move") or "failed to start" in (g["reason"] or "")]
check(t.status == "finished" and len(t.games) == 24 and len({g["key"] for g in t.games}) == 24 and not crashed,
      "Stop and Continue three times in a row: %s, %d of 24 games, %d games lost to a crash" % (t.status, len(t.games), len(crashed)))

t = st.tournaments.create(build_tournament_cfg(st, dict(slow, name="pause", format="roundrobin", engines=ids + ["f3"], games=2, rounds=2)))
t.start()
time.sleep(0.8)
t.pause()
e0 = t.elapsed()
time.sleep(1.5)
e1 = t.elapsed()
check(e1 - e0 < 0.2, "the time of a pause is not counted as elapsed time (%.2f s after 1.5 s of pause)" % (e1 - e0))
t.resume()
time.sleep(0.5)
check(t.elapsed() - e1 > 0.3, "...and counts again after Resume")
t.stop()
wait(t)

# ----------------------------------------------------------------------------------------------------------------- saving the settings while another thread reads the file
t = st.tournaments.create(build_tournament_cfg(st, dict(common, name="locked", format="roundrobin", engines=ids, games=2)))
cfg_file = os.path.join(t.dir, "config.json")
reader = open(cfg_file, "rb")                                               # the list of saved tournaments has the file open right now (Windows then refuses to replace it)
threading.Timer(0.4, reader.close).start()
t0 = time.time()
try:
    t.save_config()
    check(time.time() - t0 >= 0.3, "saving waits until a reader has closed the file instead of failing (%.2f s)" % (time.time() - t0))
except PermissionError as e:
    check(False, "saving failed while the file was open: %s" % e)
reader.close()

# ----------------------------------------------------------------------------------------------------------------- deleting a tournament
q = st.bus.subscribe()
tid = t.id
st.tournaments.delete(tid)
evs = []
while not q.empty():
    evs.append(q.get_nowait())
check(any(e.get("type") == "tournament_deleted" and e.get("tid") == tid for e in evs) and not os.path.exists(os.path.join(st.tournaments.root, tid)), "deleting a tournament tells every open window and removes its folder")

# ----------------------------------------------------------------------------------------------------------------- opening files
fens = os.path.join(tmp, "fens.epd")
open(fens, "w").write("\n".join(["# a list of FENs, as many opening books are",
                                 "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                                 "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
                                 'r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - bm Bb5; id "ruy";',
                                 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1 ; the second move is Black's",
                                 "this is not a position", ""]))
e = openings.load_epd(fens)
check(len(e) == 4 and all(x["fen"] for x in e) and e[3]["fen"].split()[1] == "b", "an opening file of plain FEN lines (and EPD lines with operations, and a comment after a FEN) is read: %d positions" % len(e))
book = openings.build_book({"source": "file", "path": fens, "order": "sequential"}, tmp)
check(len(book) == 4 and book.pick(0, 0, 0, 1)["fen"] == e[0]["fen"], "a book made of it picks the lines in file order")
nothing = os.path.join(tmp, "nothing.epd")
open(nothing, "w").write("nonsense\n")
try:
    openings.build_book({"source": "file", "path": nothing}, tmp)
    check(False, "an opening file without a position is refused")
except ValueError as ex:
    check("no openings" in str(ex), "an opening file without a position is refused: %s" % ex)

# ----------------------------------------------------------------------------------------------------------------- the analysis board when the engine crashes
from arena.play import AnalysisSession                              # noqa: E402

crasher = os.path.join(tmp, "crasher.py")
marker = os.path.join(tmp, "crashed_once")
open(crasher, "w").write(textwrap.dedent("""
    import os, sys
    marker = sys.argv[1]
    for line in sys.stdin:
        c = line.split()
        if not c:
            continue
        if c[0] == "uci":
            print("id name Crasher"); print("uciok", flush=True)
        elif c[0] == "isready":
            print("readyok", flush=True)
        elif c[0] == "go":
            if not os.path.exists(marker):
                open(marker, "w").write("x")
                sys.exit(3)                                         # the first search kills the engine
            print("info depth 5 score cp 12 pv e2e4 e7e5", flush=True)
        elif c[0] == "stop":
            print("bestmove e2e4", flush=True)
        elif c[0] == "quit":
            break
"""))
spec = {"path": sys.executable, "args": '"%s" "%s"' % (crasher, marker), "cwd": tmp, "options": {}, "name": "Crasher", "id": "crasher"}
q = st.bus.subscribe()
ana = AnalysisSession(st.bus, spec)
start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
ana.analyse(start_fen, 1)


def next_event(pred, secs=15):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            ev = q.get(timeout=0.2)
        except Exception:
            continue
        if pred(ev):
            return ev
    return None


ev = next_event(lambda e: e.get("type") == "analysis" and e.get("error"))
check(ev is not None and "engine process ended" in ev["error"], "an engine that crashes during the analysis is reported to the page: %s" % (ev or {}).get("error"))
time.sleep(0.5)
ana.analyse(start_fen, 1)                                                       # the next request works with a fresh engine
ev = next_event(lambda e: e.get("type") == "analysis" and e.get("lines"))
check(ev is not None and not ev.get("error") and ev["lines"][0]["cp"] == 12, "...it was started again and the next request gives lines: %s" % ((ev or {}).get("lines") or [{}])[0].get("pv"))
ana.close()

st.shutdown()
print("\nFAILED: %d" % fails if fails else "\nALL OK")
sys.exit(1 if fails else 0)
