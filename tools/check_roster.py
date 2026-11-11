"""Checks every engine of the list: starts it, plays a few games against a reference engine (the bundled Kestrel) with the Arena's own game loop and reports start-up times and every
kind of fault (time forfeit, illegal move, no move, crash, failed start).  Use it before a tournament, especially at a very fast time control.

usage: python tools/check_roster.py [--tc 0.5+0.005] [--games 6] [--concurrency 5] [--margin 600] [--data DIR] [--engines id,id,...] [--out report.json]
                                    [--set id:Option=value ...] [--hash 64] [--threads 1] [--opponent kestrel-3.2] [--include-disabled] [--movetime MS]
                                    [--overhead MS] [--quirks none|JSON] [--first-opening N] [--probe-only]
  --overhead    set the 'Move Overhead' / 'TimeMargin' option of every engine that has one to this many ms (the tournament setting 'Engine time overhead')
  --quirks      the workarounds for engines that cannot play very fast games (arena/engines.py KNOWN_QUIRKS): default = as in a tournament; 'none' = no workarounds;
                or a JSON object {"id": {"warmup": true, "retry_bad_move": true, "clock_to_movetime": true, ...}} replacing them for these engines
  --data        the folder with settings.json (default: environment KESTREL_ARENA_DATA, else ./data); the file is only read
  --engines     only these ids (default: every enabled engine of the list)
  --set         temporary option for one engine, e.g. --set uralochka:TimeMargin=0
  --movetime    fixed time per move in milliseconds instead of a clock
  --first-opening  shift the openings, to run the same check again with other positions
Games are played from the bundled balanced openings, every pair of games with both colours; engines run at above-normal priority like in the tournaments."""
import argparse
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import chess                                              # noqa: E402
from arena import openings as openings_mod               # noqa: E402
from arena.engines import Registry, quirks_of            # noqa: E402
from arena.game import play_game                          # noqa: E402
from arena.tournament import auto_overhead                # noqa: E402
from arena.uci import EngineError, UciEngine              # noqa: E402

FAULTS = ("illegal move", "no move", "time forfeit", "no move in time", "engine crashed", "engine error", "engine not responding")
TIME_OPTION_RE = r"overhead|margin|buffer|latency|\blag\b|safety|slow ?mover"


