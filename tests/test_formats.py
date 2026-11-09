"""Pairing logic tests with invented results (no engines): round robin, gauntlet, Swiss (odd/even, no rematches, byes) and knockout (byes, ties, champion).
usage: python tests/test_formats.py"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena import formats                                  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def play_out(sched, rng, draw_p=0.3, rating=None, ties_first=False):
    """Plays every batch with random results; returns (games, batches). A game record has white, black, result, batch, pair."""
    games, batches = [], 0
    while True:
        done_before = [g for g in games if g["batch"] < batches]
        batch = sched.next_batch(done_before, batches)
        if batch is None:
            return games, batches
        for p in batch:
            for g in range(p.games):
                w, b = (p.a, p.b) if g % 2 == 0 else (p.b, p.a)
                if ties_first and batches == 0:
                    res = "1/2-1/2"                              # force a tied match in the first batch
                else:
                    r = rng.random()
                    res = "1/2-1/2" if r < draw_p else ("1-0" if rng.random() < 0.5 else "0-1")
                games.append({"white": w, "black": b, "result": res, "batch": batches, "pair": p.index})
        batches += 1
        if batches > 200:
            raise RuntimeError("runaway tournament")


rng = random.Random(7)

# round robin
ids = ["e%d" % i for i in range(1, 9)]
rr = formats.make_scheduler("roundrobin", ids, {}, {"rounds": 2, "games": 2}, 1)
games, nb = play_out(rr, rng)
pairs = {}
for g in games:
    k = frozenset((g["white"], g["black"]))
    pairs[k] = pairs.get(k, 0) + 1
check(len(games) == rr.total_games() == 8 * 7 // 2 * 2 * 2 and all(v == 4 for v in pairs.values()) and len(pairs) == 28, "round robin 8 engines x2 rounds x2 games: 112 games, every pair 4 times")
w = {i: sum(1 for g in games if g["white"] == i) for i in ids}
check(max(w.values()) - min(w.values()) <= 1, "colours are balanced in the round robin: %s" % sorted(w.values()))

# single-game round robins: colours must be balanced within one game for every player
for n in range(3, 14):
    pid = ["p%d" % i for i in range(n)]
    sched = formats.make_scheduler("roundrobin", pid, {}, {"rounds": 1, "games": 1}, 1)
    wh = {i: 0 for i in pid}
    bl = {i: 0 for i in pid}
    for p in sched.next_batch([], 0):
        wh[p.a] += 1
        bl[p.b] += 1
    spread = max(wh[i] - bl[i] for i in pid) - min(wh[i] - bl[i] for i in pid)
    worst = max(abs(wh[i] - bl[i]) for i in pid)
    check(worst <= 1, "single-game round robin of %d: |whites - blacks| <= 1 for everybody (worst %d)" % (n, worst))

# gauntlet
gt = formats.make_scheduler("gauntlet", ids, {}, {"hero": "e3", "games": 4, "rounds": 1}, 1)
games, nb = play_out(gt, rng)
check(len(games) == 7 * 4 and all("e3" in (g["white"], g["black"]) for g in games), "gauntlet: hero e3 plays 4 games against each of 7 opponents")

# swiss
for n in (8, 9, 12):
    ids = ["s%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - k * 10 for k, i in enumerate(ids)}
    sw = formats.make_scheduler("swiss", ids, ratings, {"swiss_rounds": 5, "games": 2}, 3)
    games, nb = play_out(sw, rng)
    seen, rematch, per_round = set(), 0, {}
    for g in games:
        k = (g["batch"], frozenset((g["white"], g["black"])))
        per_round.setdefault(g["batch"], set()).update([g["white"], g["black"]])
        if frozenset((g["white"], g["black"])) in {s[1] for s in seen if s[0] != g["batch"]}:
            rematch += 1
        seen.add(k)
    expected_players = n - (n % 2)
    check(nb == 5 and all(len(v) == expected_players for v in per_round.values()), "swiss %d players: 5 rounds, %d players paired per round" % (n, expected_players))
    check(rematch == 0, "swiss %d players: no rematches (%d)" % (n, rematch))
    if n % 2:
        byes = [v for k, v in sorted(sw.byes.items())]
        check(len(set(byes)) == len(byes), "swiss %d players: nobody gets two byes: %s" % (n, byes))

# knockout
for n in (2, 3, 6, 8, 11):
    ids = ["k%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - k * 10 for k, i in enumerate(ids)}
    ko = formats.make_scheduler("knockout", ids, ratings, {"ko_games": 2}, 1)
    games, nb = play_out(ko, rng, ties_first=(n == 6))
    st = ko.structure([{"white": g["white"], "black": g["black"], "result": g["result"], "batch": g["batch"], "pair": g["pair"]} for g in games])
    champ = st.get("champion")
    check(champ in ids, "knockout %d players: champion %s after %d batches (%d games)" % (n, champ, nb, len(games)))
    check(all(m["winner"] for r in st["rounds"] for m in r["matches"]), "knockout %d players: every match has a winner" % n)

# knockout, resume in the middle: the schedule is identical when regenerated from the finished games
ids = ["k%d" % i for i in range(1, 9)]
ko = formats.make_scheduler("knockout", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"ko_games": 2}, 1)
games, nb = play_out(ko, random.Random(1))
ko2 = formats.make_scheduler("knockout", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"ko_games": 2}, 1)
same = True
for b in range(nb):
    a = [(p.a, p.b, p.index) for p in ko.next_batch([g for g in games if g["batch"] < b], b)]
    c = [(p.a, p.b, p.index) for p in ko2.next_batch([g for g in games if g["batch"] < b], b)]
    same &= a == c
check(same, "knockout: the schedule regenerates identically for a resumed tournament")

print("\nFAILED: %d" % fails if fails else "\nALL OK")
sys.exit(1 if fails else 0)
