"""Account management window: Lichess and Chess.com."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import chesscom, lichess, storage, theme

SERVICES = list(storage.SERVICE_LABELS.items())
LABELS = storage.SERVICE_LABELS

HINTS = {
    "lichess": (
        "Public games can be downloaded without a token. A token is only needed for "
        "private data; it is stored in a file with 600 permissions."
    ),
    "chess.com": (
        "Chess.com serves games publicly; no token is needed or requested "
        "for reading. History arrives month by month."
    ),
}


class AccountsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Accounts")
        self.setMinimumSize(460, 430)
        self.setStyleSheet(theme.QSS)

        self.accounts = storage.load_accounts()
        self._lookup = None
        self._pending_username: str | None = None
        self._pending_service: str = "lichess"
        self._refreshing_index: int | None = None

        self._build_ui()
        self._reload_list()

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        caption = QLabel("SAVED ACCOUNTS")
        caption.setObjectName("heading")
        layout.addWidget(caption)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._update_buttons)
        layout.addWidget(self.list, 1)

        services = QHBoxLayout()
        services.setSpacing(14)
        self.service_group = QButtonGroup(self)
        for value, label in SERVICES:
            button = QRadioButton(label)
            button.setProperty("service", value)
            button.setChecked(value == "lichess")
            self.service_group.addButton(button)
            services.addWidget(button)
        services.addStretch(1)
        self.service_group.buttonToggled.connect(self._on_service_changed)
        layout.addLayout(services)

        self.username_edit = QLineEdit()
        self.username_edit.returnPressed.connect(self.add_account)
        layout.addWidget(self.username_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("API token — optional")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.token_edit)

        self.hint = QLabel("")
        self.hint.setObjectName("status")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)

        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_account)
        buttons.addWidget(self.add_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_account)
        buttons.addWidget(self.refresh_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_account)
        buttons.addWidget(self.remove_button)

        buttons.addStretch(1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._on_service_changed()
        self._update_buttons()

    def current_service(self) -> str:
        button = self.service_group.checkedButton()
        return button.property("service") if button else "lichess"

    @Slot()
    def _on_service_changed(self, *_args) -> None:
        """Ask for a token only on Lichess — Chess.com has none for reading."""
        service = self.current_service()
        self.username_edit.setPlaceholderText(f"username on {LABELS[service]}")
        self.token_edit.setVisible(service == "lichess")
        self.hint.setText(HINTS[service])

    def _reload_list(self) -> None:
        current = self.list.currentRow()
        self.list.clear()
        for account in self.accounts:
            client = storage.client(account)
            item = QListWidgetItem(
                f"{storage.service_name(account)} · {client.summary(account)}")
            if account.get("token"):
                item.setToolTip("token saved")
            self.list.addItem(item)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self._update_buttons()

    @Slot()
    def _update_buttons(self) -> None:
        has_selection = self.list.currentRow() >= 0
        busy = self._lookup is not None
        self.add_button.setEnabled(not busy)
        self.refresh_button.setEnabled(has_selection and not busy)
        self.remove_button.setEnabled(has_selection and not busy)

    # --- actions ----------------------------------------------------

    @Slot()
    def add_account(self) -> None:
        username = self.username_edit.text().strip().lstrip("@")
        service = self.current_service()
        if not username:
            self.status.setText("Enter a username.")
            return
        if any(a["username"].lower() == username.lower()
               and a.get("service", "lichess") == service for a in self.accounts):
            self.status.setText(f"“{username}” on {LABELS[service]} is already in the list.")
            return

        self._refreshing_index = None
        self._pending_username = username
        token = self.token_edit.text().strip() if service == "lichess" else ""
        self._start_lookup(service, username, token or None)

    @Slot()
    def refresh_account(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        account = self.accounts[row]
        self._refreshing_index = row
        self._pending_username = account["username"]
        self._start_lookup(account.get("service", "lichess"),
                           account["username"], account.get("token"))

    @Slot()
    def remove_account(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        name = self.accounts[row]["username"]
        confirm = QMessageBox.question(
            self, "ChessLab", f"Remove “{name}” from the list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        del self.accounts[row]
        storage.save_accounts(self.accounts)
        self._reload_list()
        self.status.setText(f"“{name}” removed.")

    # --- network ----------------------------------------------------

    def _start_lookup(self, service: str, username: str, token: str | None) -> None:
        self._pending_service = service
        self.status.setText(f"Asking {LABELS[service]} about “{username}”…")
        module = chesscom if service == "chess.com" else lichess
        self._lookup = module.UserLookup()
        self._lookup.found.connect(self._on_found)
        self._lookup.failed.connect(self._on_failed)
        self._update_buttons()
        self._lookup.start(username, token)

    @Slot(dict)
    def _on_found(self, profile: dict) -> None:
        service = self._pending_service
        token = (
            self.accounts[self._refreshing_index].get("token")
            if self._refreshing_index is not None
            else (self.token_edit.text().strip() or None)
        )
        record = {
            "service": service,
            "username": profile.get("username") or self._pending_username,
            "token": token if service == "lichess" else None,
            "profile": profile,
            "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        if self._refreshing_index is not None:
            self.accounts[self._refreshing_index] = record
            self.status.setText(f"“{record['username']}” updated.")
        else:
            self.accounts.append(record)
            self.username_edit.clear()
            self.token_edit.clear()
            self.status.setText(
                f"“{record['username']}” added. Games are in “My Games”."
            )

        storage.save_accounts(self.accounts)
        self._finish_lookup()
        self._reload_list()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self._finish_lookup()

    def _finish_lookup(self) -> None:
        self._lookup = None
        self._pending_username = None
        self._refreshing_index = None
        self._update_buttons()
