"""Kestrel Arena - tournament runner, tournament simulator and play/analysis board with the bundled Kestrel engine.

    python main.py                 opens the app window (pywebview / Edge WebView2)
    python main.py --browser       only serves the UI and prints the address (or opens the default browser); dev aid
"""
import argparse
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from arena.api import AppState          # noqa: E402
from arena.server import start_server   # noqa: E402
from arena.uci import full_speed_self   # noqa: E402


def say(*a):
    try:
        print(*a, flush=True)
    except Exception:
        pass                                                 # no console in the windowed exe


def main():
    ap = argparse.ArgumentParser(description="Kestrel Arena")
    ap.add_argument("--browser", action="store_true", help="serve only and open the default browser (no app window)")
    ap.add_argument("--no-open", action="store_true", help="with --browser: do not open the browser, just print the address")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--debug", action="store_true", help="WebView developer tools")
    args = ap.parse_args()

    try:                                                   # the app (it times the engines' moves) runs a little ahead of background programs, like the engines it starts
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00008000)          # ABOVE_NORMAL_PRIORITY_CLASS
    except Exception:
        pass
    full_speed_self()                                      # ... and out of Windows' efficiency throttling (EcoQoS), see arena/uci.py
    st = AppState()
    start_server(st, args.port)
    url = "http://127.0.0.1:%d/" % st.port
    say("Kestrel Arena at", url)
    if args.browser:                                       # dev / test aid only: where to find the server and its token
        try:
            with open(os.path.join(st.data_dir, "dev_session.json"), "w") as f:
                json.dump({"url": url, "port": st.port, "token": st.token, "pid": os.getpid()}, f)
        except OSError:
            pass
        if not args.no_open:
            import webbrowser
            webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        import webview
        window = webview.create_window("Kestrel Arena", url, width=1400, height=900, min_size=(1060, 700), background_color="#f0f0f0")
        st.window = window
        webview.start(private_mode=False, storage_path=os.path.join(st.data_dir, "webview"), debug=args.debug)
    st.shutdown()


if __name__ == "__main__":
    main()
