"""Patched engine builds (engines/patched/<id>.exe, made from the engines' own source with tools/engine_patches), with invented files - no real engines:
  - Registry.patched_build finds the build of a roster engine by its id; not for the bundled Kestrel, not for an entry with "patched": false, not when there is no file
  - a tournament uses the patched build (name with ' (patched)', no command-line arguments of the official entry) and none of the Arena's workarounds: the engine has its bugs fixed
  - ... unless the entry has its own "quirks", and an entry with "patched": false keeps the official exe and the workarounds
usage: python tests/test_patched.py"""
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.api import build_tournament_cfg               # noqa: E402
from arena.engines import KNOWN_QUIRKS, Registry, quirks_of    # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="arena_patched_test_")
data, app, official = (os.path.join(tmp, d) for d in ("data", "app", "official"))
for d in (data, os.path.join(app, "engines", "patched"), official):
    os.makedirs(d)
for name in ("yukari.exe", "smallbrain.exe"):
    open(os.path.join(app, "engines", "patched", name), "w").close()
for name in ("yukari.exe", "smallbrain.exe", "zed.exe", "quanticade.exe"):
    open(os.path.join(official, name), "w").close()


def entry(eid, **kw):
    e = {"id": eid, "name": eid.capitalize(), "path": os.path.join(official, eid + ".exe"), "cwd": official, "args": "--official-arg", "options": {}, "enabled": True}
    e.update(kw)
    return e


reg = Registry(data, ROOT, app)
reg.save_engines([entry("yukari"), entry("smallbrain", patched=False), entry("zed"), entry("quanticade", quirks={"warmup": True})])
reg = Registry(data, ROOT, app)
mine = {e["id"]: e for e in reg.engines() if not e.get("builtin")}
patched_yukari = os.path.join(app, "engines", "patched", "yukari.exe")

check(reg.patched_build(mine["yukari"]) == patched_yukari, "a roster engine with a file in engines/patched gets it: %s" % os.path.basename(reg.patched_build(mine["yukari"]) or ""))
check(reg.patched_build(mine["smallbrain"]) is None, "an entry with \"patched\": false keeps the official exe (saved with the list: %s)" % mine["smallbrain"].get("patched"))
check(reg.patched_build(mine["zed"]) is None and reg.patched_build(mine["quanticade"]) is None, "no file, no patched build")
check(all(reg.patched_build(e) is None for e in reg.engines() if e.get("builtin")), "never for the bundled Kestrel")
check(reg.patched_build(None) is None and reg.patched_build({}) is None, "nothing to look up, nothing found")

check(quirks_of({"id": "yukari"}) == KNOWN_QUIRKS["yukari"] and quirks_of({"id": "yukari"}, patched=True) == {}, "the workarounds of the table are not needed by a patched build")
check(quirks_of({"id": "yukari", "quirks": {"warmup": True}}, patched=True) == {"warmup": True}, "... unless the entry has its own")

st = types.SimpleNamespace(registry=reg)
cfg = build_tournament_cfg(st, {"name": "t", "format": "roundrobin", "engines": ["yukari", "smallbrain", "zed"], "games": 2, "tc": {"mode": "movetime", "ms": 100}})
sp = cfg["engines"]
check(sp["yukari"]["path"] == patched_yukari and sp["yukari"]["name"] == "Yukari (patched)", "a tournament plays the patched build, named as such: %s" % sp["yukari"]["name"])
check(sp["yukari"]["args"] == "" and sp["yukari"]["cwd"] is None and sp["yukari"]["quirks"] == {}, "... without the arguments and folder of the official entry, and without workarounds")
check(sp["smallbrain"]["path"] == mine["smallbrain"]["path"] and sp["smallbrain"]["name"] == "Smallbrain" and sp["smallbrain"]["args"] == "--official-arg",
      "\"patched\": false: the official exe, name and arguments")
check(sp["smallbrain"]["quirks"] == KNOWN_QUIRKS["smallbrain"], "... and the workarounds of the table")
check(sp["zed"]["path"] == mine["zed"]["path"] and sp["zed"]["quirks"] == {}, "an engine without a patched build is as before")

(open(os.path.join(app, "engines", "patched", "quanticade.exe"), "w")).close()
reg2 = Registry(data, ROOT, app)
sp2 = build_tournament_cfg(types.SimpleNamespace(registry=reg2), {"name": "t", "engines": ["quanticade", "zed"], "games": 2})["engines"]
check(sp2["quanticade"]["name"].endswith("(patched)") and sp2["quanticade"]["quirks"] == {"warmup": True}, "a patched build keeps the entry's own workarounds: %s" % sp2["quanticade"]["quirks"])

print("\nALL OK" if not fails else "\n%d FAILED" % fails)
sys.exit(1 if fails else 0)
