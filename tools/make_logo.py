"""Draws the Kestrel logo (used for the Kestrel engine and for Kestrel Arena): a white bold sans K on a small chessboard (4 x 4 squares, two dark greys), nothing else.
The K fills exactly the four middle squares.

Writes   assets/logo/kestrel-logo.svg, kestrel-logo-<size>.png (1024 ... 16), kestrel-logo.ico   and   web/assets/logo.svg + web/assets/icon.svg (the app).
The SVG and the PNGs come from the same shapes, so they match.
usage: python tools/make_logo.py [folder for an extra copy of assets/logo]
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
DARK, LIGHT, FG = "#1e1e1e", "#484848", "#ffffff"      # the two board colours and the K

# the light squares of the 4 x 4 board (128 x 128 each; the top left square is dark)
SQUARES = [(c * 128, r * 128) for r in range(4) for c in range(4) if (r + c) % 2 == 1]

def sans_k(x0=128.0, y0=128.0, h=256.0, stem=67.0, arm_w=96.0, arm_r=252.0, apex_x=118.0, leg_r=256.0, leg_w=96.0):
    """One closed outline of a bold sans K (like the K of Verdana / Tahoma Bold, drawn by hand): a stem, an arm that goes up to the right and a leg that branches
    from the arm; horizontal terminals, diagonal strokes as thick as the stem. It fills the square (x0, y0) .. (x0 + 256, y0 + 256), i.e. the four middle board squares."""
    apex_y = h / 2
    slope_arm = (arm_r - apex_x) / apex_y                   # horizontal run per unit of height of the arm's edges
    slope_leg = (leg_r - apex_x) / (h - apex_y)
    a_left, l_left = arm_r - arm_w, leg_r - leg_w
    y_arm = (a_left - stem) / slope_arm                     # the arm's upper edge meets the stem here
    y_leg = h - (l_left - stem) / slope_leg                 # the leg's lower edge meets the stem here
    pts = [(0, 0), (stem, 0), (stem, y_arm), (a_left, 0), (arm_r, 0), (apex_x, apex_y), (leg_r, h), (l_left, h), (stem, y_leg), (stem, h), (0, h)]
    return [(round(x0 + x, 2), round(y0 + y, 2)) for x, y in pts]


K = [sans_k()]


def svg():
    board = " ".join("M %d %d h128 v128 h-128 Z" % s for s in SQUARES)
    k = " ".join("M " + " L ".join("%g %g" % p for p in poly) + " Z" for poly in K)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">\n'
            '  <title>Kestrel</title>\n'
            '  <rect width="512" height="512" fill="%s"/>\n'
            '  <path d="%s" fill="%s"/>\n'
            '  <path d="%s" fill="%s"/>\n'
            '</svg>\n') % (DARK, board, LIGHT, k, FG)


def render(size, ss=8):
    """RGBA picture of the logo, drawn at ss times the size and reduced (anti-aliased)."""
    from PIL import Image, ImageDraw
    S = size * ss
    k = S / 512.0
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    img = Image.new("RGBA", (S, S), hexrgb(DARK))
    d = ImageDraw.Draw(img)
    for x, y in SQUARES:
        d.rectangle([x * k, y * k, (x + 128) * k - 1, (y + 128) * k - 1], fill=hexrgb(LIGHT))
    for poly in K:
        d.polygon([(x * k, y * k) for x, y in poly], fill=hexrgb(FG))
    return img.resize((size, size), Image.BOX)              # exact area averaging (no ringing at the sharp edges)


def main(extra=None):
    out = os.path.join(ROOT, "assets", "logo")
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "web", "assets"), exist_ok=True)
    with open(os.path.join(out, "kestrel-logo.svg"), "w", encoding="utf-8") as f:
        f.write(svg())
    for target in ("logo.svg", "icon.svg"):
        shutil.copy(os.path.join(out, "kestrel-logo.svg"), os.path.join(ROOT, "web", "assets", target))
    sizes = [1024, 512, 256, 128, 64, 48, 32, 24, 16]
    pics = {s: render(s) for s in sizes}
    for s, im in pics.items():
        im.save(os.path.join(out, "kestrel-logo-%d.png" % s))
    pics[256].save(os.path.join(out, "kestrel-logo.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)], append_images=[pics[s] for s in (16, 24, 32, 48, 64, 128)])
    if extra:
        os.makedirs(extra, exist_ok=True)
        for fn in os.listdir(out):
            shutil.copy(os.path.join(out, fn), os.path.join(extra, fn))
    return out


if __name__ == "__main__":
    print("wrote", main(sys.argv[1] if len(sys.argv) > 1 else None))
