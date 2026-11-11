"""Runs a tournament: scheduler batches -> game tasks -> worker threads -> finished-game records on disk + live events.

Files per tournament (data/tournaments/<id>/):  config.json (settings, engines, status)  games.jsonl (one finished game per line, with moves).
A stopped (or crashed) tournament is resumed by loading it again: finished games are skipped, the schedule is regenerated deterministically.
"""
import json
import os
import queue
import threading
import time
import traceback

import chess

from . import formats
from . import game as gamemod
from . import openings as openmod
from .pool import EnginePool
from .uci import EngineError


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def normalize_tc(tc):
    tc = dict(tc or {})
    mode = tc.get("mode", "time")
    if mode == "time":
        return {"mode": "time", "base": max(0.05, float(tc.get("base", 10))), "inc": max(0.0, float(tc.get("inc", 0.1)))}
    if mode == "movetime":
        return {"mode": "movetime", "ms": max(5, int(tc.get("ms", 100)))}
    if mode == "nodes":
        return {"mode": "nodes", "n": max(100, int(tc.get("n", 20000)))}
    if mode == "depth":
        return {"mode": "depth", "d": max(1, min(60, int(tc.get("d", 8))))}
    raise ValueError("unknown time control mode: %s" % mode)


AUTO_OVERHEAD_MS = 5          # 'auto': the most an engine keeps back per move in very fast games


is_very_fast = gamemod.is_very_fast


def auto_overhead(tc):
    """'Automatic' engine time overhead for a time control, in ms (None = leave the engines alone): very fast games get a small one, a third of the increment at most 5 ms (an overhead as large as the
    increment would eat the clock: with 0.5 s + 0.005 s and 5 ms several engines ran out of time that were fine with 1 ms); longer games keep the engines' own settings."""
    mode = (tc or {}).get("mode", "time")
    if mode == "time" and float(tc.get("base", 10)) <= 3.0:
        return int(min(AUTO_OVERHEAD_MS, max(0.0, float(tc.get("inc", 0)) * 1000.0 / 3.0)))
    if mode == "movetime" and int(tc.get("ms", 100)) <= 200:
        return AUTO_OVERHEAD_MS
    return None


def effective_overhead(cfg):
    """The 'Engine time overhead' of a tournament in ms, or None for the engines' own settings ('auto': see auto_overhead)."""
    ov = cfg.get("overhead_ms")
    return auto_overhead(cfg.get("tc")) if ov == "auto" else ov


