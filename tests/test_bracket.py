"""The bracket of the elimination format (arena/bracket.py: winners bracket + losers brackets, formats.Elimination: the schedule): structure of the classic double elimination, the rules of the
losers brackets for any number of losses, byes, order of play, drawing positions, and whole tournaments with invented results checked by an independent referee (every player has exactly as many
losses as the number of the bracket he plays in, one survivor, the right number of matches, the final stage, a resumed tournament regenerates the same batches).
usage: python tests/test_bracket.py"""
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena import formats                                   # noqa: E402
from arena.bracket import Bracket                           # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def real(br):
    return [br.matches[i] for i in br.real]


# --------------------------------------------------------------------------------------------------------------------- the classic double elimination
br = Bracket(8, 2)
M = br.matches
wb, lb = br.brackets[0]["rounds"], br.brackets[1]["rounds"]
check([len(r) for r in wb] == [4, 2, 1] and [len(r) for r in lb] == [2, 2, 1, 1], "8 players: winners bracket 4-2-1 matches, losers bracket 2-2-1-1: %s %s" % ([len(r) for r in wb], [len(r) for r in lb]))
check([M[i].src for i in lb[0]] == [(("L", wb[0][0]), ("L", wb[0][1])), (("L", wb[0][2]), ("L", wb[0][3]))], "losers round 1: the losers of the first round of the winners bracket meet in pairs")
check([M[i].src for i in lb[1]] == [(("W", lb[0][0]), ("L", wb[1][1])), (("W", lb[0][1]), ("L", wb[1][0]))], "losers round 2: the survivors meet the losers of winners round 2, in reversed order (nobody meets the player he has just beaten)")
check(M[lb[2][0]].src == (("W", lb[1][0]), ("W", lb[1][1])) and M[lb[3][0]].src == (("W", lb[2][0]), ("L", wb[2][0])), "losers round 3: the survivors meet; round 4: the survivor meets the loser of the winners final")
check(br.first_final() == (("W", wb[2][0]), ("W", lb[3][0])), "the grand final: the winner of the winners final against the winner of the losers final")
check([M[i].step for r in wb for i in r] == [1, 1, 1, 1, 2, 2, 3] and [M[i].step for r in lb for i in r] == [2, 2, 3, 3, 4, 5], "order of play: winners 1 1 1 1 | 2 2 | 3, losers 2 2 | 3 3 | 4 | 5")
check([br.matches[i].no for i in br.real] == list(range(1, 14)) and len(br.steps) == 5, "13 matches before the grand final, numbered in the order of play, in 5 steps")
for n, sizes in ((16, [4, 4, 2, 2, 1, 1]), (32, [8, 8, 4, 4, 2, 2, 1, 1]), (4, [1, 1]), (64, [16, 16, 8, 8, 4, 4, 2, 2, 1, 1])):
    b2 = Bracket(n, 2)
    check([len(r) for r in b2.brackets[1]["rounds"]] == sizes, "%d players: the losers bracket has %s matches per round" % (n, sizes))
b2 = Bracket(2, 2)
check(b2.brackets[1]["rounds"] == [] and b2.first_final() == (("W", 0), ("L", 0)), "2 players: no losers bracket, the grand final is the winner against the loser of the only match")

