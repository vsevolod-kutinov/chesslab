"""FIDE tournament card.

The `tournament_information.phtml` page is a plain "label -> value" table,
so it is parsed with a regex, no external libraries. We need the name,
city, country, dates and time control; the rest of the page is not used.

Network access runs in a separate thread, following the `lichess.py` pattern.
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

URL = "https://ratings.fide.com/tournament_information.phtml?event={}"
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
TIMEOUT = 20

# table row: <td class=info_table_l>Label</td><td ...>value</td>
_ROW = re.compile(
    r"info_table_l[^>]*>(?P<label>[^<]+)</td>\s*<td[^>]*>(?P<value>.*?)</td>",
    re.S | re.I,
)
_TAGS = re.compile(r"<[^>]*>")

# FIDE federation codes that can realistically occur. An unknown code
# is shown as is: three letters beat an empty field.
COUNTRIES = {
    "ALB": "Albania", "AND": "Andorra", "ARM": "Armenia", "AUT": "Austria",
    "AZE": "Azerbaijan", "BLR": "Belarus", "BEL": "Belgium",
    "BIH": "Bosnia and Herzegovina", "BUL": "Bulgaria", "CRO": "Croatia",
    "CYP": "Cyprus", "CZE": "Czechia", "DEN": "Denmark", "ENG": "England",
    "ESP": "Spain", "EST": "Estonia", "FAI": "Faroe Islands",
    "FIN": "Finland", "FRA": "France", "GEO": "Georgia", "GER": "Germany",
    "GCI": "Guernsey", "GRE": "Greece", "HUN": "Hungary", "IRL": "Ireland",
    "ISL": "Iceland", "ISR": "Israel", "ITA": "Italy", "JCI": "Jersey",
    "KAZ": "Kazakhstan", "KGZ": "Kyrgyzstan", "LAT": "Latvia",
    "LIE": "Liechtenstein", "LTU": "Lithuania", "LUX": "Luxembourg",
    "MDA": "Moldova", "MKD": "North Macedonia", "MLT": "Malta",
    "MNE": "Montenegro", "MNC": "Monaco", "NED": "Netherlands",
    "NOR": "Norway", "POL": "Poland", "POR": "Portugal",
    "ROU": "Romania", "RUS": "Russia", "SCO": "Scotland", "SMR": "San Marino",
    "SRB": "Serbia", "SUI": "Switzerland", "SVK": "Slovakia", "SLO": "Slovenia",
    "SWE": "Sweden", "TUR": "Turkey", "UKR": "Ukraine", "UZB": "Uzbekistan",
    "WLS": "Wales",
    "ARG": "Argentina", "AUS": "Australia", "BRA": "Brazil",
    "CAN": "Canada", "CHI": "Chile", "CHN": "China", "COL": "Colombia",
    "CUB": "Cuba", "EGY": "Egypt", "IND": "India", "INA": "Indonesia",
    "IRI": "Iran", "JPN": "Japan", "KOR": "South Korea",
    "MAR": "Morocco", "MEX": "Mexico", "MGL": "Mongolia", "NZL": "New Zealand",
    "PER": "Peru", "PHI": "Philippines", "RSA": "South Africa", "SGP": "Singapore",
    "THA": "Thailand", "TUN": "Tunisia", "USA": "United States", "VIE": "Vietnam",
}

# FIDE's "Standard" maps to our "classical"
SPEEDS = {"standard": "classical", "rapid": "rapid", "blitz": "blitz"}


class FideError(Exception):
    pass


def event_id(text: str) -> str:
    """Tournament id from a URL or a bare number."""
    text = (text or "").strip()
    if text.isdigit():
        return text
    match = re.search(r"[?&]event=(\d+)", text)
    if match:
        return match.group(1)
    raise FideError("No tournament id (event=...) found in the link.")


def country_name(code: str) -> str:
    return COUNTRIES.get((code or "").upper(), code or "")


def parse_time_control(raw: str) -> tuple[str, str]:
    """«Standard: 60 minutes with 30 seconds increment» → («60+30», «classical»).

    Returns the short form and the speed. If the string cannot be parsed, the speed is empty
    and the short form stays as the original.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", ""

    speed = ""
    for word, key in SPEEDS.items():
        if word in raw.lower():
            speed = key
            break

    minutes = re.search(r"(\d+)\s*min", raw, re.I)
    seconds = re.search(r"(\d+)\s*sec", raw, re.I)
    if minutes and seconds:
        short = f"{minutes.group(1)}+{seconds.group(1)}"
    elif minutes:
        short = minutes.group(1)
    else:
        short = raw
    return short, speed


def _text(value: str) -> str:
    return html.unescape(_TAGS.sub(" ", value)).replace("\xa0", " ").strip()


def parse_page(page: str) -> dict:
    """Page table -> dict of our fields."""
    fields = {_text(match.group("label")).lower(): _text(match.group("value"))
              for match in _ROW.finditer(page)}
    if "tournament name" not in fields:
        raise FideError("No tournament card on the page - check the link.")

    control, speed = parse_time_control(fields.get("time control", ""))
    country = fields.get("country", "")
    return {
        "event": fields.get("event code", ""),
        "name": fields.get("tournament name", ""),
        "city": fields.get("city", ""),
        "country": country,
        "country_name": country_name(country),
        "started": _date(fields.get("start date", "")),
        "finished": _date(fields.get("end date", "")),
        "time_control": control,
        "speed": speed,
        "raw_control": fields.get("time control", ""),
        "players": fields.get("number of players", ""),
    }


def _date(value: str) -> str:
    """FIDE writes an unset date as zeros; skip those."""
    return "" if value.startswith("0000") else value


def fetch_tournament(link: str) -> dict:
    """Download and parse the card. Accepts a link or a tournament id."""
    url = URL.format(urllib.parse.quote(event_id(link)))
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            page = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise FideError(f"FIDE responded {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise FideError(f"Cannot reach ratings.fide.com: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FideError("FIDE did not respond in time.") from exc

    return parse_page(page)


class TournamentLookup(QObject):
    """One-off card request in the background so the window does not freeze."""

    found = Signal(dict)
    failed = Signal(str)

    def start(self, link: str) -> None:
        threading.Thread(
            target=self._run, args=(link,), name="fide-tournament", daemon=True,
        ).start()

    def _run(self, link: str) -> None:
        try:
            self.found.emit(fetch_tournament(link))
        except FideError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")
