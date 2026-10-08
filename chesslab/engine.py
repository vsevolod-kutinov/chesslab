"""Link to Stockfish.

The engine lives in a plain thread, not a QThread: the analysis loop blocks on
reading from the engine, so the Qt event queue would not spin in it anyway.
Only signals stick out — they can be safely emitted from another thread,
and Qt delivers them to the GUI thread through the queue.
"""

from __future__ import annotations

import math
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import chess
import chess.engine
from PySide6.QtCore import QObject, Signal

from . import games_db


def open_engine(path: str) -> chess.engine.SimpleEngine:
    """Start a UCI engine.

    On Windows the engine is a console program: started from a windowed app
    it would pop up its own console window, so ask for none.
    """
    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW
    return chess.engine.SimpleEngine.popen_uci(path, **extra)


@dataclass
class Line:
    """One line from the engine output."""

    rank: int  # multipv number, starting at 1
    score_cp: int | None  # evaluation in centipawns, from White's point of view
    mate_in: int | None  # mate in N moves (sign = which side delivers mate)
    san: list[str] = field(default_factory=list)
    uci: list[str] = field(default_factory=list)

    def text(self, limit: int = 12) -> str:
        moves = self.san[:limit]
        tail = " …" if len(self.san) > limit else ""
        return " ".join(moves) + tail

    @property
    def best_move(self) -> str | None:
        return self.uci[0] if self.uci else None


MAX_RESTARTS = 3  # how many times to try to bring up a crashed engine


class EngineBridge(QObject):
    """Holds the engine process and runs endless analysis of the current position."""

    ready = Signal(str)  # engine name
    failed = Signal(str)  # error text
    updated = Signal(str, int, int, list)  # fen, depth, nps, list[Line]

    def __init__(self, path: str, multipv: int = 3, max_depth: int = 26,
                 threads: int = 2, hash_mb: int = 256) -> None:
        super().__init__()
        self._path = path
        self._multipv = multipv
        self._max_depth = max_depth
        self._threads = threads
        self._hash_mb = hash_mb

        self._engine: chess.engine.SimpleEngine | None = None
        self._target: str | None = None  # FEN that needs to be analysed
        self._last_fen: str | None = None  # last requested position
        self._generation = 0  # grows when settings change — aborts the current analysis
        self._analysis: chess.engine.SimpleAnalysisResult | None = None  # analysis in progress
        self._stopping = False
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)

    # --- called from the GUI thread -------------------------------------

    def start(self) -> None:
        self._thread.start()

    def analyse(self, fen: str) -> None:
        """Switch analysis to a new position. The previous analysis is dropped."""
        with self._cv:
            self._target = fen
            self._last_fen = fen
            self._cv.notify_all()
            self._interrupt()

    def set_multipv(self, n: int) -> None:
        with self._cv:
            self._multipv = n
            self._generation += 1
            self._cv.notify_all()
            self._interrupt()

    def set_max_depth(self, depth: int | None) -> None:
        """None — analyse indefinitely."""
        with self._cv:
            self._max_depth = depth
            self._generation += 1
            self._cv.notify_all()
            self._interrupt()

    def _interrupt(self) -> None:
        """Abort the current analysis without waiting for the next line from the engine.

        Without this the thread learns about a new position only from the next line of
        output. And Stockfish in infinite analysis, once it hits the depth limit,
        goes silent and idles — the panel hung on "…" forever.
        Call with self._cv held.
        """
        if self._analysis is not None:
            try:
                self._analysis.stop()  # thread-safe: goes into the engine loop
            except Exception:
                pass

    def shutdown(self) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
            self._interrupt()
        self._thread.join(timeout=5)
        if self._engine is not None:
            try:
                self._engine.quit()
            except chess.engine.EngineError:
                pass

    # --- engine thread --------------------------------------------------

    def _run(self) -> None:
        """Runs the engine and brings it back up if it dies.

        Previously any engine error killed the thread for good: the panel simply
        went silent and stayed with dashes, without a word about the cause.
        """
        restarts = 0
        while True:
            if not self._boot():
                return
            if self._serve():
                return  # normal exit on shutdown

            self._drop_engine()
            if restarts >= MAX_RESTARTS:
                self.failed.emit(
                    "The engine keeps crashing. Restart ChessLab."
                )
                return
            restarts += 1
            self.failed.emit(f"The engine died, restarting ({restarts})…")
            with self._cv:
                if self._target is None:
                    self._target = self._last_fen  # recompute what was there

    def _boot(self) -> bool:
        try:
            self._engine = open_engine(self._path)
            self._engine.configure({"Threads": self._threads, "Hash": self._hash_mb})
        except (OSError, chess.engine.EngineError) as exc:
            self.failed.emit(f"Could not start the engine: {exc}")
            return False
        self.ready.emit(self._engine.id.get("name", "engine"))
        return True

    def _drop_engine(self) -> None:
        engine, self._engine = self._engine, None
        if engine is None:
            return
        try:
            engine.quit()
        except Exception:
            pass

    def _serve(self) -> bool:
        """Analyses positions until asked to exit.

        True — asked to exit, False — the engine broke.
        """
        while True:
            with self._cv:
                while self._target is None and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return True
                fen, multipv = self._target, self._multipv
                depth, generation = self._max_depth, self._generation
                self._target = None

            try:
                self._analyse_one(fen, multipv, depth, generation)
            except chess.engine.EngineError:
                return False
            finally:
                with self._cv:
                    self._analysis = None

    def _superseded(self, generation: int) -> bool:
        """A new position arrived or settings changed — the current analysis is not needed."""
        with self._cv:
            return (
                self._stopping
                or self._target is not None
                or self._generation != generation
            )

    def _analyse_one(self, fen: str, multipv: int, max_depth: int | None,
                     generation: int) -> None:
        board = chess.Board(fen)
        if board.is_game_over():
            self.updated.emit(fen, 0, 0, [])
            return

        limit = chess.engine.Limit(depth=max_depth)
        lines: dict[int, Line] = {}

        with self._engine.analysis(board, limit, multipv=multipv) as analysis:
            with self._cv:
                self._analysis = analysis
            if self._superseded(generation):  # the request arrived while starting up
                return
            for info in analysis:
                if self._superseded(generation):
                    return

                pv = info.get("pv")
                if not pv or "score" not in info:
                    continue

                rank = info.get("multipv", 1)
                lines[rank] = _to_line(board, rank, info, pv)

                self.updated.emit(
                    fen,
                    info.get("depth", 0),
                    info.get("nps", 0),
                    [lines[k] for k in sorted(lines)],
                )


