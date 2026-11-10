"""Strength levels for Kestrel in the Play tab: the same levels as the web page (docs/play/levels.js of the Kestrel repository).

Why not UCI_Elo?  Kestrel's UCI_Elo option limits the nodes searched per move, but the engine looks at its node count only every 2048 nodes, so every setting
below about 2300 searches the same ~2000 nodes (4 to 7 plies, with a very good evaluation) and plays at one and the same, rather high, strength.
And a weak player does not just look less deeply: a weak player also overlooks things now and then.  So a level has four parts:

    depth | nodes   how much Kestrel looks at (plies, or positions)
    k               how many of its best moves it keeps in mind (MultiPV, at most 8)
    temp            how carelessly it picks among them: a move that looks x centipawns worse than the best one gets the weight exp(-x / temp)
    slip            the chance per move of a plain oversight: a random legal move other than the best one

The first moves of a game are always played with care (and, at the strong levels, with a little variety).  A player who is clearly ahead gets steadier: temp and slip
shrink when the best move is worth more than EASE_FROM centipawns.  The numbers are matched to the mistakes players of that rating make (an estimate, not a
measurement against people); the table was calibrated with games of the levels against each other and against Stockfish, see docs/BUILDING.md in the Kestrel repository.

tests/test_levels.py checks this file against the JavaScript with the same random numbers (tests/levels_vectors.json, made by tools/make_level_vectors.py): when the
table or the rules of the web page change, run that tool and update TABLE below."""
import math
import random
import time

OPENING_PLIES = 10          # half moves played carefully
SAFE_PLIES = 8              # no oversights before this ply
OPENING_TEMP = 25           # centipawns: variety of the first moves (at the strong levels, and the floor for the others)
OPENING_WINDOW = 25         # in the opening only moves within this many centipawns of the best one may be played at the strong levels
SCORE_CAP = 3000            # scores are cut at +-30 pawns: a mate counts as +-30 pawns (faster mates a little more)
EASE_FROM = 150             # when the best move is worth more than this many centipawns the player gets steadier: temp and slip shrink ...
EASE_TO = 800               # ... down to EASE_MIN times their value at this score
EASE_MIN = 0.35
DEFAULT_RATING = 1300
MIN_REPLY_S = 0.45          # a level answers no faster than this (plus up to MORE_S) so that the reply does not come before the player has seen their own move
MORE_S = 0.45

# (rating, search, k, temp, slip): rating 0 = full strength; search = ("depth", d) or ("nodes", n)
TABLE = [
    (500, ("depth", 1), 8, 800, 0.30),
    (700, ("depth", 1), 8, 430, 0.17),
    (900, ("depth", 1), 8, 260, 0.10),
    (1100, ("depth", 1), 8, 130, 0.052),
    (1300, ("depth", 2), 6, 175, 0.07),
    (1500, ("depth", 3), 5, 160, 0.064),
    (1700, ("depth", 3), 5, 112, 0.045),
    (1900, ("depth", 4), 4, 100, 0.04),
    (2100, ("depth", 4), 4, 55, 0.022),
    (2300, ("depth", 5), 4, 40, 0.016),
    (2500, ("nodes", 4096), 2, 15, 0),
    (2800, ("nodes", 16384), 1, 0, 0),
    (0, None, 1, 0, 0),
]


def _make_levels():
    out = []
    for i, (rating, search, k, temp, slip) in enumerate(TABLE):
        lv = {"id": i, "rating": rating, "k": k, "temp": temp, "slip": slip, "full": rating == 0 and search is None}
        if search:
            lv[search[0]] = search[1]
        lv["name"] = "Full strength" if lv["full"] else "Level %d (about %d)" % (i + 1, rating)
        out.append(lv)
    return out


LEVELS = _make_levels()


def level_for_rating(r):
    """The level whose rating is nearest to r (0 = full strength)."""
    if r == 0:
        return LEVELS[-1]
    best = LEVELS[0]
    for lv in LEVELS:
        if not lv["full"] and abs(lv["rating"] - r) < abs(best["rating"] - r):
            best = lv
    return best


def blurb(lv):
    """One sentence under the level menu (the mistake rates are those measured for the page's levels, see docs/BUILDING.md of the Kestrel repository)."""
    if lv["full"]:
        return "Kestrel at its best, for as long as you let it think."
    r = lv["rating"]
    if r <= 800:
        return "A beginner: sees one move ahead and makes a serious mistake every eight to twelve moves."
    if r <= 1200:
        return "Sees one move ahead and makes a serious mistake every fifteen to twenty moves."
    if r <= 1600:
        return "Sees two or three moves ahead and makes a serious mistake about every twenty moves."
    if r <= 2000:
        return "Sees three or four moves ahead and makes a serious mistake about every thirty moves."
    if r <= 2400:
        return "Sees four or five moves ahead and makes a serious mistake only every sixty moves or so."
    return "Looks at thousands of positions for every move and seldom makes a serious mistake."


def public_list():
    """What the web UI needs: id, name, rating, one-line description."""
    return [{"id": lv["id"], "name": lv["name"], "rating": lv["rating"], "full": lv["full"], "blurb": blurb(lv)} for lv in LEVELS]


