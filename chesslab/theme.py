"""Palette, fonts and styles. All visual styling lives here.

Neutral tones are slightly warm rather than grey: the board is green and cream,
and a cold grey next to it looked alien and dull. Surface steps are spaced
clearly - in a dark theme depth is only readable through lightness.
"""

from pathlib import Path

from PySide6.QtGui import QColor, QFont

ASSETS = Path(__file__).parent / "assets"

# --- surfaces, from deep to raised --------------------------------------
BG = QColor("#141310")        # page
INSET = QColor("#1b1a16")     # "sunken" things: lists, input fields
PANEL = QColor("#232119")     # side panel, menus
PANEL_HI = QColor("#302d24")  # raised: buttons, hover
BORDER = QColor("#3a3730")
BORDER_HI = QColor("#514c40")

TEXT = QColor("#ece9e2")      # not pure white - it hurts the eyes on dark
TEXT_MUTED = QColor("#9b9689")
TEXT_FAINT = QColor("#6b675d")

ACCENT = QColor("#8fc16d")        # brighter than the board, so it reads as "alive"
ACCENT_DIM = QColor("#3a4a2c")
SELECTION = QColor("#3b4a2d")

SCORE_GOOD = QColor("#9ccd76")
SCORE_BAD = QColor("#e08b6a")

# site tags in the games list: colour tells the source faster than a caption
SERVICE_COLORS = {
    "lichess": QColor("#c9c3b4"),
    "chess.com": QColor("#8fc16d"),
    "otb": QColor("#d4a95f"),   # over-the-board game entered by hand
}

# --- board ---------------------------------------------------------------
SQ_LIGHT = QColor("#eeeed2")
SQ_DARK = QColor("#769656")
COORD_ON_LIGHT = QColor("#769656")
COORD_ON_DARK = QColor("#eeeed2")
BOARD_EDGE = QColor("#4a4436")

LAST_MOVE = QColor(247, 236, 116, 110)
SELECTED = QColor(247, 236, 116, 170)
LEGAL_DOT = QColor(20, 20, 20, 60)
LEGAL_RING = QColor(20, 20, 20, 55)
CHECK = QColor(214, 76, 62, 190)
ARROW = QColor(47, 158, 158, 200)

# user marks on the board: right button draws, a modifier changes the colour.
# There is deliberately no green as on Lichess - the board itself is green and
# an arrow would vanish on the dark squares. Amber is visible on cream and dark alike.
MARK_COLORS = {
    "default": QColor(232, 150, 45, 205),
    "shift": QColor(214, 76, 62, 205),
    "ctrl": QColor(74, 144, 194, 205),
    "alt": QColor(152, 112, 202, 205),
}

EVAL_WHITE = QColor("#f4f2ec")
# darker and colder than the page background: otherwise the black part of the bar
# merges with the window and the bar reads as empty space
EVAL_BLACK = QColor("#3d3a33")
EVAL_MID = QColor(140, 134, 120, 200)  # "equal" tick in the middle

# --- sizes ---------------------------------------------------------------
# one grid step; every distance in the UI is a multiple of it
SPACE = 4


def gap(steps: int) -> int:
    return SPACE * steps


def tabular(font: QFont, size: float | None = None, bold: bool = False) -> QFont:
    """Monospaced digits. Needed wherever numbers update or stack in a column:
    otherwise the evaluation jumps in width on every engine move."""
    out = QFont(font)
    out.setFeature(QFont.Tag("tnum"), 1)
    if size is not None:
        out.setPointSizeF(size)
    if bold:
        out.setWeight(QFont.Weight.DemiBold)
    return out