def parse_value(v):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tc", default="0.5+0.005")
    ap.add_argument("--movetime", type=int, default=0)
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--first-opening", type=int, default=0, help="shift the openings (to run the same check again with other positions)")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--margin", type=int, default=600)
    ap.add_argument("--data", default=os.environ.get("KESTREL_ARENA_DATA") or os.path.join(ROOT, "data"))
    ap.add_argument("--engines", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--hash", type=int, default=64)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--opponent", default="kestrel-3.2")
    ap.add_argument("--include-disabled", action="store_true")
    ap.add_argument("--probe-only", action="store_true", help="only start every engine (start-up time, time-related options), play no games")
    ap.add_argument("--overhead", default=None, help="set the 'Move Overhead'-type option of every engine that has one to this many ms, or 'auto' (the tournament setting 'Engine time overhead')")
    ap.add_argument("--quirks", default="", help="workarounds for engines that cannot play very fast games: default = as in a tournament (arena/engines.py KNOWN_QUIRKS), 'none', or a JSON "
                                                 "object {\"id\": {\"warmup\": true, ...}} replacing them for these engines")
    a = ap.parse_args()

    if a.movetime:
        tc = {"mode": "movetime", "ms": a.movetime}
        tc_text = "%d ms per move" % a.movetime
    else:
        base, inc = a.tc.split("+")
        tc = {"mode": "time", "base": float(base), "inc": float(inc)}
        tc_text = a.tc
    if a.overhead == "auto":
        a.overhead = auto_overhead(tc)
    elif a.overhead is not None:
        a.overhead = int(a.overhead)
    print("engine time overhead: %s" % ("the engines' own settings" if a.overhead is None else "%d ms" % a.overhead), flush=True)
    overrides = {}
    for s in a.set:
        eid, rest = s.split(":", 1)
        k, v = rest.split("=", 1)
        overrides.setdefault(eid, {})[k] = parse_value(v)

    reg = Registry(a.data, ROOT, ROOT)
    allengines = reg.engines()
    opp = next((e for e in allengines if e["id"] == a.opponent), None)
    if opp is None:
        sys.exit("reference engine %r not found" % a.opponent)
    want = [x for x in a.engines.split(",") if x]
    todo = [e for e in allengines if e["id"] != a.opponent and (e["id"] in want if want else (a.include_disabled or e.get("enabled", True)))]
    book = openings_mod.load_epd(os.path.join(ROOT, "data", "openings.epd"))
    if not book:
        sys.exit("no openings (data/openings.epd)")

    def spec_options(e):
        o = {"Hash": a.hash, "Threads": a.threads}
        o.update(e.get("options") or {})
        o.update(overrides.get(e["id"], {}))
        return o

    mine = json.loads(a.quirks) if a.quirks.startswith("{") else {}

    def quirks_for(e):
        if a.quirks == "none":
            return {}
        return dict(mine[e["id"]]) if e["id"] in mine else quirks_of(e)

    def start(e, options):
        eng = UciEngine(e["path"], cwd=e.get("cwd"), options=options, name=e["name"], args=e.get("args"), priority="above", overhead_ms=a.overhead, quirks=quirks_for(e))
        t0 = time.perf_counter()
        eng.start()                                        # the Arena's own start-up timeout (20 s)
        return eng, time.perf_counter() - t0

    def check(idx_e):
        idx, e = idx_e
        out = {"id": e["id"], "name": e["name"], "start_s": None, "games": [], "error": None}
        try:
            eng, out["start_s"] = start(e, spec_options(e))
        except Exception as ex:                            # EngineError, timeout ...
            out["error"] = "failed to start: %s" % ex
            return out
        out["time_options"] = {o["name"]: o["default"] for o in eng.supported.values() if re.search(TIME_OPTION_RE, o["name"], re.I)}       # what could be turned down for very fast games
        out["id_name"] = eng.id_name
        if a.probe_only:
            eng.kill()
            return out
        ref, _ = start(opp, {"Hash": a.hash, "Threads": a.threads})
        try:
            for g in range(a.games):
                if not eng.alive:
                    try:
                        eng.kill()
                        eng, _ = start(e, spec_options(e))
                    except Exception as ex:
                        out["games"].append({"reason": "engine failed to start: %s" % ex, "result": "-", "plies": 0, "first_ms": None, "avg_ms": None})
                        continue
                op = book[(idx * 7 + g // 2 + a.first_opening) % len(book)]
                board = chess.Board(op["fen"]) if op["fen"] else chess.Board()
                w, b = (eng, ref) if g % 2 == 0 else (ref, eng)
                rec = play_game({"gid": g, "round": 1, "white": "w", "black": "b", "start_fen": op["fen"], "opening_moves": [], "tc": tc, "adj": {}, "margin_ms": a.margin}, w, b)
                first_is_white = (g % 2 == 0) == (board.turn == chess.WHITE)
                mine = [m for m in rec["ms"][0 if first_is_white else 1::2] if m is not None]
                out["games"].append({"reason": rec["reason"], "result": rec["result"], "plies": rec["plies"], "first_ms": mine[0] if mine else None,
                                     "avg_ms": round(sum(mine) / len(mine), 1) if mine else None, "max_ms": max(mine) if mine else None,
                                     "white": g % 2 == 0, "engine_dead": bool(eng.dead), "retries": rec.get("retries", 0)})
                if rec["reason"] in FAULTS:
                    eng.kill()                             # like a tournament: an engine that faulted is not reused
                if not ref.alive:
                    ref.kill()
                    ref, _ = start(opp, {"Hash": a.hash, "Threads": a.threads})
        finally:
            eng.kill()
            ref.kill()
        return out

    print("checking %d engines, %d games each (%d with each colour) against %s at %s, %d at a time ..." % (len(todo), a.games, a.games // 2, opp["name"], tc_text, a.concurrency), flush=True)
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        for i, r in enumerate(ex.map(check, list(enumerate(todo))), 1):
            results.append(r)
            f = sum(1 for g in r["games"] if g["reason"] in FAULTS or str(g["reason"]).startswith("engine failed")) + (1 if r["error"] else 0)
            print("  [%3d/%d] %-34s %s" % (i, len(todo), r["name"][:34], "start failed" if r["error"] else ("%d fault%s" % (f, "" if f == 1 else "s") if f else "ok")), flush=True)

    rows = []
    for r in results:
        kinds = {}
        for g in r["games"]:
            if g["reason"] in FAULTS or str(g["reason"]).startswith("engine failed"):
                k = "failed to start" if str(g["reason"]).startswith("engine failed") else g["reason"]
                kinds[k] = kinds.get(k, 0) + 1
        if r["error"]:
            kinds["failed to start"] = kinds.get("failed to start", 0) + 1
        firsts = [g["first_ms"] for g in r["games"] if g.get("first_ms") is not None]
        avgs = [g["avg_ms"] for g in r["games"] if g.get("avg_ms") is not None]
        rows.append({"id": r["id"], "name": r["name"], "games": len(r["games"]), "faults": sum(kinds.values()), "kinds": kinds, "start_s": r["start_s"], "error": r["error"],
                     "first_ms_median": statistics.median(firsts) if firsts else None, "avg_ms": round(statistics.mean(avgs), 1) if avgs else None,
                     "retries": sum(g.get("retries", 0) for g in r["games"]), "quirks": quirks_for(next(x for x in todo if x["id"] == r["id"])),
                     "time_options": r.get("time_options") or {}})
    rows.sort(key=lambda x: (-x["faults"], x["name"].lower()))
    bad = [x for x in rows if x["faults"]]
    print("\n%d of %d engines had no fault in %d games at %s; %d had faults (%.0f s):" % (len(rows) - len(bad), len(rows), a.games, tc_text, len(bad), time.time() - t0))
    for x in bad:
        print("  %-34s %2d of %d  %s   first move %s ms, start %s s   time options %s" % (x["name"][:34], x["faults"], x["games"], x["kinds"], x["first_ms_median"],
                                                                                   None if x["start_s"] is None else round(x["start_s"], 1), x["time_options"] or "-"))
    retried = [x for x in rows if x["retries"]]
    if retried:
        print("moves asked for twice ('retry_bad_move'): " + ", ".join("%s %d" % (x["name"], x["retries"]) for x in retried))
    slow = sorted((x for x in rows if x["start_s"]), key=lambda x: -x["start_s"])[:5]
    print("slowest to start: " + ", ".join("%s %.1f s" % (x["name"], x["start_s"]) for x in slow))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"tc": tc_text, "games": a.games, "margin_ms": a.margin, "opponent": opp["name"], "summary": rows, "detail": results, "when": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
        print("report:", a.out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
