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


# ---------------------------------------------------------------------------------------------------------------- elimination (N losses)
def drive(sched, fn, max_batches=3000):
    """Plays the whole tournament with the results fn(white, black, batch, pair, g); returns (games, batches of Pairing)."""
    games, batches = [], []
    while True:
        done_before = [g for g in games if g["batch"] < len(batches)]
        batch = sched.next_batch(done_before, len(batches))
        if batch is None:
            return games, batches
        for p in batch:
            for g in range(p.games):
                w, b = (p.a, p.b) if g % 2 == 0 else (p.b, p.a)
                games.append({"white": w, "black": b, "result": fn(w, b, len(batches), p.index, g), "batch": len(batches), "pair": p.index})
        batches.append(batch)
        if len(batches) > max_batches:
            raise RuntimeError("runaway tournament")


def results(policy, order, seed):
    """Results that depend only on (white, black, batch, pair, game): the better seed always wins ('chalk'), the worse one ('upset'), all draws, or random with 30 % draws."""
    pos = {s: k for k, s in enumerate(order)}

    def fn(w, b, batch, pair, g):
        if policy == "draws":
            return "1/2-1/2"
        if policy in ("chalk", "upset"):
            better_white = pos[w] < pos[b]
            return "1-0" if better_white == (policy == "chalk") else "0-1"
        r = random.Random("%s|%s|%s|%s|%s|%s" % (seed, w, b, batch, pair, g))
        x = r.random()
        return "1/2-1/2" if x < 0.3 else ("1-0" if r.random() < 0.5 else "0-1")
    return fn


def shape(batches):
    return [[(p.a, p.b, p.games, p.round, p.index, p.tag) for p in batch] for batch in batches]


def referee(sched, games, batches, n_losses):
    """Checks a finished elimination against the rules, independently of the scheduler's own bookkeeping; returns a list of problems."""
    bad = []
    order = sched.seeds
    n = len(order)
    rank = {s: k for k, s in enumerate(order)}
    st = sched.structure(games)
    by_round, batch_round = {}, {}
    for bi, batch in enumerate(batches):
        for p in batch:
            by_round.setdefault(p.round, []).append((bi, p))
            if batch_round.setdefault(bi, p.round) != p.round:
                bad.append("batch %d mixes rounds" % bi)
    score = {}                                                    # (round, pairing index) -> [points of a, points of b]
    pairing = {}
    for bi, batch in enumerate(batches):
        for p in batch:
            pairing.setdefault((p.round, p.index), (p.a, p.b))
            if pairing[(p.round, p.index)] != (p.a, p.b):
                bad.append("round %d pairing %d changes its players in a tie-break" % (p.round, p.index))
    for g in games:
        r = batch_round[g["batch"]]
        a, b = pairing[(r, g["pair"])]
        pa, pb = formats.points_of(g["result"], g["white"] == a)
        s = score.setdefault((r, g["pair"]), [0.0, 0.0])
        s[0] += pa
        s[1] += pb
    loss = {s: 0 for s in order}
    matches = 0
    if st["rounds"][-1]["round"] != max(by_round):
        bad.append("structure has %d rounds, the schedule %d" % (st["rounds"][-1]["round"], max(by_round)))
    for r in sorted(by_round):
        items = by_round[r]
        first = min(bi for bi, p in items)
        lvl0 = [p for bi, p in items if bi == first]
        ui = {(m["a"], m["b"]): m for m in st["rounds"][r - 1]["matches"] if not m["bye"]}
        alive = [s for s in order if loss[s] < n_losses]
        play = [s for p in lvl0 for s in (p.a, p.b)]
        if len(play) != len(set(play)):
            bad.append("round %d: a player is paired twice" % r)
        if any(loss[s] >= n_losses for s in play):
            bad.append("round %d: a player who is out plays" % r)
        if any(p.games != sched.games for p in lvl0):
            bad.append("round %d: a match has not %d games" % (r, sched.games))
        if any(p.tag for p in lvl0):
            bad.append("round %d: a first batch is a tie-break" % r)
        counts = {}
        for s in alive:
            counts[loss[s]] = counts.get(loss[s], 0) + 1
        mixed = [p for p in lvl0 if loss[p.a] != loss[p.b]]
        if mixed:
            two = sorted(alive, key=lambda s: (-loss[s], rank[s]))[:2]
            if not (len(lvl0) == 1 and set(counts.values()) == {1} and {mixed[0].a, mixed[0].b} == set(two)):
                bad.append("round %d: players with different losses meet although others could play (%s)" % (r, shape([lvl0])))
        byes = [m["a"] for m in st["rounds"][r - 1]["matches"] if m["bye"]]
        for s in byes:
            if loss[s] == 0 and r == 1:
                continue
            if counts.get(loss[s], 0) < 3 or counts.get(loss[s], 0) % 2 == 0:
                bad.append("round %d: %s has a bye in a group of %d" % (r, s, counts.get(loss[s], 0)))
        if len(byes) != len(set(byes)) or (r > 1 and len({loss[s] for s in byes}) != len(byes)):
            bad.append("round %d: more than one bye in a group" % r)
        for s in alive:
            if s not in play and s not in byes and counts[loss[s]] != 1:
                bad.append("round %d: %s (%d losses) neither plays nor has a bye although his group has %d players" % (r, s, loss[s], counts[loss[s]]))
        for p in lvl0:
            sa, sb = score[(r, p.index)]
            win = p.a if sa > sb else (p.b if sb > sa else (p.a if rank[p.a] < rank[p.b] else p.b))
            m = ui.get((p.a, p.b))
            if m is None or m["winner"] != win:
                bad.append("round %d match %s-%s: the structure's winner differs from the games" % (r, p.a, p.b))
            loss[p.b if win == p.a else p.a] += 1
            matches += 1
    alive = [s for s in order if loss[s] < n_losses]
    if len(alive) != 1:
        bad.append("%d survivors" % len(alive))
    else:
        if st.get("champion") != alive[0]:
            bad.append("champion %s, referee says %s" % (st.get("champion"), alive[0]))
        if any(loss[s] != n_losses for s in order if s != alive[0]):
            bad.append("somebody else has not lost %d matches" % n_losses)
        if matches != n_losses * (n - 1) + loss[alive[0]]:
            bad.append("%d matches, expected %d" % (matches, n_losses * (n - 1) + loss[alive[0]]))
    if st["loss"] != loss:
        bad.append("structure's loss counts differ from the referee's")
    if len(st["out"]) != n - 1 or [o["id"] for o in st["out"]] != [o["id"] for o in sorted(st["out"], key=lambda o: o["round"])]:
        bad.append("structure's list of eliminated players is wrong")
    if len(games) < sched.total_games():
        bad.append("%d games are fewer than the minimum %d" % (len(games), sched.total_games()))
    return bad


