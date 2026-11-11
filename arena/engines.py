"""Engine list: the bundled Kestrel builds (chosen for this CPU), engines the user added, and an optional import of the tournament project's roster."""
import json
import os
import re
import sys
import threading

KESTREL_ELO = {"3.2": 3581}                           # CCRL-Blitz-style estimate from the roster tournament (1 thread)
VARIANT_RANK = {"avx2": 3, "sse42": 2, "generic": 1}


def cpu_variant():
    """Best bundled build for this CPU: 'avx2' > 'sse42' > 'generic'."""
    if sys.platform != "win32":
        return "generic"
    try:
        import ctypes
        f = ctypes.windll.kernel32.IsProcessorFeaturePresent
        if f(40):
            return "avx2"
        if f(38):
            return "sse42"
    except Exception:
        pass
    return "generic"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "engine"


# Workarounds the Arena applies for engines of the roster that cannot play very fast games (0.5 s + 5 ms per game) on their own; keys of the roster entries (their 'id').  Used by tournaments and
# tools/check_roster.py; an entry of settings.json with its own "quirks" (even an empty {}) replaces these.  An engine with a patched build (engines/patched/<id>.exe, tools/engine_patches:
# Quanticade, Yukari, Smallbrain) has its bugs fixed in the engine and uses none of them.  What the keys do:
#   warmup             one short search when the engine starts (True: depth 1; a number: that many ms), so its slow first search is not played on a game clock
#   prepare_game       a depth-1 search of the start position of every game before the clocks run
#   retry_bad_move     (the default for EVERY engine in very fast games: a clock of at most 3 s or at most 200 ms per move; {"retry_bad_move": false} in an entry switches it off)
#                      an answer that is no move, or not a legal move (a search cut off before its first iteration was done) is asked for again, up to twice: 20 ms or more (100 ms or more for the
#                      third try), twice (three times) the previous try if that is longer, at most 150 ms (300 ms) and never beyond the time forfeit margin; only the last try counts on the clock
#   clock_to_movetime  in very fast games (clock of at most 3 s) the Arena sends 'go movetime <ms>' computed from the clock (a fortieth of it plus most of the increment) instead of the clock
#   movetime_add       ms the engine subtracts from every movetime on its own (added to the computed time, and to a fixed time per move)
#   movetime_min       the least movetime the engine copes with (ms; also applied to a fixed time per move)
KNOWN_QUIRKS = {
    "bagatur": {"warmup": 2000, "prepare_game": True},                                            # Java: slow while the JIT is cold, and about 1.2 s to rebuild everything whenever a game starts from another position
    "serendipity": {"warmup": True, "clock_to_movetime": True, "movetime_min": 30, "retry_bad_move": True},      # pauses of 10-30 ms (more under load); cut off before depth 1 it answers with its previous move
    "yukari": {"clock_to_movetime": True, "movetime_add": 19, "movetime_min": 30},                # takes 19 ms off every movetime, and crashes (Rust panic) when nothing is left
    "quanticade": {"retry_bad_move": True},                                                       # its timer ticks every 15.6 ms: a movetime below that can end the search before depth 1
    "smallbrain": {"clock_to_movetime": True, "retry_bad_move": True},                            # overshoots a nearly empty clock; now and then an illegal move in a tournament under load
    "uralochka": {"retry_bad_move": True},                                                        # now and then the null move a1a1 when the clock is nearly empty (1 s + 0.01 s: 2 games in 24)
    "elixir": {"movetime_add": 95},                                                               # its source takes DEFAULT_MOVE_OVERHEAD (100 ms) off every 'go movetime' too: 'go movetime 100' = depth 1 (fine with a clock)
}


def quirks_of(e, patched=False):
    """The workarounds for an engine entry (see KNOWN_QUIRKS).  A patched build (Registry.patched_build) has its bugs fixed in the engine, so it needs none - unless the entry has its own."""
    q = e.get("quirks")
    if q is None and patched:
        return {}
    return dict(q) if q is not None else dict(KNOWN_QUIRKS.get(e.get("id"), {}))