# --------------------------------------------------------------------------------------------------------- counts, byes, order, usage of every result
problems = []
for N in range(1, 7):
    for n in list(range(2, 41)) + [48, 64, 65, 102]:
        b = Bracket(n, N)
        if b.counts() != [max(0, n - 1 - k) for k in range(N)]:
            problems.append("n=%d N=%d: matches per bracket %s" % (n, N, b.counts()))
        used_w, used_l = {}, {}
        for m in b.matches:
            for sl in m.src:
                if sl is not None and sl[1] >= m.id and sl[0] != "S":
                    problems.append("n=%d N=%d: match %d refers to a later match" % (n, N, m.id))
                c = b.canon(sl)
                if m.kind == "real" and c is not None and c[0] != "S":                 # (a bye only passes its player on)
                    d = used_w if c[0] == "W" else used_l
                    d[c[1]] = d.get(c[1], 0) + 1
        for bk in b.brackets:                                                          # the champion of a bracket is used by the final stage
            c = b.canon(bk["champion"])
            if c is not None and c[0] != "S":
                d = used_w if c[0] == "W" else used_l
                d[c[1]] = d.get(c[1], 0) + 1
        for m in b.matches:                                                            # every real match: its winner is used once; its loser goes to exactly one place of the next bracket (nowhere from the last one)
            if m.kind == "real":
                want_l = 0 if m.k == N - 1 else 1
                if used_l.get(m.id, 0) != want_l or used_w.get(m.id, 0) != 1:
                    problems.append("n=%d N=%d: match %d (bracket %d): winner used %d times, loser %d times" % (n, N, m.id, m.k, used_w.get(m.id, 0), used_l.get(m.id, 0)))
        steps_ok = all(m.step == 1 + max([b.matches[s[1]].step for s in map(b.canon, m.src) if s and s[0] != "S"] or [0]) for m in real(b))
        if not steps_ok or [x.no for x in sorted(real(b), key=lambda x: x.no)] != list(range(1, len(b.real) + 1)):
            problems.append("n=%d N=%d: order of play" % (n, N))
        for st in b.steps:                                                              # the matches of a step do not depend on each other
            ids = set(st)
            for i in st:
                for s in map(b.canon, b.matches[i].src):
                    if s and s[0] != "S" and s[1] in ids:
                        problems.append("n=%d N=%d: step matches depend on each other" % (n, N))
        for bk in b.brackets:                                                           # drawing: strictly increasing rows in a column, a match with two feeders in the middle of them
            for rnd in bk["rounds"]:
                ys = [b.matches[i].y for i in rnd]
                if ys != sorted(set(ys)):
                    problems.append("n=%d N=%d: rows of a column are not increasing" % (n, N))
            for i in [i for rnd in bk["rounds"] for i in rnd]:
                m = b.matches[i]
                same = [x for x in (s[1] for s in m.src if s and s[0] != "S") if b.matches[x].k == m.k]
                if len(same) == 2 and m.y != (b.matches[same[0]].y + b.matches[same[1]].y) / 2:
                    problems.append("n=%d N=%d: a match is not between its feeders" % (n, N))
check(not problems, "counts per bracket (n-1-k), every loser goes down exactly once, order of play, drawing rows: n = 2..40, 48, 64, 65, 102 x 1..6 losses%s" % ("" if not problems else " - " + "; ".join(problems[:4])))

b = Bracket(11, 2)
r1 = [b.matches[i] for i in b.brackets[0]["rounds"][0]]
check(sorted(m.src[0][1] for m in r1 if m.kind == "bye") == [0, 1, 2, 3, 4] and sum(1 for m in r1 if m.kind == "real") == 3, "11 players: the top 5 seeds have a bye in round 1, 3 real matches")
lb1 = [b.matches[i] for i in b.brackets[1]["rounds"][0]]
check([m.kind for m in lb1] == ["bye", "empty", "bye", "bye"], "...so the first losers round has three players who go on without a match and one empty place: %s" % [m.kind for m in lb1])
check(b.canon(("W", lb1[0].id)) is not None and b.canon(("W", lb1[1].id)) is None, "a bye in the losers bracket passes the player on, an empty place nobody")

# one loss = the knockout
same = True
for n in range(2, 34):
    ids = ["p%d" % i for i in range(1, n + 1)]
    ko = formats.make_scheduler("knockout", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"ko_games": 2}, 1)
    br1 = Bracket(n, 1)
    pairs_ko = [(x, y) for x, y in ko._first_round() if x is not None and y is not None]
    pairs_br = [(ids[m.src[0][1]], ids[m.src[1][1]]) for m in (br1.matches[i] for i in br1.brackets[0]["rounds"][0]) if m.kind == "real"]
    same &= pairs_ko == pairs_br and len(br1.brackets) == 1 and len(br1.real) == n - 1
