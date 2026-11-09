"""Where things live: bundled resources (web/, data defaults), the application folder (engines/), and the writable data folder."""
import os
import sys
import tempfile

FROZEN = bool(getattr(sys, "frozen", False))
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """Read-only resources shipped with the app: web/, data/openings.epd, data/roster_ratings.json."""
    return getattr(sys, "_MEIPASS", None) or PACKAGE_ROOT


def app_dir():
    """Folder of the exe (frozen) or of the source tree; contains engines/."""
    return os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else PACKAGE_ROOT


def data_dir():
    """Writable folder for settings and tournaments: $KESTREL_ARENA_DATA if set (tests), else <app>/data when writable, else %APPDATA%/KestrelArena."""
    d = os.environ.get("KESTREL_ARENA_DATA") or os.path.join(app_dir(), "data")
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return d
    except OSError:
        base = os.environ.get("APPDATA") or tempfile.gettempdir()
        d = os.path.join(base, "KestrelArena")
        os.makedirs(d, exist_ok=True)
        return d
