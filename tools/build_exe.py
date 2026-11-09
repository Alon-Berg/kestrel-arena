"""Builds the Windows app: dist/KestrelArena/KestrelArena.exe (PyInstaller, one folder) with the engines/ folder next to it.

usage: python tools/build_exe.py            (about a minute)
The dev data folder (settings, tournaments) is NOT copied: the packaged app starts with a clean data/ folder of its own.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "KestrelArena")


def link_or_copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def main():
    t0 = time.time()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import make_logo                                   # (re)draws the logo files: assets/logo/*, web/assets/logo.svg, web/assets/icon.svg
    make_logo.main()
    ico = os.path.join(ROOT, "assets", "logo", "kestrel-logo.ico")
    # the user's settings and saved tournaments live in dist/KestrelArena/data: keep them across rebuilds
    keep = None
    if os.path.isdir(os.path.join(OUT, "data")):
        keep = os.path.join(tempfile.mkdtemp(prefix="arena_data_keep_"), "data")
        shutil.move(os.path.join(OUT, "data"), keep)
    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)
    sep = ";" if os.name == "nt" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "KestrelArena", "--icon", ico,
           "--add-data", os.path.join(ROOT, "web") + sep + "web",
           "--add-data", os.path.join(ROOT, "data", "openings.epd") + sep + "data",
           "--add-data", os.path.join(ROOT, "data", "roster_ratings.json") + sep + "data",
           "--hidden-import", "webview.platforms.edgechromium", "--hidden-import", "webview.platforms.winforms", "--collect-submodules", "webview",
           "--exclude-module", "tkinter", "--exclude-module", "numpy", "--exclude-module", "PIL", "--exclude-module", "matplotlib",
           "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"), "--specpath", os.path.join(ROOT, "build"), os.path.join(ROOT, "main.py")]
    print(" ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0 or not os.path.exists(os.path.join(OUT, "KestrelArena.exe")):
        sys.exit("PyInstaller failed")
    # engines next to the exe (hard links when possible: no extra disk space)
    src = os.path.join(ROOT, "engines")
    for dp, dn, fn in os.walk(src):
        for f in fn:
            s = os.path.join(dp, f)
            link_or_copy(s, os.path.join(OUT, "engines", os.path.relpath(s, src)))
    if keep:
        shutil.move(keep, os.path.join(OUT, "data"))
        print("kept your data folder (settings, saved tournaments)")
    with open(os.path.join(OUT, "README.txt"), "w", encoding="utf-8") as f:
        text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read() if os.path.exists(os.path.join(ROOT, "README.md")) else "Kestrel Arena\n"
        f.write("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("<")) + "\n")        # no HTML (the logo line) in the plain-text copy
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(OUT) for f in fn)
    print("built %s in %.0fs (%.0f MB incl. engines)" % (OUT, time.time() - t0, size / 1e6))


if __name__ == "__main__":
    main()
