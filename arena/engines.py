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
                user.append({"id": eid, "name": name, "path": e.get("path", ""), "args": e.get("args") or "", "cwd": e.get("cwd") or None, "options": dict(e.get("options") or {}),
                             "elo": elo, "enabled": bool(e.get("enabled", True)), "note": e.get("note", "")})
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

    def import_roster(self, folder=None):
        """Adds the engines of <project>/config/verify_results.json (name -> dir/cmd) with their tournament ratings. Returns (added, skipped)."""
        proj = folder or self.project_dir()
        if not proj:
            raise ValueError("Could not find config/verify_results.json of the tournament project. Pick the project folder in the Engines tab.")
        with open(os.path.join(proj, "config", "verify_results.json"), encoding="utf-8") as f:
            ver = json.load(f)
        ratings = {r["name"]: r["rating"] for r in self.roster_ratings()["engines"] if r.get("name")}
        have = {os.path.normcase(e.get("path", "")) for e in self.settings["engines"]}
        names = {e["name"].lower() for e in self.engines()}
        added = skipped = 0
        with self.lock:
            for key, v in ver.items():
                if not v.get("ok", True):
                    skipped += 1
                    continue
                path = os.path.join(v.get("dir", ""), v.get("cmd", ""))
                if os.path.normcase(path) in have or not os.path.exists(path):
                    skipped += 1
                    continue
                name = v.get("id_name") or key
                if name.lower() in names:
                    name = "%s (%s)" % (name, key)
                names.add(name.lower())
                self.settings["engines"].append({"id": slug(key), "name": name, "path": path, "args": v.get("args") or "", "cwd": v.get("dir"), "options": {},
                                                  "elo": ratings.get(key), "enabled": True, "note": "roster"})
                added += 1
            self.settings["project_dir"] = proj
        self.save()
        return added, skipped
