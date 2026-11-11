"""Application state and the JSON endpoints used by the web UI."""
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import chess

from . import levels, paths
from .engines import Registry, cpu_variant, quirks_of
from .events import Bus
from .game import DEFAULT_ADJ
from .play import AnalysisSession, PlaySession, apply_move, legal_info
from .tournament import TournamentManager, normalize_tc
from .uci import EngineError, UciEngine, probe_engine

VERSION = "1.0"


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


class Resp:
    """A non-JSON response (downloads)."""

    def __init__(self, data, ctype="text/plain; charset=utf-8", filename=None):
        self.data = data if isinstance(data, bytes) else data.encode("utf-8")
        self.ctype, self.filename = ctype, filename


class AppState:
    def __init__(self):
        self.res_dir, self.app_dir, self.data_dir = paths.resource_dir(), paths.app_dir(), paths.data_dir()
        self.registry = Registry(self.data_dir, self.res_dir, self.app_dir)
        self.bus = Bus()
        self.tournaments = TournamentManager(os.path.join(self.data_dir, "tournaments"), self.bus, self.data_dir, self.res_dir)
        self.play = None
        self.analysis = None
        self.token = secrets.token_urlsafe(18)
        self.window = None
        self.port = None
        self.lock = threading.Lock()

    def shutdown(self):
        for obj in (self.play, self.analysis):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        self.tournaments.shutdown()

    # ------------------------------------------------------------------------------------------------ file dialog
    def pick_file(self, kind):
        types = {"exe": ("Programs (*.exe)", "All files (*.*)"), "epd": ("Openings (*.epd;*.pgn)", "All files (*.*)"), "any": ("All files (*.*)",),
                 "folder": None}.get(kind, ("All files (*.*)",))
        if self.window is not None:
            import webview
            try:
                dlg_type = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
            except AttributeError:
                dlg_type = webview.FOLDER_DIALOG if kind == "folder" else webview.OPEN_DIALOG
            res = self.window.create_file_dialog(dlg_type, allow_multiple=False, **({} if types is None else {"file_types": types}))
            return res[0] if res else None
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                return filedialog.askdirectory() or None
            return filedialog.askopenfilename(filetypes=[(t.split(" (")[0], t.split("(")[1].rstrip(")").replace(";", " ")) for t in types]) or None
        finally:
            root.destroy()


# ------------------------------------------------------------------------------------------------------------- helpers
def _need(body, key, typ=None):
    if key not in body:
        raise ApiError("missing field: %s" % key)
    return body[key]


