"""Tests of the human-like levels of the Play tab (arena/levels.py):
  1. the port against the JavaScript of the web page (tests/levels_vectors.json: the same table, the same decisions for the same random numbers),
  2. the rules in isolation (reading engine lines, steadiness, the weights of the choice, the commands sent to the engine, with a pretend engine),
  3. with the real Kestrel (engines/Kestrel): legal moves, the depth limit, reproducible, a mate in one, weaker levels agree less often with the best move,
  4. a whole Play session at a level and at full strength.
usage: python tests/test_levels.py"""
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import chess                                              # noqa: E402
from arena import levels as L                             # noqa: E402
from arena.play import PlaySession                        # noqa: E402
from arena.uci import UciEngine                           # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def seq_rng(values):
    state = {"i": 0}

    def rng():
        v = values[state["i"] % len(values)]
        state["i"] += 1
        return v
    return rng


# ------------------------------------------------------------------------------------------------ 1. against the JavaScript
vec_path = os.path.join(ROOT, "tests", "levels_vectors.json")
if os.path.exists(vec_path):
    vec = json.load(open(vec_path, encoding="utf-8"))
    same = len(vec["table"]) == len(L.LEVELS)
    for a, b in zip(vec["table"], L.LEVELS):
        same = same and a["name"] == b["name"] and a["rating"] == b["rating"] and a["depth"] == b.get("depth", 0) and a["nodes"] == b.get("nodes", 0) \
            and a["k"] == b["k"] and a["temp"] == b["temp"] and a["slip"] == b["slip"] and a["full"] == b["full"]
    check(same, "the table of levels equals the one of the web page (%d levels)" % len(vec["table"]))
    c = vec["constants"]
    check(all(getattr(L, k) == v for k, v in c.items()), "the constants equal the web page's: %s" % c)
    check([L.blurb(lv) for lv in L.LEVELS] == vec["blurbs"], "the one-line descriptions equal the web page's")
    bad = 0
    for case, want in zip(vec["cases"]["picks"], vec["picks"]):
        got = L.pick_move(case["cands"], case["legal"], L.LEVELS[case["level"]], case["ply"], seq_rng(case["rng"]))
        bad += got != want
    check(bad == 0, "pick_move gives the web page's move in all %d cases (%d differ)" % (len(vec["picks"]), bad))
    check(all(abs(L.steadiness(b) - w) < 1e-12 for b, w in zip(vec["cases"]["steady"], vec["steady"])), "steadiness equals the web page's")
    check(all(abs(L.line_score({"mate": s["score"]} if s["mate"] else {"cp": s["score"]}) - w) < 1e-9 for s, w in zip(vec["cases"]["scores"], vec["scores"])), "line scores (cut, mates) equal the web page's")
    check(all(L.wanted_lines(L.LEVELS[w["level"]], w["ply"], w["n"]) == x for w, x in zip(vec["cases"]["wanted"], vec["wanted"])), "the number of lines asked for equals the web page's")
else:
    print("skip tests/levels_vectors.json is missing (python tools/make_level_vectors.py makes it)")

# ------------------------------------------------------------------------------------------------ 2. the rules
real = [lv for lv in L.LEVELS if not lv["full"]]
check(L.LEVELS[-1]["full"] and not any(lv["full"] for lv in L.LEVELS[:-1]), "the last level is full strength, and only the last")
check(all(b["rating"] > a["rating"] for a, b in zip(real, real[1:])), "ratings rise from level to level")
check(all((lv.get("depth", 0) > 0) != (lv.get("nodes", 0) > 0) for lv in real), "a level limits either the depth or the nodes")
check(all(1 <= lv["k"] <= 8 for lv in real), "k is a valid MultiPV (1 to 8)")
same_limit = lambda a, b: a.get("depth", 0) == b.get("depth", 0) and a.get("nodes", 0) == b.get("nodes", 0)      # noqa: E731
check(all(not same_limit(a, b) or (b["temp"] <= a["temp"] and b["slip"] <= a["slip"]) for a, b in zip(real, real[1:])), "with the same search limit the carelessness never rises with the level")
check(all((b.get("depth") or 99) >= (a.get("depth") or 0) for a, b in zip(real, real[1:])), "the depth never falls with the level")
check(all(not b.get("nodes") or not a.get("nodes") or b["nodes"] >= a["nodes"] for a, b in zip(real, real[1:])), "the node limit never falls with the level")
check(all(lv.get("nodes", 2048) >= 2048 for lv in real), "node limits are at least 2048 (the engine looks at its node count every 2048 nodes)")
check(L.level_for_rating(0)["full"] and L.level_for_rating(1300)["rating"] == 1300 and L.level_for_rating(1390)["rating"] == 1300 and L.level_for_rating(99999)["rating"] == real[-1]["rating"], "level_for_rating")
check(all(len(x["blurb"]) > 20 for x in L.public_list()) and len(L.public_list()) == len(L.LEVELS), "the list for the web UI")

