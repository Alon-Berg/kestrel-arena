"""Writes data/roster_ratings.json (shipped with the app) from the tournament project's latest config/actual_ratings_*.json.
usage: python tools/make_roster_snapshot.py [project_dir]"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proj = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(ROOT)
cfg = os.path.join(proj, "config")
files = sorted(f for f in os.listdir(cfg) if re.match(r"actual_ratings_.*\.json$", f))
if not files:
    sys.exit("no actual_ratings_*.json in " + cfg)
data = json.load(open(os.path.join(cfg, files[-1]), encoding="utf-8"))
rows = [{"name": r[1], "id_name": r[2], "rating": r[3], "ccrl": r[4]} for r in data]
rows.append({"name": "Kestrel 3.2", "id_name": "Kestrel 3.2", "rating": 3581, "ccrl": None})
out = os.path.join(ROOT, "data", "roster_ratings.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=0)
print("wrote %s from %s: %d engines" % (out, files[-1], len(rows)))