CP_CAP = 2000  # mate and extreme evaluations are clamped to this, otherwise losses blow up

# thresholds by loss of winning chances, in percentage points
TAG_THRESHOLDS = ((30.0, "blunder"), (20.0, "mistake"), (10.0, "inaccuracy"))


def win_percent(cp: int) -> float:
    """White's winning chances, 0..100. Same formula as Lichess."""
    cp = max(-CP_CAP, min(CP_CAP, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(loss: float) -> float:
    """Accuracy of a single move by loss of winning chances, 0..100."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


class GameAnalyzer(QObject):
    """Runs the engine over a whole game: its own engine copy, its own thread.

    Interactive analysis of the current position keeps working meanwhile —
    it is a separate engine process.
    """

    progress = Signal(int, int)  # done, total
    finished = Signal(list)  # rows for the analysis table
    failed = Signal(str)

    def __init__(self, path: str, threads: int = 2, hash_mb: int = 256) -> None:
        super().__init__()
        self._path = path
        self._threads = threads
        self._hash_mb = hash_mb
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, game_id: str, start_fen: str, moves: list[chess.Move],
              depth: int = 16) -> None:
        thread = threading.Thread(
            target=self._run, args=(game_id, start_fen, moves, depth),
            name="analyzer", daemon=True,
        )
        thread.start()

    def _run(self, game_id: str, start_fen: str, moves: list[chess.Move],
             depth: int) -> None:
        engine = None
        try:
            engine = open_engine(self._path)
            engine.configure({"Threads": self._threads, "Hash": self._hash_mb})

            board = chess.Board(start_fen)
            positions = [board.fen()]
            for move in moves:
                board.push(move)
                positions.append(board.fen())

            total = len(positions)
            evaluations: list[tuple[int, int | None, str | None]] = []

            for index, fen in enumerate(positions):
                if self._cancel.is_set():
                    self.failed.emit("Analysis interrupted.")
                    return
                info = engine.analyse(chess.Board(fen), chess.engine.Limit(depth=depth))
                score = info["score"].white()
                principal = info.get("pv") or []
                evaluations.append((
                    score.score(mate_score=CP_CAP),
                    score.mate(),
                    principal[0].uci() if principal else None,
                ))
                self.progress.emit(index + 1, total)

            self.finished.emit(_build_rows(game_id, start_fen, moves, evaluations))
        except (OSError, chess.engine.EngineError) as exc:
            self.failed.emit(f"Analysis failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected analysis error: {exc!r}")
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except chess.engine.EngineError:
                    pass


def _build_rows(game_id: str, start_fen: str, moves: list[chess.Move],
                evaluations: list[tuple[int, int | None, str | None]]) -> list[dict]:
    """Turns position evaluations into per-ply records with losses and tags."""
    rows = [{
        "game_id": game_id, "ply": 0,
        "cp": evaluations[0][0], "mate": evaluations[0][1],
        "best_uci": evaluations[0][2], "played_uci": None,
        "loss": None, "tag": "",
    }]

    board = chess.Board(start_fen)
    for index, move in enumerate(moves):
        white_moved = board.turn == chess.WHITE
        before_cp, _, best_uci = evaluations[index]
        after_cp, after_mate, _ = evaluations[index + 1]

        before = win_percent(before_cp)
        after = win_percent(after_cp)
        if not white_moved:
            before, after = 100 - before, 100 - after
        loss = max(0.0, before - after)

        tag = ""
        for threshold, name in TAG_THRESHOLDS:
            if loss >= threshold:
                tag = name
                break

        rows.append({
            "game_id": game_id, "ply": index + 1,
            "cp": after_cp, "mate": after_mate,
            "best_uci": best_uci, "played_uci": move.uci(),
            "loss": round(loss, 2), "tag": tag,
        })
        board.push(move)

    return rows


_SAVE_BATCH = 200  # write to the database in batches, so an interrupted analysis is not lost


class OpeningAnalyzer(QObject):
    """Analyses with the engine only the opening part of many games, with a position cache.

    What matters is not individual games but systematic holes in the opening across
    the whole database. Opening positions repeat massively between games, so
    each is computed only once and stored in the positions table: analysing
    a growing database gets cheaper over time, not more expensive.

    Its own engine copy and its own thread — as with GameAnalyzer, and for the same reason:
    QThread is not involved, the Qt event queue does not spin in a blocking
    analysis loop anyway.
    """

    progress = Signal(int, int)  # done, total — counted in positions, not games
    finished = Signal(int)  # how many positions the engine actually computed
    failed = Signal(str)

    def __init__(self, path: str, threads: int = 2, hash_mb: int = 256) -> None:
        super().__init__()
        self._path = path
        self._threads = threads
        self._hash_mb = hash_mb
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, games: list[sqlite3.Row], plies: int = 16,
              depth: int = 16) -> None:
        thread = threading.Thread(
            target=self._run, args=(games, plies, depth),
            name="opening-analyzer", daemon=True,
        )
        thread.start()

    def _run(self, games: list[sqlite3.Row], plies: int, depth: int) -> None:
        # own connection: sqlite3 dislikes an object from another thread being
        # used directly, and this code always lives in a separate thread
        engine = None
        conn = None
        try:
            conn = games_db.connect()
            epds = _collect_opening_epds(games, plies)
            cached = games_db.position_evals(conn, epds, min_depth=depth)
            todo = [epd for epd in epds if epd not in cached]

            total = len(todo)
            self.progress.emit(0, total)
            if not todo:
                self.finished.emit(0)
                return

            engine = open_engine(self._path)
            engine.configure({"Threads": self._threads, "Hash": self._hash_mb})
            limit = chess.engine.Limit(depth=depth)

            batch: list[dict] = []
            done = 0
            for epd in todo:
                if self._cancel.is_set():
                    break

                info = engine.analyse(chess.Board(epd), limit)
                score = info["score"].white()
                principal = info.get("pv") or []
                batch.append({
                    "epd": epd,
                    "depth": depth,
                    "cp": score.score(mate_score=CP_CAP),
                    "mate": score.mate(),
                    "best_uci": principal[0].uci() if principal else None,
                    "checked_at": int(time.time() * 1000),
                })
                done += 1
                self.progress.emit(done, total)

                if len(batch) >= _SAVE_BATCH:
                    games_db.save_positions(conn, batch)
                    batch = []

            if batch:
                games_db.save_positions(conn, batch)

            if self._cancel.is_set():
                self.failed.emit("Analysis interrupted.")
                return
            self.finished.emit(done)
        except (OSError, chess.engine.EngineError) as exc:
            self.failed.emit(f"Analysis failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected analysis error: {exc!r}")
        finally:
            if engine is not None:
                try:
                    engine.quit()
                except chess.engine.EngineError:
                    pass
            if conn is not None:
                conn.close()


def _collect_opening_epds(games: list[sqlite3.Row], plies: int) -> list[str]:
    """EPD of the first `plies` half-moves of each game, including the position before move 1.

    EPD without move counters — transpositions converge to one position, and
    that is exactly why the cache is effective. Returns a list without repeats, in order
    of first appearance; games with broken notation are simply cut off at the
    point of mismatch.
    """
    seen: dict[str, None] = {}
    for row in games:
        board = chess.Board()
        seen.setdefault(board.epd(), None)
        for san in (row["moves"] or "").split()[:plies]:
            try:
                board.push_san(san)
            except ValueError:
                break
            seen.setdefault(board.epd(), None)
    return list(seen)


def _to_line(board: chess.Board, rank: int, info: chess.engine.InfoDict,
             pv: list[chess.Move]) -> Line:
    white_score = info["score"].white()
    san: list[str] = []
    walker = board.copy(stack=False)
    for move in pv:
        if not walker.is_legal(move):
            break
        san.append(walker.san(move))
        walker.push(move)

    return Line(
        rank=rank,
        score_cp=white_score.score(),
        mate_in=white_score.mate(),
        san=san,
        uci=[m.uci() for m in pv],
    )
