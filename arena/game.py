"""Plays one game between two started UciEngine objects and builds PGN text.

Time controls (dict):  {"mode": "time", "base": 20, "inc": 0.5}   seconds, Fischer clock
                       {"mode": "movetime", "ms": 100}            fixed time per move
                       {"mode": "nodes", "n": 20000}              fixed nodes per move
                       {"mode": "depth", "d": 10}                 fixed depth per move
"""
import time

import chess
import chess.pgn

from .uci import EngineCrashed, EngineError, EngineTimeout, score_to_cp

DEFAULT_ADJ = {"draw": False, "draw_after_move": 40, "draw_moves": 8, "draw_cp": 10,
               "resign": False, "resign_moves": 4, "resign_cp": 800, "max_plies": 700}      # off by default: games are played to their natural end

_TERMINATION = {
    chess.Termination.CHECKMATE: "checkmate",
    chess.Termination.STALEMATE: "stalemate",
    chess.Termination.INSUFFICIENT_MATERIAL: "insufficient material",
    chess.Termination.SEVENTYFIVE_MOVES: "75-move rule",
    chess.Termination.FIVEFOLD_REPETITION: "fivefold repetition",
    chess.Termination.FIFTY_MOVES: "50-move rule",
    chess.Termination.THREEFOLD_REPETITION: "threefold repetition",
}


def tc_label(tc):
    m = tc.get("mode", "time")
    if m == "time":
        return "%g+%g" % (tc.get("base", 10), tc.get("inc", 0))
    if m == "movetime":
        return "%g ms/move" % tc.get("ms", 100)
    if m == "nodes":
        return "%d nodes/move" % tc.get("n", 10000)
    if m == "depth":
        return "depth %d" % tc.get("d", 8)
    return str(tc)


def _limits(tc, clock, side):
    m = tc.get("mode", "time")
    if m == "time":
        inc = int(tc.get("inc", 0) * 1000)
        return {"wtime": int(clock[chess.WHITE]), "btime": int(clock[chess.BLACK]), "winc": inc, "binc": inc}
    if m == "movetime":
        return {"movetime": int(tc.get("ms", 100))}
    if m == "nodes":
        return {"nodes": int(tc.get("n", 10000))}
    return {"depth": int(tc.get("d", 8))}


def _fmt_pv(board, pv, n=8):
    """PV in uci -> short SAN string, e.g. '1. e4 e5 2. Nf3' (empty when anything is illegal)."""
    try:
        mv = []
        b = board.copy(stack=False)
        for u in pv[:n]:
            m = chess.Move.from_uci(u)
            if m not in b.legal_moves:
                break
            mv.append(m)
            b.push(m)
        return board.variation_san(mv) if mv else ""
    except Exception:
        return ""


