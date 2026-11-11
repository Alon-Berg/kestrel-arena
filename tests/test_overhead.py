"""The 'Engine time overhead' setting (arena/uci.py, arena/tournament.py): a pretend engine with spin options 'Move Overhead' (default 100), 'TimeMargin', 'UciOverhead', a pruning margin and a
check option; only the time-margin spin options are set, an option given by hand wins, limits are respected, nothing is sent when the setting is off, and 'automatic' only
acts for very fast games.
usage: python tests/test_overhead.py"""
import os
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.tournament import effective_overhead           # noqa: E402
from arena.uci import UciEngine                           # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


tmp = tempfile.mkdtemp(prefix="arena_overhead_test_")
fake = os.path.join(tmp, "fake.py")
log = os.path.join(tmp, "received.txt")
open(fake, "w").write(textwrap.dedent('''
    import sys
    log = open(%r, "a")
    for line in sys.stdin:
        log.write(line); log.flush()
        c = line.split()
        if not c: continue
        if c[0] == "uci":
            print("id name Fake")
            print("option name Move Overhead type spin default 100 min 0 max 5000")
            print("option name TimeMargin type spin default 200 min 5 max 1000")
            print("option name RazorMargin type spin default 266 min 0 max 1000")
            print("option name UciOverhead type spin default 50 min 0 max 100")
            print("option name Hash type spin default 16 min 1 max 1024")
            print("option name Overhead Mode type check default false")
            print("uciok")
        elif c[0] == "isready": print("readyok")
        elif c[0] == "quit": break
        sys.stdout.flush()
''' % log))


def run(**kw):
    if os.path.exists(log):
        os.remove(log)
    e = UciEngine(sys.executable, args=fake, **kw).start(timeout=20)
    e.quit()
    return [l.strip() for l in open(log).read().splitlines() if l.startswith("setoption")]


s = run(overhead_ms=5)
check("setoption name Move Overhead value 5" in s and "setoption name UciOverhead value 5" in s, "the 'overhead' spin options are set to 5: %s" % s)
check("setoption name TimeMargin value 5" in s, "TimeMargin is set, and the engine's minimum (5) is respected")
check(not any("RazorMargin" in x or "Overhead Mode" in x for x in s), "a pruning margin and a check option are left alone")
s = run(overhead_ms=0)
check("setoption name TimeMargin value 5" in s and "setoption name Move Overhead value 0" in s, "0 ms: options with a higher minimum get that minimum: %s" % s)
s = run(overhead_ms=500)
check("setoption name UciOverhead value 100" in s, "the engine's maximum (100) is respected: %s" % s)
s = run(overhead_ms=5, options={"Move Overhead": 77})
check("setoption name Move Overhead value 77" in s and "setoption name Move Overhead value 5" not in s, "an option set by hand wins: %s" % s)
s = run()
check(not s, "nothing is sent when the setting is off: %s" % s)

tc = lambda **k: {"tc": k}                                # noqa: E731
check(effective_overhead({"overhead_ms": None, "tc": {"mode": "time", "base": 0.5, "inc": 0.005}}) is None, "off stays off, even for very fast games")
check(effective_overhead({"overhead_ms": 10, "tc": {"mode": "time", "base": 60, "inc": 0}}) == 10, "a number is used as it is")
check(effective_overhead({"tc": {"mode": "time", "base": 0.5, "inc": 0.005}}) is None, "a tournament made before the setting existed is left as it was")
auto = lambda t: effective_overhead({"overhead_ms": "auto", "tc": t})     # noqa: E731
check(auto({"mode": "time", "base": 3, "inc": 0.03}) == 5 and auto({"mode": "time", "base": 1, "inc": 0.02}) == 5, "automatic: 5 ms for clocks up to 3 s with an increment of 15 ms or more")
check(auto({"mode": "time", "base": 0.5, "inc": 0.005}) == 1 and auto({"mode": "time", "base": 1, "inc": 0.01}) == 3 and auto({"mode": "time", "base": 2, "inc": 0}) == 0, "automatic: a third of the increment for smaller ones (5 ms -> 1, 10 ms -> 3, none -> 0)")
check(auto({"mode": "time", "base": 5, "inc": 0.1}) is None and auto({"mode": "time", "base": 10, "inc": 0.1}) is None, "automatic: the engines' own setting for longer games")
check(auto({"mode": "movetime", "ms": 100}) == 5 and auto({"mode": "movetime", "ms": 1000}) is None, "automatic: 100 ms per move counts as very fast, one second does not")
check(auto({"mode": "nodes", "n": 20000}) is None and auto({"mode": "depth", "d": 8}) is None, "automatic: nodes and depth limits are left alone")

print("\n%s" % ("ALL OK" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
