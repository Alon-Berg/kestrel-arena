"""The per-engine workarounds for very fast games (arena/engines.py KNOWN_QUIRKS, used by arena/game.py and arena/uci.py): the clock turned into a time per move (with an engine's own constant
subtracted and its minimum respected), one retry after an unusable answer (no move, or an illegal move), a depth-1 search of every game's start position, a first search when the engine starts.
Pretend engines (a Python script with python-chess that logs every command it gets) play short games with the Arena's own game loop.
usage: python tests/test_quirks.py"""
import json
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import chess                                              # noqa: E402
from arena import engines as engmod                       # noqa: E402
from arena.engines import KNOWN_QUIRKS, Registry, quirks_of    # noqa: E402
from arena.game import is_very_fast, play_game, shim_limits    # noqa: E402
from arena.pool import engine_signature                   # noqa: E402
from arena.uci import UciEngine                           # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# ------------------------------------------------------------------------------------------------------------- the time per move
lim = lambda own, inc=5: {"wtime": own, "btime": own + 1, "winc": inc, "binc": inc + 1}      # noqa: E731
check(shim_limits(lim(500), chess.WHITE) == {"movetime": 16}, "500 ms + 5 ms: a fortieth plus 0.8 x the increment = 16 ms: %s" % shim_limits(lim(500), chess.WHITE))
check(shim_limits(lim(30), chess.WHITE) == {"movetime": 4}, "a clock of 30 ms: 4 ms")
check(shim_limits(lim(3), chess.WHITE) == {"movetime": 2}, "a clock of 3 ms: a third of it, but at least 2 ms")
check(shim_limits(lim(500), chess.BLACK) == {"movetime": 17}, "black uses btime and binc (501 ms + 6 ms: 12.5 + 4.8 = 17 ms): %s" % shim_limits(lim(500), chess.BLACK))
yuk = {"movetime_add": 19, "movetime_min": 30}
check(shim_limits(lim(500), chess.WHITE, yuk) == {"movetime": 35}, "an engine that takes 19 ms off every movetime gets 19 ms more: 16.5 + 19 = 35")
check(shim_limits(lim(30), chess.WHITE, yuk) == {"movetime": 30}, "... and never less than its minimum of 30 ms: %s" % shim_limits(lim(30), chess.WHITE, yuk))
check(is_very_fast({"mode": "time", "base": 3, "inc": 0.03}) and not is_very_fast({"mode": "time", "base": 5, "inc": 0.1}) and is_very_fast({"mode": "movetime", "ms": 200}), "'very fast': a clock of up to 3 s, or up to 200 ms per move")

# ------------------------------------------------------------------------------------------------------------- the known engines
check(quirks_of({"id": "yukari"}) == KNOWN_QUIRKS["yukari"] and quirks_of({"id": "kestrel-3.2"}) == {} and quirks_of({"id": "bagatur"}).get("prepare_game"), "known engines get their workarounds by id")
check(quirks_of({"id": "yukari", "quirks": {}}) == {} and quirks_of({"id": "yukari", "quirks": {"warmup": True}}) == {"warmup": True}, "an entry's own 'quirks' replaces them (an empty one switches them off)")
check(quirks_of({"id": "elixir"}) == {"movetime_add": 95}, "Elixir (takes 100 ms off every movetime) gets 95 ms more on a fixed time per move")
a = {"path": "x.exe", "options": {}, "overhead_ms": 1}
check(engine_signature(dict(a, quirks={"warmup": True})) != engine_signature(a), "the engine pool tells engines with other workarounds apart")

tmp = tempfile.mkdtemp(prefix="arena_quirks_test_")
data = os.path.join(tmp, "data")
os.makedirs(data)
reg = Registry(data, ROOT, ROOT)
reg.save_engines([{"id": "yukari", "name": "Yukari", "path": "y.exe", "quirks": {"warmup": True}}, {"id": "zed", "name": "Zed", "path": "z.exe", "keep_off": True, "enabled": False}])
reg2 = Registry(data, ROOT, ROOT)
mine = {e["id"]: e for e in reg2.engines() if not e.get("builtin")}
check(mine["yukari"].get("quirks") == {"warmup": True} and mine["zed"].get("keep_off") is True, "saving the list from the Engines tab keeps 'quirks' and 'keep_off'")

# ------------------------------------------------------------------------------------------------------------- pretend engines
fake = os.path.join(tmp, "fake.py")
open(fake, "w").write(textwrap.dedent('''
    import sys
    import chess
    mode, log = sys.argv[1], open(sys.argv[2], "a")
    board = chess.Board()
    seen = {}
    key = None
    for line in sys.stdin:
        log.write(line); log.flush()
        c = line.split()
        if not c:
            continue
        if c[0] == "uci":
            print("id name Fake"); print("uciok")
        elif c[0] == "isready":
            print("readyok")
        elif c[0] == "position":
            key = line.strip()
            i = c.index("moves") if "moves" in c else len(c)
            board = chess.Board() if c[1] == "startpos" else chess.Board(" ".join(c[2:i]))
            for m in c[i + 1:]:
                board.push_uci(m)
        elif c[0] == "go":
            n = seen.get(key, 0)
            seen[key] = n + 1
            if mode == "none_first" and n == 0:
                print("bestmove (none)")
            elif mode == "stale_first" and n == 0:
                print("bestmove a1a1")
            elif mode == "always_none":
                print("bestmove (none)")
            elif mode == "slow_none_first" and n == 0:
                import time
                time.sleep(0.15)
                print("bestmove (none)")
            else:
                print("info depth 1 score cp 10 nodes 5 pv " + next(iter(board.legal_moves)).uci())
                print("bestmove " + next(iter(board.legal_moves)).uci())
        elif c[0] == "quit":
            break
        sys.stdout.flush()
'''))