check(same, "with one loss the bracket is the knockout's: the same first round and byes (2..33 players)")


# ---------------------------------------------------------------------------------------------------------------------------------- whole tournaments
def drive(sched, fn, max_batches=5000):
    games, batches = [], []
    while True:
        done_before = [g for g in games if g["batch"] < len(batches)]
        batch = sched.next_batch(done_before, len(batches))
        if batch is None:
            return games, batches
        for p in batch:
            for g in range(p.games):
                w, b = (p.a, p.b) if g % 2 == 0 else (p.b, p.a)
                games.append({"white": w, "black": b, "result": fn(w, b, len(batches), g), "batch": len(batches), "pair": p.index, "gid": len(games) + 1})
        batches.append(batch)
        if len(batches) > max_batches:
            raise RuntimeError("runaway tournament")


def results(policy, order, seed):
    pos = {s: k for k, s in enumerate(order)}

    def fn(w, b, batch, g):
        if policy == "draws":
            return "1/2-1/2"
        if policy in ("chalk", "upset"):
            return "1-0" if (pos[w] < pos[b]) == (policy == "chalk") else "0-1"
        r = random.Random("%s|%s|%s|%s|%s" % (seed, w, b, batch, g))
        x = r.random()
        return "1/2-1/2" if x < 0.3 else ("1-0" if r.random() < 0.5 else "0-1")
    return fn


def shape(batches):
    return [[(p.a, p.b, p.games, p.round, p.index, p.tag) for p in batch] for batch in batches]


