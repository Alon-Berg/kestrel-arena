"""A small pool of started engine processes, so consecutive games with the same engine and options do not pay the start-up cost again."""
import json
import threading

from .uci import UciEngine


def engine_signature(spec):
    return (spec["path"], spec.get("args") or "", spec.get("priority") or "", json.dumps(spec.get("options") or {}, sort_keys=True, default=str))


class EnginePool:
    def __init__(self, max_idle=10):
        self.max_idle = max_idle
        self._idle = []
        self._lock = threading.Lock()
        self._all = set()

    def acquire(self, spec):
        sig = engine_signature(spec)
        with self._lock:
            for i, e in enumerate(self._idle):
                if e.signature == sig and e.alive:
                    self._idle.pop(i)
                    return e
        eng = UciEngine(spec["path"], cwd=spec.get("cwd"), options=spec.get("options"), name=spec.get("name"), args=spec.get("args"), priority=spec.get("priority"))
        eng.signature = sig
        with self._lock:
            self._all.add(eng)
        try:
            eng.start()
        except Exception:
            eng.kill()
            with self._lock:
                self._all.discard(eng)
            raise
        return eng

    def release(self, eng, keep=True):
        if not keep or not eng.alive:
            eng.kill()
            with self._lock:
                self._all.discard(eng)
            return
        drop = []
        with self._lock:
            self._idle.append(eng)
            while len(self._idle) > self.max_idle:
                drop.append(self._idle.pop(0))
        for e in drop:
            with self._lock:
                self._all.discard(e)
            threading.Thread(target=e.quit, daemon=True).start()

    def close_all(self):
        with self._lock:
            engines = list(self._all)
            self._idle.clear()
            self._all.clear()
        for e in engines:
            e.kill()
