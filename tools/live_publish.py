"""Publishes a tournament of the Arena on the website's Live page (https://alon-berg.github.io/Kestrel-chess-engine/live/).

The page on the site is static (GitHub Pages): it shows whatever is in docs/live/data/current.json.  This tool builds that file from a tournament of the Arena (the standings, crosstable, bracket, every
finished game, and the moves of the latest games) and pushes it to the site with YOUR git login - nobody else can publish, visitors can only watch, and nothing on the site can start a tournament.
It only reads the Arena's data folder (no engine is started, nothing is changed there).  The site updates a minute or so after a push, so this is "live" in steps, not move by move.

usage:  python tools/live_publish.py [--tournament latest|<id>] [--every 10] [--records 150] [--data <Arena data folder>] [--site <folder>] [--no-push]
  --every N   keep publishing every N minutes while the tournament changes (default: once); GitHub Pages builds a site at most 10 times an hour, so use 10 or more
  --site      the folder of the private working copy of the site (default: kestrel_live_site next to the Arena; it is cloned there the first time)
  --no-push   only write the file (into --site) and say what would be published
"""
import argparse
import json
import os
import subprocess
import sys
import time
import types
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.events import Bus                                          # noqa: E402
from arena.live import LiveHub                                        # noqa: E402
from arena.tournament import Tournament                               # noqa: E402

SITE_URL = "https://github.com/Alon-Berg/Kestrel-chess-engine.git"
PAGE_URL = "https://alon-berg.github.io/Kestrel-chess-engine/live/"
IDENT = ("Alon-Berg", "alonbergman2014@gmail.com")
RUNNING_WINDOW = 300                    # a tournament whose games file changed in the last 5 minutes is shown as in progress


def data_folder(arg=None):
    for d in (arg, os.environ.get("KESTREL_ARENA_DATA"), os.path.join(ROOT, "dist", "KestrelArena", "data"), os.path.join(ROOT, "data")):
        if d and os.path.isdir(os.path.join(d, "tournaments")):
            return d
    raise SystemExit("No Arena data folder with tournaments found: use --data")


def latest_id(data_dir):
    root = os.path.join(data_dir, "tournaments")
    ids = sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "config.json")))
    if not ids:
        raise SystemExit("There is no tournament in " + root)
    return ids[-1]


def make_snapshot(data_dir, tid="latest", records=150, now=None):
    """The state of a saved tournament as the Live page reads it (the same object the Arena's own export makes), with the moves of the latest `records` games."""
    tid = latest_id(data_dir) if tid in (None, "latest") else tid
    d = os.path.join(data_dir, "tournaments", tid)
    with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    t = Tournament(cfg, d, Bus(), data_dir, ROOT)                                       # (only reads: no engine is started, nothing is written)
    snap = LiveHub(types.SimpleNamespace(tournaments=types.SimpleNamespace(current=t))).snapshot()
    keep = sorted(snap["records"])[-max(0, records):]
    snap["records"] = {gid: snap["records"][gid] for gid in keep}
    jl = os.path.join(d, "games.jsonl")
    age = time.time() - os.path.getmtime(jl) if os.path.exists(jl) else 1e9
    if snap["tournament"]["status"] in ("stopped", "ready", "running", "paused") and age < RUNNING_WINDOW:
        snap["tournament"]["status"] = "running"                                        # (the Arena was closed when it saved 'running'; a games file that still grows means it is)
    elif snap["tournament"]["status"] == "running":
        snap["tournament"]["status"] = "stopped"
    now = now or datetime.now().astimezone()
    mins = int(now.utcoffset().total_seconds() // 60)
    taken = now.strftime("%Y-%m-%d %H:%M") + " (UTC%s%02d:%02d)" % ("+" if mins >= 0 else "-", abs(mins) // 60, abs(mins) % 60)
    snap.update({"snapshot": True, "active": [], "taken": taken})
    return snap


def git(site, *args, check=True):
    r = subprocess.run(["git", "-C", site, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit("git %s failed: %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:300]))
    return r


def ensure_clone(site, url=SITE_URL):
    """A small private working copy of the site (only docs/live and the home page are checked out): the Arena's own repository is not used, so nothing in it is disturbed."""
    if os.path.isdir(os.path.join(site, ".git")):
        return
    os.makedirs(os.path.dirname(os.path.abspath(site)), exist_ok=True)
    r = subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", url, site], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("git clone failed: " + (r.stderr or r.stdout).strip()[:300])
    git(site, "sparse-checkout", "set", "--no-cone", "/docs/live/", "/docs/index.html")
    git(site, "config", "user.name", IDENT[0])
    git(site, "config", "user.email", IDENT[1])
    git(site, "checkout", "-q", "main")


def publish(site, snap, push=True):
    """Writes docs/live/data/current.json in the working copy of the site and pushes it (one commit); returns False when nothing changed."""
    git(site, "pull", "--ff-only", "-q")
    path = os.path.join(site, "docs", "live", "data", "current.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(json.dumps(snap, separators=(",", ":")).encode("utf-8"))
    git(site, "add", "docs/live/data/current.json")
    if git(site, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    m = snap.get("tournament") or {}
    git(site, "commit", "-q", "-m", "Live: %s - %d of %d games (%s)" % (m.get("name") or "tournament", m.get("done", 0), m.get("total", 0), m.get("status", "")))
    if push:
        git(site, "push", "-q", "origin", "HEAD:main")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tournament", default="latest")
    ap.add_argument("--every", type=float, default=0, help="minutes between publications (0 = once)")
    ap.add_argument("--records", type=int, default=150, help="how many of the latest games get their moves published")
    ap.add_argument("--data", default=None)
    ap.add_argument("--site", default=os.path.join(os.path.dirname(ROOT), "kestrel_live_site"))
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    data = data_folder(a.data)
    ensure_clone(a.site)
    last = None
    while True:
        snap = make_snapshot(data, a.tournament, a.records)
        m = snap["tournament"]
        key = (m["id"], m["done"], m["status"])
        if key != last:
            changed = publish(a.site, snap, push=not a.no_push)
            print("%s  %s: %d of %d games, %s -> %s" % (time.strftime("%H:%M:%S"), m["name"], m["done"], m["total"], m["status"], ("published " + PAGE_URL) if changed and not a.no_push else "written, nothing pushed" if changed else "nothing new"), flush=True)
            last = key
        if not a.every:
            break
        if m["status"] != "running" and last == key and m["status"] in ("finished", "stopped"):
            if a.every:
                print("The tournament is not running any more: done.", flush=True)
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
