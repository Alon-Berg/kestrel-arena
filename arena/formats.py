"""Tournament formats. A scheduler hands out *batches* of pairings; a batch is played completely before the next one is asked for.

    sched = make_scheduler("swiss", engine_ids, ratings, params, seed)
    batch = sched.next_batch(finished_games_of_earlier_batches, batch_no)       # list[Pairing] or None when the tournament is over

next_batch() is deterministic in (parameters, seed, results of the earlier batches), so a stopped tournament resumes exactly where it was.
A pairing plays `games` games (colours alternate, starting with `a` as White); both colours of a pair of games use the same opening.
Finished game records passed in carry: white, black (engine ids), result, batch, pair (index of the pairing inside its batch).
"""
import random

from .bracket import Bracket, seed_positions


class Pairing:
    __slots__ = ("a", "b", "games", "round", "index", "tag")

    def __init__(self, a, b, games, round_no, index, tag=""):
        self.a, self.b, self.games, self.round, self.index, self.tag = a, b, games, round_no, index, tag

    def to_dict(self):
        return {"a": self.a, "b": self.b, "games": self.games, "round": self.round, "index": self.index, "tag": self.tag}


def points_of(result, white_is_a):
    """(points of a, points of b) for a game result."""
    if result == "1/2-1/2":
        return 0.5, 0.5
    if result == "1-0":
        return (1.0, 0.0) if white_is_a else (0.0, 1.0)
    if result == "0-1":
        return (0.0, 1.0) if white_is_a else (1.0, 0.0)
    return 0.0, 0.0


def first_white(ia, ib, n):
    """Balanced colours for single games: True when list position ia has White against ib in a field of n.
    Odd n: the cyclic tournament (i beats the next (n-1)/2 players) gives everybody equally many Whites and Blacks.
    Even n: the same on n-1 players plus a last player who alternates against the others (|whites - blacks| <= 1 for everybody)."""
    m = n if n % 2 else n - 1
    if n % 2 == 0 and (ia == n - 1 or ib == n - 1):
        k = ib if ia == n - 1 else ia
        k_white = k % 2 == 0
        return (not k_white) if ia == n - 1 else k_white
    return 1 <= (ib - ia) % m <= (m - 1) // 2


def _circle_rounds(ids):
    """Round-robin rounds by the circle method (so that every engine plays once per round): list of rounds, each a list of (a, b) with a = White first."""
    order = list(ids)
    pos = {x: i for i, x in enumerate(order)}
    n_real = len(order)
    players = list(order)
    if len(players) % 2:
        players.append(None)
    n = len(players)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = players[i], players[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b) if first_white(pos[a], pos[b], n_real) else (b, a))
        rounds.append(pairs)
        players = [players[0]] + [players[-1]] + players[1:-1]
    return rounds


class RoundRobin:
    name = "roundrobin"

    def __init__(self, ids, ratings, params, seed):
        self.ids, self.params = list(ids), params
        self.rounds = max(1, int(params.get("rounds", 1)))
        self.games = max(1, int(params.get("games", 2)))

    def total_games(self):
        n = len(self.ids)
        return n * (n - 1) // 2 * self.games * self.rounds

    def next_batch(self, done, batch_no):
        if batch_no > 0:
            return None
        out, idx = [], 0
        for rep in range(self.rounds):
            for rnd, pairs in enumerate(_circle_rounds(self.ids)):
                for a, b in pairs:
                    if rep % 2 == 1:
                        a, b = b, a
                    out.append(Pairing(a, b, self.games, rep * 1000 + rnd + 1, idx))
                    idx += 1
        return out

    def structure(self, done):
        return None


class Gauntlet(RoundRobin):
    name = "gauntlet"

    def __init__(self, ids, ratings, params, seed):
        super().__init__(ids, ratings, params, seed)
        self.hero = params.get("hero") or self.ids[0]
        if self.hero not in self.ids:
            self.hero = self.ids[0]

    def total_games(self):
        return (len(self.ids) - 1) * self.games * self.rounds

    def next_batch(self, done, batch_no):
        if batch_no > 0:
            return None
        others = [i for i in self.ids if i != self.hero]
        out, idx = [], 0
        for rep in range(self.rounds):
            for o in others:
                a, b = (self.hero, o) if rep % 2 == 0 else (o, self.hero)
                out.append(Pairing(a, b, self.games, rep + 1, idx))
                idx += 1
        return out