class Registry:
    def __init__(self, data_dir, resource_dir, app_dir):
        self.data_dir, self.resource_dir, self.app_dir = data_dir, resource_dir, app_dir
        self.path = os.path.join(data_dir, "settings.json")
        self.lock = threading.RLock()
        self.settings = {"engines": [], "builtin_options": {}, "ui": {}, "project_dir": None}
        self.load()

    # ----------------------------------------------------------------------------------------------------- persistence
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except Exception:
            pass

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
            os.replace(tmp, self.path)

    # ----------------------------------------------------------------------------------------------------------- list
    def builtin_engines(self):
        d = os.path.join(self.app_dir, "engines", "Kestrel")
        found = {}
        if os.path.isdir(d):
            for fn in os.listdir(d):
                m = re.match(r"kestrel-(\d+(?:\.\d+)*)-(avx2|sse42|generic)\.exe$", fn, re.I)
                if m:
                    found.setdefault(m.group(1), {})[m.group(2).lower()] = os.path.join(d, fn)
        want = cpu_variant()
        out = []
        for ver in sorted(found, key=lambda v: [int(x) for x in v.split(".")], reverse=True):
            variants = found[ver]
            usable = [v for v in variants if VARIANT_RANK[v] <= VARIANT_RANK[want]]
            if not usable:
                continue
            best = max(usable, key=lambda v: VARIANT_RANK[v])
            eid = "kestrel-" + ver
            out.append({"id": eid, "name": "Kestrel " + ver, "path": variants[best], "cwd": d, "options": dict(self.settings["builtin_options"].get(eid, {})),
                        "elo": KESTREL_ELO.get(ver), "builtin": True, "enabled": True, "note": "bundled (%s build)" % best})
        return out

    def engines(self):
        with self.lock:
            user = [dict(e) for e in self.settings["engines"]]
        for e in user:
            e["builtin"] = False
            e["exists"] = os.path.exists(e.get("path", ""))
        bi = self.builtin_engines()
        for e in bi:
            e["exists"] = True
        return bi + user

    def get(self, eid):
        for e in self.engines():
            if e["id"] == eid:
                return e
        return None

    def patched_build(self, e):
        """Path of the patched build of a roster engine, engines/patched/<id>.exe, or None (see tools/engine_patches: the engine's own time-handling bugs fixed, the search unchanged).
        Not for the bundled Kestrel, and not for an entry with "patched": false in settings.json."""
        if not e or e.get("builtin") or e.get("patched") is False or not e.get("id"):
            return None
        p = os.path.join(self.app_dir, "engines", "patched", str(e["id"]) + ".exe")
        return p if os.path.isfile(p) else None

    def default_engine_id(self):
        e = self.engines()
        return e[0]["id"] if e else None

    def save_engines(self, engines):
        """engines: the full list as edited in the UI (built-in entries only contribute their option overrides)."""
        user, seen = [], set()
        with self.lock:
            for e in engines:
                if e.get("builtin"):
                    self.settings["builtin_options"][e["id"]] = dict(e.get("options") or {})
                    seen.add(e["id"])
                    continue
                name = (e.get("name") or "").strip() or os.path.splitext(os.path.basename(e.get("path", "engine")))[0]
                eid = e.get("id") or slug(name)
                base, n = eid, 2
                while eid in seen:
                    eid, n = "%s-%d" % (base, n), n + 1
                seen.add(eid)
                elo = e.get("elo")
                try:
                    elo = int(elo) if elo not in (None, "") else None
                except (TypeError, ValueError):
                    elo = None
                entry = {"id": eid, "name": name, "path": e.get("path", ""), "args": e.get("args") or "", "cwd": e.get("cwd") or None, "options": dict(e.get("options") or {}),
                         "elo": elo, "enabled": bool(e.get("enabled", True)), "note": e.get("note", "")}
                if e.get("keep_off"):
                    entry["keep_off"] = True                       # (kept when the list is saved from the Engines tab)
                if e.get("quirks") is not None:
                    entry["quirks"] = dict(e["quirks"])
                if e.get("patched") is False:
                    entry["patched"] = False                       # (play the official exe even if engines/patched has a build of this engine)
                user.append(entry)
            self.settings["engines"] = user
        self.save()
        return self.engines()

    # ---------------------------------------------------------------------------------------------------------- roster
    def project_dir(self):
        cands = []
        if self.settings.get("project_dir"):
            cands.append(self.settings["project_dir"])
        if os.environ.get("KESTREL_ARENA_PROJECT"):
            cands.append(os.environ["KESTREL_ARENA_PROJECT"])
        p = self.app_dir
        for _ in range(5):
            cands.append(p)
            p = os.path.dirname(p)
        for c in cands:
            if c and os.path.exists(os.path.join(c, "config", "verify_results.json")):
                return c
        return None

    def roster_ratings(self):
        """Ratings of the tournament project's roster (project file when present, else the snapshot shipped with the app)."""
        proj = self.project_dir()
        rows = None
        source = "bundled snapshot"
        if proj:
            try:
                cfgdir = os.path.join(proj, "config")
                files = sorted(f for f in os.listdir(cfgdir) if re.match(r"actual_ratings_.*\.json$", f))
                if files:
                    with open(os.path.join(cfgdir, files[-1]), encoding="utf-8") as f:
                        data = json.load(f)
                    rows = [{"name": r[1], "id_name": r[2], "rating": r[3], "ccrl": r[4]} for r in data]
                    source = "project: " + files[-1]
            except Exception:
                rows = None
        if rows is None:
            for d in (self.data_dir, self.resource_dir):
                p = os.path.join(d, "data", "roster_ratings.json") if d == self.resource_dir else os.path.join(d, "roster_ratings.json")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        rows = json.load(f)
                    break
        rows = rows or []
        k = KESTREL_ELO.get("3.2")
        if k and not any("kestrel" in (r.get("name") or "").lower() for r in rows):
            rows = rows + [{"name": "Kestrel 3.2", "id_name": "Kestrel 3.2", "rating": k, "ccrl": None}]
        return {"source": source, "engines": rows}

    @staticmethod
    def roster_file(proj):
        """The roster of the tournament project: the newest config/verify_results_updated_*.json (the roster after the updates of the engines), else config/verify_results.json."""
        cfg = os.path.join(proj, "config")
        updated = sorted(f for f in os.listdir(cfg) if re.match(r"verify_results_updated_.*\.json$", f))
        return os.path.join(cfg, updated[-1]) if updated else os.path.join(cfg, "verify_results.json")

    @staticmethod
    def parse_roster_options(lst):
        """['option.OwnBook=false', 'option.Hash=64'] -> {'OwnBook': False, 'Hash': 64}"""
        out = {}
        for s in lst or []:
            m = re.match(r"option\.(.+?)=(.*)$", str(s).strip())
            if not m:
                continue
            k, v = m.group(1), m.group(2)
            if v.lower() in ("true", "false"):
                out[k] = v.lower() == "true"
            else:
                try:
                    out[k] = int(v)
                except ValueError:
                    out[k] = v
        return out

    def import_roster(self, folder=None):
        """Makes the engine list match the roster of the tournament project (the newest verify_results_updated_*.json, else verify_results.json): engines that are new are added with their
        tournament ratings, engines whose file or version changed are updated (your own option settings stay), engines the roster no longer has are switched off (not deleted).
        Engines you added yourself are never touched.  Returns {'added', 'updated', 'disabled', 'skipped', 'file'}."""
        proj = folder or self.project_dir()
        if not proj:
            raise ValueError("Could not find config/verify_results.json of the tournament project. Pick the project folder in the Engines tab.")
        rfile = self.roster_file(proj)
        with open(rfile, encoding="utf-8") as f:
            ver = json.load(f)
        ratings = {r["name"]: r["rating"] for r in self.roster_ratings()["engines"] if r.get("name")}
        added = updated = disabled = skipped = 0
        with self.lock:
            engines = self.settings["engines"]
            by_id = {e.get("id"): e for e in engines}
            have = {(os.path.normcase(e.get("path", "")), e.get("args") or "") for e in engines}          # file AND arguments: two Java engines share java.exe
            names = {e["name"].lower() for e in self.engines()}
            seen = set()
            for key, v in ver.items():
                eid = slug(key)
                if not v.get("ok", True):
                    skipped += 1
                    continue
                path = os.path.join(v.get("dir", ""), v.get("cmd", ""))
                if not os.path.exists(path):
                    skipped += 1
                    continue
                seen.add(eid)
                name = v.get("id_name") or key
                args = v.get("args") or ""
                opts = self.parse_roster_options(v.get("opts"))
                cur = by_id.get(eid)
                if cur is not None and str(cur.get("note", "")).startswith("roster"):
                    keep_off = bool(cur.get("keep_off"))                        # switched off by hand and meant to stay off (e.g. an engine that cannot play very fast games)
                    changed = (os.path.normcase(cur.get("path", "")) != os.path.normcase(path) or (cur.get("args") or "") != args or cur.get("cwd") != v.get("dir")
                               or cur.get("name") != name or (not keep_off and (not cur.get("enabled", True) or cur.get("note") != "roster"))
                               or any(k2 not in (cur.get("options") or {}) for k2 in opts))
                    if changed:
                        names.discard(str(cur.get("name", "")).lower())
                        if name.lower() in names:
                            name = "%s (%s)" % (name, key)
                        names.add(name.lower())
                        cur.update({"name": name, "path": path, "args": args, "cwd": v.get("dir"), "elo": ratings.get(key, cur.get("elo")),
                                    "enabled": cur.get("enabled", True) if keep_off else True, "note": cur.get("note") if keep_off else "roster"})
                        for k2, v2 in opts.items():
                            cur.setdefault("options", {}).setdefault(k2, v2)
                        updated += 1
                    continue
                if (os.path.normcase(path), args) in have or cur is not None:    # the same engine is in the list already (or the id is taken by an engine of yours)
                    skipped += 1
                    continue
                if name.lower() in names:
                    name = "%s (%s)" % (name, key)
                names.add(name.lower())
                engines.append({"id": eid, "name": name, "path": path, "args": args, "cwd": v.get("dir"), "options": opts, "elo": ratings.get(key), "enabled": True, "note": "roster"})
                have.add((os.path.normcase(path), args))
                added += 1
            for e in engines:                                                   # engines of an older roster that the current one no longer has
                if e.get("note") == "roster" and e.get("id") not in seen and e.get("enabled", True):
                    e["enabled"] = False
                    e["note"] = "roster (no longer in the roster)"
                    disabled += 1
            self.settings["project_dir"] = proj
        self.save()
        return {"added": added, "updated": updated, "disabled": disabled, "skipped": skipped, "file": os.path.basename(rfile)}
