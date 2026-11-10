"""Makes tests/levels_vectors.json: what the JavaScript levels of the web page (docs/play/levels.js of the Kestrel repository) do with fixed candidates and fixed random
numbers.  tests/test_levels.py runs the same cases through arena/levels.py, so the two can not drift apart.

usage: python tools/make_level_vectors.py [path of levels.js]        (needs Deno, https://deno.com; the default path is ../kestrel_repo/docs/play/levels.js)
Run it again whenever the table or the rules of the web page change, then update TABLE in arena/levels.py until tests/test_levels.py passes."""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_JS = os.path.join(os.path.dirname(ROOT), "kestrel_repo", "docs", "play", "levels.js")

JS = r"""
import * as L from %(url)s;
const cases = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = { table: L.LEVELS.map((l) => ({ id: l.id, name: l.name, rating: l.rating, depth: l.depth || 0, nodes: l.nodes || 0, k: l.k, temp: l.temp, slip: l.slip, full: !!l.full })),
  constants: { OPENING_PLIES: L.OPENING_PLIES, SAFE_PLIES: L.SAFE_PLIES, OPENING_TEMP: L.OPENING_TEMP, OPENING_WINDOW: L.OPENING_WINDOW, SCORE_CAP: L.SCORE_CAP, EASE_FROM: L.EASE_FROM, EASE_TO: L.EASE_TO, EASE_MIN: L.EASE_MIN, DEFAULT_RATING: L.DEFAULT_RATING },
  blurbs: L.LEVELS.map((l) => L.levelBlurb(l)),
  picks: [], steady: [], scores: [], wanted: [] };
for (const c of cases.picks) {
  let i = 0; const rng = () => c.rng[i++ % c.rng.length];
  out.picks.push(L.pickMove(c.cands, c.legal, L.LEVELS[c.level], c.ply, rng));
}
for (const b of cases.steady) out.steady.push(L.steadiness(b));
for (const s of cases.scores) out.scores.push(L.lineScore({ mate: s.mate, score: s.score }));
for (const w of cases.wanted) out.wanted.push(L.wantedLines(L.LEVELS[w.level], w.ply, w.n));
console.log(JSON.stringify(out));
"""


def make_cases(n_levels, seed=7):
    rng = random.Random(seed)
    moves = ["e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "g2g3", "b2b3", "f2f4", "a2a3", "h2h3", "e2e3", "d2d3"]
    cases = {"picks": [], "steady": [], "scores": [], "wanted": []}
    for _ in range(1200):
        lv = rng.randrange(n_levels)
        ply = rng.choice([0, 3, 7, 8, 9, 10, 11, 20, 41, 80])
        kk = rng.randint(1, 8)
        cands, s = [], rng.choice([rng.randint(-300, 300), rng.randint(-1500, 1500), rng.randint(500, 3000)])
        for j in range(kk):
            cands.append({"uci": moves[j], "score": s, "depth": 3})
            s -= rng.choice([0, 1, 5, 12, 30, 60, 120, 300, 700])
        legal = moves[:]
        rng.shuffle(legal)
        if rng.random() < 0.2:
            legal = [m for m in legal if m != cands[0]["uci"]] + [cands[0]["uci"]]
        cases["picks"].append({"level": lv, "ply": ply, "cands": cands, "legal": legal, "rng": [round(rng.random(), 6) for _ in range(4)]})
    cases["steady"] = [-500, -1, 0, 100, 150, 151, 300, 475, 800, 801, 1500, 5000]
    for _ in range(40):
        if rng.random() < 0.5:
            cases["scores"].append({"mate": False, "score": rng.randint(-5000, 5000)})
        else:
            cases["scores"].append({"mate": True, "score": rng.choice([1, 2, 5, 49, 50, 51, 99, -1, -3, -50, -80, 0])})
    for lv in range(n_levels):
        for ply in (0, 9, 10, 30):
            for n in (2, 3, 5, 12, 30):
                cases["wanted"].append({"level": lv, "ply": ply, "n": n})
    return cases


def main():
    js_path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_JS)
    deno = shutil.which("deno")
    if not deno:
        sys.exit("Deno is needed (https://deno.com)")
    url = "file:///" + js_path.replace("\\", "/").lstrip("/")
    tmp = tempfile.mkdtemp()
    script, cfile = os.path.join(tmp, "vectors.js"), os.path.join(tmp, "cases.json")
    with open(script, "w", encoding="utf-8") as f:
        f.write(JS.replace("%(url)s", json.dumps(url)))
    # the number of levels: ask the module itself (a first run without cases)
    with open(cfile, "w", encoding="utf-8") as f:
        json.dump({"picks": [], "steady": [], "scores": [], "wanted": []}, f)
    probe = subprocess.run([deno, "run", "--quiet", "--allow-read", script, cfile], capture_output=True, text=True)
    if probe.returncode != 0:
        sys.exit("could not load levels.js: " + probe.stderr)
    n_levels = len(json.loads(probe.stdout)["table"])
    cases = make_cases(n_levels)
    with open(cfile, "w", encoding="utf-8") as f:
        json.dump(cases, f)
    r = subprocess.run([deno, "run", "--quiet", "--allow-read", script, cfile], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("deno failed: " + r.stderr)
    out = json.loads(r.stdout)
    out["cases"] = cases
    out["source"] = "docs/play/levels.js of the Kestrel repository"          # (not the path on this computer)
    dst = os.path.join(ROOT, "tests", "levels_vectors.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print("wrote %s: %d levels, %d pick cases (%d KB)" % (dst, n_levels, len(cases["picks"]), os.path.getsize(dst) // 1024))


if __name__ == "__main__":
    main()
