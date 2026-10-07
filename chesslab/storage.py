"""ChessLab local data: everything stays with the user and goes nowhere."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _data_dir() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(root) / "ChessLab"
    return Path(
        os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    ) / "chesslab"


DATA_DIR = _data_dir()

ACCOUNTS_FILE = DATA_DIR / "accounts.json"

# service key in accounts.json -> its human-readable name
SERVICE_LABELS = {"lichess": "Lichess", "chess.com": "Chess.com"}


def service_name(account: dict) -> str:
    return SERVICE_LABELS.get(account.get("service", "lichess"), "Lichess")


def label(account: dict) -> str:
    """Username together with the service - lists can contain both."""
    return f'{account["username"]} · {service_name(account)}'


def load_accounts() -> list[dict]:
    if not ACCOUNTS_FILE.is_file():
        return []
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_accounts(accounts: list[dict]) -> None:
    """Write via a temporary file so data is not lost on failure."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    # the file may hold tokens - only the owner should be able to read it
    os.chmod(tmp, 0o600)
    tmp.replace(ACCOUNTS_FILE)


def load_settings() -> dict:
    """Small app settings; computed path: tests override DATA_DIR."""
    try:
        data = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fill_account_box(box, accounts: list[dict]) -> None:
    """Fill the account dropdown with name and site.

    When there is more than one account, the first entry is "All accounts" with
    data None: one's own games are a single set, no need to count them in halves.
    """
    if len(accounts) > 1:
        box.addItem("All accounts", None)
    for account in accounts:
        box.addItem(label(account), account["username"])


def client(account: dict):
    """Client module for the account: Lichess or Chess.com.

    The import is inside the function on purpose: clients pull in the database,
    and the database is this module, so a top-level import would be circular.
    """
    from . import chesscom, lichess

    return chesscom if account.get("service") == "chess.com" else lichess