def _num(v, default, lo=None, hi=None, integer=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return int(x) if integer else x


def build_tournament_cfg(st, p):
    ids = list(_need(p, "engines"))
    if len(ids) < 2:
        raise ApiError("Pick at least two engines.")
    if len(set(ids)) != len(ids):
        raise ApiError("An engine is listed twice.")
    specs = {}
    for eid in ids:
        e = st.registry.get(eid)
        if not e:
            raise ApiError("Unknown engine: %s" % eid)
        if not os.path.exists(e["path"]):
            raise ApiError("%s: the file %s does not exist." % (e["name"], e["path"]))
        patched = st.registry.patched_build(e)            # a patched build of the engine (engines/patched/<id>.exe) is used where there is one
        specs[eid] = {"id": eid, "name": e["name"] + (" (patched)" if patched else ""), "path": patched or e["path"], "args": "" if patched else (e.get("args") or ""),
                      "cwd": None if patched else e.get("cwd"), "options": e.get("options") or {}, "elo": e.get("elo"), "quirks": quirks_of(e, patched=bool(patched))}
    fmt = p.get("format", "roundrobin")
    if fmt not in ("roundrobin", "gauntlet", "swiss", "knockout"):
        raise ApiError("Unknown format: %s" % fmt)
    try:
        tc = normalize_tc(p.get("tc"))
    except ValueError as e:
        raise ApiError(str(e))
    adj = dict(DEFAULT_ADJ)
    adj.update({k: v for k, v in (p.get("adj") or {}).items() if k in DEFAULT_ADJ})
    games = _num(p.get("games"), 2, 1, 1000, True)
    params = {"rounds": _num(p.get("rounds"), 1, 1, 100, True), "games": games, "hero": p.get("hero") or ids[0],
              "swiss_rounds": _num(p.get("swiss_rounds"), 5, 1, 50, True), "ko_games": _num(p.get("ko_games"), 2, 2, 1000, True),
              "seeding": p.get("seeding") if p.get("seeding") in ("rating", "random", "list") else "rating"}
    op = p.get("openings") or {}
    cfg = {"name": (p.get("name") or "").strip() or "Tournament %s" % time.strftime("%d %b %H:%M"), "format": fmt, "engine_ids": ids, "engines": specs, "params": params,
           "tc": tc, "hash": _num(p.get("hash"), 64, 1, 65536, True), "threads": _num(p.get("threads"), 1, 1, 128, True), "margin_ms": _num(p.get("margin_ms"), 600, 0, 10000, True),
           "overhead_ms": "auto" if p.get("overhead_ms") == "auto" else (None if p.get("overhead_ms") in (None, "") else _num(p.get("overhead_ms"), 5, 0, 1000, True)),
           "concurrency": _num(p.get("concurrency"), 2, 1, 64, True), "priority": "normal" if p.get("priority") == "normal" else "above",
           "openings": {"source": op.get("source", "builtin"), "path": op.get("path", ""), "order": op.get("order", "random"), "seed": _num(p.get("seed"), 1, 0, 10 ** 9, True)},
           "adj": adj, "seed": _num(p.get("seed"), 1, 0, 10 ** 9, True)}
    return cfg


def _engine_spec(st, eid):
    e = st.registry.get(eid or st.registry.default_engine_id())
    if not e:
        raise ApiError("Unknown engine.")
    if not os.path.exists(e["path"]):
        raise ApiError("%s: the file %s does not exist." % (e["name"], e["path"]))
    return {"path": e["path"], "args": e.get("args") or "", "cwd": e.get("cwd"), "options": e.get("options") or {}, "name": e["name"], "id": e["id"]}


# --------------------------------------------------------------------------------------------------------- endpoints
def ep_bootstrap(st, req):
    cur = st.tournaments.current
    return {"app": "Kestrel Arena", "version": VERSION, "engines": st.registry.engines(), "ui": st.registry.settings.get("ui", {}),
            "cpu": {"logical": os.cpu_count() or 1, "variant": cpu_variant()}, "tournaments": st.tournaments.list(), "current": cur.snapshot() if cur else None,
            "adj_defaults": DEFAULT_ADJ, "project_dir": st.registry.project_dir(), "data_dir": st.data_dir,
            "levels": {"default": levels.DEFAULT_RATING, "list": levels.public_list()}}


def ep_engines_save(st, req):
    return {"engines": st.registry.save_engines(_need(req.body, "engines"))}


def ep_engines_probe(st, req):
    path = _need(req.body, "path")
    if not os.path.exists(path):
        return {"ok": False, "error": "file not found", "options": []}
    return probe_engine(path, cwd=req.body.get("cwd"), args=req.body.get("args"))


def ep_engines_check(st, req):
    """Starts each engine briefly (in parallel) to make sure it speaks UCI before a tournament begins."""
    ids = _need(req.body, "ids")

    def one(eid):
        e = st.registry.get(eid)
        if not e:
            return {"id": eid, "ok": False, "error": "unknown engine"}
        t0 = time.perf_counter()
        eng = UciEngine(e["path"], cwd=e.get("cwd"), options=e.get("options"), name=e["name"], args=e.get("args"))
        try:
            eng.start(timeout=60)
            return {"id": eid, "name": e["name"], "ok": True, "id_name": eng.id_name, "ms": int((time.perf_counter() - t0) * 1000)}
        except EngineError as ex:
            return {"id": eid, "name": e["name"], "ok": False, "error": str(ex)}
        finally:
            eng.quit()
    with ThreadPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(one, ids))
    return {"results": res}


