"""Human-vs-engine games and engine analysis for the Play tab. Chess rules come from python-chess; the browser only draws the board."""
import queue
import threading
import time

import chess
import chess.pgn

from .game import _TERMINATION
from .uci import EngineError, UciEngine, score_to_cp


def legal_info(fen):
    """Legal moves of a position for the board UI."""
    try:
        b = chess.Board(fen)
    except ValueError as e:
        raise ValueError("invalid FEN: %s" % e)
    moves = []
    for m in b.legal_moves:
        moves.append({"uci": m.uci(), "from": chess.square_name(m.from_square), "to": chess.square_name(m.to_square),
                      "promo": chess.piece_symbol(m.promotion) if m.promotion else None, "san": b.san(m)})
    out = b.outcome(claim_draw=True)
    return {"fen": b.fen(), "turn": "w" if b.turn else "b", "moves": moves, "check": chess.square_name(b.king(b.turn)) if b.is_check() else None,
            "over": out is not None, "result": None if out is None else ("1/2-1/2" if out.winner is None else ("1-0" if out.winner else "0-1")),
            "reason": None if out is None else _TERMINATION.get(out.termination, "game over")}


def apply_move(fen, uci):
    """Applies one move to a position (for the analysis board): new FEN, SAN, check square, game-over info."""
    b = chess.Board(fen)
    try:
        m = chess.Move.from_uci(uci)
    except ValueError:
        raise ValueError("bad move")
    if m not in b.legal_moves:
        raise ValueError("illegal move")
    san = b.san(m)
    b.push(m)
    out = b.outcome(claim_draw=True)
    return {"fen": b.fen(), "san": san, "uci": m.uci(), "check": chess.square_name(b.king(b.turn)) if b.is_check() else None,
            "over": out is not None, "result": None if out is None else ("1/2-1/2" if out.winner is None else ("1-0" if out.winner else "0-1")),
            "reason": None if out is None else _TERMINATION.get(out.termination, "game over")}


def _white_pov(cp_or_none, turn_white):
    if cp_or_none is None:
        return None
    return cp_or_none if turn_white else -cp_or_none


def _info_payload(board, inf):
    """Engine info (side-to-move point of view) -> display dict (white point of view, PV in SAN)."""
    cp = score_to_cp(inf)
    mate = inf.get("mate")
    pv_san = ""
    pv = inf.get("pv") or []
    if pv:
        try:
            mv, b = [], board.copy(stack=False)
            for u in pv[:12]:
                m = chess.Move.from_uci(u)
                if m not in b.legal_moves:
                    break
                mv.append(m)
                b.push(m)
            pv_san = board.variation_san(mv) if mv else ""
        except Exception:
            pv_san = ""
    w = board.turn
    return {"multipv": inf.get("multipv", 1), "depth": inf.get("depth"), "seldepth": inf.get("seldepth"), "cp": None if cp is None or mate is not None else _white_pov(cp, w),
            "mate": None if mate is None else (mate if w else -mate), "nodes": inf.get("nodes"), "nps": inf.get("nps"), "time": inf.get("time"), "pv": pv_san,
            "wdl": inf.get("wdl")}


