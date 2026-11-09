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


def make_icon(path):
    """A plain dark-grey square with a white chess knight (the black knight symbol of the Windows symbol font; a bold K when that font is missing)."""
    from PIL import Image, ImageDraw, ImageFont
    S = 512
    img = Image.new("RGBA", (S, S), (58, 58, 58, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, S - 1, S - 1], outline=(25, 25, 25, 255), width=10)
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    font, glyph = None, "K"
    for name, ch in (("seguisym.ttf", "\u265E"), ("arialbd.ttf", "K")):
        try:
            font, glyph = ImageFont.truetype(os.path.join(fonts, name), int(S * 0.74)), ch
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), glyph, font=font)
    d.text(((S - (box[2] - box[0])) / 2 - box[0], (S - (box[3] - box[1])) / 2 - box[1]), glyph, font=font, fill=(255, 255, 255, 255))
    img.resize((256, 256), Image.LANCZOS).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


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
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    ico = os.path.join(ROOT, "assets", "icon.ico")
    make_icon(ico)
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
        f.write(open(os.path.join(ROOT, "README.md"), encoding="utf-8").read() if os.path.exists(os.path.join(ROOT, "README.md")) else "Kestrel Arena\n")
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fn in os.walk(OUT) for f in fn)
    print("built %s in %.0fs (%.0f MB incl. engines)" % (OUT, time.time() - t0, size / 1e6))


if __name__ == "__main__":
    main()
