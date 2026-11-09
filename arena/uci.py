"""Small, robust UCI engine wrapper: one child process, one reader thread, blocking calls with timeouts.

    eng = UciEngine(r"C:\\engines\\kestrel.exe", options={"Hash": 64, "Threads": 1})
    eng.start()
    res = eng.go({"movetime": 100}, timeout=5)        # -> {"bestmove": "e2e4", "elapsed_ms": 101.3, "info": {...}}
    eng.quit()

On Windows every engine is put into a job object that kills it when the app dies, so crashes or a force-closed window
never leave engine processes behind.
"""
import os
import queue
import re
import subprocess
import threading
import time

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class EngineError(Exception):
    pass


class EngineCrashed(EngineError):
    pass


class EngineTimeout(EngineError):
    pass


# ---------------------------------------------------------------------------------------------------------- job object
_job_handle = None
_job_lock = threading.Lock()


def _assign_to_kill_job(proc):
    """Windows only: engines die together with this process (job object with KILL_ON_JOB_CLOSE). Failures are ignored."""
    global _job_handle
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        with _job_lock:
            if _job_handle is None:
                h = k32.CreateJobObjectW(None, None)
                if not h:
                    return
                info = EXT()
                info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                k32.SetInformationJobObject(wintypes.HANDLE(h), 9, ctypes.byref(info), ctypes.sizeof(info))
                _job_handle = h
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job_handle), wintypes.HANDLE(int(proc._handle)))
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------------------ parsing
_INT_KEYS = ("depth", "seldepth", "multipv", "nodes", "nps", "time", "hashfull", "tbhits", "currmovenumber")


def parse_info(line):
    """Parses a UCI 'info' line into a dict: depth, seldepth, multipv, nodes, nps, time, cp | mate, bound, wdl, pv (list of uci moves)."""
    t = line.split()
    if not t or t[0] != "info":
        return None
    d = {}
    i, n = 1, len(t)
    while i < n:
        k = t[i]
        if k in _INT_KEYS and i + 1 < n:
            try:
                d[k] = int(t[i + 1])
            except ValueError:
                pass
            i += 2
        elif k == "score" and i + 2 < n:
            kind = t[i + 1]
            try:
                val = int(t[i + 2])
            except ValueError:
                val = 0
            if kind == "cp":
                d["cp"] = val
                d.pop("mate", None)
            elif kind == "mate":
                d["mate"] = val
                d.pop("cp", None)
            i += 3
            if i < n and t[i] in ("lowerbound", "upperbound"):
                d["bound"] = t[i]
                i += 1
        elif k == "wdl" and i + 3 < n:
            try:
                d["wdl"] = [int(t[i + 1]), int(t[i + 2]), int(t[i + 3])]
            except ValueError:
                pass
            i += 4
        elif k == "pv":
            d["pv"] = t[i + 1:]
            break
        elif k in ("string", "refutation", "currline"):
            break
        else:
            i += 1
    return d


def parse_option(line):
    """'option name Hash type spin default 16 min 1 max 33554432' -> {'name','type','default','min','max','vars'} (or None)."""
    m = re.match(r"\s*option\s+name\s+(.+?)\s+type\s+(\w+)\s*(.*)$", line)
    if not m:
        return None
    name, typ, rest = m.group(1), m.group(2).lower(), m.group(3)
    opt = {"name": name, "type": typ, "default": None, "min": None, "max": None, "vars": []}
    key, buf = None, []

    def flush():
        if key is None:
            return
        v = " ".join(buf)
        if key == "var":
            opt["vars"].append(v)
        elif key in ("min", "max"):
            try:
                opt[key] = int(v)
            except ValueError:
                pass
        else:
            opt["default"] = v
    for tok in rest.split():
        if tok in ("default", "min", "max", "var"):
            flush()
            key, buf = tok, []
        else:
            buf.append(tok)
    flush()
    if typ == "check":
        opt["default"] = str(opt["default"]).lower() == "true"
    elif typ == "spin":
        try:
            opt["default"] = int(opt["default"])
        except (TypeError, ValueError):
            pass
    return opt