class PlaySession:
    def __init__(self, bus, spec, human_white, fen=None, limits=None, strength=None):
        self.bus = bus
        self.spec = spec
        self.start_fen = fen or None
        self.board = chess.Board(fen) if fen else chess.Board()
        self.human = chess.WHITE if human_white else chess.BLACK
        self.limits = limits or {"movetime": 1000}
        self.uci, self.san = [], []
        self.fens = [self.board.fen()]                          # position before the first move, then after every move
        self.version = 0
        self.closed = False
        self.thinking = False
        self.result = self.reason = None
        self.lock = threading.RLock()
        self.cmdq = queue.Queue()
        self._last_info = 0.0
        self.engine = UciEngine(spec["path"], cwd=spec.get("cwd"), options=spec.get("options"), name=spec["name"], args=spec.get("args"))
        self.engine.start()
        strength = strength or {}
        if strength.get("elo"):
            self.engine.set_option("UCI_LimitStrength", True)
            self.engine.set_option("UCI_Elo", int(strength["elo"]))
        if strength.get("skill") is not None:
            self.engine.set_option("Skill Level", int(strength["skill"]))
        self.engine.new_game()
        self._check_over()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        if not self.result and self.board.turn != self.human:
            self.cmdq.put("think")

    # --------------------------------------------------------------------------------------------------------------- state
    def state(self):
        with self.lock:
            b = self.board
            return {"fen": b.fen(), "fens": list(self.fens), "turn": "w" if b.turn else "b", "uci": list(self.uci), "san": list(self.san), "last": self.uci[-1] if self.uci else None,
                    "check": chess.square_name(b.king(b.turn)) if b.is_check() else None, "over": self.result is not None, "result": self.result, "reason": self.reason,
                    "human": "white" if self.human == chess.WHITE else "black", "thinking": self.thinking, "engine": self.spec["name"], "limits": self.limits,
                    "start_fen": self.start_fen}

    def _emit(self):
        s = self.state()
        s["type"] = "play_state"
        self.bus.publish(s)

    def _check_over(self):
        out = self.board.outcome(claim_draw=True)
        if out is not None:
            self.result = "1/2-1/2" if out.winner is None else ("1-0" if out.winner else "0-1")
            self.reason = _TERMINATION.get(out.termination, "game over")

    def _push(self, mv):
        self.san.append(self.board.san(mv))
        self.board.push(mv)
        self.uci.append(mv.uci())
        self.fens.append(self.board.fen())
        self.version += 1

    # -------------------------------------------------------------------------------------------------------- user actions
    def human_move(self, uci):
        with self.lock:
            if self.closed or self.result is not None:
                raise ValueError("the game is over")
            if self.board.turn != self.human:
                raise ValueError("it is not your turn")
            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                raise ValueError("bad move")
            if mv not in self.board.legal_moves:
                raise ValueError("illegal move")
            self._push(mv)
            self._check_over()
            if self.result is None:
                self.cmdq.put("think")
        self._emit()

    def undo(self):
        with self.lock:
            if not self.uci:
                return
            for _ in range(2):
                if not self.uci:
                    break
                self.board.pop()
                self.uci.pop()
                self.san.pop()
                self.fens.pop()
                if self.board.turn == self.human:
                    break
            self.version += 1
            self.result = self.reason = None
            self._check_over()
            if self.result is None and self.board.turn != self.human:
                self.cmdq.put("think")
        self._emit()

    def resign(self):
        with self.lock:
            if self.result is None:
                self.result = "0-1" if self.human == chess.WHITE else "1-0"
                self.reason = "resignation"
                self.version += 1
        self._emit()

    def set_limits(self, limits):
        with self.lock:
            self.limits = limits

    def pgn(self):
        with self.lock:
            g = chess.pgn.Game.from_board(self.board)
            h = g.headers
            h["Event"] = "Kestrel Arena game"
            h["Site"] = "local"
            h["Date"] = time.strftime("%Y.%m.%d")
            human_name = "You"
            h["White"] = human_name if self.human == chess.WHITE else self.spec["name"]
            h["Black"] = human_name if self.human == chess.BLACK else self.spec["name"]
            h["Result"] = self.result or "*"
            if self.reason:
                h["Termination"] = self.reason
            return str(g)

    # ------------------------------------------------------------------------------------------------------------- engine
    def _loop(self):
        while True:
            cmd = self.cmdq.get()
            if cmd == "quit" or self.closed:
                return
            try:
                self._think()
            except EngineError as e:
                with self.lock:
                    self.thinking = False
                    self.result, self.reason = "*", "engine error: %s" % e
                self._emit()

    def _think(self):
        with self.lock:
            if self.result is not None or self.board.turn == self.human:
                return
            ver = self.version
            moves, fen = list(self.uci), self.start_fen
            board_now = self.board.copy(stack=False)
            self.thinking = True
        self._emit()
        lim = dict(self.limits)
        timeout = lim.get("movetime", 0) / 1000.0 + 5.0 if "movetime" in lim else 180.0

        def on_info(inf):
            now = time.monotonic()
            if now - self._last_info < 0.2 or inf.get("multipv", 1) != 1 or not ("cp" in inf or "mate" in inf):
                return
            self._last_info = now
            p = _info_payload(board_now, inf)
            p["type"] = "play_info"
            self.bus.publish(p)
        self.engine.position(fen, moves)
        res = self.engine.go(lim, timeout=timeout, on_info=on_info, abort=lambda: self.version != ver or self.closed)
        with self.lock:
            self.thinking = False
            if self.version != ver or self.closed or self.result is not None:
                pass
            else:
                try:
                    mv = chess.Move.from_uci(res["bestmove"] or "")
                except ValueError:
                    mv = None
                if mv is None or mv not in self.board.legal_moves:
                    self.result, self.reason = ("0-1" if self.human == chess.WHITE else "1-0"), "the engine made an illegal move"
                else:
                    self._push(mv)
                    self._check_over()
        self._emit()

    def close(self):
        self.closed = True
        self.version += 1
        self.cmdq.put("quit")
        try:
            self.engine.quit()
        except Exception:
            pass


