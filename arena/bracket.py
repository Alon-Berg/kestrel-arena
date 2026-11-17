"""The fixed bracket of the elimination format (arena/formats.py, class Elimination): a winners bracket and N-1 losers brackets, drawn like the brackets of every tournament site.

A *slot* says where a player of a match comes from: ("S", rank) a seed of the field (rank 0 = the best seed), ("W", id) the winner of match id, ("L", id) the loser of match id, None = nobody (a bye).
Everything in this module depends only on the number of players and on the number of losses, never on results: which matches exist, who feeds whom, which matches are byes, in which order
they are played ("step") and where each one is drawn. The scheduler (formats.Elimination) fills the players in as the results come.

Winners bracket (bracket 0): the classic seeded single elimination of p = 2^m places (byes for the top seeds when the field is smaller), m rounds.
Losers bracket k (k = 1 .. N-1): every round of bracket k-1 sends its losers, as one batch, into bracket k, which takes the batches one after the other:
    * the survivors of the bracket so far play each other (an *internal* round) while there are more of them than the next batch has players,
    * then every survivor plays a player of the new batch (a *merge* round; the batch is taken in reversed order, so that nobody meets the one he has just played, as in the usual double elimination),
    * after the last batch the survivors play each other until one is left: the champion of bracket k.
For k = 1 this is exactly the losers bracket of a double elimination (2m-2 rounds: p/4, p/4, p/8, p/8, ... 1, 1 matches); for k >= 2 it is the same rule one level down.
A player who loses in bracket k has k+1 losses and goes down to bracket k+1 (he is out when that is bracket N). The champions of the brackets (champion of bracket k has k losses) meet in the final
stage, which depends on the results and is handled by the scheduler (the two players with the most losses play each other until one player is left: the classic grand final and its reset for N = 2).
"""


def seed_positions(p):
    """Standard bracket order of the seeds 1..p (p a power of two): [1,16,8,9,4,13,5,12,2,15,7,10,3,14,6,11] for p=16."""
    order = [1]
    while len(order) < p:
        m = len(order) * 2 + 1
        order = [x for s in order for x in (s, m - s)]
    return order


class BMatch:
    """One place of the bracket. kind: 'real' (two players), 'bye' (one: he goes on), 'empty' (nobody). typ: 'wb', 'in' (internal round), 'mg' (merge round)."""
    __slots__ = ("id", "k", "rnd", "pos", "src", "kind", "typ", "step", "no", "y", "feed")

    def __init__(self, mid, k, rnd, pos, src, typ):
        self.id, self.k, self.rnd, self.pos, self.src, self.typ = mid, k, rnd, pos, src, typ
        self.kind = "empty"
        self.step = 0                    # the order of play: a real match is played one step after the last of its feeders
        self.no = None                   # match number (real matches, in the order of play)
        self.y = 0.0                     # row in the drawing of its bracket (the column is `rnd`)
        self.feed = []                   # ids of the matches of the same bracket that feed it (the lines of the drawing)


def _perm(size, merge_no):
    """The order in which a batch of losers is placed against the survivors, so that nobody meets a player he has met already (as far as the bracket allows): the merge rounds of a bracket take
    the reversed order (the losers of the first matches meet the survivors of the last ones, as in every double elimination), then the halves swapped, then the plain order, and again.
    (Chosen by simulation: of all such cycles this one had the fewest rematches, with random results and with results by strength: 1 % of the matches in a double elimination of 32 or 64 against
    3 % for the reversed order every time.)"""
    kind = merge_no % 3
    if size < 2 or kind == 2:
        return list(range(size))
    if kind == 0:
        return list(range(size - 1, -1, -1))
    h = size // 2
    return [(j + h) % size for j in range(size)]


