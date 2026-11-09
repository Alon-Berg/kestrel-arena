"""Backend smoke test with the real bundled Kestrel (no UI): engine probe, one game, a 4-game match through the tournament manager, PGN, resume.
usage: python tests/smoke_backend.py"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from arena import formats                                  # noqa: E402
from arena.api import AppState, build_tournament_cfg       # noqa: E402
from arena.engines import Registry                         # noqa: E402
from arena.game import build_pgn, play_game                # noqa: E402
from arena.uci import UciEngine, probe_engine              # noqa: E402


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        check.failed += 1


check.failed = 0

st = AppState()
engines = st.registry.engines()
check(len(engines) >= 1 and engines[0]["builtin"], "bundled Kestrel found: %s" % [e["name"] + " -> " + os.path.basename(e["path"]) for e in engines[:3]])
k = engines[0]

# 1. probe
t0 = time.time()
pr = probe_engine(k["path"])
check(pr["ok"] and "Kestrel" in (pr["id_name"] or ""), "probe: %s (%d options, %.2fs)" % (pr["id_name"], len(pr["options"]), time.time() - t0))
names = [o["name"] for o in pr["options"]]
check("Hash" in names and "Threads" in names and "UCI_Elo" in names, "options parsed: %s" % names[:8])

# 2. a single game, nodes 3000 per move, two separate engine processes
w = UciEngine(k["path"], options={"Hash": 16}).start()
b = UciEngine(k["path"], options={"Hash": 16}).start()
spec = {"gid": 1, "round": 1, "white": "A", "black": "B", "start_fen": None, "opening_moves": ["e2e4", "e7e5", "g1f3", "b8c6"], "tc": {"mode": "nodes", "n": 3000},
        "adj": {"max_plies": 400}, "margin_ms": 500}
upd = []
rec = play_game(spec, w, b, on_update=upd.append)
w.quit(); b.quit()
check(rec["result"] in ("1-0", "0-1", "1/2-1/2"), "single game finished: %s by %s after %d plies (%.1fs)" % (rec["result"], rec["reason"], rec["plies"], rec["dur"]))
check(len(upd) == rec["plies"] - 4, "one update per engine move (%d)" % len(upd))
pgn = build_pgn(rec, {"A": "Kestrel A", "B": "Kestrel B"}, tc=spec["tc"])
check(pgn.startswith("[Event") and "1. e4 { book } 1... e5" in pgn and "[Result " in pgn, "PGN built (%d chars)" % len(pgn))

# 3. formats
ids = ["e%d" % i for i in range(1, 7)]
rr = formats.make_scheduler("roundrobin", ids, {}, {"rounds": 1, "games": 2}, 1)
bt = rr.next_batch([], 0)
pairs = {frozenset((p.a, p.b)) for p in bt}
check(len(bt) == 15 and len(pairs) == 15 and rr.total_games() == 30, "round robin of 6: 15 distinct pairings")
sw = formats.make_scheduler("swiss", ids[:5], {}, {"swiss_rounds": 3, "games": 2}, 1)
b0 = sw.next_batch([], 0)
check(len(b0) == 2 and sw.byes.get(0) is not None, "swiss of 5: 2 pairings + a bye")
ko = formats.make_scheduler("knockout", ids, {"e1": 3, "e2": 2, "e3": 1}, {"ko_games": 2}, 1)
kb = ko.next_batch([], 0)
check(len(kb) == 2, "knockout of 6: round 1 has 2 real matches + 2 byes (%s)" % [(p.a, p.b) for p in kb])

# 4. a real 3-engine round robin through the manager (fixed nodes, 2 games per pairing => 6 games)
cfg = build_tournament_cfg(st, {"name": "smoke", "format": "roundrobin", "engines": [k["id"]], "games": 2})  if False else None
reg = st.registry
reg.settings["engines"] = [{"id": "kb", "name": "Kestrel B (16MB)", "path": k["path"], "cwd": k["cwd"], "options": {"Hash": 16}, "elo": None, "enabled": True},
                           {"id": "kc", "name": "Kestrel C (limited)", "path": k["path"], "cwd": k["cwd"], "options": {"Hash": 16, "UCI_LimitStrength": True, "UCI_Elo": 1500}, "elo": 1500, "enabled": True}]
cfg = build_tournament_cfg(st, {"name": "smoke test", "format": "roundrobin", "engines": [k["id"], "kb", "kc"], "games": 2, "tc": {"mode": "nodes", "n": 2000}, "concurrency": 3,
                                "adj": {"max_plies": 300}, "hash": 16, "openings": {"source": "builtin"}})
tdir = tempfile.mkdtemp(prefix="arena_smoke_")
st.tournaments.root = tdir
t = st.tournaments.create(cfg)
t.start()
t0 = time.time()
while t.status in ("running", "paused", "stopping") and time.time() - t0 < 300:
    time.sleep(0.5)
check(t.status == "finished", "tournament finished: %s in %.1fs (%d/%d games)" % (t.status, time.time() - t0, len(t.games), t.total))
res = {}
for g in t.games:
    pw, pb = formats.points_of(g["result"], True)
    res[g["white"]] = res.get(g["white"], 0) + pw
    res[g["black"]] = res.get(g["black"], 0) + pb
check(abs(sum(res.values()) - len(t.games)) < 1e-9, "points add up: %s" % res)
check(all(g["reason"] not in ("engine crashed", "illegal move", "time forfeit") for g in t.games), "no crashes / illegal moves: %s" % sorted({g["reason"] for g in t.games}))
gm = t.get_game(t.games[0]["gid"])
check(gm and len(gm["fens"]) == gm["plies"] + 1, "game replay data: %d fens" % len(gm["fens"]))
pg = t.pgn_text()
check(pg.count("[Event ") == len(t.games), "PGN has %d games" % pg.count("[Event "))

# 5. resume: a new manager loads the finished tournament
st2 = AppState()
st2.tournaments.root = tdir
t2 = st2.tournaments.load(cfg and t.id)
check(len(t2.games) == len(t.games) and t2.status in ("finished", "stopped"), "reloaded from disk: %d games, status %s" % (len(t2.games), t2.status))
st.shutdown()
print("\nFAILED: %d" % check.failed if check.failed else "\nALL OK")
sys.exit(1 if check.failed else 0)