class AnalysisSession:
    """Continuous analysis of whatever position the board shows (go infinite with MultiPV)."""

    def __init__(self, bus, spec):
        self.bus = bus
        self.spec = spec
        self.engine = UciEngine(spec["path"], cwd=spec.get("cwd"), options=spec.get("options"), name=spec["name"], args=spec.get("args"))
        self.engine.start()
        self.job = None
        self.job_version = 0
        self.closed = False
        self.cv = threading.Condition()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def analyse(self, fen, multipv=1):
        b = chess.Board(fen)                                    # validates
        with self.cv:
            self.job = (b.fen(), max(1, min(8, int(multipv))))
            self.job_version += 1
            self.cv.notify_all()

    def pause(self):
        with self.cv:
            self.job = None
            self.job_version += 1
            self.cv.notify_all()

    def _loop(self):
        while not self.closed:
            with self.cv:
                while self.job is None and not self.closed:
                    self.cv.wait(timeout=0.5)
                if self.closed:
                    return
                fen, mpv = self.job
                ver = self.job_version
            board = chess.Board(fen)
            lines = {}
            last = [0.0]

            def on_info(inf, board=board, lines=lines, fen=fen, ver=ver):
                if not ("cp" in inf or "mate" in inf) or inf.get("bound"):
                    return
                lines[inf.get("multipv", 1)] = _info_payload(board, inf)
                now = time.monotonic()
                if now - last[0] >= 0.2:
                    last[0] = now
                    self.bus.publish({"type": "analysis", "fen": fen, "lines": [lines[k] for k in sorted(lines)]})
            try:
                self.engine.set_option("MultiPV", mpv)
                self.engine.position(fen, [])
                if board.outcome(claim_draw=True) is not None:
                    self.bus.publish({"type": "analysis", "fen": fen, "lines": []})
                    with self.cv:
                        while self.job_version == ver and not self.closed:
                            self.cv.wait(timeout=0.5)
                    continue
                self.engine.go({"infinite": True}, timeout=1e9, on_info=on_info, abort=lambda: self.job_version != ver or self.closed)
                if lines:
                    self.bus.publish({"type": "analysis", "fen": fen, "lines": [lines[k] for k in sorted(lines)]})
            except EngineError:
                time.sleep(0.3)

    def close(self):
        self.closed = True
        with self.cv:
            self.cv.notify_all()
        try:
            self.engine.quit()
        except Exception:
            pass