L_ = lambda cp, mv, depth=3, mpv=1: {"depth": depth, "multipv": mpv, "cp": cp, "pv": [mv, "a7a6"]}      # noqa: E731
infos = [L_(10, "e2e4", 1, 1), L_(5, "d2d4", 1, 2), L_(0, "g1f3", 1, 3), L_(12, "e2e4", 2, 1), L_(6, "d2d4", 2, 2), L_(1, "g1f3", 2, 3), L_(20, "e2e4", 3, 1), L_(8, "d2d4", 3, 2)]
c = L.candidates_from(infos, 3)
check(len(c) == 3 and c[0]["uci"] == "e2e4" and c[0]["score"] == 12 and c[0]["depth"] == 2 and c[2]["uci"] == "g1f3", "an unfinished deeper iteration (2 of 3 lines) is ignored")
c = L.candidates_from(infos, 2)
check(len(c) == 2 and c[0]["depth"] == 3 and c[0]["score"] == 20, "the deepest iteration with enough lines is used")
check(L.candidates_from([], 3) == [] and L.candidates_from([{"depth": 1, "multipv": 1, "pv": []}], 1) == [], "no lines, no candidates")
check(L.line_score({"cp": 35}) == 35 and L.line_score({"cp": 9000}) == L.SCORE_CAP and L.line_score({"cp": -9000}) == -L.SCORE_CAP, "scores are cut at the cap")
check(L.line_score({"mate": 1}) > L.line_score({"mate": 5}) > L.SCORE_CAP - 100 and L.line_score({"mate": -2}) < -L.SCORE_CAP + 100, "mates")
check(L.steadiness(0) == 1 and L.steadiness(L.EASE_FROM) == 1 and L.steadiness(-500) == 1 and L.steadiness(L.EASE_TO) == L.EASE_MIN and L.steadiness(5000) == L.EASE_MIN, "steadiness is 1 in level positions and falls to its floor in won ones")

legal = ["e2e4", "d2d4", "g1f3", "a2a3", "h2h4"]
cands = [{"uci": "e2e4", "score": 30, "depth": 2}, {"uci": "d2d4", "score": 10, "depth": 2}, {"uci": "g1f3", "score": -170, "depth": 2}]
flat, sloppy, slippy = {"temp": 0, "slip": 0, "k": 3}, {"temp": 100, "slip": 0, "k": 3}, {"temp": 0, "slip": 1, "k": 3}
rng = random.Random(5).random
check(L.pick_move(cands, legal, flat, 20, rng) == "e2e4", "temperature 0 plays the best move")
check(all(L.pick_move(cands, legal, slippy, 20, random.Random(i).random) != "e2e4" for i in range(500)), "a slip never plays the best move")
check(L.pick_move(cands, legal, slippy, L.SAFE_PLIES - 1, rng) == "e2e4", "no slip in the first moves")
won = [{"uci": "e2e4", "score": 900, "depth": 2}, {"uci": "d2d4", "score": 800, "depth": 2}]
r2 = random.Random(99).random
n = sum(L.pick_move(won, legal, slippy, 40, r2) != "e2e4" for _ in range(6000))
check(abs(n / 6000 - L.EASE_MIN) < 0.03, "a won position cuts the chance of an oversight to %.2f of its value (%.3f)" % (L.EASE_MIN, n / 6000))
count = {"e2e4": 0, "d2d4": 0, "g1f3": 0}
r3 = random.Random(42).random
for _ in range(20000):
    count[L.pick_move(cands, legal, sloppy, 30, r3)] += 1