def play_game(spec, white, black, on_update=None, should_stop=None):
    """spec: gid, round, white, black (names), start_fen (or None), opening_moves (uci), tc, adj, margin_ms.
    Returns the game record (dict). Result '*' with reason 'aborted' means the game was cancelled."""
    tc = spec["tc"]
    adj = dict(DEFAULT_ADJ)
    adj.update(spec.get("adj") or {})
    margin = float(spec.get("margin_ms", 600))
    start_fen = spec.get("start_fen") or None
    board = chess.Board(start_fen) if start_fen else chess.Board()
    uci, san, cps, depths, times, nodes_l = [], [], [], [], [], []
    for u in spec.get("opening_moves") or []:
        mv = chess.Move.from_uci(u)
        if mv not in board.legal_moves:
            raise ValueError("illegal opening move %s" % u)
        san.append(board.san(mv))
        board.push(mv)
        uci.append(u)
        cps.append(None), depths.append(None), times.append(None), nodes_l.append(None)
    n_book = len(uci)
    t_start = time.time()
    engines = {chess.WHITE: white, chess.BLACK: black}
    result = reason = None
    for color, e in ((chess.WHITE, white), (chess.BLACK, black)):
        try:
            e.new_game()
        except EngineError:
            result, reason = ("0-1" if color == chess.WHITE else "1-0"), "engine not responding"
            e.dead = True
            break
    clock = {chess.WHITE: 0.0, chess.BLACK: 0.0}
    if tc.get("mode", "time") == "time":
        clock[chess.WHITE] = clock[chess.BLACK] = float(tc.get("base", 10)) * 1000.0
    inc_ms = float(tc.get("inc", 0)) * 1000.0 if tc.get("mode", "time") == "time" else 0.0
    own = {chess.WHITE: [], chess.BLACK: []}            # each side's own scores (own point of view), for adjudication

    while result is None:
        out = board.outcome(claim_draw=True)
        if out is not None:
            result = "1/2-1/2" if out.winner is None else ("1-0" if out.winner else "0-1")
            reason = _TERMINATION.get(out.termination, "game over")
            break
        if len(uci) >= int(adj["max_plies"]):
            result, reason = "1/2-1/2", "move limit"
            break
        if adj.get("draw") and len(uci) >= 2 * int(adj["draw_after_move"]):
            k = int(adj["draw_moves"])
            if len(own[chess.WHITE]) >= k and len(own[chess.BLACK]) >= k and \
                    all(abs(s) <= adj["draw_cp"] for s in own[chess.WHITE][-k:] + own[chess.BLACK][-k:]):
                result, reason = "1/2-1/2", "adjudicated draw"
                break
        if adj.get("resign"):
            k = int(adj["resign_moves"])
            if len(own[chess.WHITE]) >= k and len(own[chess.BLACK]) >= k:
                w, b = own[chess.WHITE][-k:], own[chess.BLACK][-k:]
                if all(s >= adj["resign_cp"] for s in w) and all(s <= -adj["resign_cp"] for s in b):
                    result, reason = "1-0", "adjudicated win"
                    break
                if all(s >= adj["resign_cp"] for s in b) and all(s <= -adj["resign_cp"] for s in w):
                    result, reason = "0-1", "adjudicated win"
                    break
        if should_stop and should_stop():
            result, reason = "*", "aborted"
            break

        side = board.turn
        eng = engines[side]
        mode = tc.get("mode", "time")
        if mode == "time":
            timeout = max(0.05, clock[side] / 1000.0) + margin / 1000.0 + 0.3
        elif mode == "movetime":
            timeout = tc.get("ms", 100) / 1000.0 + margin / 1000.0 + 1.0
        else:
            timeout = 120.0
        board_before = board.copy(stack=False)
        try:
            eng.position(start_fen, uci)
            res = eng.go(_limits(tc, clock, side), timeout=timeout, abort=should_stop)
        except EngineTimeout:
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = "time forfeit" if mode == "time" else "no move in time"
            eng.dead = True
            break
        except EngineCrashed:
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = "engine crashed"
            break
        except EngineError:
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = "engine error"
            break
        if should_stop and should_stop():
            result, reason = "*", "aborted"
            break
        elapsed = res["elapsed_ms"]
        if mode == "time":
            if elapsed > clock[side] + margin:
                result = "0-1" if side == chess.WHITE else "1-0"
                reason = "time forfeit"
                break
            clock[side] = max(0.0, clock[side] - elapsed) + inc_ms
        u = res["bestmove"]
        mv = None
        try:
            mv = chess.Move.from_uci(u) if u else None
        except ValueError:
            mv = None
        if mv is None or mv not in board.legal_moves:
            result = "0-1" if side == chess.WHITE else "1-0"
            reason = "illegal move" if u not in (None, "(none)", "0000") else "no move"
            break
        info = res.get("info") or {}
        cp_own = score_to_cp(info)
        san.append(board.san(mv))
        board.push(mv)
        uci.append(u)
        cps.append(None if cp_own is None else (cp_own if side == chess.WHITE else -cp_own))
        depths.append(info.get("depth")), times.append(int(round(elapsed))), nodes_l.append(info.get("nodes"))
        if cp_own is not None:
            own[side].append(cp_own)
        if on_update:
            upd = {"ply": len(uci), "uci": u, "san": san[-1], "fen": board.fen(), "check": chess.square_name(board.king(board.turn)) if board.is_check() else None,
                   "clocks": [int(clock[chess.WHITE]), int(clock[chess.BLACK])], "cp": cps[-1], "depth": info.get("depth"), "nodes": info.get("nodes"),
                   "nps": info.get("nps"), "ms": int(round(elapsed)), "pv": _fmt_pv(board_before, info.get("pv") or []), "san_list": san, "uci_list": uci}
            try:
                on_update(upd)
            except Exception:
                pass
    rec = {"white": spec["white"], "black": spec["black"], "round": spec.get("round"), "result": result, "reason": reason, "start_fen": start_fen,
           "uci": uci, "san": san, "cp": cps, "depth": depths, "ms": times, "nodes": nodes_l, "book": n_book, "plies": len(uci),
           "dur": round(time.time() - t_start, 1), "ended": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return rec


# ------------------------------------------------------------------------------------------------------------------ PGN
def _cp_text(cp):
    if cp is None:
        return None
    if abs(cp) >= 29000:
        return ("+" if cp > 0 else "-") + "M%d" % (30000 - abs(cp))
    return "%+.2f" % (cp / 100.0)


def build_pgn(rec, names, event="Kestrel Arena", tc=None, site="Kestrel Arena", game_no=None):
    """rec: game record; names: id -> display name. Comments follow the usual '+0.35/12 0.52s' form (engine's own point of view)."""
    g = chess.pgn.Game()
    h = g.headers
    h["Event"] = event
    h["Site"] = site
    h["Date"] = (rec.get("ended") or time.strftime("%Y-%m-%d"))[:10].replace("-", ".")
    h["Round"] = str(rec.get("round") if rec.get("round") is not None else (game_no or "-"))
    h["White"] = names.get(rec["white"], rec["white"])
    h["Black"] = names.get(rec["black"], rec["black"])
    h["Result"] = rec.get("result") or "*"
    if tc:
        h["TimeControl"] = ("%g+%g" % (tc.get("base", 0), tc.get("inc", 0))) if tc.get("mode", "time") == "time" else tc_label(tc)
    if rec.get("start_fen"):
        h["SetUp"] = "1"
        h["FEN"] = rec["start_fen"]
    if rec.get("reason"):
        h["Termination"] = rec["reason"]
    h["PlyCount"] = str(rec.get("plies", len(rec["uci"])))
    node = g
    book = rec.get("book", 0)
    white_to_move = chess.Board(rec["start_fen"]).turn if rec.get("start_fen") else chess.WHITE
    for i, u in enumerate(rec["uci"]):
        node = node.add_variation(chess.Move.from_uci(u))
        if i < book:
            node.comment = "book"
            continue
        cp = rec["cp"][i] if i < len(rec["cp"]) else None
        mover_white = white_to_move if i % 2 == 0 else (not white_to_move)
        own = None if cp is None else (cp if mover_white else -cp)
        txt = _cp_text(own)
        parts = []
        if txt is not None:
            d = rec["depth"][i] if i < len(rec["depth"]) else None
            parts.append(txt + ("/%d" % d if d else ""))
        ms = rec["ms"][i] if i < len(rec["ms"]) else None
        if ms is not None:
            parts.append("%.2fs" % (ms / 1000.0))
        if parts:
            node.comment = " ".join(parts)
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=True)
    return g.accept(exporter)
