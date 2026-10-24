"""Shared UI building blocks: safe-call wrapper, data table with search/sort/paging, KPI card, money & date inputs."""
import logging
from datetime import date, datetime

from PySide6.QtCore import QDate, QRegularExpression, Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.security.auth import LoginError
from app.security.permissions import PermissionDenied, ValidationError
from app.services import settings
from app.utils import money

log = logging.getLogger("digit_academy")
STATUS_COLORS = {"Paid": "#16a34a", "Overdue": "#dc2626", "Partial": "#d97706", "Pending": "#64748b", "Active": "#16a34a", "Inactive": "#64748b",
                 "Completed": "#2563eb", "Withdrawn": "#dc2626", "Transferred": "#d97706", "posted": "#16a34a", "reversed": "#dc2626", "reversal": "#d97706"}


# ------------------------------------------------------------------ messages
def info(parent, text, title="Digit Academy"):
    QMessageBox.information(parent, title, text)


def warn(parent, text, title="Please check"):
    QMessageBox.warning(parent, title, text)


def confirm(parent, text, title="Please confirm", yes="Yes", danger=False) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    y = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is y


def safe(parent, fn, *args, **kwargs):
    """Runs fn; shows friendly messages for expected errors; logs unexpected ones. Returns (ok, result)."""
    try:
        return True, fn(*args, **kwargs)
    except (ValidationError, PermissionDenied, LoginError) as ex:
        warn(parent, str(ex))
    except ValueError as ex:
        warn(parent, str(ex))
    except Exception as ex:                                         # noqa: BLE001 - last line of defence for the UI
        log.exception("Unexpected error")
        QMessageBox.critical(parent, "Something went wrong", f"{ex}\n\nNothing was damaged. Details are saved in the log folder.")
    return False, None


# ------------------------------------------------------------------ small inputs
class MoneyEdit(QLineEdit):
    def __init__(self, parent=None, placeholder="0.00"):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,9}(\.\d{0,2})?$")))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def paise(self) -> int:
        return money.to_paise(self.text() or "0")

    def set_paise(self, p: int):
        self.setText("" if not p else money.plain(p))


class OptDate(QDateEdit):
    """Date picker that can be 'not set'."""
    MIN = QDate(1900, 1, 1)

    def __init__(self, parent=None, optional=True, default_today=False):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd-MM-yyyy")
        self.optional = optional
        if optional:
            self.setMinimumDate(self.MIN)
            self.setSpecialValueText("Not set")
            self.setDate(QDate.currentDate() if default_today else self.MIN)
        else:
            self.setDate(QDate.currentDate())

    def iso(self):
        d = self.date()
        if self.optional and d == self.MIN:
            return None
        return d.toString("yyyy-MM-dd")

    def set_iso(self, iso):
        if not iso:
            self.setDate(self.MIN if self.optional else QDate.currentDate())
        else:
            self.setDate(QDate.fromString(str(iso)[:10], "yyyy-MM-dd"))


def fill_combo(combo: QComboBox, items, blank: str | None = None, current=None):
    """items: [(label, data)]"""
    combo.blockSignals(True)
    combo.clear()
    if blank is not None:
        combo.addItem(blank, None)
    for label, data in items:
        combo.addItem(str(label), data)
    if current is not None:
        i = combo.findData(current)
        if i >= 0:
            combo.setCurrentIndex(i)
    combo.blockSignals(False)


def data_of(combo: QComboBox):
    return combo.currentData()


def btn(text, variant=None, tip=None, slot=None) -> QPushButton:
    b = QPushButton(text.replace("&&", "&").replace("&", "&&"))
    if variant:
        b.setProperty("variant", variant)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def label(text, name=None) -> QLabel:
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(True)
    return l


def logo_pixmap(db, size: int) -> QPixmap:
    pm = QPixmap(settings.logo_file(db))
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def page_header(title: str, subtitle: str = "", *buttons) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(label(title, "h1"))
    if subtitle:
        col.addWidget(label(subtitle, "muted"))
    h.addLayout(col, 1)
    for b in buttons:
        h.addWidget(b)
    return w


