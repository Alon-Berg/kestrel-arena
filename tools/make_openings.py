"""Builds data/openings.epd: balanced, varied opening positions made with the bundled Kestrel.

Each candidate is a random 8-ply line from the start position in which every ply is picked among the engine's top moves (within 45 cp of the best, MultiPV 4,
3000 nodes); the final position is kept when a deeper search (30000 nodes) scores it within +-40 cp for the side to move. Positions are de-duplicated.
usage: python tools/make_openings.py [count=600] [seed=1]      (about a minute; uses one CPU thread)"""
import os
import random
import sys
import time

import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.engines import Registry                                # noqa: E402
from arena import paths                                           # noqa: E402
from arena.uci import UciEngine, score_to_cp                      # noqa: E402

count = int(sys.argv[1]) if len(sys.argv) > 1 else 600
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
rng = random.Random(seed)
reg = Registry(paths.data_dir(), paths.resource_dir(), paths.app_dir())
k = reg.builtin_engines()[0]
eng = UciEngine(k["path"], cwd=k["cwd"], options={"Hash": 32, "MultiPV": 4}).start()
eng.new_game()


def top_moves(board):
    """The engine's candidate moves with scores (cp, side to move) at 3000 nodes, MultiPV 4."""
    lines = {}

    def on_info(i):
        if "pv" in i and ("cp" in i or "mate" in i) and not i.get("bound"):
            lines[i.get("multipv", 1)] = (i["pv"][0], score_to_cp(i))
    eng.position(board.fen(), [])
    eng.go({"nodes": 3000}, timeout=20, on_info=on_info)
    return [lines[m] for m in sorted(lines)]


seen, out = set(), []
t0 = time.time()
tried = 0
while len(out) < count and time.time() - t0 < 900:
    tried += 1
    b = chess.Board()
    ok = True
    for ply in range(8):
        cands = top_moves(b)
        if not cands:
            ok = False
            break
        best = cands[0][1]
        pool = [m for m, s in cands if s is not None and best is not None and s >= best - 45] or [cands[0][0]]
        b.push_uci(rng.choice(pool))
        if b.is_game_over():
            ok = False
            break
    if not ok:
        continue
    key = " ".join(b.fen().split()[:4])
    if key in seen:
        continue
    eng.new_game()
    eng.position(b.fen(), [])
    r = eng.go({"nodes": 30000}, timeout=30)
    cp = score_to_cp(r.get("info"))
    if cp is None or abs(cp) > 40:
        continue
    seen.add(key)
    out.append("%s ce %d;" % (" ".join(b.fen().split()[:4]), cp))
    if len(out) % 50 == 0:
        print("%d positions (%d candidates, %.0fs)" % (len(out), tried, time.time() - t0), flush=True)
eng.quit()
rng.shuffle(out)
dst = os.path.join(ROOT, "data", "openings.epd")
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print("wrote %s: %d balanced positions from %d candidates in %.0fs" % (dst, len(out), tried, time.time() - t0))