tot = 1 + 2.718281828 ** -0.2 + 2.718281828 ** -2
check(abs(count["e2e4"] / 20000 - 1 / tot) < 0.015 and abs(count["d2d4"] / 20000 - 2.718281828 ** -0.2 / tot) < 0.015 and abs(count["g1f3"] / 20000 - 2.718281828 ** -2 / tot) < 0.01, "moves are chosen with the weights exp(-loss / temp): %s" % count)

# a pretend engine records what the level asks of it
class FakeEngine:
    def __init__(self, infos, best):
        self.sent, self.infos, self.best = [], infos, best

    def set_option(self, name, value):
        self.sent.append(("option", name, value))
        return True

    def position(self, fen, moves):
        self.sent.append(("position", fen, tuple(moves)))

    def go(self, limits, timeout=60.0, on_info=None, abort=None):
        self.sent.append(("go", dict(limits)))
        for i in self.infos:
            if on_info:
                on_info(i)
        return {"bestmove": self.best, "elapsed_ms": 1.0, "info": {}}


board = chess.Board()
lv = next(x for x in L.LEVELS if x.get("depth") == 2)
fake = FakeEngine([L_(40, "e2e4", 2, 1), L_(30, "d2d4", 2, 2), L_(20, "g1f3", 2, 3), L_(-400, "a2a3", 2, 4)], "e2e4")
mv, cs = L.choose_move(fake, None, [], board, lv, 20, rng=random.Random(3).random)
check(mv in [m.uci() for m in board.legal_moves], "choose_move plays a legal move")
check(("option", "UCI_LimitStrength", False) in fake.sent and ("option", "MultiPV", lv["k"]) in fake.sent and ("go", {"depth": 2}) in fake.sent and fake.sent[-1] == ("option", "MultiPV", 1), "choose_move sets the options, searches to the level's depth and puts MultiPV back: %s" % fake.sent)
one = chess.Board("7k/8/8/8/8/8/6q1/K7 w - - 0 1")           # a single legal move
fake = FakeEngine([], "a1b1")
mv, cs = L.choose_move(fake, None, [], one, lv, 20)
check(mv == "a1b1" and [m.uci() for m in one.legal_moves] == ["a1b1"], "a single legal move is played")
check(fake.sent == [], "... without asking the engine")
node_level = next(x for x in L.LEVELS if x.get("nodes"))
fake = FakeEngine([L_(10, "e2e4", 9, 1)], "e2e4")
mv, cs = L.choose_move(fake, None, [], board, node_level, 30)
check(("go", {"nodes": node_level["nodes"]}) in fake.sent, "a node level searches that many nodes")

# ------------------------------------------------------------------------------------------------ 3. the real Kestrel
KESTREL = os.path.join(ROOT, "engines", "Kestrel", "kestrel-3.2-avx2.exe")
if not os.path.exists(KESTREL):
    for f in sorted(os.listdir(os.path.join(ROOT, "engines", "Kestrel"))) if os.path.isdir(os.path.join(ROOT, "engines", "Kestrel")) else []:
        if f.endswith(".exe"):
            KESTREL = os.path.join(ROOT, "engines", "Kestrel", f)
            break
if not os.path.exists(KESTREL):
    print("skip no engine in engines/Kestrel: the tests with the real engine are skipped")
