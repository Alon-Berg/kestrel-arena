"""The roster import of the Engines tab (arena/engines.py) and the move text of engines (arena/uci.py), with invented files (no real engines):
  - the newest config/verify_results_updated_*.json is the roster, else verify_results.json
  - new engines are added, changed ones are updated (own options stay), engines that left the roster are switched off, engines you added yourself are never touched
  - a second import changes nothing; options like 'option.OwnBook=false' become {'OwnBook': False}
  - promotions written with a capital letter (a7a8Q) are accepted
usage: python tests/test_roster_sync.py"""
import json
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import chess                                              # noqa: E402
from arena.engines import Registry                        # noqa: E402
from arena.uci import UciEngine, fix_move                 # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="arena_roster_test_")
proj, data, app = (os.path.join(tmp, d) for d in ("project", "data", "app"))
for d in (os.path.join(proj, "config"), data, app):
    os.makedirs(d)


def exe(folder, name):
    p = os.path.join(proj, "engines", folder)
    os.makedirs(p, exist_ok=True)
    open(os.path.join(p, name), "w").close()
    return p


dA, dA2, dB, dC, dD = exe("A", "a-1.0.exe"), exe("A_new", "a-2.0.exe"), exe("B", "b.exe"), exe("C", "c.exe"), exe("D", "d.exe")
old = {"A": {"ok": True, "id_name": "Alpha 1.0", "cmd": "a-1.0.exe", "dir": dA}, "B": {"ok": True, "id_name": "Beta", "cmd": "b.exe", "dir": dB},
       "C": {"ok": True, "id_name": "Gamma", "cmd": "c.exe", "dir": dC}}
json.dump(old, open(os.path.join(proj, "config", "verify_results.json"), "w"))
reg = Registry(data, app, app)
r = reg.import_roster(proj)
check(r["added"] == 3 and r["updated"] == 0 and r["disabled"] == 0 and r["file"] == "verify_results.json", "the first import takes verify_results.json: %s" % r)
reg.settings["engines"].append({"id": "mine", "name": "My engine", "path": os.path.join(dB, "b.exe") + ".mine", "args": "", "cwd": None, "options": {}, "elo": None, "enabled": True, "note": ""})
next(e for e in reg.settings["engines"] if e["id"] == "b")["options"] = {"Hash": 16}          # an option the user set
reg.save()

new = {"A": {"ok": True, "id_name": "Alpha 2.0", "cmd": "a-2.0.exe", "dir": dA2}, "B": {"ok": True, "id_name": "Beta", "cmd": "b.exe", "dir": dB, "opts": ["option.OwnBook=false", "option.Skill=7"]},
       "D": {"ok": True, "id_name": "Delta 1", "cmd": "d.exe", "dir": dD, "args": "-x 1"}, "E": {"ok": True, "id_name": "Missing", "cmd": "nothere.exe", "dir": dD}}
json.dump(new, open(os.path.join(proj, "config", "verify_results_updated_2026-01-02.json"), "w"))
json.dump(new, open(os.path.join(proj, "config", "verify_results_updated_2026-01-01.json"), "w"))          # an older update: the newest one wins
reg = Registry(data, app, app)
r = reg.import_roster(proj)
by = {e["id"]: e for e in reg.settings["engines"]}
check(r["file"] == "verify_results_updated_2026-01-02.json", "the newest updated roster is used: %s" % r["file"])
check(r["added"] == 1 and r["updated"] == 2 and r["disabled"] == 1 and r["skipped"] == 1, "added 1 (D), updated 2 (A new version, B new options), switched off 1 (C), skipped 1 (E has no file): %s" % r)
check(by["a"]["name"] == "Alpha 2.0" and by["a"]["path"].endswith("a-2.0.exe") and by["a"]["cwd"] == dA2, "A points to the new version")
check(by["b"]["options"] == {"Hash": 16, "OwnBook": False, "Skill": 7}, "B keeps the option the user set and gets the roster's: %s" % by["b"]["options"])
check(by["c"]["enabled"] is False and by["c"]["note"].startswith("roster"), "C is switched off, not deleted")
check(by["d"]["args"] == "-x 1" and by["d"]["enabled"] is True, "D is added with its arguments")
check(by["mine"]["enabled"] is True and by["mine"]["note"] == "" and by["mine"]["name"] == "My engine", "an engine you added yourself is untouched")
r2 = Registry(data, app, app).import_roster(proj)
check((r2["added"], r2["updated"], r2["disabled"]) == (0, 0, 0), "a second import changes nothing: %s" % r2)
json.dump(old, open(os.path.join(proj, "config", "verify_results_updated_2026-01-03.json"), "w"))               # C comes back
javaexe = os.path.join(dD, "d.exe")                                                                             # two engines that start the same program with different arguments (Java jars)
json.dump({"J1": {"ok": True, "id_name": "Jar one", "cmd": javaexe, "dir": dD, "args": "-jar one.jar"}, "J2": {"ok": True, "id_name": "Jar two", "cmd": javaexe, "dir": dD, "args": "-jar two.jar"}},
          open(os.path.join(proj, "config", "verify_results_updated_2026-01-09.json"), "w"))
