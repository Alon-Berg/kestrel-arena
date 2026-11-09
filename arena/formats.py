"""Tournament formats. A scheduler hands out *batches* of pairings; a batch is played completely before the next one is asked for.

    sched = make_scheduler("swiss", engine_ids, ratings, params, seed)
    batch = sched.next_batch(finished_games_of_earlier_batches, batch_no)       # list[Pairing] or None when the tournament is over

next_batch() is deterministic in (parameters, seed, results of the earlier batches), so a stopped tournament resumes exactly where it was.
A pairing plays `games` games (colours alternate, starting with `a` as White); both colours of a pair of games use the same opening.
Finished game records passed in carry: white, black (engine ids), result, batch, pair (index of the pairing inside its batch).
"""
import random


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


def _seed_positions(p):
    """Standard bracket order of the seeds 1..p (p a power of two): [1,16,8,9,4,13,5,12,2,15,7,10,3,14,6,11] for p=16."""
    order = [1]
    while len(order) < p:
        m = len(order) * 2 + 1
        order = [x for s in order for x in (s, m - s)]
    return order


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


FORMATS = {"roundrobin": RoundRobin, "gauntlet": Gauntlet, "swiss": Swiss, "knockout": Knockout}


def make_scheduler(fmt, ids, ratings, params, seed):
    cls = FORMATS.get(fmt)
    if cls is None:
        raise ValueError("unknown tournament format: %s" % fmt)
    if len(ids) < 2:
        raise ValueError("a tournament needs at least two engines")
    return cls(ids, ratings, params, seed)