_QSS_TEMPLATE = """
QWidget {
    background: #141310;
    color: #ece9e2;
    font-size: 13px;
}

/* --- containers ------------------------------------------------------- */
QFrame#panel {
    background: #232119;
    border: 1px solid #3a3730;
    border-radius: 10px;
}

/* The QWidget rule above paints everything with the page background, including
   labels inside a panel - which left dark stripes behind them. A transparent
   background returns them to the parent's colour. */
QLabel, QCheckBox { background: transparent; }

/* --- text roles ------------------------------------------------------- */
QLabel#heading {
    color: #9b9689;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.09em;
}
QLabel#player {
    font-size: 15px;
    padding: 4px 0;
}
QLabel#status {
    color: #9b9689;
    font-size: 12px;
}
QLabel#eval {
    font-size: 26px;
    font-weight: 600;
    color: #ece9e2;
}

/* --- buttons ---------------------------------------------------------- */
QPushButton {
    background: #302d24;
    border: 1px solid #3a3730;
    border-radius: 6px;
    padding: 8px 12px;
}
QPushButton#compact { padding: 8px 8px; }
QPushButton:hover   { background: #3b382d; border-color: #514c40; }
QPushButton:pressed { background: #262319; }
QPushButton:focus   { border-color: #8fc16d; }
QPushButton:disabled { color: #6b675d; background: #26241d; }

QPushButton:checked {
    background: #3b4a2d;
    border-color: #8fc16d;
}
QPushButton:checked:hover { background: #465935; }

QPushButton#primary {
    background: #3a4a2c;
    border-color: #4d6139;
    color: #ece9e2;
    font-weight: 600;
}
QPushButton#primary:hover   { background: #465935; }
QPushButton#primary:pressed { background: #303d24; }

/* --- menus ------------------------------------------------------------ */
QMenuBar {
    background: #1b1a16;
    border-bottom: 1px solid #3a3730;
    padding: 2px 4px;
}
QMenuBar::item {
    padding: 6px 12px;
    border-radius: 5px;
    background: transparent;
}
QMenuBar::item:selected { background: #302d24; }
QMenu {
    background: #232119;
    border: 1px solid #3a3730;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 7px 28px 7px 20px;
    border-radius: 5px;
}
QMenu::item:selected { background: #3b4a2d; }
QMenu::item:disabled { color: #6b675d; }
QMenu::separator {
    height: 1px;
    background: #3a3730;
    margin: 4px 8px;
}

/* --- tabs --------------------------------------------------------------- */
QTabWidget::pane {
    border: 1px solid #3a3730;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 2px;
    color: #9b9689;
}
QTabBar::tab:hover { background: #262319; color: #ece9e2; }
QTabBar::tab:selected { background: #232119; color: #ece9e2; }

/* --- fields ----------------------------------------------------------- */
QLineEdit, QPlainTextEdit {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #3b4a2d;
}
QLineEdit:hover, QPlainTextEdit:hover { border-color: #514c40; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #8fc16d; }

QComboBox {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-radius: 6px;
    padding: 6px 8px;
}
QComboBox:hover { border-color: #514c40; }
QComboBox:focus { border-color: #8fc16d; }
QComboBox::drop-down { border: none; width: 20px; }
/* Qt cannot draw a triangle with borders like CSS on the web,
   so the check mark is a separate SVG in assets/ */
QComboBox::down-arrow {
    image: url(@CARET@);
    width: 10px;
    height: 6px;
    margin-right: 7px;
}
QComboBox QAbstractItemView {
    background: #232119;
    border: 1px solid #3a3730;
    selection-background-color: #3b4a2d;
    outline: none;
}

QSpinBox {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-radius: 6px;
    padding: 6px 8px;
}
QSpinBox:hover { border-color: #514c40; }
QSpinBox:focus { border-color: #8fc16d; }
QSpinBox::up-button, QSpinBox::down-button {
    background: #302d24;
    border-left: 1px solid #3a3730;
    width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #3b382d; }
/* once buttons get a style, Qt stops drawing their native
   arrows and leaves empty rectangles. So the arrows are custom too */
QSpinBox::up-arrow   { image: url(@CARET_UP@); width: 10px; height: 6px; }
QSpinBox::down-arrow { image: url(@CARET@);    width: 10px; height: 6px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #514c40;
    border-radius: 4px;
    background: #1b1a16;
}
QCheckBox::indicator:hover   { border-color: #8fc16d; }
QCheckBox::indicator:checked {
    background: #8fc16d;
    border-color: #8fc16d;
}

/* same thing as a circle: a dot cannot be drawn inside QSS, so
   the selected option is just a filled disc */
QRadioButton { spacing: 8px; outline: none; }
QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #514c40;
    border-radius: 8px;
    background: #1b1a16;
}
QRadioButton::indicator:hover   { border-color: #8fc16d; }
QRadioButton::indicator:checked {
    background: #8fc16d;
    border-color: #8fc16d;
}

/* --- lists and tables -------------------------------------------------- */
QListWidget, QTableWidget, QTextBrowser {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-radius: 6px;
}
QListWidget:focus, QTableWidget:focus, QTextBrowser:focus {
    border-color: #8fc16d;
}
QListWidget::item { padding: 5px 8px; }
QListWidget::item:hover    { background: #262319; }
QListWidget::item:selected { background: #3b4a2d; color: #ffffff; }

QTableWidget { gridline-color: #262319; }
QTableWidget::item { padding: 6px 8px; }
QTableWidget::item:hover    { background: #262319; }
QTableWidget::item:selected { background: #3b4a2d; color: #ffffff; }

QHeaderView::section {
    background: #232119;
    color: #9b9689;
    border: none;
    border-bottom: 1px solid #3a3730;
    padding: 7px 8px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
}

QTableWidget#lines::item {
    padding: 7px 8px;
    border-bottom: 1px solid #232119;
}
QTableWidget#lines::item:selected { background: #3b4a2d; color: #ffffff; }

QTextBrowser#moves {
    padding: 10px 12px;
    font-size: 14px;
}

/* --- progress ----------------------------------------------------------- */
QProgressBar {
    background: #1b1a16;
    border: 1px solid #3a3730;
    border-radius: 6px;
    text-align: center;
    color: #ece9e2;
}
QProgressBar::chunk {
    background: #8fc16d;
    border-radius: 5px;
}

/* --- scroll bars ------------------------------------------------------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #3a3730;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #514c40; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip {
    background: #302d24;
    color: #ece9e2;
    border: 1px solid #514c40;
    border-radius: 5px;
    padding: 5px 7px;
}
"""

# Qt styles need a real file path; they do not understand url() with data:
QSS = (_QSS_TEMPLATE
       .replace("@CARET_UP@", (ASSETS / "caret-up.svg").as_posix())
       .replace("@CARET@", (ASSETS / "caret.svg").as_posix()))