def elim_run(n, n_losses, policy, seed=1, games_per_match=2, seeding="rating"):
    ids = ["p%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - 10 * k for k, i in enumerate(ids)}
    params = {"losses": n_losses, "ko_games": games_per_match, "seeding": seeding}
    sched = formats.make_scheduler("elimination", ids, ratings, params, seed)
    games, batches = drive(sched, results(policy, sched.seeds, seed))
    bad = referee(sched, games, batches, sched.losses)
    again = formats.make_scheduler("elimination", ids, ratings, params, seed)       # the resumed tournament regenerates every batch from the finished games
    for b in range(len(batches)):
        if shape([again.next_batch([g for g in games if g["batch"] < b], b)]) != shape([batches[b]]):
            bad.append("batch %d is not regenerated identically" % b)
            break
    return sched, games, batches, bad


for n_losses in (1, 2, 3):
    problems, runs, matches_total = [], 0, 0
    for n in range(2, 18):
        for policy in ("chalk", "upset", "draws", "random", "random"):
            _, games, _, bad = elim_run(n, n_losses, policy, seed=n * 7 + runs)
            runs += 1
            matches_total += len(games)
            problems += ["n=%d %s: %s" % (n, policy, x) for x in bad]
    check(not problems, "elimination with %d loss%s, 2..17 engines x 5 result patterns (%d tournaments, %d games): rules hold, one survivor, schedule regenerates identically%s"
          % (n_losses, "" if n_losses == 1 else "es", runs, matches_total, "" if not problems else " - " + "; ".join(problems[:4])))

# more losses, bigger fields, 4 games per match, other seedings
problems = []
for n_losses, n, policy, gpm, seeding in [(4, 9, "random", 2, "rating"), (5, 6, "random", 2, "list"), (3, 24, "random", 4, "random"), (2, 33, "random", 2, "rating"), (3, 40, "draws", 2, "rating"),
                                          (10, 5, "random", 2, "rating"), (12, 4, "random", 2, "rating")]:
    sched, games, batches, bad = elim_run(n, n_losses, policy, seed=n, games_per_match=gpm, seeding=seeding)
    problems += ["N=%d n=%d: %s" % (sched.losses, n, x) for x in bad]
    if sched.losses != min(n_losses, formats.MAX_LOSSES):
        problems.append("N=%d is not capped at %d: %d" % (n_losses, formats.MAX_LOSSES, sched.losses))
check(not problems, "elimination with up to 10 losses, 24..40 engines, 4 games per match, other seedings: rules hold%s" % ("" if not problems else " - " + "; ".join(problems[:4])))

# one loss = the knockout (same bracket, same byes, same tie-breaks, same pairing indexes)
same = True
for n in range(2, 18):
    ids = ["p%d" % i for i in range(1, n + 1)]
    ratings = {i: 3000 - 10 * k for k, i in enumerate(ids)}
    for policy in ("random", "draws", "upset"):
        ko = formats.make_scheduler("knockout", ids, ratings, {"ko_games": 2}, 5)
        el = formats.make_scheduler("elimination", ids, ratings, {"losses": 1, "ko_games": 2}, 5)
        fn = results(policy, ko.seeds, 3)
        g1, b1 = drive(ko, fn)
        g2, b2 = drive(el, fn)
        if shape(b1) != shape(b2) or g1 != g2:
            same = False
            print("     differs for n=%d %s" % (n, policy))
check(same, "elimination with 1 loss plays exactly the knockout: the same pairings, colours, byes, tie-breaks and indexes (2..17 engines)")

# the champion of a double elimination: the unbeaten player wins the final in one match; the other one needs two (bracket reset)
ids = ["p%d" % i for i in range(1, 9)]
sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 2}, 1)
games, batches = drive(sched, results("chalk", sched.seeds, 1))
st = sched.structure(games)
last = st["rounds"][-1]["matches"][0]
check(st["champion"] == "p1" and last["final"] and last["la"] == 0 and last["lb"] == 1 and len(games) == 14 * 2 and st["loss"]["p1"] == 0,
      "double elimination of 8, the better seed always wins: 14 matches, the final p1 (0 losses) against p2 (1 loss), champion p1")


sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 2}, 1)
games0, batches0 = drive(sched, results("chalk", sched.seeds, 1))
first_final = len(batches0) - 1                                   # the batch of the final in the chalk tournament
rank0 = {s: k for k, s in enumerate(sched.seeds)}
chalk = results("chalk", sched.seeds, 1)
sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 2}, 1)
# the same results until the final; from the final on the worse seed wins every game: the player with one loss beats the unbeaten one -> both have one loss -> a deciding match (the 'bracket reset')
games, batches = drive(sched, lambda w, b, batch, pair, g: chalk(w, b, batch, pair, g) if batch < first_final else ("1-0" if rank0[w] > rank0[b] else "0-1"))
st = sched.structure(games)
fin = [m for r in st["rounds"] for m in r["matches"] if m["final"]]
check(len(fin) == 2 and fin[0]["la"] == 0 and fin[0]["lb"] == 1 and fin[0]["winner"] == fin[0]["b"] and fin[1]["la"] == 1 and fin[1]["lb"] == 1 and st["champion"] == fin[1]["winner"]
      and st["loss"][st["champion"]] == 1 and len(games) == 15 * 2,
      "double elimination: the player with one loss beats the unbeaten one -> both have one loss -> a deciding match (the bracket reset): %d games, champion %s with %d loss"
      % (len(games), st["champion"], st["loss"][st["champion"]]))

# triple elimination: the final phase when three players with 0, 1 and 2 losses are left - the two with the most losses play first, the unbeaten player waits
ids = ["p%d" % i for i in range(1, 4)]
sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 3}, 1)
games, batches = drive(sched, results("random", sched.seeds, 11))
bad = referee(sched, games, batches, 3)
check(not bad, "triple elimination of 3 players: rules hold (%s)" % "; ".join(bad[:3]))

# byes: 11 players, 2 losses: round 1 has 5 byes for the top seeds and 3 matches; the 3 losers wait or play in their group; an odd loss group gives exactly one player a bye
ids = ["p%d" % i for i in range(1, 12)]
sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 2}, 1)
games, batches = drive(sched, results("chalk", sched.seeds, 1))
st = sched.structure(games)
r1 = st["rounds"][0]["matches"]
check(sum(1 for m in r1 if m["bye"]) == 5 and sorted(m["a"] for m in r1 if m["bye"]) == ["p1", "p2", "p3", "p4", "p5"] and sum(1 for m in r1 if not m["bye"]) == 3,
      "11 players: round 1 has byes for the top 5 seeds and 3 matches")
r2 = st["rounds"][1]["matches"]
g1 = [m for m in r2 if m["group"] == 1]
check(len(g1) == 2 and sum(1 for m in g1 if m["bye"]) == 1 and sum(1 for m in g1 if not m["bye"]) == 1, "the 3 losers of round 1 form group 1: one match and one bye (%s)" % [(m["a"], m["b"]) for m in g1])