class Kpi(QFrame):
    def __init__(self, title: str, color: str = "#13a89e", parent=None):
        super().__init__(parent)
        self.setObjectName("kpi")
        self.setMinimumSize(150, 84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"QFrame#kpi {{ border-left: 5px solid {color}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 12, 10)
        v.setSpacing(2)
        self.t = QLabel(title.upper())
        self.t.setObjectName("kpit")
        self.v = QLabel("-")
        self.v.setObjectName("kpiv")
        self.s = QLabel("")
        self.s.setObjectName("muted")
        v.addWidget(self.t)
        v.addWidget(self.v)
        v.addWidget(self.s)

    def set(self, value, sub=""):
        self.v.setText(str(value))
        self.s.setText(sub)


# ------------------------------------------------------------------ data table
class _Item(QTableWidgetItem):
    def __init__(self, text, sort=None):
        super().__init__(text)
        self.sort = text if sort is None else sort


class DataTable(QWidget):
    """columns: [(title, key_or_callable, kind)]  kind: text | money | date | int | pct | status.
    Holds up to a few thousand rows in memory, with search-as-you-type (local), header-click sorting and paging."""
    activated = Signal(object)
    selected = Signal(object)

    def __init__(self, columns, page_size=100, empty="Nothing to show.", search=False, parent=None):
        super().__init__(parent)
        self.columns, self.page_size, self.empty = columns, page_size, empty
        self._all, self._view, self._page = [], [], 0
        self._sort_col, self._sort_desc = None, False
        self.color_fn = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.filter = None
        if search:
            self.filter = QLineEdit()
            self.filter.setPlaceholderText("Type to filter this list...")
            self.filter.setClearButtonEnabled(True)
            self.filter.textChanged.connect(self._apply_filter)
            v.addWidget(self.filter)
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c[0] for c in columns])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        hh.setSectionsClickable(True)
        hh.setMinimumSectionSize(60)
        hh.sectionClicked.connect(self._sort)
        self.table.itemSelectionChanged.connect(lambda: self.selected.emit(self.current()))
        self.table.cellDoubleClicked.connect(lambda r, c: self.activated.emit(self.current()))
        v.addWidget(self.table, 1)
        self.emptyl = QLabel(empty)
        self.emptyl.setObjectName("muted")
        self.emptyl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.emptyl)
        bar = QHBoxLayout()
        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.prev, self.next = btn("◀ Previous"), btn("Next ▶")
        self.pagel = QLabel("")
        self.prev.clicked.connect(lambda: self._goto(self._page - 1))
        self.next.clicked.connect(lambda: self._goto(self._page + 1))
        bar.addWidget(self.count, 1)
        bar.addWidget(self.prev)
        bar.addWidget(self.pagel)
        bar.addWidget(self.next)
        self.bar = QWidget()
        self.bar.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.bar)
        self._autosize = True

    # --- data
    def set_rows(self, rows):
        self._all = [dict(r) if not isinstance(r, dict) else r for r in rows]
        self._page = 0
        self._apply_filter()

    def _text(self, r) -> str:
        return " ".join(str(self._raw(r, c)) for c in self.columns).lower()

    def _raw(self, r, col):
        k = col[1]
        v = k(r) if callable(k) else r.get(k)
        return "" if v is None else v

    def _apply_filter(self):
        t = (self.filter.text().strip().lower() if self.filter else "")
        self._view = [r for r in self._all if not t or t in self._text(r)]
        if self._sort_col is not None:
            self._view.sort(key=lambda r: self._key(self._raw(r, self.columns[self._sort_col])), reverse=self._sort_desc)
        self._page = min(self._page, max((len(self._view) - 1) // self.page_size, 0))
        self._render()

    @staticmethod
    def _key(v):
        return (0, v) if isinstance(v, (int, float)) else (1, str(v).lower())

    def _sort(self, col):
        self._sort_desc = (not self._sort_desc) if self._sort_col == col else False
        self._sort_col = col
        self._apply_filter()

    def _goto(self, p):
        self._page = max(0, min(p, (len(self._view) - 1) // self.page_size))
        self._render()

    def _fmt(self, kind, v):
        if v in ("", None):
            return ""
        if kind == "money":
            return money.fmt(int(v), "")
        if kind == "pct":
            return f"{float(v):.1f}%"
        if kind == "date":
            try:
                return datetime.fromisoformat(str(v)[:19] if len(str(v)) > 10 else str(v)).strftime("%d-%m-%Y")
            except ValueError:
                return str(v)
        return str(v)

    def _render(self):
        n = len(self._view)
        start = self._page * self.page_size
        rows = self._view[start:start + self.page_size]
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            tint = self.color_fn(r) if self.color_fn else None
            for j, col in enumerate(self.columns):
                raw = self._raw(r, col)
                it = _Item(self._fmt(col[2], raw), raw if isinstance(raw, (int, float)) else None)
                if col[2] in ("money", "int", "pct"):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col[2] == "status" and str(raw) in STATUS_COLORS:
                    it.setForeground(QColor(STATUS_COLORS[str(raw)]))
                    f = it.font(); f.setBold(True); it.setFont(f)
                if tint:
                    it.setBackground(QColor(tint))
                it.setData(Qt.ItemDataRole.UserRole, i)
                self.table.setItem(i, j, it)
        self.table.setUpdatesEnabled(True)
        if self._autosize and rows:
            self.table.resizeColumnsToContents()
            for j in range(len(self.columns)):
                self.table.setColumnWidth(j, min(max(self.table.columnWidth(j) + 14, 70), 320))
        pages = max((n - 1) // self.page_size + 1, 1)
        self.emptyl.setVisible(n == 0)
        self.table.setVisible(n > 0)
        self.bar.setVisible(n > 0)
        self.count.setText(f"{n} record(s)" + (f" — showing {start + 1} to {start + len(rows)}" if n > self.page_size else ""))
        self.pagel.setText(f"Page {self._page + 1} of {pages}")
        self.prev.setEnabled(self._page > 0)
        self.next.setEnabled(self._page < pages - 1)
        self.prev.setVisible(pages > 1)
        self.next.setVisible(pages > 1)
        self.pagel.setVisible(pages > 1)

    def current(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        idx = self._page * self.page_size + r
        return self._view[idx] if idx < len(self._view) else None

    def rows(self):
        return list(self._view)
