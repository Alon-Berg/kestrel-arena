"""Opening suites for tournaments: the bundled balanced suite (data/openings.epd), an EPD or PGN file of the user's choice, or the start position.
Both colours of a pair of games get the same opening; the choice is deterministic in (seed, batch, pairing, pair of games)."""
import os
import random

import chess
import chess.pgn

# used when data/openings.epd is missing: well-known lines (checked for legality when loaded)
FALLBACK_LINES = [
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7", "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6", "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6",
    "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6 Nc3 Bg7", "e4 c5 Nf3 e6 d4 cxd4 Nxd4 Nc6 Nc3 Qc7", "e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7 e5 Nfd7",
    "e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7 Bd3 c5", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6", "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 c6",
    "e4 d6 d4 Nf6 Nc3 g6 f4 Bg7 Nf3 O-O", "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O", "d4 d5 c4 c6 Nf3 Nf6 Nc3 dxc4 a4 Bf5",
    "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O Bd3 d5", "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O", "d4 Nf6 c4 e6 Nf3 b6 g3 Bb7 Bg2 Be7",
    "d4 Nf6 Nf3 g6 g3 Bg7 Bg2 O-O O-O d6", "c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5 cxd5 Nxd5", "c4 c5 Nf3 Nf6 Nc3 Nc6 g3 g6 Bg2 Bg7",
    "Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7 d3 O-O", "Nf3 Nf6 c4 e6 g3 d5 Bg2 Be7 O-O O-O", "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5",
    "e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4 Nf3 Be7", "d4 d5 Nf3 Nf6 Bf4 e6 e3 c5 c3 Nc6", "e4 g6 d4 Bg7 Nc3 d6 Nf3 Nf6 Be2 O-O",
]


def _entry_from_san(line):
    b = chess.Board()
    moves = []
    for tok in line.split():
        try:
            m = b.push_san(tok)
        except ValueError:
            return None
        moves.append(m.uci())
    return {"fen": None, "moves": moves}


def load_epd(path, limit=200000):
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                b = chess.Board()
                b.set_epd(line)
            except Exception:
                continue
            fen = b.fen()
            out.append({"fen": None if fen == chess.STARTING_FEN else fen, "moves": []})
            if len(out) >= limit:
                break
    return out


def load_pgn(path, max_plies=16, limit=50000):
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        while len(out) < limit:
            try:
                g = chess.pgn.read_game(f)
            except Exception:
                break
            if g is None:
                break
            b = g.board()
            start = None if b.fen() == chess.STARTING_FEN else b.fen()
            moves = []
            for m in g.mainline_moves():
                if len(moves) >= max_plies:
                    break
                moves.append(m.uci())
            out.append({"fen": start, "moves": moves})
    return out


class OpeningBook:
    def __init__(self, entries, seed=1, shuffle=True, name="suite"):
        self.entries = list(entries) or [{"fen": None, "moves": []}]
        self.name = name
        self.perm = list(range(len(self.entries)))
        if shuffle:
            random.Random(int(seed or 1)).shuffle(self.perm)

    def __len__(self):
        return len(self.entries)

    def pick(self, batch, pair_index, pair_game, per_pair=1):
        n = len(self.entries)
        counter = batch * 9973 + pair_index * max(1, per_pair) + pair_game
        return self.entries[self.perm[counter % n]]


def builtin_book(data_dir, resource_dir=None):
    # a user copy in the data folder wins; otherwise the suite shipped inside the app (<resources>/data/openings.epd)
    for d in (data_dir, os.path.join(resource_dir, "data") if resource_dir else None):
        if d and os.path.exists(os.path.join(d, "openings.epd")):
            e = load_epd(os.path.join(d, "openings.epd"))
            if e:
                return e, "built-in balanced suite (%d positions)" % len(e)
    e = [x for x in (_entry_from_san(s) for s in FALLBACK_LINES) if x]
    return e, "built-in short list (%d lines)" % len(e)


def build_book(cfg, data_dir, resource_dir=None):
    """cfg: {'source': 'builtin'|'none'|'file', 'path': ..., 'order': 'random'|'sequential', 'seed': n}"""
    cfg = cfg or {}
    src = cfg.get("source", "builtin")
    seed = cfg.get("seed", 1)
    shuffle = cfg.get("order", "random") != "sequential"
    if src == "none":
        return OpeningBook([{"fen": None, "moves": []}], seed, False, "start position")
    if src == "file" and cfg.get("path"):
        p = cfg["path"]
        if not os.path.exists(p):
            raise ValueError("opening file not found: %s" % p)
        entries = load_pgn(p) if p.lower().endswith(".pgn") else load_epd(p)
        if not entries:
            raise ValueError("no openings could be read from %s" % p)
        return OpeningBook(entries, seed, shuffle, os.path.basename(p))
    entries, label = builtin_book(data_dir, resource_dir)
    return OpeningBook(entries, seed, shuffle, label)
