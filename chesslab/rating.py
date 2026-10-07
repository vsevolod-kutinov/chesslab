"""Rating growth: a chart from your own games and a breakdown by month.

Computed from the local database, not from Lichess's `/rating-history` endpoint: for
this account it returns an empty array, while the ratings in games are stored here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import games_db, storage, theme
from .games import SPEEDS

PERIODS = [(0, "all time"), (365, "past year"), (180, "past 6 months"),
           (90, "past 3 months"), (30, "past month"), (7, "past week"),
           (1, "past day")]

# buckets the table under the chart is split into
GROUPS = [(games_db.GROUP_DAY, "by day", "Day"),
          (games_db.GROUP_WEEK, "by week", "Week"),
          (games_db.GROUP_MONTH, "by month", "Month"),
          (games_db.GROUP_YEAR, "by year", "Year")]

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

HEADERS = ["Month", "Games", "Rating", "Change", "Score"]

DAY_MS = 86_400_000
GAP_DAYS = 45  # a gap after which the line is broken

# rating grid steps: take the smallest one that gives no more than five lines
STEPS = (10, 25, 50, 100, 200, 500)
FILL = QColor("#2d3a24")


def period_title(key: str, group: str) -> str:
    """Bucket key in human-readable form."""
    try:
        if group == games_db.GROUP_DAY:
            day = datetime.strptime(key, "%Y-%m-%d")
            return f'{day:%Y-%m-%d}, {WEEKDAYS[day.weekday()]}'
        if group == games_db.GROUP_WEEK:
            monday = datetime.strptime(key[1:], "%Y-%m-%d")
            sunday = monday + timedelta(days=6)
            return f'{monday:%m-%d} – {sunday:%Y-%m-%d}'
        if group == games_db.GROUP_YEAR:
            return key
        year, month = key.split("-")
        return f"{MONTHS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return key


class RatingChart(QWidget):
    """Rating curve over time. The X axis is dates, not game numbers."""

    hovered = Signal(object)  # (milliseconds, rating) or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setMouseTracking(True)
        self.points: list[tuple[int, int]] = []
        self._hover: int | None = None

    def set_points(self, points: list[tuple[int, int]]) -> None:
        self.points = points
        self._hover = None
        self.update()

    # --- mouse ------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        index = self._nearest(event.position().x())
        if index != self._hover:
            self._hover = index
            self.hovered.emit(self.points[index] if index is not None else None)
            self.update()

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.hovered.emit(None)
            self.update()

    def _nearest(self, x: float) -> int | None:
        if len(self.points) < 2:
            return None
        plot = self._plot()
        span = self._span()
        if plot.width() <= 0 or span <= 0:
            return None
        target = self.points[0][0] + (x - plot.left()) / plot.width() * span
        return min(range(len(self.points)),
                   key=lambda i: abs(self.points[i][0] - target))

    # --- geometry ---------------------------------------------------

    def _plot(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        return area.adjusted(48, 14, -14, -24)

    def _span(self) -> float:
        return max(1.0, self.points[-1][0] - self.points[0][0])

    def _segments(self) -> list[list[tuple[int, int]]]:
        """Gaps in play are not connected: between 2019 and 2025 there is nothing, and
        a straight line across the whole chart would read as a slow decline."""
        out: list[list[tuple[int, int]]] = [[self.points[0]]]
        for previous, point in zip(self.points, self.points[1:]):
            if point[0] - previous[0] > GAP_DAYS * DAY_MS:
                out.append([])
            out[-1].append(point)
        return out

    def _bounds(self) -> tuple[int, int, int]:
        """Bottom and top grid lines plus the step."""
        ratings = [r for _, r in self.points]
        low, high = min(ratings), max(ratings)
        span = max(1, high - low)
        step = next((s for s in STEPS if span / s <= 5), STEPS[-1])
        return (low // step) * step, -(-high // step) * step, step

    # --- painting ---------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(theme.BORDER, 1))
        painter.setBrush(theme.INSET)
        painter.drawRoundedRect(area, 6, 6)

        if len(self.points) < 2:
            painter.setPen(QPen(theme.TEXT_MUTED))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter,
                             "not enough games for a chart")
            painter.end()
            return

        plot = self._plot()
        low, high, step = self._bounds()
        span = self._span()
        start = self.points[0][0]

        def x(stamp: int) -> float:
            return plot.left() + (stamp - start) / span * plot.width()

        def y(rating: float) -> float:
            return plot.bottom() - (rating - low) / max(1, high - low) * plot.height()

        small = QFont(self.font())
        small.setPointSizeF(8.5)
        painter.setFont(theme.tabular(small))

        value = low
        while value <= high:
            line_y = y(value)
            painter.setPen(QPen(theme.BORDER, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(plot.left(), line_y), QPointF(plot.right(), line_y))
            painter.setPen(QPen(theme.TEXT_FAINT))
            painter.drawText(
                QRectF(area.left() + 6, line_y - 8, 38, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(int(value)),
            )
            value += step

        for segment in self._segments():
            if len(segment) < 2:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.ACCENT)
                painter.drawEllipse(
                    QPointF(x(segment[0][0]), y(segment[0][1])), 2, 2
                )
                continue

            fill = QPainterPath()
            fill.moveTo(x(segment[0][0]), plot.bottom())
            for stamp, rating in segment:
                fill.lineTo(x(stamp), y(rating))
            fill.lineTo(x(segment[-1][0]), plot.bottom())
            fill.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(FILL)
            painter.drawPath(fill)

            curve = QPainterPath()
            curve.moveTo(x(segment[0][0]), y(segment[0][1]))
            for stamp, rating in segment:
                curve.lineTo(x(stamp), y(rating))
            painter.setPen(QPen(theme.ACCENT, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(curve)

        self._paint_dates(painter, plot, x)
        self._paint_hover(painter, plot, x, y)
        painter.end()

    def _paint_dates(self, painter: QPainter, plot: QRectF, x) -> None:
        painter.setPen(QPen(theme.TEXT_FAINT))
        span_days = self._span() / DAY_MS
        pattern = "%m-%d" if span_days < 200 else "%Y-%m"

        marks = 4 if plot.width() > 420 else 2
        for step in range(marks + 1):
            stamp = self.points[0][0] + self._span() * step / marks
            text = datetime.fromtimestamp(stamp / 1000).strftime(pattern)
            box = QRectF(x(stamp) - 34, plot.bottom() + 5, 68, 16)
            align = Qt.AlignmentFlag.AlignCenter
            if step == 0:
                box.moveLeft(plot.left())
                align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif step == marks:
                box.moveRight(plot.right())
                align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(box, align, text)

    def _paint_hover(self, painter: QPainter, plot: QRectF, x, y) -> None:
        if self._hover is None:
            return
        stamp, rating = self.points[self._hover]
        painter.setPen(QPen(theme.BORDER_HI, 1))
        painter.drawLine(QPointF(x(stamp), plot.top()),
                         QPointF(x(stamp), plot.bottom()))
        painter.setPen(QPen(theme.BG, 1.5))
        painter.setBrush(theme.ACCENT)
        painter.drawEllipse(QPointF(x(stamp), y(rating)), 4, 4)


class RatingDialog(QDialog):
    """The "Rating Growth" window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rating Growth")
        self.resize(880, 720)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        self.accounts = storage.load_accounts()
        self.series: list[tuple[int, int]] = []

        self._build_ui()
        self.reload()

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        margin = theme.gap(4)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.gap(3))

        layout.addLayout(self._build_top_row())

        self.headline = QLabel("—")
        self.headline.setObjectName("eval")
        self.headline.setFont(theme.tabular(self.font(), 30, bold=True))
        layout.addWidget(self.headline)

        self.subline = QLabel("")
        self.subline.setWordWrap(True)
        layout.addWidget(self.subline)

        self.chart = RatingChart()
        self.chart.hovered.connect(self._on_hover)
        layout.addWidget(self.chart, 3)

        self.hover_label = QLabel(" ")
        self.hover_label.setObjectName("status")
        self.hover_label.setFont(theme.tabular(self.font()))
        layout.addWidget(self.hover_label)

        self.table_label = QLabel("BY MONTH")
        self.table_label.setObjectName("heading")
        layout.addWidget(self.table_label)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(0).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.table, 2)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _build_top_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.gap(2))

        self.account_box = QComboBox()
        for account in self.accounts:
            self.account_box.addItem(storage.label(account), account["username"])
        self.account_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.account_box, 1)

        self.speed_box = QComboBox()
        for value, label in SPEEDS[1:]:  # "any time control" makes no sense here:
            self.speed_box.addItem(label, value)  # each mode has its own rating
        self.speed_box.setCurrentIndex(1)  # blitz
        self.speed_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.speed_box)

        self.period_box = QComboBox()
        for value, label in PERIODS:
            self.period_box.addItem(label, value)
        # default is a year: "all time" hinges on a single game from 2019,
        # and the whole chart gets squeezed into the right quarter
        self.period_box.setCurrentIndex(1)
        self.period_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.period_box)

        self.group_box = QComboBox()
        for value, label, _ in GROUPS:
            self.group_box.addItem(label, value)
        self.group_box.setCurrentIndex(2)  # by month
        self.group_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.group_box)

        return row

    # --- data -------------------------------------------------------

    @Slot()
    def reload(self) -> None:
        account = self.account_box.currentData()
        if not account:
            self.headline.setText("—")
            self.subline.setText("Add an account: menu “Accounts → Lichess and Chess.com…”")
            self.chart.set_points([])
            self.table.setRowCount(0)
            return

        speed = self.speed_box.currentData()
        full = games_db.rating_series(self.conn, account, speed)

        days = self.period_box.currentData()
        edge = (datetime.now().timestamp() * 1000 - days * DAY_MS) if days else 0
        self.series = [p for p in full if p[0] >= edge] if edge else full

        self.chart.set_points(self.series)

        group = self.group_box.currentData()
        self._fill_table(
            games_db.progress(self.conn, account, speed, group=group, since=edge),
            group,
        )
        self._update_labels(full)

    def _update_labels(self, full: list[tuple[int, int]]) -> None:
        if not full:
            self.headline.setText("—")
            self.subline.setText("No rated games in the database for this mode.")
            self.status.setText("Download games: menu “Library → My games…”.")
            return

        current = full[-1][1]
        self.headline.setText(str(current))

        if len(self.series) >= 2:
            delta = self.series[-1][1] - self.series[0][1]
            grew = f"{'+' if delta > 0 else ''}{delta}"
            period = self.period_box.currentText()
            color = (theme.SCORE_GOOD if delta > 0
                     else theme.SCORE_BAD if delta < 0 else theme.TEXT_MUTED)
            change = (f'<span style="color:{color.name()}">{grew}</span> '
                      f'{period}   ·   ')
        else:
            change = ""

        # max and min are computed over the same sample as the growth, otherwise
        # a two-year-old record would sit next to "+16 past month"
        window = self.series or full
        ratings = [r for _, r in window]
        best, worst = max(ratings), min(ratings)
        best_at = next(s for s, r in window if r == best)
        self.subline.setText(
            f'{change}{len(window)} games in sample   ·   '
            f'best {best} ({datetime.fromtimestamp(best_at / 1000):%Y-%m-%d})'
            f'   ·   worst {worst}'
        )
        self.status.setText("Move the mouse over the chart to see the date and rating.")

    def _fill_table(self, chunks: list[dict], group: str) -> None:
        title = next(g[2] for g in GROUPS if g[0] == group)
        self.table_label.setText(self.group_box.currentText().upper())
        self.table.setHorizontalHeaderLabels([title] + HEADERS[1:])
        self.table.horizontalHeaderItem(0).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        months = list(reversed(chunks))  # newest on top
        self.table.setRowCount(len(months))
        for index, month in enumerate(months):
            cells = [
                period_title(month["period"], group),
                str(month["games"]),
                str(month["rating"] or "—"),
                _delta_text(month["delta"]),
                f'{month["score"]:.0f}%',
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setFont(theme.tabular(self.font()))
                if column == 3 and month["delta"]:
                    item.setForeground(theme.SCORE_GOOD if month["delta"] > 0
                                       else theme.SCORE_BAD)
                self.table.setItem(index, column, item)

    @Slot(object)
    def _on_hover(self, point: tuple[int, int] | None) -> None:
        if point is None:
            self.hover_label.setText(" ")
            return
        stamp, rating = point
        when = datetime.fromtimestamp(stamp / 1000)
        self.hover_label.setText(f"{when:%Y-%m-%d %H:%M}   ·   rating {rating}")

    def closeEvent(self, event) -> None:
        self.conn.close()
        super().closeEvent(event)


def _delta_text(delta: int | None) -> str:
    """A dash — nothing to compare with, zero — the rating held."""
    if delta is None:
        return "—"
    if delta == 0:
        return "0"
    return f"{'+' if delta > 0 else ''}{delta}"