# ------------------------------------------------------------------------------------------------------------ choosing a move
def clamp_score(s):
    return max(-SCORE_CAP, min(SCORE_CAP, s))


def line_score(info):
    """Score of an info line (dict of arena.uci.parse_info) for the side to move: centipawns, cut at +-SCORE_CAP; a mate is worth the cap minus a little for each move."""
    if "mate" in info:
        m = info["mate"]
        return SCORE_CAP - min(m, 50) * 0.5 if m > 0 else -SCORE_CAP + min(-m, 50) * 0.5
    return clamp_score(info.get("cp", 0))


def candidates_from(infos, want):
    """The candidate moves of the deepest iteration that finished for all wanted lines: [{'uci', 'score', 'depth'}] best first."""
    by_depth = {}
    for i in infos:
        if not i.get("pv") or ("cp" not in i and "mate" not in i):
            continue
        by_depth.setdefault(i.get("depth", 0), {})[i.get("multipv", 1)] = i
    if not by_depth:
        return []
    depths = sorted(by_depth, reverse=True)
    pick = next((d for d in depths if len(by_depth[d]) >= want), depths[0])
    lines = sorted(by_depth[pick].values(), key=lambda i: i.get("multipv", 1))
    return [{"uci": i["pv"][0], "score": line_score(i), "depth": pick} for i in lines]


def temp_at(lv, ply):
    return max(OPENING_TEMP, lv["temp"] * 0.4) if ply < OPENING_PLIES else lv["temp"]


def slip_at(lv, ply):
    return 0 if ply < SAFE_PLIES else lv["slip"]


def steadiness(best):
    """1 in a level position, EASE_MIN in a won one: how much of its carelessness a player shows when the best move is worth `best` centipawns."""
    if not best > EASE_FROM:
        return 1.0
    return max(EASE_MIN, 1 - (1 - EASE_MIN) * (best - EASE_FROM) / (EASE_TO - EASE_FROM))


def wanted_lines(lv, ply, n_legal):
    """How many lines the engine is asked for at this point of the game."""
    opening = ply < OPENING_PLIES
    return max(1, min(n_legal, max(lv["k"], 4) if opening else lv["k"]))


def pick_move(cands, legal, lv, ply, rng):
    """Chooses one of the candidates (best first) the way the level says.  rng() gives numbers in [0, 1)."""
    if not cands:
        return legal[int(rng() * len(legal))]
    g = steadiness(cands[0]["score"])
    slip = slip_at(lv, ply) * g
    if slip > 0 and rng() < slip:
        others = [u for u in legal if u != cands[0]["uci"]]
        if others:
            return others[int(rng() * len(others))]
    t = temp_at(lv, ply) * g
    if not t > 0 or len(cands) == 1:
        return cands[0]["uci"]
    top = cands[0]["score"]
    # in the opening of the strong levels only moves close to the best one are allowed
    pool = [c for c in cands if c["score"] >= top - OPENING_WINDOW] if (lv["temp"] * 0.4 < OPENING_TEMP and ply < OPENING_PLIES) else cands
    w = [math.exp((c["score"] - top) / t) for c in pool]
    r = rng() * sum(w)
    k = 0
    while k < len(pool) - 1 and r >= w[k]:
        r -= w[k]
        k += 1
    return pool[k]["uci"]


def choose_move(engine, fen, moves, board, lv, ply, rng=random.random, on_info=None, abort=None, timeout=60.0):
    """The whole decision for a level (not for full strength).  engine: a started arena.uci.UciEngine; fen/moves: the position as for engine.position();
    board: the python-chess board of that position; ply: half moves played since the start of the game.  Returns (uci move, candidates)."""
    legal = [m.uci() for m in board.legal_moves]
    if not legal:
        return None, []
    if len(legal) == 1:
        return legal[0], []
    k = wanted_lines(lv, ply, len(legal))
    limits = {"depth": lv["depth"]} if lv.get("depth") else {"nodes": lv["nodes"]}
    engine.set_option("UCI_LimitStrength", False)
    engine.set_option("MultiPV", k)
    engine.position(fen, moves)
    infos = []

    def collect(inf):
        if inf.get("pv"):
            infos.append(inf)
        if on_info:
            on_info(inf)
    try:
        res = engine.go(limits, timeout=timeout, on_info=collect, abort=abort)
    finally:
        try:
            engine.set_option("MultiPV", 1)
        except Exception:
            pass
    best = res.get("bestmove")
    cands = candidates_from(infos, k) if k > 1 else []
    if not cands:
        # one line wanted (or none came back): the engine's own choice, unless the level slips
        slip = slip_at(lv, ply)
        if slip > 0 and rng() < slip:
            others = [u for u in legal if u != best]
            if others:
                return others[int(rng() * len(others))], cands
        return (best if best in legal else legal[int(rng() * len(legal))]), cands
    uci = pick_move(cands, legal, lv, ply, rng)
    return (uci if uci in legal else legal[0]), cands


def reply_pause(rng, spent_s):
    """Seconds still to wait before a (non-full) level plays, so that the answer does not come at once."""
    return MIN_REPLY_S + rng() * MORE_S - spent_s


def sleep_checked(seconds, stop):
    """Sleeps in small steps; stop() returning True ends the wait early."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not stop():
        time.sleep(min(0.05, max(0.0, end - time.monotonic())))