class Tournament:
    def __init__(self, cfg, directory, bus, data_dir, resource_dir=None):
        self.cfg = cfg
        self.id = cfg["id"]
        self.dir = directory
        os.makedirs(self.dir, exist_ok=True)
        self.bus = bus
        self.status = cfg.get("status", "ready")
        self.error = None
        self.games = []                                    # summaries of the finished games, in finishing order
        self.offsets = {}                                  # gid -> (offset, length) in games.jsonl
        self.done_keys = set()
        self.next_gid = 1
        self.active = {}
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.pool = EnginePool(max_idle=max(4, 2 * int(cfg.get("concurrency", 2))))
        self.taskq = queue.Queue()
        self.cv = threading.Condition()
        self.outstanding = 0
        self.workers = []
        self.controller = None
        self.elapsed_before = float(cfg.get("elapsed", 0.0))
        self.run_started = None
        self._last_emit = {}
        self._jsonl = os.path.join(self.dir, "games.jsonl")
        self.book = openmod.build_book(cfg.get("openings"), data_dir, resource_dir)
        ratings = {eid: (spec.get("elo") or 0) for eid, spec in cfg["engines"].items()}
        self.sched = formats.make_scheduler(cfg["format"], cfg["engine_ids"], ratings, cfg.get("params") or {}, cfg.get("seed", 1))
        self.total = self.sched.total_games()
        self._load_existing()

    # ------------------------------------------------------------------------------------------------------- persistence
    def _load_existing(self):
        if not os.path.exists(self._jsonl):
            return
        with open(self._jsonl, "rb") as f:
            while True:
                off = f.tell()
                line = f.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                self.games.append(self._summary(rec))
                self.offsets[rec["gid"]] = (off, len(line))
                self.done_keys.add(rec["key"])
                self.next_gid = max(self.next_gid, rec["gid"] + 1)
        if self.status in ("running", "paused", "stopping"):
            self.status = "stopped"                         # the app was closed while it ran

    def save_config(self):
        d = dict(self.cfg)
        d["status"] = self.status
        d["elapsed"] = self.elapsed()
        d["saved"] = _now_iso()
        tmp = os.path.join(self.dir, "config.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, os.path.join(self.dir, "config.json"))

    @staticmethod
    def _summary(rec):
        return {k: rec.get(k) for k in ("gid", "key", "batch", "pair", "g", "round", "white", "black", "result", "reason", "plies", "dur", "ended")}

    def elapsed(self):
        return self.elapsed_before + ((time.monotonic() - self.run_started) if self.run_started else 0.0)

    # ------------------------------------------------------------------------------------------------------------ events
    def _emit(self, ev):
        ev["tid"] = self.id
        self.bus.publish(ev)

    def _emit_state(self):
        self._emit({"type": "tournament", "status": self.status, "done": len(self.games), "total": self.total, "elapsed": round(self.elapsed(), 1),
                    "active": len(self.active), "error": self.error, "structure": self.sched.structure(self._games_for_sched())})

    def _games_for_sched(self):
        return [{"white": g["white"], "black": g["black"], "result": g["result"], "batch": g["batch"], "pair": g["pair"]} for g in self.games]

    # --------------------------------------------------------------------------------------------------------------- run
    def start(self):
        with self.lock:
            if self.status in ("running", "paused"):
                return
            if self.status == "finished":
                return
            self.status = "running"
            self.error = None
            self.stop_flag.clear()
            self.pause_flag.clear()
            self.run_started = time.monotonic()
        self.save_config()
        n = max(1, int(self.cfg.get("concurrency", 2)))
        self.workers = [threading.Thread(target=self._worker, name="worker-%d" % i, daemon=True) for i in range(n)]
        for w in self.workers:
            w.start()
        self.controller = threading.Thread(target=self._control, name="tournament-controller", daemon=True)
        self.controller.start()
        self._emit_state()

    def pause(self):
        with self.lock:
            if self.status == "running":
                self.pause_flag.set()
                self.status = "paused"
        self._emit_state()

    def resume(self):
        with self.lock:
            if self.status == "paused":
                self.pause_flag.clear()
                self.status = "running"
        self._emit_state()

    def stop(self):
        with self.lock:
            if self.status not in ("running", "paused"):
                return
            self.status = "stopping"
        self.stop_flag.set()
        self.pause_flag.clear()
        try:
            while True:
                self.taskq.get_nowait()
                self._task_done()
        except queue.Empty:
            pass
        self._emit_state()

    def _control(self):
        try:
            batch_no = 0
            while not self.stop_flag.is_set():
                batch = self.sched.next_batch(self._games_for_sched_before(batch_no), batch_no)
                if batch is None:
                    break
                tasks = self._expand(batch, batch_no)
                tasks = [t for t in tasks if t["key"] not in self.done_keys]
                if tasks:
                    with self.cv:
                        self.outstanding = len(tasks)
                    for t in tasks:
                        self.taskq.put(t)
                    with self.cv:
                        while self.outstanding > 0:
                            self.cv.wait(timeout=0.5)
                self._emit_state()
                if self.stop_flag.is_set():
                    break
                batch_no += 1
            with self.lock:
                self.status = "stopped" if self.stop_flag.is_set() else "finished"
        except Exception:
            self.error = traceback.format_exc()
            with self.lock:
                self.status = "error"
        finally:
            for _ in self.workers:
                self.taskq.put(None)
            self.run_started_end()
            self.save_config()                                   # status first (a reload right now must see 'finished' / 'stopped'), then shut the engines down
            self._emit_state()
            self.pool.close_all()

    def run_started_end(self):
        if self.run_started:
            self.elapsed_before += time.monotonic() - self.run_started
            self.run_started = None

    def _games_for_sched_before(self, batch_no):
        return [{"white": g["white"], "black": g["black"], "result": g["result"], "batch": g["batch"], "pair": g["pair"]} for g in self.games if g["batch"] < batch_no]

    def _expand(self, batch, batch_no):
        tasks = []
        for p in batch:
            for g in range(p.games):
                w, b = (p.a, p.b) if g % 2 == 0 else (p.b, p.a)
                tasks.append({"key": "%d:%d:%d" % (batch_no, p.index, g), "batch": batch_no, "pair": p.index, "g": g, "round": p.round,
                              "white": w, "black": b, "per_pair": (p.games + 1) // 2})
        return tasks

    def _task_done(self):
        with self.cv:
            self.outstanding -= 1
            self.cv.notify_all()

    def _worker(self):
        while True:
            task = self.taskq.get()
            if task is None:
                return
            try:
                while self.pause_flag.is_set() and not self.stop_flag.is_set():
                    time.sleep(0.15)
                if not self.stop_flag.is_set():
                    self._run_task(task)
            except Exception:
                self.error = traceback.format_exc()
            finally:
                self._task_done()

    # --------------------------------------------------------------------------------------------------------------- game
    def _engine_spec(self, eid):
        s = self.cfg["engines"][eid]
        opts = {}
        if self.cfg.get("hash"):
            opts["Hash"] = int(self.cfg["hash"])
        if self.cfg.get("threads"):
            opts["Threads"] = int(self.cfg["threads"])
        opts.update(s.get("options") or {})
        return {"path": s["path"], "args": s.get("args") or "", "cwd": s.get("cwd"), "options": opts, "name": s["name"], "priority": self.cfg.get("priority", "above"),
                "overhead_ms": effective_overhead(self.cfg), "quirks": s.get("quirks") or {}}

    def _run_task(self, task):
        opening = self.book.pick(task["batch"], task["pair"], task["g"] // 2, task["per_pair"])
        board = chess.Board(opening["fen"]) if opening.get("fen") else chess.Board()
        for u in opening.get("moves") or []:
            board.push_uci(u)
        with self.lock:
            gid = self.next_gid
            self.next_gid += 1
            live = {"gid": gid, "key": task["key"], "round": task["round"], "white": task["white"], "black": task["black"], "ply": len(opening.get("moves") or []),
                    "fen": board.fen(), "san": [], "clocks": None, "cp": None, "depth": None, "nodes": None, "nps": None, "pv": "", "check": None, "started": time.time()}
            self.active[gid] = live
        self._emit({"type": "game_start", "game": live})
        w = b = None
        rec = None
        keep = True
        try:
            try:
                w = self.pool.acquire(self._engine_spec(task["white"]))
            except EngineError as e:
                rec = self._forfeit(task, "0-1", "engine failed to start: %s" % e)
            if rec is None:
                try:
                    b = self.pool.acquire(self._engine_spec(task["black"]))
                except EngineError as e:
                    rec = self._forfeit(task, "1-0", "engine failed to start: %s" % e)
            if rec is None:
                spec = {"gid": gid, "round": task["round"], "white": task["white"], "black": task["black"], "start_fen": opening.get("fen"),
                        "opening_moves": opening.get("moves") or [], "tc": self.cfg["tc"], "adj": self.cfg.get("adj"), "margin_ms": self.cfg.get("margin_ms", 600)}

                def on_update(u):
                    live.update({"ply": u["ply"], "fen": u["fen"], "san": u["san_list"], "clocks": u["clocks"], "cp": u["cp"], "depth": u["depth"],
                                 "nodes": u["nodes"], "nps": u["nps"], "pv": u["pv"], "check": u["check"], "last": u["uci"]})
                    now = time.monotonic()
                    if now - self._last_emit.get(gid, 0) >= 0.12:
                        self._last_emit[gid] = now
                        self._emit({"type": "game_update", "gid": gid, "ply": u["ply"], "fen": u["fen"], "san": u["san_list"], "clocks": u["clocks"],
                                    "cp": u["cp"], "depth": u["depth"], "nodes": u["nodes"], "nps": u["nps"], "pv": u["pv"], "check": u["check"], "last": u["uci"]})
                rec = gamemod.play_game(spec, w, b, on_update=on_update, should_stop=self.stop_flag.is_set)
                if rec["reason"] in ("time forfeit", "engine crashed", "engine error", "illegal move", "no move", "no move in time", "engine not responding", "aborted"):
                    keep = False
        except Exception:
            rec = None
            self.error = traceback.format_exc()
            keep = False
        finally:
            for e in (w, b):
                if e is not None:
                    self.pool.release(e, keep=keep)
            with self.lock:
                self.active.pop(gid, None)
            self._last_emit.pop(gid, None)
        if rec is None or rec["result"] not in ("1-0", "0-1", "1/2-1/2"):
            self._emit({"type": "game_aborted", "gid": gid})
            return
        rec.update({"gid": gid, "key": task["key"], "batch": task["batch"], "pair": task["pair"], "g": task["g"], "round": task["round"]})
        self._store(rec)

    def _forfeit(self, task, result, why):
        return {"white": task["white"], "black": task["black"], "round": task["round"], "result": result, "reason": why, "start_fen": None, "uci": [], "san": [],
                "cp": [], "depth": [], "ms": [], "nodes": [], "book": 0, "plies": 0, "dur": 0.0, "ended": _now_iso()}

    def _store(self, rec):
        line = (json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            with open(self._jsonl, "ab") as f:
                off = f.tell()
                f.write(line)
            self.offsets[rec["gid"]] = (off, len(line))
            summ = self._summary(rec)
            self.games.append(summ)
            self.done_keys.add(rec["key"])
        fen_final = None
        try:
            b = chess.Board(rec["start_fen"]) if rec.get("start_fen") else chess.Board()
            for u in rec["uci"]:
                b.push_uci(u)
            fen_final = b.fen()
        except Exception:
            pass
        self._emit({"type": "game_end", "game": summ, "fen": fen_final})
        self._emit_state()

    # -------------------------------------------------------------------------------------------------------------- views
    def snapshot(self):
        with self.lock:
            games = list(self.games)
            active = [dict(v) for v in self.active.values()]
        return {"id": self.id, "name": self.cfg.get("name"), "format": self.cfg["format"], "status": self.status, "error": self.error, "total": self.total,
                "done": len(games), "elapsed": round(self.elapsed(), 1), "cfg": self.public_cfg(), "games": games, "active": active,
                "structure": self.sched.structure([{"white": g["white"], "black": g["black"], "result": g["result"], "batch": g["batch"], "pair": g["pair"]} for g in games]),
                "book": self.book.name, "book_size": len(self.book)}

    def public_cfg(self):
        c = dict(self.cfg)
        return c

    def get_game(self, gid):
        off = self.offsets.get(gid)
        if off is None:
            return None
        with open(self._jsonl, "rb") as f:
            f.seek(off[0])
            rec = json.loads(f.read(off[1]).decode("utf-8"))
        b = chess.Board(rec["start_fen"]) if rec.get("start_fen") else chess.Board()
        fens, checks = [b.fen()], [chess.square_name(b.king(b.turn)) if b.is_check() else None]
        for u in rec["uci"]:
            b.push_uci(u)
            fens.append(b.fen())
            checks.append(chess.square_name(b.king(b.turn)) if b.is_check() else None)
        rec["fens"], rec["checks"] = fens, checks
        return rec

    def pgn_text(self):
        names = {eid: s["name"] for eid, s in self.cfg["engines"].items()}
        out = []
        for gsum in sorted(self.games, key=lambda g: g["gid"]):
            off = self.offsets.get(gsum["gid"])
            if off is None:
                continue
            with open(self._jsonl, "rb") as f:
                f.seek(off[0])
                rec = json.loads(f.read(off[1]).decode("utf-8"))
            out.append(gamemod.build_pgn(rec, names, event=self.cfg.get("name") or "Kestrel Arena", tc=self.cfg.get("tc"), game_no=rec["gid"]))
        return "\n\n".join(out) + "\n"

    def close(self):
        self.stop_flag.set()
        self.pool.close_all()


class TournamentManager:
    def __init__(self, root, bus, data_dir, resource_dir=None):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.bus = bus
        self.data_dir = data_dir
        self.resource_dir = resource_dir
        self.current = None
        self.lock = threading.Lock()

    def _dir(self, tid):
        return os.path.join(self.root, tid)

    def create(self, cfg):
        with self.lock:
            if self.current and self.current.status in ("running", "paused", "stopping"):
                raise ValueError("A tournament is still running. Stop it first.")
            cfg = dict(cfg)
            cfg["id"] = cfg.get("id") or time.strftime("t%Y%m%d_%H%M%S")
            cfg["created"] = _now_iso()
            cfg["status"] = "ready"
            t = Tournament(cfg, self._dir(cfg["id"]), self.bus, self.data_dir, self.resource_dir)
            t.save_config()
            if self.current:
                self.current.close()
            self.current = t
            return t

    def get(self, tid):
        with self.lock:
            if self.current and self.current.id == tid:
                return self.current
        return None

    def load(self, tid):
        """Loads a saved tournament (to look at it or to resume it)."""
        with self.lock:
            if self.current and self.current.id == tid:
                return self.current
            if self.current and self.current.status in ("running", "paused", "stopping"):
                raise ValueError("A tournament is still running. Stop it first.")
            p = os.path.join(self._dir(tid), "config.json")
            if not os.path.exists(p):
                raise ValueError("unknown tournament")
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
            t = Tournament(cfg, self._dir(tid), self.bus, self.data_dir, self.resource_dir)
            if self.current:
                self.current.close()
            self.current = t
            return t

    def list(self):
        out = []
        for tid in sorted(os.listdir(self.root), reverse=True):
            p = os.path.join(self.root, tid, "config.json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    c = json.load(f)
            except Exception:
                continue
            n = 0
            jl = os.path.join(self.root, tid, "games.jsonl")
            if os.path.exists(jl):
                with open(jl, "rb") as f:
                    n = sum(1 for _ in f)
            status = c.get("status", "ready")
            if self.current and self.current.id == tid:
                status, n = self.current.status, len(self.current.games)
            elif status in ("running", "paused", "stopping"):
                status = "stopped"
            out.append({"id": tid, "name": c.get("name"), "format": c.get("format"), "status": status, "games": n, "engines": len(c.get("engine_ids", [])),
                        "created": c.get("created"), "tc": c.get("tc")})
        return out

    def delete(self, tid):
        import shutil
        with self.lock:
            if self.current and self.current.id == tid:
                if self.current.status in ("running", "paused", "stopping"):
                    raise ValueError("Stop the tournament before deleting it.")
                self.current.close()
                self.current = None
            d = self._dir(tid)
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "config.json")):
                shutil.rmtree(d, ignore_errors=True)

    def shutdown(self):
        if self.current:
            try:
                self.current.stop()
            except Exception:
                pass
            self.current.close()
