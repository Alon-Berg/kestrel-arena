"""In-process event bus: publishers call bus.publish(dict); every connected browser/webview subscribes through the server-sent-events endpoint."""
import queue
import threading


class Bus:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=20000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, ev):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                try:                                            # a stuck client: drop its backlog and tell it to resynchronise
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait({"type": "resync"})
                except queue.Full:
                    pass
