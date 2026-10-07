"""A tournament from a chess-results.com page, in full.

Unlike `fide.py`, which fetches a single tournament card, this brings in the whole
crosstable: the starting list, all pairings of all rounds and the final standings. This is
needed to compute not only one's own result but also one's place among the others -
tournament upsets, opponents' performance, strength of schedule.

chess-results pages are simple, server-rendered, and open without JS:

    tnr<id>.aspx?lan=1                    tournament card
    ...&art=2&rd=<round>                  round pairings
    ...&art=1&rd=<round>                  standings after the round
    ...&art=9&snr=<start number>          player card

The `art` numbering really is this way: 2 is pairings, 1 is standings, although the
site menu lists them in the opposite order. Verified on tournament 1493854.

Tables are parsed by regex on column headers, not by column numbers:
tournaments without flags and titles have fewer columns, and hard-coded indexes would
break. Network access runs in a separate thread, following the `fide.py` pattern.
"""

from __future__ import annotations

import html
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

from . import __version__

HOST = "chess-results.com"
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
TIMEOUT = 25

_TABLE = re.compile(r'<table[^>]*class="CRs1"[^>]*>(.*?)</table>', re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAGS = re.compile(r"<[^>]*>")
_SCRIPT = re.compile(r"<script.*?</script>", re.S | re.I)
# tournament card: <td>label</td><td>value</td> in a table without a class
_META = re.compile(r"<tr[^>]*>\s*<td[^>]*>([^<]{3,40})</td>\s*<td[^>]*>(.*?)</td>",
                   re.S | re.I)

# points given to each side: for a forfeit the digit is replaced by a plus or minus
_POINTS = {"1": 1.0, "0": 0.0, "½": 0.5, "+": 1.0, "-": 0.0, "0.5": 0.5}


class ResultsError(Exception):
    pass


def parse_link(text: str) -> tuple[str, str]:
    """Link or bare id -> (host, tournament id).

    Keep the host the user gave: tournaments live on numbered mirrors
    (s1, s2, ...), and although the main domain serves them too, there is
    no reason to argue with a working link.
    """
    text = (text or "").strip()
    if text.isdigit():
        return HOST, text

    match = re.search(r"tnr(\d+)\.aspx", text, re.I)
    if not match:
        raise ResultsError("No tournament id (tnr...aspx) found in the link.")
    host = urllib.parse.urlparse(text if "//" in text else f"https://{text}").netloc
    return host or HOST, match.group(1)


def _text(raw: str) -> str:
    return html.unescape(_TAGS.sub(" ", raw)).replace("\xa0", " ").strip()


def _fetch_page(host: str, event: str, extra: str = "") -> str:
    url = f"https://{host}/tnr{event}.aspx?lan=1{extra}"
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ResultsError(f"chess-results responded {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ResultsError(f"Cannot reach {host}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ResultsError("chess-results did not respond in time.") from exc


def _tables(page: str) -> list[list[list[str]]]:
    """All result tables: list of tables -> rows -> cells."""
    page = _SCRIPT.sub("", page)
    out = []
    for body in _TABLE.findall(page):
        rows = []
        for raw_row in _ROW.findall(body):
            cells = [_text(cell) for cell in _CELL.findall(raw_row)]
            if cells:
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


def _columns(header: list[str]) -> dict[str, int]:
    """Header -> {label: column index}. Empty labels are skipped."""
    found: dict[str, int] = {}
    for index, name in enumerate(header):
        key = name.strip().lower().rstrip(".")
        if key and key not in found:
            found[key] = index
    return found


def _pick(row: list[str], columns: dict[str, int], *names: str) -> str:
    for name in names:
        index = columns.get(name)
        if index is not None and index < len(row):
            return row[index]
    return ""


def _number(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() else None


def _score(text: str) -> float | None:
    """"5,5" -> 5.5. The comma is the decimal separator: the site is German."""
    text = text.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


# --- page parsing --------------------------------------------------------

def parse_meta(page: str) -> dict:
    """Tournament card: name, place, time control, number of rounds."""
    fields = {}
    for label, value in _META.findall(_SCRIPT.sub("", page)):
        key = _text(label).lower()
        if key and key not in fields:
            fields[key] = _text(value)

    title = re.search(r"<h2[^>]*>(.*?)</h2>", page, re.S | re.I)
    name = _text(title.group(1)) if title else fields.get("tournament name", "")

    rounds = _number(fields.get("number of rounds", "")) or 0
    average = re.match(r"\s*(\d+)", fields.get("rating-ø / average age", ""))
    control, speed = _control(fields)
    return {
        "name": name,
        "location": fields.get("location", ""),
        "federation": fields.get("federation", ""),
        "date": fields.get("date", "").replace("/", "-"),
        "time_control": control,
        "speed": speed,
        "kind": fields.get("tournament type", ""),
        "arbiter": fields.get("chief arbiter", ""),
        "rounds": rounds,
        "field_average": int(average.group(1)) if average else None,
        "rated": fields.get("rating calculation", ""),
    }


def _control(fields: dict) -> tuple[str, str]:
    """Time control and speed.

    The speed is written in the field LABEL itself - "Time control (Blitz)" - not in the
    value: the value holds a local notation like "3 minúty + 2 sekundy".
    If the label has no parentheses, decide by minutes, as FIDE does.
    """
    for label, value in fields.items():
        if not label.startswith("time control"):
            continue
        match = re.search(r"\((\w+)\)", label)
        if match:
            word = match.group(1).lower()
            if word in ("blitz", "rapid"):
                return value, word
            return value, "classical"

        minutes = re.match(r"\s*(\d+)", value)
        if minutes:
            count = int(minutes.group(1))
            return value, "blitz" if count <= 10 else (
                "rapid" if count < 60 else "classical")
        return value, "classical"
    return "", "classical"


def parse_standings(page: str) -> list[dict]:
    """Final table: who is in which place, with points and Buchholz."""
    players = []
    for rows in _tables(page):
        columns = _columns(rows[0])
        if "snr" not in columns and "sno" not in columns:
            continue
        if "name" not in columns or "pts" not in columns:
            continue
        for row in rows[1:]:
            snr = _number(_pick(row, columns, "sno", "snr"))
            if snr is None:
                continue
            players.append({
                "snr": snr,
                "rank": _number(_pick(row, columns, "rk")),
                "name": _pick(row, columns, "name"),
                "fed": _pick(row, columns, "fed"),
                "rating": _number(_pick(row, columns, "rtg")) or 0,
                "club": _pick(row, columns, "club/city", "club"),
                "points": _score(_pick(row, columns, "pts")) or 0.0,
                "tb1": _score(_pick(row, columns, "tb1")),
                "tb2": _score(_pick(row, columns, "tb2")),
            })
        if players:
            break
    if not players:
        raise ResultsError("No final table on the page - check the link.")
    return players


def parse_pairings(page: str, round_no: int) -> list[dict]:
    """Pairings of one round: who plays whom, with which colour and score."""
    games = []
    for rows in _tables(page):
        columns = _columns(rows[0])
        if "white" not in columns or "black" not in columns:
            continue
        # there are two "No." and "Rtg" columns - for White and for Black; take the outer ones
        numbers = [i for i, name in enumerate(rows[0])
                   if name.strip().lower().rstrip(".") in ("no", "sno", "snr")]
        ratings = [i for i, name in enumerate(rows[0])
                   if name.strip().lower().rstrip(".") == "rtg"]
        if len(numbers) < 2 or len(ratings) < 2:
            continue

        for row in rows[1:]:
            if max(numbers[-1], ratings[-1]) >= len(row):
                continue
            white, black = _number(row[numbers[0]]), _number(row[numbers[-1]])
            if white is None or black is None:
                continue
            games.append({
                "round": round_no,
                "board": _number(_pick(row, columns, "bo")),
                "white": white,
                "black": black,
                "white_rating": _number(row[ratings[0]]) or 0,
                "black_rating": _number(row[ratings[-1]]) or 0,
                "result": _pick(row, columns, "result"),
            })
        if games:
            break
    return games


def parse_card(page: str) -> dict:
    """Player card: the official performance and FIDE ID come from here."""
    fields = {}
    for label, value in _META.findall(_SCRIPT.sub("", page)):
        key = _text(label).lower()
        if key and key not in fields:
            fields[key] = _text(value)
    return {
        "performance": _number(fields.get("performance rating", "")),
        "fide_id": fields.get("fide-id", ""),
        "birth": fields.get("year of birth", ""),
        "rank": _number(fields.get("rank", "")),
    }


def split_result(result: str) -> tuple[float, float] | None:
    """"1 - 0" -> (1, 0), "½ - ½" -> (.5, .5), "- - +" -> (0, 1) on a forfeit."""
    parts = _result_parts(result)
    if parts is None:
        return None
    try:
        return _POINTS[parts[0]], _POINTS[parts[1]]
    except KeyError:
        return None


def is_forfeit(result: str) -> bool:
    """A point without a game: "+" and "-" instead of a digit.

    Such games count neither toward performance nor the upsets list - nothing
    happened at the board. chess-results counts the same way, checked against
    player cards.
    """
    parts = _result_parts(result)
    return bool(parts) and any(part in ("+", "-") for part in parts)


def _result_parts(result: str) -> list[str] | None:
    parts = [part.strip() for part in re.split(r"\s-\s", (result or "").strip())]
    return parts if len(parts) == 2 else None


# --- full download -------------------------------------------------------

def fetch(link: str, progress=None) -> dict:
    """Download the whole tournament: card, players, all games of all rounds."""
    host, event = parse_link(link)

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    say("Opening tournament...")
    meta = parse_meta(_fetch_page(host, event))
    rounds = meta["rounds"]
    if not rounds:
        raise ResultsError("The page does not say how many rounds there were.")

    say("Reading final table...")
    players = parse_standings(
        _fetch_page(host, event, f"&art=1&rd={rounds}&turdet=YES&flag=30"))

    games = []
    for round_no in range(1, rounds + 1):
        say(f"Round {round_no} of {rounds}...")
        games.extend(parse_pairings(
            _fetch_page(host, event, f"&art=2&rd={round_no}&turdet=YES&flag=30"),
            round_no))
    if not games:
        raise ResultsError("Round pairings cannot be read - the tournament may still be in progress.")

    return {"host": host, "event": event, "meta": meta,
            "players": players, "games": games}


def fetch_card(host: str, event: str, snr: int) -> dict:
    page = _fetch_page(host, event, f"&art=9&fed=NONE&turdet=YES&flag=30&snr={snr}")
    return parse_card(page)


class TournamentLookup(QObject):
    """Background tournament download: many pages, the window must not wait."""

    found = Signal(dict)
    failed = Signal(str)
    progress = Signal(str)

    def start(self, link: str) -> None:
        threading.Thread(
            target=self._run, args=(link,),
            name="chess-results", daemon=True,
        ).start()

    def _run(self, link: str) -> None:
        try:
            data = fetch(link, progress=self.progress.emit)
        except ResultsError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")
        else:
            self.found.emit(data)