rj = Registry(data, app, app).import_roster(proj)
byj = {e["id"]: e for e in Registry(data, app, app).settings["engines"]}
check(rj["added"] == 2 and byj["j1"]["args"] == "-jar one.jar" and byj["j2"]["args"] == "-jar two.jar", "two engines with the same program and different arguments are both added: %s" % rj)
os.remove(os.path.join(proj, "config", "verify_results_updated_2026-01-09.json"))
r3 = Registry(data, app, app).import_roster(proj)
by = {e["id"]: e for e in Registry(data, app, app).settings["engines"]}
check(by["c"]["enabled"] is True and by["c"]["note"] == "roster", "an engine that returns to the roster is switched on again: %s" % r3)
check(Registry.parse_roster_options(["option.A=true", "option.B=12", "option.C=x y", "junk"]) == {"A": True, "B": 12, "C": "x y"}, "roster options are parsed")

# an engine switched off by hand with keep_off stays off across imports (but still follows its new file)
regk = Registry(data, app, app)
ek = next(e for e in regk.settings["engines"] if e["id"] == "b")
ek["enabled"] = False
ek["keep_off"] = True
regk.save()
newer = {"A": {"ok": True, "id_name": "Alpha 2.0", "cmd": "a-2.0.exe", "dir": dA2}, "B": {"ok": True, "id_name": "Beta 2", "cmd": "b.exe", "dir": dB, "opts": ["option.OwnBook=false", "option.Skill=7"]},
         "C": {"ok": True, "id_name": "Gamma", "cmd": "c.exe", "dir": dC}, "D": {"ok": True, "id_name": "Delta 1", "cmd": "d.exe", "dir": dD, "args": "-x 1"}}
json.dump(newer, open(os.path.join(proj, "config", "verify_results_updated_2026-02-01.json"), "w"))
rk = Registry(data, app, app).import_roster(proj)
bk = {e["id"]: e for e in Registry(data, app, app).settings["engines"]}
check(bk["b"]["enabled"] is False and bk["b"].get("keep_off") is True and bk["b"]["name"] == "Beta 2", "keep_off: the engine stays switched off but its name follows the roster: %s" % rk)
saved = Registry(data, app, app)
saved.save_engines(saved.engines())
check(next(e for e in Registry(data, app, app).settings["engines"] if e["id"] == "b").get("keep_off") is True, "keep_off survives saving the list from the Engines tab")

# promotion letters
check([fix_move(x) for x in ("a7a8Q", "h2h1n", "e7e8R", "e2e4", "O-O", None, "0000", "a7a8q")] == ["a7a8q", "h2h1n", "e7e8r", "e2e4", "O-O", None, "0000", "a7a8q"], "fix_move lower-cases the promotion piece only")
fake = os.path.join(tmp, "fake_engine.py")
open(fake, "w").write(textwrap.dedent('''
    import sys
    for line in sys.stdin:
        c = line.split()
        if not c: continue
        if c[0] == "uci": print("id name Fake"); print("uciok")
        elif c[0] == "isready": print("readyok")
        elif c[0] == "go": print("info depth 1 score cp 10 pv a7a8Q"); print("bestmove a7a8Q ponder b8c8")
        elif c[0] == "quit": break
        sys.stdout.flush()
'''))
eng = UciEngine(sys.executable, args=fake).start(timeout=20)
eng.position("8/P6k/8/8/8/8/8/K7 w - - 0 1", [])
res = eng.go({"depth": 1}, timeout=10)
eng.quit()
check(res["bestmove"] == "a7a8q" and res["ponder"] == "b8c8", "an engine that answers 'a7a8Q' gets 'a7a8q': %s" % res["bestmove"])
check(chess.Move.from_uci(res["bestmove"]) in chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1").legal_moves, "... which is a legal move")

print("\n%s" % ("ALL OK" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