def ep_engines_import(st, req):
    try:
        res = st.registry.import_roster(req.body.get("folder"))
    except (ValueError, OSError) as e:
        raise ApiError(str(e))
    res["engines"] = st.registry.engines()
    return res


def ep_settings_save(st, req):
    st.registry.settings["ui"] = dict(req.body.get("ui") or {})
    st.registry.save()
    return {"ok": True}


def ep_pick_file(st, req):
    try:
        return {"path": st.pick_file(req.body.get("kind", "any"))}
    except Exception as e:
        raise ApiError("File dialog failed: %s" % e)


def ep_t_create(st, req):
    cfg = build_tournament_cfg(st, req.body)
    try:
        t = st.tournaments.create(cfg)
    except ValueError as e:
        raise ApiError(str(e), 409)
    if req.body.get("start", True):
        t.start()
    return t.snapshot()


def _tour(st, tid):
    t = st.tournaments.get(tid)
    if t is None:
        try:
            t = st.tournaments.load(tid)
        except ValueError as e:
            raise ApiError(str(e), 404 if "unknown" in str(e) else 409)
    return t


def ep_t_get(st, req):
    return _tour(st, req.m["tid"]).snapshot()


def ep_t_list(st, req):
    return {"tournaments": st.tournaments.list()}


def ep_t_action(st, req):
    t = _tour(st, req.m["tid"])
    act = req.m["act"]
    if act == "start":
        t.start()
    elif act == "pause":
        t.pause()
    elif act == "resume":
        t.resume()
    elif act == "stop":
        t.stop()
    return {"status": t.status}


def ep_t_delete(st, req):
    try:
        st.tournaments.delete(req.m["tid"])
    except ValueError as e:
        raise ApiError(str(e), 409)
    return {"tournaments": st.tournaments.list()}


def ep_t_game(st, req):
    t = _tour(st, req.m["tid"])
    g = t.get_game(int(req.m["gid"]))
    if g is None:
        raise ApiError("unknown game", 404)
    return g


def ep_t_pgn(st, req):
    t = _tour(st, req.m["tid"])
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", t.cfg.get("name") or t.id) + ".pgn"
    return Resp(t.pgn_text(), "application/x-chess-pgn; charset=utf-8", name)


def ep_legal(st, req):
    try:
        return legal_info(_need(req.body, "fen"))
    except ValueError as e:
        raise ApiError(str(e))


def ep_apply(st, req):
    try:
        return apply_move(_need(req.body, "fen"), _need(req.body, "uci"))
    except ValueError as e:
        raise ApiError(str(e))


def ep_play_new(st, req):
    b = req.body
    if st.play:
        st.play.close()
        st.play = None
    spec = _engine_spec(st, b.get("engine"))
    color = b.get("color", "white")
    if color == "random":
        color = "white" if secrets.randbelow(2) else "black"
    lim = {}
    mode = b.get("limit", "movetime")
    if mode == "nodes":
        lim["nodes"] = _num(b.get("nodes"), 50000, 100, 10 ** 9, True)
    elif mode == "depth":
        lim["depth"] = _num(b.get("depth"), 10, 1, 60, True)
    else:
        lim["movetime"] = _num(b.get("movetime"), 1000, 10, 600000, True)
    fen = (b.get("fen") or "").strip() or None
    if fen:
        try:
            chess.Board(fen)
        except ValueError as e:
            raise ApiError("invalid FEN: %s" % e)
    strength = {}
    if b.get("level") is not None and b.get("level") != "":
        strength["level"] = _num(b.get("level"), levels.DEFAULT_RATING, 0, 4000, True)         # a rating of the human-like levels (0 = full strength); wins over elo
    elif b.get("elo"):
        strength["elo"] = _num(b.get("elo"), 2000, 1, 4000, True)
    if b.get("skill") is not None and b.get("skill") != "":
        strength["skill"] = _num(b.get("skill"), 20, 0, 20, True)
    try:
        st.play = PlaySession(st.bus, spec, color == "white", fen, lim, strength)
    except EngineError as e:
        raise ApiError(str(e))
    st.play._emit()
    return st.play.state()