def engine(mode, name, quirks=None):
    logf = os.path.join(tmp, name + ".log")
    open(logf, "w").close()
    e = UciEngine(sys.executable, args='"%s" %s "%s"' % (fake, mode, logf), name=name, quirks=quirks).start(timeout=30)
    e.logf = logf
    return e


def commands(e):
    return [l.strip() for l in open(e.logf).read().splitlines()]


def game(w, b, tc, fen=None, plies=8):
    return play_game({"gid": 0, "round": 1, "white": "w", "black": "b", "start_fen": fen, "opening_moves": [], "tc": tc, "adj": {"max_plies": plies, "draw": False, "resign": False},
                      "margin_ms": 600}, w, b)


fast = {"mode": "time", "base": 0.5, "inc": 0.005}
w, b = engine("normal", "shim_w", {"clock_to_movetime": True, "movetime_add": 19, "movetime_min": 30}), engine("normal", "shim_b")
rec = game(w, b, fast)
gw = [c for c in commands(w) if c.startswith("go")]
gb = [c for c in commands(b) if c.startswith("go")]
check(rec["reason"] == "move limit" and len(gw) == 4 and all(c.startswith("go movetime ") for c in gw), "very fast game: the engine with 'clock_to_movetime' only gets 'go movetime N': %s" % gw[:2])
check(all(int(c.split()[2]) >= 30 for c in gw), "... of at least its minimum (30 ms)")
check(len(gb) == 4 and all(c.startswith("go wtime ") for c in gb), "... and the other engine still gets the clock: %s" % gb[:1])
w.quit(), b.quit()

w, b = engine("normal", "slow_w", {"clock_to_movetime": True}), engine("normal", "slow_b")
rec = game(w, b, {"mode": "time", "base": 10, "inc": 0.1})
gw = [c for c in commands(w) if c.startswith("go")]
check(rec["reason"] == "move limit" and gw and all(c.startswith("go wtime ") for c in gw), "a game of 10 s: the clock is sent as it is: %s" % gw[:1])
w.quit(), b.quit()

# a fixed time per move: the engine's own constant and minimum apply to it as well
w, b = engine("normal", "mt_w", {"movetime_add": 95}), engine("normal", "mt_b")
rec = game(w, b, {"mode": "movetime", "ms": 100})
gw = [c for c in commands(w) if c.startswith("go")]
gb = [c for c in commands(b) if c.startswith("go")]
check(rec["reason"] == "move limit" and gw and all(c == "go movetime 195" for c in gw), "fixed 100 ms per move: an engine that loses 95 ms of it on its own gets 195: %s" % gw[:2])
check(gb and all(c == "go movetime 100" for c in gb), "... the other engine gets the 100 ms: %s" % gb[:1])
w.quit(), b.quit()
w, b = engine("normal", "mtmin_w", {"movetime_min": 30}), engine("normal", "mtmin_b")
rec = game(w, b, {"mode": "movetime", "ms": 20})
gw = [c for c in commands(w) if c.startswith("go")]
check(rec["reason"] == "move limit" and gw and all(c == "go movetime 30" for c in gw), "fixed 20 ms per move: an engine that needs 30 ms gets 30: %s" % gw[:2])
w.quit(), b.quit()
w, b = engine("normal", "twice_w", {"clock_to_movetime": True, "movetime_add": 100}), engine("normal", "twice_b")
rec = game(w, b, fast)
gw = [int(c.split()[2]) for c in commands(w) if c.startswith("go movetime")]
check(gw and all(110 <= t <= 130 for t in gw), "very fast clock game: the engine's constant is added once (about 116 ms, not 216): %s" % gw[:4])
w.quit(), b.quit()