def referee(sched, games, batches, N):
    """Plays the rules again from the games alone; returns the problems."""
    bad = []
    br, order = sched.bracket, sched.seeds
    n = len(order)
    rank = {s: k for k, s in enumerate(order)}
    loss = {s: 0 for s in order}
    st = sched.structure(games)
    by_round, batch_round, pairing, score = {}, {}, {}, {}
    for bi, batch in enumerate(batches):
        for p in batch:
            by_round.setdefault(p.round, []).append((bi, p))
            if batch_round.setdefault(bi, p.round) != p.round:
                bad.append("batch %d mixes rounds" % bi)
            if pairing.setdefault((p.round, p.index), (p.a, p.b)) != (p.a, p.b):
                bad.append("a tie-break changes the players of a match")
    for g in games:
        r = batch_round[g["batch"]]
        a, b = pairing[(r, g["pair"])]
        pa, pb = formats.points_of(g["result"], g["white"] == a)
        s = score.setdefault((r, g["pair"]), [0.0, 0.0])
        s[0] += pa
        s[1] += pb
    matches = 0
    per_bracket = [0] * N
    nsteps = len(br.steps)
    final_rounds = 0
    for r in sorted(by_round):
        items = by_round[r]
        first = min(bi for bi, p in items)
        lvl0 = [p for bi, p in items if bi == first]
        play = [s for p in lvl0 for s in (p.a, p.b)]
        if len(play) != len(set(play)):
            bad.append("round %d: a player plays twice" % r)
        if any(loss[s] >= N for s in play):
            bad.append("round %d: a player who is out plays" % r)
        if any(p.tag for p in lvl0) or any(p.games != sched.games for p in lvl0):
            bad.append("round %d: wrong games or a tie-break first" % r)
        if r <= nsteps:
            ids = br.steps[r - 1]
            if len(lvl0) != len(ids):
                bad.append("step %d: %d matches, the bracket has %d" % (r, len(lvl0), len(ids)))
            for p in sorted(lvl0, key=lambda p: p.index):
                m = br.matches[ids[p.index]]
                if not (loss[p.a] == loss[p.b] == m.k):
                    bad.append("step %d: match %d of bracket %d between players with %d and %d losses" % (r, m.no, m.k, loss[p.a], loss[p.b]))
                per_bracket[m.k] += 1
        else:
            final_rounds += 1
            alive = [s for s in order if loss[s] < N]
            two = sorted(alive, key=lambda s: (-loss[s], rank[s]))[:2]
            if len(lvl0) != 1 or {lvl0[0].a, lvl0[0].b} != set(two):
                bad.append("final stage round %d: not the two players with the most losses" % r)
            else:
                f = st["final"][final_rounds - 1] if final_rounds <= len(st["final"]) else {}
                if (f.get("a"), f.get("b"), f.get("la"), f.get("lb")) != (lvl0[0].a, lvl0[0].b, loss[lvl0[0].a], loss[lvl0[0].b]):
                    bad.append("final stage round %d: the drawing shows other players or losses (before the match) than the games" % r)
        for p in lvl0:
            sa, sb = score[(r, p.index)]
            win = p.a if sa > sb else (p.b if sb > sa else (p.a if rank[p.a] < rank[p.b] else p.b))
            loss[p.b if win == p.a else p.a] += 1
            matches += 1
    alive = [s for s in order if loss[s] < N]
    if len(alive) != 1:
        bad.append("%d survivors" % len(alive))
        return bad
    champ = alive[0]
    if any(loss[s] != N for s in order if s != champ):
        bad.append("somebody else has not lost %d matches" % N)
    if matches != N * (n - 1) + loss[champ]:
        bad.append("%d matches, expected %d" % (matches, N * (n - 1) + loss[champ]))
    if per_bracket != br.counts():
        bad.append("matches per bracket %s, the bracket has %s" % (per_bracket, br.counts()))
    if st["kind"] != "bracket" or st["champion"] != champ or st["loss"] != loss or len(st["out"]) != n - 1 or len(st["final"]) != final_rounds:
        bad.append("the structure for the drawing differs (champion %s, %d final matches, referee %s %d)" % (st["champion"], len(st["final"]), champ, final_rounds))
    if any(not m["done"] for bk in st["brackets"] for rnd in bk["rounds"] for m in rnd["matches"]):
        bad.append("a match of the drawing is not done in a finished tournament")
    listed = sorted(g for bk in st["brackets"] for rnd in bk["rounds"] for m in rnd["matches"] if m["kind"] == "real" for g in m["g"]) + sorted(g for f in st["final"] for g in f["g"])
    if sorted(listed) != [g["gid"] for g in games]:
        bad.append("the games of the matches in the drawing are not all the games (%d of %d)" % (len(listed), len(games)))
    if len(games) < sched.total_games():
        bad.append("fewer games than the minimum")
    return bad