else:
    FENS = [chess.STARTING_FEN, "r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P1B2/2PBPN2/PP1N1PPP/R2QK2R w KQ - 0 8",
            "2r2rk1/1b2qppp/p2pbn2/1p2p3/4P3/1BN1BN2/PPP2PPP/2RQ1RK1 w - - 0 14", "8/5pk1/6p1/3P4/2K5/8/6PP/8 w - - 0 40"]
    MATE1 = "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1"
    eng = UciEngine(KESTREL, options={"Hash": 16, "Threads": 1}).start(timeout=60)
    deepest = []

    def one_move(level, fen, seed, ply=30):
        eng.new_game()
        b = chess.Board(fen)
        depth_seen = [0]

        def on_info(inf):
            depth_seen[0] = max(depth_seen[0], inf.get("depth", 0))
        mv, _ = L.choose_move(eng, fen, [], b, level, ply, rng=random.Random(seed).random, on_info=on_info)
        deepest.append((level["name"], depth_seen[0]))
        return mv, b

    ok_legal = ok_repeat = ok_depth = True
    for level in L.LEVELS:
        if level["full"]:
            continue
        for fen in FENS:
            a, b = one_move(level, fen, 11)
            a2, _ = one_move(level, fen, 11)
            ok_legal = ok_legal and chess.Move.from_uci(a) in b.legal_moves
            ok_repeat = ok_repeat and a == a2
            if level.get("depth"):
                ok_depth = ok_depth and deepest[-1][1] <= level["depth"]
    check(ok_legal, "every level: a legal move in four positions")
    check(ok_repeat, "every level: the same random numbers give the same move")
    check(ok_depth, "depth levels stay inside their depth limit")
    # a mate in one is found by the strong levels
    bad = []
    for level in [x for x in L.LEVELS if not x["full"] and x["rating"] >= 1700]:
        found = sum(one_move(level, MATE1, s, 40)[0] == "d1d8" for s in range(1, 21))
        if found < 19:
            bad.append((level["name"], found))
    check(not bad, "from level 1700 up the mate in one is found in at least 19 of 20 tries %s" % (bad or ""))
    # weaker levels agree less often with the best move of the strong level
    best_of = {fen: one_move(real[-1], fen, 1)[0] for fen in FENS}

    def agree(level):
        n = t = 0
        for fen in FENS:
            for s in range(1, 13):
                t += 1
                n += one_move(level, fen, s)[0] == best_of[fen]
        return n / t
    a_weak, a_mid, a_strong = agree(real[0]), agree(real[len(real) // 2]), agree(real[-2])
    print("     agreement with the best move: first level %.2f, middle level %.2f, second strongest level %.2f" % (a_weak, a_mid, a_strong))
    check(a_weak < a_mid + 0.05 and a_mid < a_strong + 0.05 and a_weak < a_strong, "weaker levels agree with the best move less often")
    eng.quit()

    # ---------------------------------------------------------------------------------------------- 4. a whole Play session
    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, ev):
            self.events.append(ev)

    spec = {"path": KESTREL, "args": "", "cwd": os.path.dirname(KESTREL), "options": {"Hash": 16}, "name": "Kestrel 3.2", "id": "k"}

    def play_one(strength, limits):
        bus = Bus()
        s = PlaySession(bus, spec, True, None, limits, strength)
        t0 = time.time()
        s.human_move("e2e4")
        while (s.thinking or len(s.uci) < 2) and time.time() - t0 < 20:
            time.sleep(0.05)
        out = (list(s.uci), round(time.time() - t0, 2), s.state(), s.pgn())
        s.close()
        return out

    uci, secs, st, pgn = play_one({"level": 1300}, {"movetime": 1000})
    after_e4 = chess.Board()
    after_e4.push_uci("e2e4")
    check(len(uci) == 2 and chess.Move.from_uci(uci[1]) in after_e4.legal_moves, "a session at level 1300 answers 1.e4 (%s in %.2f s)" % (uci, secs))
    check(0.4 <= secs <= 3.0, "a level takes a moment before it answers (%.2f s)" % secs)
    check(st["engine"] == "Kestrel 3.2 - Level 5 (about 1300)" and st["level"] == 1300 and "Kestrel 3.2 - Level 5 (about 1300)" in pgn, "the level shows in the game state and the PGN: %s" % st["engine"])
    uci, secs, st, pgn = play_one({}, {"movetime": 100})
    check(len(uci) == 2 and st["level"] is None and st["engine"] == "Kestrel 3.2", "a session at full strength is unchanged (%s in %.2f s)" % (uci, secs))
    uci, secs, st, pgn = play_one({"level": 0}, {"movetime": 100})
    check(len(uci) == 2 and st["level"] is None, "level 0 is full strength")

print("\n%s" % ("ALL OK" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
