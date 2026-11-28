"""Writes the static Live page for a website (the Kestrel site keeps it in docs/live/): the page, its style and scripts, the logo, and an empty data/current.json (an existing one is kept).
The page reads data/current.json every 30 seconds; tools/live_publish.py puts a tournament there.  Visitors can only watch: the page has no controls, there is no server, and the data file can
only be changed by someone who can push to the repository.
usage: python tools/make_live_site.py <folder, e.g. <clone of the site>/docs/live> [--home ../]"""
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
FILES = ["live.css", "js/live_util.js", "js/board.js", "js/elo.js", "js/live.js", "assets/logo.svg", "assets/icon.svg"]
SITE = "https://alon-berg.github.io/Kestrel-chess-engine/"
LIVE_TITLE = "Kestrel Live - engine tournaments"
LIVE_DESC = ("Watch chess engine tournaments played with the Kestrel Arena: board, evaluation, standings, crosstable, bracket and game replay. "
             "View-only: the owner publishes a tournament here, the page updates in steps.")


def social_meta(title, desc, url):
    """description + Open Graph + Twitter card tags (a link to the page looks right when it is shared; no image: the site has none)."""
    esc = lambda s: s.replace("&", "&amp;").replace('"', "&quot;")
    rows = [("name", "description", desc), ("property", "og:type", "website"), ("property", "og:site_name", "Kestrel chess engine"), ("property", "og:title", title),
            ("property", "og:description", desc), ("property", "og:url", url), ("name", "twitter:card", "summary"), ("name", "twitter:title", title), ("name", "twitter:description", desc)]
    return "".join('<meta %s="%s" content="%s">\n' % (k, n, esc(v)) for k, n, v in rows)


def build(out, home="../", refresh=30):
    for rel in FILES:
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(WEB, rel), "r", encoding="utf-8", newline="") as f:
            text = f.read()
        if rel == "js/live.js":
            text = text.replace("'/assets/logo.svg'", "'assets/logo.svg'")                       # (the page of the Arena names the logo with a root path)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    with open(os.path.join(WEB, "live.html"), "r", encoding="utf-8") as f:
        html = f.read().replace("__ARENA_TOKEN__", "")
    html = re.sub(r'(href|src)="/', r'\1="', html)                                                # root paths -> relative to the folder of the page
    default = {"src": "data/current.json", "refresh": refresh, "home": home}
    html = html.replace("<!--BROADCAST-->", "<script>window.LIVE_DEFAULT=" + json.dumps(default) + ";</script>")
    assert html.count("<title>Kestrel Arena - Live</title>") == 1
    html = html.replace("<title>Kestrel Arena - Live</title>", "<title>" + LIVE_TITLE + "</title>\n" + social_meta(LIVE_TITLE, LIVE_DESC, SITE + "live/").rstrip("\n"))
    assert 'src="/' not in html and 'href="/' not in html
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="") as f:
        f.write(html)
    data = os.path.join(out, "data", "current.json")
    if not os.path.exists(data):
        os.makedirs(os.path.dirname(data), exist_ok=True)
        with open(data, "w", encoding="utf-8") as f:
            f.write('{"t":"state","tournament":null}')
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    home = sys.argv[sys.argv.index("--home") + 1] if "--home" in sys.argv else "../"
    print("written:", build(os.path.abspath(sys.argv[1]), home))
