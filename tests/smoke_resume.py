"""Real-engine test of stop / resume (a new process would load the same files) and of Swiss, knockout and N-loss elimination through the tournament manager.
usage: python tests/smoke_resume.py"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.api import AppState, build_tournament_cfg       # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails += 1


st = AppState()
k = st.registry.engines()[0]
st.registry.settings["engines"] = [
    {"id": "e%d" % i, "name": "Kestrel L%d" % i, "path": k["path"], "cwd": k["cwd"], "options": {"Hash": 16, "UCI_LimitStrength": True, "UCI_Elo": 1400 + 300 * i}, "elo": 1400 + 300 * i, "enabled": True}
    for i in range(5)]
tdir = tempfile.mkdtemp(prefix="arena_resume_")
st.tournaments.root = tdir
ids = ["e%d" % i for i in range(5)]


def wait(t, secs=240):
    t0 = time.time()
    while t.status in ("running", "paused", "stopping") and time.time() - t0 < secs:
        time.sleep(0.2)


common = {"tc": {"mode": "nodes", "n": 600}, "concurrency": 2, "hash": 16, "adj": {"max_plies": 200}, "openings": {"source": "builtin"}}

# 1. round robin of 4, stopped after a few games, then resumed from disk
cfg = build_tournament_cfg(st, dict(common, name="resume test", format="roundrobin", engines=ids[:4], games=2))
t = st.tournaments.create(cfg)
t.start()
t0 = time.time()
while len(t.games) < 3 and time.time() - t0 < 120:
    time.sleep(0.1)
t.stop()
wait(t)
done_at_stop = len(t.games)
check(t.status == "stopped" and 3 <= done_at_stop < 12, "stopped after %d of 12 games (status %s)" % (done_at_stop, t.status))
st2 = AppState()
st2.tournaments.root = tdir
t2 = st2.tournaments.load(t.id)
check(len(t2.games) == done_at_stop and t2.status == "stopped", "reloaded: %d games, status %s" % (len(t2.games), t2.status))
t2.start()
wait(t2)
keys = [g["key"] for g in t2.games]
check(t2.status == "finished" and len(t2.games) == 12 and len(set(keys)) == 12, "resumed to the end: %d games, %d distinct keys, status %s" % (len(t2.games), len(set(keys)), t2.status))

# 2. Swiss with 5 engines (odd), 3 rounds, 2 games per pairing = 12 games
cfg = build_tournament_cfg(st, dict(common, name="swiss test", format="swiss", engines=ids, games=2, swiss_rounds=3))
st3 = AppState()
st3.tournaments.root = tdir
st3.registry.settings["engines"] = st.registry.settings["engines"]
t3 = st3.tournaments.create(cfg)
t3.start()
wait(t3)
check(t3.status == "finished" and len(t3.games) == 12, "swiss: %s, %d games (expected 12)" % (t3.status, len(t3.games)))
check(len(t3.sched.byes) == 3 and len(set(t3.sched.byes.values())) == 3, "swiss byes: %s" % t3.sched.byes)

# 3. knockout with 5 engines
cfg = build_tournament_cfg(st, dict(common, name="knockout test", format="knockout", engines=ids, ko_games=2, seeding="rating"))
st4 = AppState()
st4.tournaments.root = tdir
st4.registry.settings["engines"] = st.registry.settings["engines"]
t4 = st4.tournaments.create(cfg)
t4.start()
wait(t4)
snap = t4.snapshot()
check(t4.status == "finished" and snap["structure"].get("champion") in ids, "knockout: %s, %d games, champion %s" % (t4.status, len(t4.games), snap["structure"].get("champion")))

# 3b. elimination with 2 losses and 5 engines: stopped after a few games, loaded again like after a restart of the app, resumed to the end
cfg = build_tournament_cfg(st, dict(common, name="elimination test", format="elimination", engines=ids, ko_games=2, losses=2, seeding="rating", tc={"mode": "depth", "d": 1}))      # (the five engines play alike: ties are broken by many extra games, so a depth-1 search)
check(cfg["params"]["losses"] == 2 and cfg["format"] == "elimination", "elimination: the setup is stored (2 losses)")
st6 = AppState()
st6.tournaments.root = tdir
st6.registry.settings["engines"] = st.registry.settings["engines"]
t6 = st6.tournaments.create(cfg)
t6.start()
t0 = time.time()
while len(t6.games) < 5 and time.time() - t0 < 120:
    time.sleep(0.1)
t6.stop()
wait(t6)
n_stop = len(t6.games)
check(t6.status == "stopped" and 5 <= n_stop < 30, "elimination stopped after %d games (status %s)" % (n_stop, t6.status))
st7 = AppState()
st7.tournaments.root = tdir
t7 = st7.tournaments.load(t6.id)
check(len(t7.games) == n_stop and t7.status == "stopped" and t7.snapshot()["structure"]["kind"] == "elimination", "elimination reloaded: %d games, the bracket is rebuilt from them" % len(t7.games))
t7.start()
wait(t7)
snap = t7.snapshot()
sx = snap["structure"]
keys = [g["key"] for g in t7.games]
matches = sum(1 for r in sx["rounds"] for m in r["matches"] if not m["bye"])
champ = sx["champion"]
check(t7.status == "finished" and champ in ids and len(set(keys)) == len(keys), "elimination resumed to the end: %s, %d games, champion %s" % (t7.status, len(t7.games), champ))
check(sx["loss"][champ] < 2 and all(sx["loss"][i] == 2 for i in ids if i != champ) and len(sx["out"]) == 4 and matches == 2 * 4 + sx["loss"][champ] and len(t7.games) >= 2 * matches,
      "elimination: the champion has %d losses, the other four 2, %d matches, %d games" % (sx["loss"][champ], matches, len(t7.games)))
check(t7.pgn_text().count("[Event ") == len(t7.games), "elimination: PGN has every game")
lst = [x for x in st7.tournaments.list() if x["id"] == t6.id][0]
check(lst["format"] == "elimination" and lst["losses"] == 2 and lst["status"] == "finished", "elimination: the saved list knows the format and the number of losses")

# 4. pause and resume without stopping
cfg = build_tournament_cfg(st, dict(common, name="pause test", format="roundrobin", engines=ids[:3], games=2))
t5 = st.tournaments.create(cfg)
t5.start()
time.sleep(1.0)
t5.pause()
time.sleep(2.0)
n1 = len(t5.games)
time.sleep(2.0)
check(len(t5.games) <= n1 + 2 and t5.status == "paused", "paused: games %d -> %d during 2 s, active %d" % (n1, len(t5.games), len(t5.active)))
t5.resume()
wait(t5)
check(t5.status == "finished" and len(t5.games) == 6, "paused tournament resumed and finished (%d games)" % len(t5.games))
pg = t5.pgn_text()
check(pg.count("[Event ") == 6 and "pause test" in pg, "PGN export has all games")

for s in (st, st2, st3, st4, st6, st7):
    s.shutdown()
print("\nFAILED: %d" % fails if fails else "\nALL OK")
sys.exit(1 if fails else 0)