class Bracket:
    def __init__(self, n, losses):
        if n < 2:
            raise ValueError("a bracket needs at least two players")
        self.n, self.losses = n, max(1, int(losses))
        p = 1
        while p < n:
            p *= 2
        self.p = p
        self.m = p.bit_length() - 1
        self.matches = []
        self.brackets = []                                # {"k", "rounds": [[match id]], "champion": slot}
        self._canon_cache = {}
        self._build_winners()
        prev = self.brackets[0]["rounds"]
        for k in range(1, self.losses):
            self._build_losers(k, prev)
            prev = self.brackets[k]["rounds"]
        self._finish()

    # ------------------------------------------------------------------------------------------------------------------ building
    def present(self, slot):
        if slot is None:
            return False
        t, r = slot
        if t == "S":
            return True
        kind = self.matches[r].kind
        return kind == "real" if t == "L" else kind in ("real", "bye")

    def _add(self, k, rnd, pos, a, b, typ):
        m = BMatch(len(self.matches), k, rnd, pos, (a, b), typ)
        pa, pb = self.present(a), self.present(b)
        m.kind = "real" if (pa and pb) else ("bye" if (pa or pb) else "empty")
        self.matches.append(m)
        return m.id

    def _build_winners(self):
        pos = seed_positions(self.p)
        slots = [("S", s - 1) if s <= self.n else None for s in pos]
        rounds = [[self._add(0, 0, i, slots[2 * i], slots[2 * i + 1], "wb") for i in range(self.p // 2)]]
        while len(rounds[-1]) > 1:
            prev = rounds[-1]
            r = len(rounds)
            rounds.append([self._add(0, r, i, ("W", prev[2 * i]), ("W", prev[2 * i + 1]), "wb") for i in range(len(prev) // 2)])
        self.brackets.append({"k": 0, "rounds": rounds, "champion": ("W", rounds[-1][0])})

    def _internal(self, k, rnd, surv):
        ids = []
        for j in range(0, len(surv), 2):
            ids.append(self._add(k, rnd, len(ids), surv[j], surv[j + 1] if j + 1 < len(surv) else None, "in"))
        return ids, [("W", i) for i in ids]

    def _merge(self, k, rnd, surv, batch, merge_no):
        size = max(len(surv), len(batch))
        surv = list(surv) + [None] * (size - len(surv))
        batch = list(batch) + [None] * (size - len(batch))
        order = _perm(size, merge_no)
        ids = [self._add(k, rnd, j, surv[j], batch[order[j]], "mg") for j in range(size)]
        return ids, [("W", i) for i in ids]

    def _build_losers(self, k, prev_rounds):
        batches = [[("L", mid) for mid in rnd] for rnd in prev_rounds]
        rounds = []
        if not batches:
            self.brackets.append({"k": k, "rounds": rounds, "champion": None})
            return
        surv = list(batches[0])
        merges = 0
        for batch in batches[1:]:
            while len(surv) > len(batch) and len(surv) > 1:
                ids, surv = self._internal(k, len(rounds), surv)
                rounds.append(ids)
            ids, surv = self._merge(k, len(rounds), surv, batch, merges)
            merges += 1
            rounds.append(ids)
        while len(surv) > 1:
            ids, surv = self._internal(k, len(rounds), surv)
            rounds.append(ids)
        self.brackets.append({"k": k, "rounds": rounds, "champion": surv[0]})

    # ------------------------------------------------------------------------------------------------------- what a slot really is
    def canon(self, slot):
        """The slot with the byes taken out: ('S', rank), ('W', real match), ('L', real match), or None when nobody can ever come from there."""
        if slot is None:
            return None
        if slot in self._canon_cache:
            return self._canon_cache[slot]
        t, r = slot
        if t == "S":
            out = slot
        else:
            m = self.matches[r]
            if m.kind == "real":
                out = slot
            elif m.kind == "bye" and t == "W":
                out = self.canon(m.src[0] if self.present(m.src[0]) else m.src[1])
            else:
                out = None
        self._canon_cache[slot] = out
        return out

    # ------------------------------------------------------------------------------------------------- steps, numbers, drawing
    def _finish(self):
        for m in self.matches:                                   # (matches are in topological order: a match only refers to earlier ones)
            srcs = [self.canon(s) for s in m.src]
            base = max([self.matches[s[1]].step for s in srcs if s and s[0] != "S"] or [0])
            m.step = base + 1 if m.kind == "real" else base
        real = sorted((m for m in self.matches if m.kind == "real"), key=lambda m: (m.step, m.k, m.rnd, m.pos))
        for i, m in enumerate(real):
            m.no = i + 1
        self.real = [m.id for m in real]
        self.steps = []                                          # steps[s-1] = ids of the real matches of step s, in play order
        for m in real:
            while len(self.steps) < m.step:
                self.steps.append([])
            self.steps[m.step - 1].append(m.id)
        for m in self.matches:
            same = [s[1] for s in m.src if s and s[0] != "S" and self.matches[s[1]].k == m.k]
            m.feed = [i for i in same if self.matches[i].kind != "empty"]
            if not same:
                m.y = float(m.pos)
            elif len(same) == 1:
                m.y = self.matches[same[0]].y
            else:
                m.y = (self.matches[same[0]].y + self.matches[same[1]].y) / 2.0
        for b in self.brackets:
            ys = [self.matches[i].y for rnd in b["rounds"] for i in rnd]
            b["rows"] = (max(ys) + 1.0) if ys else 0.0

    def counts(self):
        """Real matches per bracket."""
        out = [0] * self.losses
        for i in self.real:
            out[self.matches[i].k] += 1
        return out

    def first_final(self):
        """The two slots of the first match of the final stage (the champions of the two brackets with the most losses), None when fewer than two brackets have a champion."""
        champs = [self.canon(b["champion"]) for b in self.brackets]
        champs = [c for c in champs if c is not None]
        return (champs[-2], champs[-1]) if len(champs) >= 2 else None