def score_to_cp(info):
    """info dict -> centipawns from the mover's point of view (mate scores become +-(30000 - |mate|)); None without a score."""
    if not info:
        return None
    if "mate" in info:
        m = info["mate"]
        return (30000 - abs(m)) * (1 if m > 0 else -1) if m != 0 else -30000
    if "cp" in info:
        return info["cp"]
    return None


# ------------------------------------------------------------------------------------------------------------ engine
def split_args(args):
    """'--flag "a b" -jar x.jar' -> ['--flag', 'a b', '-jar', 'x.jar'] (Windows style, quotes removed)."""
    if not args:
        return []
    import shlex
    out = []
    for t in shlex.split(args, posix=False):
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        out.append(t)
    return out


ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000


class UciEngine:
    def __init__(self, path, cwd=None, options=None, name=None, args=None, priority=None):
        self.path = path
        self.priority = priority                    # "above": start the process with above-normal priority (Windows) so background programs cannot make its moves late
        self.args = args or ""
        self.cwd = cwd or os.path.dirname(os.path.abspath(path))
        self.options = dict(options or {})
        self.name = name or os.path.basename(path)
        self.id_name = None
        self.id_author = None
        self.supported = {}                    # option name -> parsed option dict
        self.proc = None
        self._q = queue.Queue()
        self._reader = None
        self._lock = threading.Lock()
        self.dead = False
        self.signature = None                  # set by the pool (path + options)

    # -- process ---------------------------------------------------------------------------------------------------
    def start(self, timeout=20.0):
        if not os.path.exists(self.path):
            raise EngineError("engine not found: %s" % self.path)
        try:
            self.proc = subprocess.Popen([self.path] + split_args(self.args), cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1,
                                         creationflags=CREATE_NO_WINDOW | (ABOVE_NORMAL_PRIORITY_CLASS if (self.priority == "above" and os.name == "nt") else 0))
        except OSError as e:
            raise EngineError("cannot start %s: %s" % (self.name, e))
        _assign_to_kill_job(self.proc)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.send("uci")
        deadline = time.perf_counter() + timeout
        while True:
            line = self._get(deadline - time.perf_counter())
            if line.startswith("id name "):
                self.id_name = line[8:].strip()
            elif line.startswith("id author "):
                self.id_author = line[10:].strip()
            elif line.startswith("option "):
                o = parse_option(line)
                if o:
                    self.supported[o["name"].lower()] = o
            elif line.strip() == "uciok":
                break
        for k, v in self.options.items():
            self.set_option(k, v)
        self.isready()
        return self

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                self._q.put(line.rstrip("\r\n"))
        except Exception:
            pass
        self._q.put(None)

    def _get(self, timeout):
        if timeout <= 0:
            raise EngineTimeout("%s: timeout" % self.name)
        try:
            line = self._q.get(timeout=timeout)
        except queue.Empty:
            raise EngineTimeout("%s: timeout" % self.name)
        if line is None:
            self.dead = True
            self._q.put(None)
            raise EngineCrashed("%s: engine process ended" % self.name)
        return line

    def send(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            self.dead = True
            raise EngineCrashed("%s: cannot write to the engine" % self.name)

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None and not self.dead

    def isready(self, timeout=15.0):
        self.send("isready")
        deadline = time.perf_counter() + timeout
        while True:
            if self._get(deadline - time.perf_counter()).strip() == "readyok":
                return

    def set_option(self, name, value):
        known = self.supported.get(name.lower())
        if self.supported and not known:
            return False                                          # engines may not support it: skip silently
        if known and known["type"] == "button":
            self.send("setoption name %s" % known["name"])
            return True
        if isinstance(value, bool):
            value = "true" if value else "false"
        self.send("setoption name %s value %s" % (known["name"] if known else name, value))
        return True

    def new_game(self):
        self.send("ucinewgame")
        self.isready()

    def position(self, fen=None, moves=()):
        cmd = "position " + ("startpos" if not fen else "fen " + fen)
        if moves:
            cmd += " moves " + " ".join(moves)
        self.send(cmd)

    def _drain(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    # -- searching -------------------------------------------------------------------------------------------------
    def go(self, limits, timeout=60.0, on_info=None, abort=None):
        """Runs 'go' with the given limits (wtime btime winc binc movestogo movetime nodes depth infinite) until 'bestmove'.
        Returns {'bestmove','ponder','elapsed_ms','info'} where info is the last exact multipv-1 info line with a score.
        Raises EngineTimeout (the search is stopped first) or EngineCrashed. 'abort()' returning True stops the search early."""
        self._drain()
        parts = ["go"]
        for k in ("wtime", "btime", "winc", "binc", "movestogo", "movetime", "nodes", "depth"):
            if limits.get(k) is not None:
                parts += [k, str(int(max(0, limits[k])))]
        if limits.get("infinite"):
            parts.append("infinite")
        t0 = time.perf_counter()
        self.send(" ".join(parts))
        best_info = {}
        stopped = False
        while True:
            elapsed = time.perf_counter() - t0
            if not stopped and ((abort and abort()) or elapsed > timeout):
                self.send("stop")
                stopped = True
                timed_out = elapsed > timeout
                stop_deadline = time.perf_counter() + 2.0
            if stopped and time.perf_counter() > stop_deadline:
                self.dead = True
                raise EngineTimeout("%s: no bestmove after stop" % self.name)
            try:
                line = self._q.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                self.dead = True
                self._q.put(None)
                raise EngineCrashed("%s: engine process ended during search" % self.name)
            if line.startswith("bestmove"):
                t = line.split()
                el = (time.perf_counter() - t0) * 1000.0
                if stopped and timed_out:
                    raise EngineTimeout("%s: search ran over the time limit" % self.name)
                return {"bestmove": t[1] if len(t) > 1 else None, "ponder": t[3] if len(t) > 3 and t[2] == "ponder" else None, "elapsed_ms": el, "info": best_info}
            if line.startswith("info"):
                inf = parse_info(line)
                if not inf:
                    continue
                if on_info:
                    try:
                        on_info(inf)
                    except Exception:
                        pass
                if ("cp" in inf or "mate" in inf) and inf.get("multipv", 1) == 1:
                    if inf.get("bound") and best_info and not best_info.get("bound") and inf.get("depth", 0) <= best_info.get("depth", 0):
                        continue                                              # keep an exact score over a later bound at the same depth
                    if "pv" not in inf and best_info.get("pv"):
                        inf["pv"] = best_info["pv"]
                    best_info = inf

    def stop(self):
        try:
            self.send("stop")
        except EngineCrashed:
            pass

    def quit(self):
        if self.proc is None:
            return
        try:
            self.send("quit")
        except EngineCrashed:
            pass
        try:
            self.proc.wait(timeout=1.5)
        except Exception:
            self.kill()
        self.dead = True

    def kill(self):
        self.dead = True
        p = self.proc
        if p is None:
            return
        try:
            try:
                import psutil
                for c in psutil.Process(p.pid).children(recursive=True):
                    try:
                        c.kill()
                    except Exception:
                        pass
            except Exception:
                pass
            p.kill()
        except Exception:
            pass


def probe_engine(path, cwd=None, timeout=20.0, args=None):
    """Starts an engine, reads its UCI id and options, quits. Returns {'ok','id_name','id_author','options':[...],'error'}."""
    eng = UciEngine(path, cwd=cwd, args=args)
    try:
        eng.start(timeout=timeout)
        return {"ok": True, "id_name": eng.id_name, "id_author": eng.id_author, "options": list(eng.supported.values()), "error": None}
    except EngineError as e:
        return {"ok": False, "id_name": None, "id_author": None, "options": [], "error": str(e)}
    finally:
        eng.quit()