def _play(st):
    if not st.play:
        raise ApiError("No game is running.", 409)
    return st.play


def ep_play_move(st, req):
    try:
        _play(st).human_move(_need(req.body, "uci"))
    except ValueError as e:
        raise ApiError(str(e))
    return st.play.state()


def ep_play_undo(st, req):
    _play(st).undo()
    return st.play.state()


def ep_play_resign(st, req):
    _play(st).resign()
    return st.play.state()


def ep_play_limits(st, req):
    b = req.body
    lim = {}
    if b.get("nodes"):
        lim["nodes"] = _num(b["nodes"], 50000, 100, 10 ** 9, True)
    elif b.get("depth"):
        lim["depth"] = _num(b["depth"], 10, 1, 60, True)
    else:
        lim["movetime"] = _num(b.get("movetime"), 1000, 10, 600000, True)
    _play(st).set_limits(lim)
    return {"ok": True}


def ep_play_state(st, req):
    return st.play.state() if st.play else {"none": True}


def ep_play_pgn(st, req):
    return Resp(_play(st).pgn(), "application/x-chess-pgn; charset=utf-8", "game.pgn")


def ep_play_close(st, req):
    if st.play:
        st.play.close()
        st.play = None
    return {"ok": True}


def ep_analysis_start(st, req):
    b = req.body
    spec = _engine_spec(st, b.get("engine"))
    try:
        if st.analysis is None or st.analysis.spec["id"] != spec["id"] or st.analysis.engine.options != spec["options"]:
            if st.analysis:
                st.analysis.close()
            st.analysis = AnalysisSession(st.bus, spec)
            st.analysis.spec["id"] = spec["id"]
        st.analysis.analyse(_need(b, "fen"), b.get("multipv", 1))
    except EngineError as e:
        raise ApiError(str(e))
    except ValueError as e:
        raise ApiError("invalid FEN: %s" % e)
    return {"ok": True}


def ep_analysis_stop(st, req):
    if st.analysis:
        st.analysis.pause()
    return {"ok": True}


ROUTES = [
    ("GET", r"/api/bootstrap", ep_bootstrap),
    ("POST", r"/api/engines/save", ep_engines_save),
    ("POST", r"/api/engines/probe", ep_engines_probe),
    ("POST", r"/api/engines/check", ep_engines_check),
    ("POST", r"/api/engines/import_roster", ep_engines_import),
    ("POST", r"/api/settings/save", ep_settings_save),
    ("POST", r"/api/pick_file", ep_pick_file),
    ("POST", r"/api/tournament/create", ep_t_create),
    ("GET", r"/api/tournaments", ep_t_list),
    ("GET", r"/api/tournament/(?P<tid>[\w.-]+)", ep_t_get),
    ("POST", r"/api/tournament/(?P<tid>[\w.-]+)/(?P<act>start|pause|resume|stop)", ep_t_action),
    ("POST", r"/api/tournament/(?P<tid>[\w.-]+)/delete", ep_t_delete),
    ("GET", r"/api/tournament/(?P<tid>[\w.-]+)/game/(?P<gid>\d+)", ep_t_game),
    ("GET", r"/api/tournament/(?P<tid>[\w.-]+)/pgn", ep_t_pgn),
    ("POST", r"/api/legal", ep_legal),
    ("POST", r"/api/apply", ep_apply),
    ("POST", r"/api/play/new", ep_play_new),
    ("POST", r"/api/play/move", ep_play_move),
    ("POST", r"/api/play/undo", ep_play_undo),
    ("POST", r"/api/play/resign", ep_play_resign),
    ("POST", r"/api/play/limits", ep_play_limits),
    ("GET", r"/api/play/state", ep_play_state),
    ("GET", r"/api/play/pgn", ep_play_pgn),
    ("POST", r"/api/play/close", ep_play_close),
    ("POST", r"/api/analysis/start", ep_analysis_start),
    ("POST", r"/api/analysis/stop", ep_analysis_stop),
]
COMPILED = [(m, re.compile("^" + pat + "$"), fn) for m, pat, fn in ROUTES]