# retry after 'no move'
w, b = engine("none_first", "retry_w", {"retry_bad_move": True}), engine("normal", "retry_b")
rec = game(w, b, fast)
gw = [c for c in commands(w) if c.startswith("go")]
check(rec["reason"] == "move limit" and rec.get("retries") == 4 and len(gw) == 8, "'no move' is asked for again: no fault, 4 retries, 8 'go': reason %s, retries %s, go %d" % (rec["reason"], rec.get("retries"), len(gw)))
check(all(c.startswith("go movetime ") and int(c.split()[2]) >= 20 for c in gw[1::2]), "... the second try with at least 20 ms (not the clock): %s" % gw[:2])
w.quit(), b.quit()
w, b = engine("none_first", "noretry_w", {"retry_bad_move": False}), engine("normal", "noretry_b")
rec = game(w, b, fast)
check(rec["reason"] == "no move" and rec["result"] == "0-1" and not rec.get("retries"), "with the retry switched off 'no move' still loses the game: %s %s" % (rec["reason"], rec["result"]))
w.quit(), b.quit()
w, b = engine("none_first", "default_w"), engine("normal", "default_b")
rec = game(w, b, fast)
check(rec["reason"] == "move limit" and rec.get("retries") == 4, "very fast game: every engine gets the retry by default (no quirks at all): %s, retries %s" % (rec["reason"], rec.get("retries")))
w.quit(), b.quit()
w, b = engine("none_first", "slowdef_w"), engine("normal", "slowdef_b")
rec = game(w, b, {"mode": "time", "base": 10, "inc": 0.1})
check(rec["reason"] == "no move" and not rec.get("retries"), "a game of 10 s: no retry unless the engine asks for it: %s" % rec["reason"])
w.quit(), b.quit()

w, b = engine("none_first", "retrymin_w", {"retry_bad_move": True, "movetime_min": 30, "movetime_add": 5}), engine("normal", "retrymin_b")
rec = game(w, b, fast)
gw = [c for c in commands(w) if c.startswith("go movetime")]
check(rec["reason"] == "move limit" and gw and all(int(c.split()[2]) >= 30 for c in gw), "a retry also respects the engine's own minimum (30 ms; 20 + 5 would be 25): %s" % gw[:2])
w.quit(), b.quit()

# the failed tries do not count on the engine's clock
w, b = engine("slow_none_first", "slow_w", {"retry_bad_move": True}), engine("normal", "slow_b")
rec = game(w, b, fast)
mine = [m for m in rec["ms"][0::2] if m is not None]
check(rec["reason"] == "move limit" and rec.get("retries") == 4 and mine and max(mine) < 120, "the 150 ms lost to the failed first try are not charged: reason %s, retries %s, own times %s" % (rec["reason"], rec.get("retries"), mine))
w.quit(), b.quit()

# retry after an illegal move (a stale move)
w, b = engine("stale_first", "stale_w", {"retry_bad_move": True}), engine("normal", "stale_b")
rec = game(w, b, {"mode": "movetime", "ms": 50})
check(rec["reason"] == "move limit" and rec.get("retries") == 4, "an illegal move is asked for again as well: reason %s, retries %s" % (rec["reason"], rec.get("retries")))
w.quit(), b.quit()
w, b = engine("stale_first", "stale2_w", {"retry_bad_move": False}), engine("normal", "stale2_b")
rec = game(w, b, {"mode": "movetime", "ms": 50})
check(rec["reason"] == "illegal move", "with the retry switched off it is still an illegal move: %s" % rec["reason"])
w.quit(), b.quit()

# at most two more tries
w, b = engine("always_none", "always_w", {"retry_bad_move": True}), engine("normal", "always_b")
rec = game(w, b, fast)
gw = [c for c in commands(w) if c.startswith("go")]
check(rec["reason"] == "no move" and len(gw) == 3, "an engine that never answers is asked three times, then loses: reason %s, go %d" % (rec["reason"], len(gw)))
mts = [int(c.split()[2]) for c in gw[1:]]
check(len(mts) == 2 and 20 <= mts[0] <= 150 and 100 <= mts[1] <= 300, "... the second try with 20-150 ms, the third with 100-300 ms: %s" % mts)
w.quit(), b.quit()

# prepare_game and warmup
fen = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
w, b = engine("normal", "prep_w", {"prepare_game": True}), engine("normal", "prep_b")
rec = game(w, b, {"mode": "time", "base": 10, "inc": 0.1}, fen=fen, plies=2)
cmds = commands(w)
i = cmds.index("ucinewgame")
after = [c for c in cmds[i:] if not c.startswith("isready")]
check(after[1] == "position fen " + fen and after[2] == "go depth 1" and after[4].startswith("go wtime"), "prepare_game: after ucinewgame a depth-1 search of the start position, then the game: %s" % after[:6])
check(not [c for c in commands(b) if c.startswith("go depth")], "... only for the engine that has it")
w.quit(), b.quit()
w = engine("normal", "warm_w", {"warmup": True})
cmds = commands(w)
check("go depth 1" in cmds and cmds.index("go depth 1") > cmds.index("ucinewgame"), "warmup: a depth-1 search when the engine starts: %s" % [c for c in cmds if c.startswith(("go", "ucinew"))])
w.quit()
w = engine("normal", "warm2_w", {"warmup": 120})
cmds = commands(w)
check("go movetime 120" in cmds and "go depth 1" not in cmds, "warmup as a number: a search of that many ms when the engine starts: %s" % [c for c in cmds if c.startswith("go")])
w.quit()
w = engine("normal", "nowarm_w")
check("go depth 1" not in commands(w), "no warm-up without the workaround")
w.quit()

print("\n%s" % ("ALL OK" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