# pairing inside a loss group: best against worst, odd -> a bye for the player with the fewest byes (better seed first), no rematch when it can be avoided
sched = formats.make_scheduler("elimination", ["p%d" % i for i in range(1, 9)], {"p%d" % i: 3000 - 10 * i for i in range(1, 9)}, {"losses": 2}, 1)
none = {s: 0 for s in sched.seeds}
check(sched._pair_group(["p2", "p3", "p4", "p5"], none, {}) == ([("p2", "p5"), ("p3", "p4")], None), "a group of 4 is paired best against worst")
check(sched._pair_group(["p2", "p3", "p4", "p5"], none, {frozenset(("p2", "p5")): 1}) == ([("p2", "p4"), ("p3", "p5")], None), "...and a rematch is avoided when another pairing exists")
check(sched._pair_group(["p2", "p3", "p4", "p5"], none, {frozenset(("p2", "p5")): 1, frozenset(("p3", "p4")): 1, frozenset(("p2", "p4")): 1, frozenset(("p3", "p5")): 1, frozenset(("p2", "p3")): 1,
                                                          frozenset(("p4", "p5")): 1}) == ([("p2", "p5"), ("p3", "p4")], None), "...and when everybody has met everybody it is best against worst again")
check(sched._pair_group(["p3", "p4", "p6"], none, {}) == ([("p4", "p6")], "p3"), "a group of 3: the best seed gets the bye")
check(sched._pair_group(["p3", "p4", "p6"], dict(none, p3=1), {}) == ([("p3", "p6")], "p4"), "...unless he had a bye already: then the next one")
check(sched._pair_group(["p3", "p4", "p6"], none, {frozenset(("p4", "p6")): 1}) == ([("p3", "p6")], "p4"), "...or a bye avoids a rematch of the other two")

# stopped in the middle (a batch with some games missing): the structure shows the partial scores and decided matches already count
ids = ["p%d" % i for i in range(1, 9)]
sched = formats.make_scheduler("elimination", ids, {i: 3000 - k for k, i in enumerate(ids)}, {"losses": 2}, 1)
games, batches = drive(sched, results("chalk", sched.seeds, 1))
partial = [g for g in games if g["batch"] == 0] + [g for g in games if g["batch"] == 1][:3]           # all of round 1, and 3 of the 8 games of round 2 (p1-p4 complete, p2-p3 half)
st = sched.structure(partial)
r2 = st["rounds"][1]["matches"]
check(len(st["rounds"]) == 2 and st["champion"] is None and r2[0]["winner"] == "p1" and r2[0]["done"] and r2[0]["sa"] == 2 and r2[1]["winner"] is None and r2[1]["sa"] == 1 and not r2[1]["done"] and not r2[2]["done"],
      "a stopped tournament: round 2 shows the decided match and the partial score")
check(st["loss"] == {"p1": 0, "p2": 0, "p3": 0, "p4": 1, "p5": 1, "p6": 1, "p7": 1, "p8": 1} and st["out"] == [] and sched.next_batch(partial, 1)[0].index == 0,
      "...the decided match counts at once (p4 has 1 loss), nobody is out, and the schedule continues with the same batch")
partial = [g for g in games if g["batch"] <= 1]                                       # round 1 and 2 complete: p5..p8 are in group 1 and play on in round 3
st = sched.structure(partial)
check(st["rounds"][-1]["round"] == 3 and all(m["done"] is False for m in st["rounds"][-1]["matches"]) and st["loss"]["p8"] == 2 and [o["id"] for o in st["out"]] == ["p8", "p7"],
      "two complete rounds: round 3 is listed with nothing played, p8 and p7 are out")

# speed: a big field must not be slow (the structure is rebuilt after every game)
import time as _t                                          # noqa: E402
t0 = _t.time()
sched, games, batches, bad = elim_run(100, 3, "random", seed=3)
dt = _t.time() - t0
check(not bad and len(batches) > 20, "100 engines, 3 losses: %d games in %d batches, rules hold, checked in %.1f s%s" % (len(games), len(batches), dt, "" if not bad else " - " + "; ".join(bad[:3])))
t0 = _t.time()
sched.structure(games)
check(_t.time() - t0 < 0.5, "...the structure of that tournament takes %.0f ms" % ((_t.time() - t0) * 1000))

# the format is known to the factory and a field of one is refused
try:
    formats.make_scheduler("elimination", ["only"], {}, {}, 1)
    check(False, "a field of one is refused")
except ValueError:
    check(True, "a field of one is refused")

print("\nFAILED: %d" % fails if fails else "\nALL OK")
sys.exit(1 if fails else 0)