class Swiss:
    name = "swiss"

    def __init__(self, ids, ratings, params, seed):
        self.ids, self.ratings, self.seed = list(ids), ratings or {}, int(seed or 1)
        self.rounds = max(1, int(params.get("swiss_rounds", params.get("rounds", 5))))
        self.games = max(1, int(params.get("games", 2)))
        self.byes = {}                                            # batch_no -> player who sat out (odd field); rebuilt by next_batch on resume

    def total_games(self):
        return self.rounds * (len(self.ids) // 2) * self.games

    def next_batch(self, done, batch_no):
        if batch_no >= self.rounds:
            return None
        rng = random.Random(self.seed * 7919 + batch_no)
        pts = {i: 0.0 for i in self.ids}
        opps = {i: set() for i in self.ids}
        for g in done:
            a, b = g["white"], g["black"]
            if a in pts and b in pts:
                pa, pb = points_of(g["result"], True)
                pts[a] += pa
                pts[b] += pb
                opps[a].add(b)
                opps[b].add(a)
        for b_no, who in self.byes.items():                       # a bye counts as a win
            if b_no < batch_no and who in pts:
                pts[who] += float(self.games)
        order = sorted(self.ids, key=lambda i: (-pts[i], -(self.ratings.get(i) or 0), rng.random()))
        players = order[:]
        if len(players) % 2:
            had = {w for b_no, w in self.byes.items() if b_no < batch_no}
            cand = [p for p in reversed(players) if p not in had] or list(reversed(players))
            players.remove(cand[0])
            self.byes[batch_no] = cand[0]
        else:
            self.byes.pop(batch_no, None)
        if batch_no == 0:
            h = len(players) // 2
            pairs = [(players[i], players[i + h]) for i in range(h)]
        else:
            pairs = self._pair(players, opps)
        out = []
        for k, (a, b) in enumerate(pairs):
            if batch_no % 2 == 1 and self.games % 2 == 1:
                a, b = b, a
            out.append(Pairing(a, b, self.games, batch_no + 1, k))
        return out

    @staticmethod
    def _pair(players, opps):
        """Top-down pairing without rematches, with backtracking (node-limited); falls back to neighbours when impossible."""
        limit = [20000]

        def rec(rest):
            if not rest:
                return []
            if limit[0] <= 0:
                return None
            a = rest[0]
            for k in range(1, len(rest)):
                b = rest[k]
                if b in opps[a]:
                    continue
                limit[0] -= 1
                sub = rec(rest[1:k] + rest[k + 1:])
                if sub is not None:
                    return [(a, b)] + sub
            return None
        res = rec(list(players))
        if res is not None:
            return res
        return [(players[i], players[i + 1]) for i in range(0, len(players) - 1, 2)]

    def structure(self, done):
        return {"byes": {str(k): v for k, v in self.byes.items()}, "bye_points": float(self.games)}


_seed_positions = seed_positions            # (the standard bracket order of the seeds; arena/bracket.py)


class Knockout:
    name = "knockout"

    def __init__(self, ids, ratings, params, seed):
        self.ratings = ratings or {}
        self.games = max(2, int(params.get("ko_games", params.get("games", 2))))
        if self.games % 2:
            self.games += 1
        rng = random.Random(int(seed or 1))
        ids = list(ids)
        seeding = params.get("seeding", "rating")
        if seeding == "random":
            rng.shuffle(ids)
        elif seeding == "rating":
            ids.sort(key=lambda i: -(self.ratings.get(i) or 0))
        self.seeds = ids
        p = 1
        while p < len(ids):
            p *= 2
        self.p = p

    def total_games(self):
        return (len(self.seeds) - 1) * self.games

    def _first_round(self):
        pos = _seed_positions(self.p)
        slots = [self.seeds[s - 1] if s <= len(self.seeds) else None for s in pos]
        return [(slots[i], slots[i + 1]) for i in range(0, len(slots), 2)]

    def _replay(self, done, batch_no, ui=False):
        """Walks the bracket with the finished games. Returns (pairings of batch `batch_no` or None, structure for the UI)."""
        by_batch = {}
        for g in done:
            by_batch.setdefault(g.get("batch"), []).append(g)
        seed_rank = {s: i for i, s in enumerate(self.seeds)}
        matches = self._first_round()
        rounds_ui = []
        b = 0
        rnd = 0
        while True:
            rnd += 1
            ui_matches, pending, score, played = [], {}, {}, {}
            for i, (x, y) in enumerate(matches):
                score[i], played[i] = [0.0, 0.0], 0
                if x is None or y is None:
                    ui_matches.append({"a": x, "b": y, "sa": 0, "sb": 0, "winner": x or y, "bye": True, "done": True})
                else:
                    pending[i] = (x, y)
                    ui_matches.append({"a": x, "b": y, "sa": 0, "sb": 0, "winner": None, "bye": False, "done": False})
            level = 0
            while pending:
                if not ui and b == batch_no:
                    out = [Pairing(x, y, self.games if level == 0 else 2, rnd, i, "tiebreak" if level else "") for i, (x, y) in sorted(pending.items())]
                    return out, None
                expected = self.games if level == 0 else 2
                cnt = {i: 0 for i in pending}
                for g in by_batch.get(b, []):
                    i = g.get("pair")
                    if i in pending:
                        x, y = pending[i]
                        pa, pb = points_of(g["result"], g["white"] == x)
                        score[i][0] += pa
                        score[i][1] += pb
                        cnt[i] += 1
                for i in pending:
                    ui_matches[i]["sa"], ui_matches[i]["sb"] = score[i]
                if ui and any(cnt[i] < expected for i in pending):
                    rounds_ui.append({"round": rnd, "matches": ui_matches})
                    return None, {"rounds": rounds_ui, "games": self.games}
                b += 1
                nxt = {}
                for i, (x, y) in pending.items():
                    sa, sb = score[i]
                    if sa != sb or level >= 8:
                        ui_matches[i]["winner"] = x if (sa > sb or (sa == sb and seed_rank[x] < seed_rank[y])) else y
                        ui_matches[i]["done"] = True
                    else:
                        nxt[i] = (x, y)
                pending = nxt
                level += 1
            rounds_ui.append({"round": rnd, "matches": ui_matches})
            winners = [m["winner"] for m in ui_matches]
            if len(winners) == 1:
                return None, {"rounds": rounds_ui, "champion": winners[0], "games": self.games}
            matches = [(winners[i], winners[i + 1]) for i in range(0, len(winners), 2)]

    def next_batch(self, done, batch_no):
        return self._replay(done, batch_no)[0]

    def structure(self, done):
        return self._replay(done, -1, ui=True)[1]


MAX_LOSSES = 10                 # the most losses a player may be allowed in the elimination format
TIEBREAK_LEVELS = 8             # extra pairs of games for a level match (the same limit as the Knockout's); after that the better seed goes through


class EliminationGroups(Knockout):
    """The first version of the N-loss elimination, kept for the tournaments that were made with it (their settings have no `bracket` parameter): every round the players are paired inside the groups of
    equal losses, there is no fixed bracket. New tournaments get the bracket (class Elimination below).

    N-loss elimination: a player is out after `losses` lost matches (1 = knockout, 2 = double elimination with a winners and a losers bracket, 3 = triple ...).

    The players are grouped by the number of matches they have lost. In every round
      * the unbeaten players (group 0) play the classic seeded bracket of the Knockout, byes for the top seeds in the first round when the field is no power of two, so with one loss the schedule is
        exactly the Knockout's;
      * the players of every other group (1 ... losses-1 lost matches) are paired inside the group, best seed against worst, without a rematch wherever that is possible; a group of an odd size (three or
        more) gives one player a bye (the one with the fewest byes so far, the better seed first), a group of one waits;
      * a match has a winner (a level match gets pairs of tie-break games, as in the Knockout) - he stays in his group, the loser moves to the next one and is out when he has lost `losses` matches.
    When every group is down to one player at most, the two players with the most losses play each other, until one player is left. With two losses that is the classic final: if the player with one loss beats
    the unbeaten one, both have one loss and play the deciding match (the 'bracket reset'); with more losses the one with fewer losses needs the others to win more often in the same way (a player is out
    only after `losses` lost matches, whoever he plays). So a match between players of different groups only happens when nobody else is left to play, and the field always ends with exactly one survivor
    (the champion has lost between 0 and losses-1 matches, everybody else exactly `losses`: N*(n-1) + the champion's losses matches in all).

    Schedule: a round = one batch of matches (plus further batches for tie-break pairs), the games of a match share the pairing index: a resumed tournament regenerates the same pairings from the finished games.
    """
    name = "elimination"

    def __init__(self, ids, ratings, params, seed):
        super().__init__(ids, ratings, params, seed)
        try:
            lives = int(params.get("losses", 2))
        except (TypeError, ValueError):
            lives = 2
        self.losses = max(1, min(MAX_LOSSES, lives))
        self.rank = {s: i for i, s in enumerate(self.seeds)}

    def total_games(self):
        """The least number of games: every player but the champion loses `losses` matches (the champion may lose up to losses-1, and tie-break pairs add games)."""
        return self.losses * (len(self.seeds) - 1) * self.games

    # ------------------------------------------------------------------------------------------------------ pairing of one loss group
    def _match_up(self, items, met, allow, budget):
        """Pairs everybody of `items` (best seed first): the best one with the worst one that fits, and so on; at most `allow` rematches in all. None when impossible (or the node budget is used up)."""
        if not items:
            return []
        a = items[0]
        for k in range(len(items) - 1, 0, -1):
            b = items[k]
            cost = 1 if met.get(frozenset((a, b))) else 0
            if cost > allow:
                continue
            budget[0] -= 1
            if budget[0] < 0:
                return None
            sub = self._match_up(items[1:k] + items[k + 1:], met, allow - cost, budget)
            if sub is not None:
                return [(a, b)] + sub
        return None

    def _pair_group(self, members, byes, met):
        """(pairs, bye) for the players of one group: with an odd number one gets a bye (fewest byes so far, better seed first); everybody else is paired best against worst, avoiding rematches
        (a player who gets the bye is chosen so that the others need no rematch if that is possible)."""
        order = sorted(members, key=self.rank.get)
        cands = sorted(order, key=lambda s: (byes[s], self.rank[s])) if len(order) % 2 else [None]
        budget = [20000]
        for allow in range(len(order) // 2 + 1):
            for bye in cands:
                pairs = self._match_up([s for s in order if s != bye], met, allow, budget)
                if pairs is not None:
                    return pairs, bye
                if budget[0] < 0:
                    break
            if budget[0] < 0:
                break
        rest = [s for s in order if s != cands[0]]                     # (a very large group: plain best against worst)
        return [(rest[i], rest[len(rest) - 1 - i]) for i in range(len(rest) // 2)], cands[0]

    def _plan_round(self, loss, byes, met, wb):
        """The matches of the next round: [{group, x, y, bye, idx}] (x = the better seed; a bye has y None; group None = players of different groups). wb: the bracket of the unbeaten players."""
        n_out = self.losses
        groups = [[] for _ in range(n_out)]
        for s in self.seeds:
            if loss[s] < n_out:
                groups[loss[s]].append(s)
        entries, idx = [], 0
        for x, y in wb:                                      # unbeaten players: the bracket (a slot without a player is a bye)
            entries.append({"group": 0, "x": x, "y": y, "bye": x is None or y is None, "idx": idx})
            idx += 1
        for k in range(1, n_out):
            if len(groups[k]) < 2:
                continue                                     # one player in a group waits for the others
            pairs, bye = self._pair_group(groups[k], byes, met)
            for x, y in pairs:
                entries.append({"group": k, "x": x, "y": y, "bye": False, "idx": idx})
                idx += 1
            if bye is not None:
                entries.append({"group": k, "x": bye, "y": None, "bye": True, "idx": None})
        if not any(not e["bye"] for e in entries):           # nobody has a partner in his group: the two with the most losses meet
            two = sorted((s for s in self.seeds if loss[s] < n_out), key=lambda s: (-loss[s], self.rank[s]))[:2]
            x, y = sorted(two, key=self.rank.get)
            entries.append({"group": None, "x": x, "y": y, "bye": False, "idx": idx})
        return entries

    # ------------------------------------------------------------------------------------------------------------------ replay
    def _struct(self, rounds_ui, loss, out, champion, live=None, rnd=None):
        loss, out = dict(loss), list(out)
        for m in live or []:                                 # matches of the round in progress that are decided count at once
            if m["done"] and not m["bye"]:
                lost = m["b"] if m["winner"] == m["a"] else m["a"]
                loss[lost] += 1
                if loss[lost] >= self.losses:
                    out.append({"id": lost, "round": rnd})
        return {"kind": "elimination", "losses": self.losses, "games": self.games, "rounds": rounds_ui, "loss": loss, "out": out, "champion": champion}

    def _walk(self, done, batch_no, ui=False):
        """Walks the tournament with the finished games. Returns (pairings of batch `batch_no` or None, structure for the UI)."""
        by_batch = {}
        for g in done:
            by_batch.setdefault(g.get("batch"), []).append(g)
        n_out, rank = self.losses, self.rank
        loss = {s: 0 for s in self.seeds}
        byes = {s: 0 for s in self.seeds}
        met, out, rounds_ui = {}, [], []
        wb = self._first_round()
        b = rnd = 0
        guard = 200 + 50 * n_out * len(self.seeds)
        while True:
            alive = [s for s in self.seeds if loss[s] < n_out]
            if len(alive) == 1:
                return None, self._struct(rounds_ui, loss, out, alive[0])
            rnd += 1
            if rnd > guard:
                raise RuntimeError("the elimination does not end")
            entries = self._plan_round(loss, byes, met, wb)
            ui_matches, pending, score, by_idx = [], {}, {}, {}
            for e in entries:
                x, y = e["x"], e["y"]
                m = {"a": x, "b": y, "sa": 0, "sb": 0, "winner": (x or y) if e["bye"] else None, "bye": e["bye"], "done": e["bye"], "group": e["group"],
                     "final": len(alive) == 2, "tb": 0, "la": loss.get(x), "lb": loss.get(y)}
                ui_matches.append(m)
                if not e["bye"]:
                    pending[e["idx"]] = (x, y)
                    score[e["idx"]] = [0.0, 0.0]
                    by_idx[e["idx"]] = m
            level = 0
            while pending:
                if not ui and b == batch_no:
                    return [Pairing(x, y, self.games if level == 0 else 2, rnd, i, "tiebreak" if level else "") for i, (x, y) in sorted(pending.items())], None
                expected = self.games if level == 0 else 2
                cnt = {i: 0 for i in pending}
                for g in by_batch.get(b, []):
                    i = g.get("pair")
                    if i in pending:
                        x, y = pending[i]
                        pa, pb = points_of(g["result"], g["white"] == x)
                        score[i][0] += pa
                        score[i][1] += pb
                        cnt[i] += 1
                for i in pending:
                    by_idx[i]["sa"], by_idx[i]["sb"], by_idx[i]["tb"] = score[i][0], score[i][1], level
                unfinished = ui and any(cnt[i] < expected for i in pending)       # (a missing game of an earlier batch is not waited for, as in the Knockout)
                nxt = {}
                for i, (x, y) in pending.items():
                    sa, sb = score[i]
                    if unfinished and cnt[i] < expected:
                        nxt[i] = (x, y)                      # still being played
                    elif sa != sb or level >= TIEBREAK_LEVELS:
                        by_idx[i]["winner"] = x if (sa > sb or (sa == sb and rank[x] < rank[y])) else y
                        by_idx[i]["done"] = True
                    else:
                        nxt[i] = (x, y)
                if unfinished:                               # a round in progress: the matches that are decided already count at once
                    rounds_ui.append({"round": rnd, "matches": ui_matches})
                    return None, self._struct(rounds_ui, loss, out, None, live=ui_matches, rnd=rnd)
                b += 1
                pending = nxt
                level += 1
            winners0 = []                                    # the round is decided: losses, byes, the next bracket round of the unbeaten players
            for e, m in zip(entries, ui_matches):
                w = m["winner"]
                if e["bye"]:
                    byes[w] += 1
                else:
                    lost = e["y"] if w == e["x"] else e["x"]
                    loss[lost] += 1
                    pair = frozenset((e["x"], e["y"]))
                    met[pair] = met.get(pair, 0) + 1
                    if loss[lost] >= n_out:
                        out.append({"id": lost, "round": rnd})
                if e["group"] == 0:
                    winners0.append(w)
            wb = [(winners0[i], winners0[i + 1]) for i in range(0, len(winners0) - 1, 2)] if len(winners0) >= 2 else []
            rounds_ui.append({"round": rnd, "matches": ui_matches})

    def next_batch(self, done, batch_no):
        return self._walk(done, batch_no)[0]

    def structure(self, done):
        return self._walk(done, -1, ui=True)[1]


class _Walk:
    """What a walk through an elimination bracket knows so far: results, points, tie-break levels, losses."""

    def __init__(self, bracket, seeds):
        self.br, self.seeds = bracket, seeds
        self.res = {}                       # match id (or 'F1', 'F2' ... in the final stage) -> (winner, loser)
        self.pts = {}                       # match id -> {player: points}
        self.tb = {}                        # match id -> tie-break pairs played so far
        self.gids = {}                      # match id -> numbers of its games (the drawing links a match to them)
        self.loss = {s: 0 for s in seeds}
        self.out = []                       # [{"id", "round"}] in the order of elimination

    def who(self, slot):
        """The player in a slot of the bracket, None when he is not known (yet)."""
        c = self.br.canon(slot)
        if c is None:
            return None
        t, r = c
        if t == "S":
            return self.seeds[r]
        got = self.res.get(r)
        return None if got is None else got[0 if t == "W" else 1]

    def decide(self, key, winner, loser, rnd, n_out):
        self.res[key] = (winner, loser)
        self.loss[loser] += 1
        if self.loss[loser] >= n_out:
            self.out.append({"id": loser, "round": rnd})


class Elimination(Knockout):
    """N-loss elimination as an actual bracket: a winners bracket and N-1 losers brackets, fixed before the first game (arena/bracket.py has the structure and the rules of the losers brackets).

    A player is out after `losses` lost matches: 1 = knockout, 2 = double elimination (winners bracket + losers bracket), 3 = triple (+ a second losers bracket) ... Every round of a bracket sends its losers
    down into the next one. The matches of a *step* (a match is played one step after the last match that feeds it) are one batch; a level match gets pairs of tie-break games as in the Knockout.
    When all brackets are decided, their champions (the champion of bracket k has k losses) meet in the final stage: the two players with the most losses play each other, again and again, until one player
    is left. For 2 losses that is the grand final of the double elimination and, if the champion of the losers bracket wins it, the deciding match (the 'bracket reset').
    The tournaments that were made before this bracket existed have no `bracket` parameter in their settings and keep their own schedule (EliminationGroups)."""
    name = "elimination"

    def __init__(self, ids, ratings, params, seed):
        super().__init__(ids, ratings, params, seed)
        try:
            lives = int(params.get("losses", 2))
        except (TypeError, ValueError):
            lives = 2
        self.losses = max(1, min(MAX_LOSSES, lives))
        self.rank = {s: i for i, s in enumerate(self.seeds)}
        self.bracket = Bracket(len(self.seeds), self.losses)

    def total_games(self):
        """The least number of games: every player but the champion loses `losses` matches (the champion may lose up to losses-1, and tie-break pairs add games)."""
        return self.losses * (len(self.seeds) - 1) * self.games

    # ------------------------------------------------------------------------------------------------------------------ the drawing
    def _payload(self, w, finals, champion, rnd):
        br, rank = self.bracket, self.rank

        def slot_text(slot):                                           # where a player comes from, for a place that has no player yet: seed rank / winner or loser of match number
            c = br.canon(slot)
            if c is None:
                return None
            return [c[0], c[1] if c[0] == "S" else br.matches[c[1]].no]

        def points(key, a, b):
            p = w.pts.get(key) or {}
            return p.get(a, 0.0) if a is not None else 0.0, p.get(b, 0.0) if b is not None else 0.0

        brackets = []
        for bk in br.brackets:
            rounds = []
            for r_idx, ids in enumerate(bk["rounds"]):
                ms = []
                for mid in ids:
                    m = br.matches[mid]
                    if m.kind == "empty":
                        continue
                    a, b = w.who(m.src[0]), w.who(m.src[1])
                    d = {"id": mid, "no": m.no, "x": m.rnd, "y": m.y, "kind": m.kind, "f": m.feed, "s": [slot_text(m.src[0]), slot_text(m.src[1])], "a": a, "b": b}
                    if m.kind == "real":
                        d["sa"], d["sb"] = points(mid, a, b)
                        d["tb"] = w.tb.get(mid, 0)
                        d["g"] = w.gids.get(mid, [])
                        got = w.res.get(mid)
                        d["winner"], d["done"] = (got[0] if got else None), got is not None
                    else:
                        d["winner"], d["done"] = (a or b), True
                    ms.append(d)
                rounds.append({"round": r_idx + 1, "matches": ms})
            name = "Winners bracket" if bk["k"] == 0 else ("Losers bracket" if self.losses == 2 else "Losers bracket %d" % bk["k"])
            brackets.append({"k": bk["k"], "name": name, "rows": bk["rows"], "rounds": rounds})
        first = br.first_final()
        first_s = [slot_text(first[0]), slot_text(first[1])] if first is not None else [None, None]       # where the players of the first match of the final stage come from
        fin = []
        for i, f in enumerate(finals):
            a, b = f["a"], f["b"]
            sa, sb = points(f["key"], a, b)
            got = w.res.get(f["key"])
            fin.append({"no": f["no"], "a": a, "b": b, "sa": sa, "sb": sb, "tb": w.tb.get(f["key"], 0), "winner": got[0] if got else None, "done": got is not None, "s": first_s if i == 0 else [None, None],
                        "g": w.gids.get(f["key"], [])})
        if not fin and first is not None:                              # the final stage has not begun: its first match, with the places it is made of
            fin.append({"no": None, "a": None, "b": None, "sa": 0.0, "sb": 0.0, "tb": 0, "winner": None, "done": False, "s": first_s})
        return {"kind": "bracket", "losses": self.losses, "games": self.games, "p": br.p, "brackets": brackets, "final": fin, "loss": dict(w.loss), "out": list(w.out), "champion": champion}

    # ------------------------------------------------------------------------------------------------------------------ the walk
    def _walk(self, done, batch_no, ui=False):
        """Walks the tournament with the finished games. Returns (pairings of batch `batch_no` or None, structure for the drawing or None)."""
        by_batch = {}
        for g in done:
            by_batch.setdefault(g.get("batch"), []).append(g)
        br, rank, n_out = self.bracket, self.rank, self.losses
        w = _Walk(br, self.seeds)
        b = 0

        def play(pend, rnd):
            """Plays the matches of `pend` {index: (key, x, y)} level by level with the games of the finished batches. Returns ('batch', pairings) at the batch asked for, ('partial', None) when games of a
            batch are still missing (the drawing only), else ('done', None): every match is decided (w.res)."""
            nonlocal b
            level = 0
            while pend:
                if not ui and b == batch_no:
                    return "batch", [Pairing(x, y, self.games if level == 0 else 2, rnd, i, "tiebreak" if level else "") for i, (key, x, y) in sorted(pend.items())]
                expected = self.games if level == 0 else 2
                cnt = {i: 0 for i in pend}
                for g in by_batch.get(b, []):
                    i = g.get("pair")
                    if i in pend:
                        key, x, y = pend[i]
                        pa, pb = points_of(g["result"], g["white"] == x)
                        w.pts[key][x] += pa
                        w.pts[key][y] += pb
                        cnt[i] += 1
                        if g.get("gid") is not None:
                            w.gids.setdefault(key, []).append(g["gid"])
                for key, x, y in pend.values():
                    w.tb[key] = level
                unfinished = ui and any(cnt[i] < expected for i in pend)       # (a missing game of an earlier batch is not waited for, as in the Knockout)
                nxt = {}
                for i, (key, x, y) in pend.items():
                    sx, sy = w.pts[key][x], w.pts[key][y]
                    if unfinished and cnt[i] < expected:
                        nxt[i] = (key, x, y)                                       # still being played
                    elif sx != sy or level >= TIEBREAK_LEVELS:
                        win = x if (sx > sy or (sx == sy and rank[x] < rank[y])) else y
                        w.decide(key, win, y if win == x else x, rnd, n_out)      # (decided matches of a round in progress count at once)
                    else:
                        nxt[i] = (key, x, y)
                if unfinished:
                    return "partial", None
                b += 1
                pend = nxt
                level += 1
            return "done", None

        rnd = 0
        for ids in br.steps:                                                   # the matches of the brackets, step by step
            if not ids:
                continue
            rnd += 1
            pend = {}
            for idx, mid in enumerate(ids):
                m = br.matches[mid]
                pa, pb = w.who(m.src[0]), w.who(m.src[1])
                x, y = (pa, pb) if rank[pa] < rank[pb] else (pb, pa)
                pend[idx] = (mid, x, y)
                w.pts[mid] = {pa: 0.0, pb: 0.0}
                w.tb[mid] = 0
            what, val = play(pend, rnd)
            if what == "batch":
                return val, None
            if what == "partial":
                return None, self._payload(w, [], None, rnd)
        finals = []                                                            # the final stage: the two players with the most losses play each other until one player is left
        guard = 4 * n_out * n_out + 10
        no = len(br.real)
        while True:
            alive = [s for s in self.seeds if w.loss[s] < n_out]
            if len(alive) == 1:
                return None, self._payload(w, finals, alive[0], rnd)
            guard -= 1
            if guard < 0:
                raise RuntimeError("the final stage does not end")
            two = sorted(alive, key=lambda s: (-w.loss[s], rank[s]))[:2]
            x, y = sorted(two, key=rank.get)
            rnd += 1
            no += 1
            key = "F%d" % (no - len(br.real))
            w.pts[key] = {x: 0.0, y: 0.0}
            w.tb[key] = 0
            finals.append({"key": key, "no": no, "a": x, "b": y})
            what, val = play({0: (key, x, y)}, rnd)
            if what == "batch":
                return val, None
            if what == "partial":
                return None, self._payload(w, finals, None, rnd)

    def next_batch(self, done, batch_no):
        return self._walk(done, batch_no)[0]

    def structure(self, done):
        return self._walk(done, -1, ui=True)[1]


FORMATS = {"roundrobin": RoundRobin, "gauntlet": Gauntlet, "swiss": Swiss, "knockout": Knockout, "elimination": Elimination}


def make_scheduler(fmt, ids, ratings, params, seed):
    cls = FORMATS.get(fmt)
    if cls is None:
        raise ValueError("unknown tournament format: %s" % fmt)
    if len(ids) < 2:
        raise ValueError("a tournament needs at least two engines")
    if fmt == "elimination" and not (params or {}).get("bracket"):
        cls = EliminationGroups                          # made before the bracket existed: its games were played in that schedule
    return cls(ids, ratings, params, seed)