def run(n, N, policy, seed=1, gpm=2, seeding="rating"):
    ids = ["p%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - 10 * k for k, i in enumerate(ids)}
    params = {"losses": N, "ko_games": gpm, "seeding": seeding, "bracket": True}
    sched = formats.make_scheduler("elimination", ids, ratings, params, seed)
    games, batches = drive(sched, results(policy, sched.seeds, seed))
    bad = referee(sched, games, batches, sched.losses)
    again = formats.make_scheduler("elimination", ids, ratings, params, seed)
    for b in range(len(batches)):
        if shape([again.next_batch([g for g in games if g["batch"] < b], b)]) != shape([batches[b]]):
            bad.append("batch %d is not regenerated identically after a resume" % b)
            break
    return sched, games, batches, bad


for N in (1, 2, 3, 4):
    problems, runs, total = [], 0, 0
    for n in range(2, 18):
        for policy in ("chalk", "upset", "draws", "random", "random"):
            _, games, _, bad = run(n, N, policy, seed=n * 5 + runs)
            runs += 1
            total += len(games)
            problems += ["n=%d %s: %s" % (n, policy, x) for x in bad]
    check(not problems, "bracket with %d loss%s, 2..17 engines x 5 result patterns (%d tournaments, %d games): the rules hold, one survivor, a resumed tournament regenerates the same batches%s"
          % (N, "" if N == 1 else "es", runs, total, "" if not problems else " - " + "; ".join(problems[:4])))

problems = []
for N, n, policy, gpm, seeding in [(5, 9, "random", 2, "rating"), (3, 24, "random", 4, "random"), (2, 33, "random", 2, "list"), (2, 45, "random", 2, "rating"), (5, 6, "random", 2, "rating"),
                                   (10, 5, "random", 2, "rating"), (12, 4, "random", 2, "rating"), (3, 40, "draws", 2, "rating")]:
    sched, games, batches, bad = run(n, N, policy, seed=n, gpm=gpm, seeding=seeding)
    problems += ["N=%d n=%d: %s" % (sched.losses, n, x) for x in bad]
check(not problems, "up to 10 losses, 24..45 engines, 4 games per match, other seedings: the rules hold%s" % ("" if not problems else " - " + "; ".join(problems[:4])))

# one loss = the knockout: the same matches, games and tie-breaks and the same champion (results that depend on the players only: the knockout lets the upper place of the bracket start as White, and plays
# round by round, while the bracket starts a match as soon as both its players are known, e.g. two seeds that both had a bye in the first round)
def by_player(policy, order, seed):
    pos = {s: k for k, s in enumerate(order)}

    def fn(w, b, batch, g):
        better, worse = (w, b) if pos[w] < pos[b] else (b, w)
        if policy == "draws":
            return "1/2-1/2"
        if policy == "upset":
            win = worse
        else:
            r = random.Random("%s|%s|%s|%s" % (seed, better, worse, g))
            x = r.random()
            if x < 0.3:
                return "1/2-1/2"
            win = better if r.random() < 0.5 else worse
        return "1-0" if win == w else "0-1"
    return fn


same = True
for n in range(2, 18):
    ids = ["p%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - 10 * k for k, i in enumerate(ids)}
    for policy in ("random", "draws", "upset"):
        ko = formats.make_scheduler("knockout", ids, ratings, {"ko_games": 2}, 5)
        el = formats.make_scheduler("elimination", ids, ratings, {"losses": 1, "ko_games": 2, "bracket": True}, 5)
        fn = by_player(policy, ko.seeds, 3)
        g1, b1 = drive(ko, fn)
        g2, b2 = drive(el, fn)
        cut = lambda bs: sorted((sorted((p.a, p.b)), p.games, p.tag) for batch in bs for p in batch)       # noqa: E731
        if cut(b1) != cut(b2) or len(g1) != len(g2) or ko.structure(g1)["champion"] != el.structure(g2)["champion"]:
            same = False
            print("     differs for n=%d %s" % (n, policy))
check(same, "with one loss the tournament is the knockout's: the same matches, games and tie-breaks, the same champion (2..17 engines)")

# ---------------------------------------------------------------------------------------------------------------- the grand final and its reset
ids = ["p%d" % i for i in range(1, 9)]
ratings = {i: 3000 - k for k, i in enumerate(ids)}


def make():
    return formats.make_scheduler("elimination", ids, ratings, {"losses": 2, "bracket": True}, 1)


sched = make()
games, batches = drive(sched, results("chalk", sched.seeds, 1))
st = sched.structure(games)
check(st["champion"] == "p1" and st["loss"]["p1"] == 0 and len(st["final"]) == 1 and st["final"][0]["a"] == "p1" and st["final"][0]["b"] == "p2" and len(games) == 14 * 2,
      "double elimination of 8, the better seed always wins: 14 matches (13 + one grand final), champion p1 without a loss")
sched = make()
rank0 = {s: k for k, s in enumerate(sched.seeds)}
chalk = results("chalk", sched.seeds, 1)
last = len(batches) - 1


def reset_results(w, b, batch, g):                                         # the same until the grand final; there the worse seed (the champion of the losers bracket) wins every game
    return chalk(w, b, batch, g) if batch < last else ("1-0" if rank0[w] > rank0[b] else "0-1")


games, batches = drive(sched, reset_results)
st = sched.structure(games)
f = st["final"]
check(len(f) == 2 and f[0]["a"] == "p1" and f[0]["b"] == "p2" and f[0]["winner"] == "p2" and {f[1]["a"], f[1]["b"]} == {"p1", "p2"} and st["champion"] == f[1]["winner"] and st["loss"][st["champion"]] == 1
      and len(games) == 15 * 2 and not referee(sched, games, batches, 2) and (f[0]["la"], f[0]["lb"], f[1]["la"], f[1]["lb"]) == (0, 1, 1, 1),
      "the champion of the losers bracket wins the grand final: both have one loss, the deciding match (the reset) follows (losses before the matches: 0 and 1, then 1 and 1): %d games, champion %s" % (len(games), st["champion"]))

# a triple elimination of 5 where the champion of the losers brackets keeps winning: the final stage goes on until one player is left
sched3 = formats.make_scheduler("elimination", ids[:5], ratings, {"losses": 3, "bracket": True}, 1)
games3, batches3 = drive(sched3, results("upset", sched3.seeds, 1))
check(not referee(sched3, games3, batches3, 3) and sched3.structure(games3)["champion"] == "p5", "triple elimination of 5, the worse seed always wins: the rules hold, champion p5 (%d games)" % len(games3))

# ------------------------------------------------------------------------------------------------------------------- rounds in progress (the drawing)
sched = make()
games, batches = drive(sched, results("chalk", sched.seeds, 1))
partial = [g for g in games if g["batch"] == 0] + [g for g in games if g["batch"] == 1][:3]       # all of step 1, and 3 of the 8 games of step 2 (the first match complete, the second half)
st = sched.structure(partial)
by_no = {m["no"]: m for bk in st["brackets"] for rnd in bk["rounds"] for m in rnd["matches"] if m["no"]}
check(by_no[5]["a"] == "p1" and by_no[5]["b"] == "p4" and by_no[5]["done"] and by_no[5]["winner"] == "p1" and by_no[6]["sa"] + by_no[6]["sb"] == 1 and not by_no[6]["done"],
      "a step in progress: the finished match is decided, the half-played one shows its score")
check(by_no[9]["a"] == "p1" and by_no[9]["b"] is None and by_no[9]["s"] == [["W", 5], ["W", 6]] and by_no[10]["a"] is None and st["champion"] is None
      and by_no[7]["a"] == "p8" and by_no[7]["b"] == "p5" and st["loss"]["p4"] == 1 and st["loss"]["p8"] == 1,
      "...the winner of the finished match is in his next place at once, the place of the other says where its player comes from (winner of #6), losers of finished matches are in their places, losses are counted")
check(sched.next_batch(partial, 1)[0].index == 0 and shape([sched.next_batch(partial, 0)]) == shape([batches[0]]), "...and the schedule continues with the same batch")
st0 = sched.structure([])
check(st0["champion"] is None and st0["loss"] == {s: 0 for s in ids} and st0["out"] == [] and st0["final"][0]["s"] == [["W", 9], ["W", 13]] and st0["final"][0]["a"] is None,
      "nothing played yet: the grand final is shown with its places (winner of #9, winner of #13)")

# -------------------------------------------------------------------------------------------------------------------------------------- speed and size
t0 = time.time()
sched, games, batches, bad = run(102, 5, "random", seed=3)
dt = time.time() - t0
check(not bad and len(batches) >= 29, "102 engines, 5 losses: %d games in %d batches, rules hold, checked in %.1f s%s" % (len(games), len(batches), dt, "" if not bad else " - " + "; ".join(bad[:3])))
t0 = time.time()
import json                                               # noqa: E402
payload = json.dumps(sched.structure(games))
dt = time.time() - t0
t1 = time.time()
sched.structure(games[:len(games) // 2])
check(dt < 0.5 and len(payload) < 600000 and time.time() - t1 < 0.5, "...its structure for the drawing: %d KB, %.0f ms (half way: %.0f ms)" % (len(payload) // 1024, dt * 1000, (time.time() - t1) * 1000))

print("\nFAILED: %d" % fails if fails else "\nALL OK")
sys.exit(1 if fails else 0)
