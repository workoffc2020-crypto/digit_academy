"""Dashboard: every number comes from the database (reports.dashboard)."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from app.services import catalog, reports
from app.ui.widgets import DataTable, Kpi, OptDate, btn, fill_combo, label, page_header, safe
from app.utils import money


class _Flow(QWidget):
    """Grid that re-flows its children into as many columns as fit."""

    def __init__(self, min_w=210):
        super().__init__()
        self.min_w, self.items, self.cols = min_w, [], 0
        self.g = QGridLayout(self)
        self.g.setContentsMargins(0, 0, 0, 0)
        self.g.setSpacing(10)

    def add(self, w):
        self.items.append(w)

    def resizeEvent(self, e):
        cols = max(1, self.width() // self.min_w)
        if cols != self.cols:
            self.cols = cols
            for i, w in enumerate(self.items):
                self.g.addWidget(w, i // cols, i % cols)
            for c in range(cols):
                self.g.setColumnStretch(c, 1)
        super().resizeEvent(e)


class _Bars(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.v = QVBoxLayout(self)
        self.v.addWidget(label(title, "h2"))
        self.body = QVBoxLayout()
        self.v.addLayout(self.body)

    def set(self, pairs):
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    x = it.layout().takeAt(0)
                    if x.widget():
                        x.widget().deleteLater()
        if not pairs:
            self.body.addWidget(label("No data yet.", "muted"))
            return
        top = max(n for _, n in pairs) or 1
        for name, n in pairs:
            r = QHBoxLayout()
            l = QLabel(name); l.setMinimumWidth(120); l.setMaximumWidth(190)
            p = QProgressBar(); p.setRange(0, top); p.setValue(n); p.setTextVisible(False); p.setFixedHeight(12)
            c = QLabel(str(n)); c.setMinimumWidth(30); c.setAlignment(Qt.AlignmentFlag.AlignRight)
            r.addWidget(l); r.addWidget(p, 1); r.addWidget(c)
            self.body.addLayout(r)


class DashboardPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(12)
        a = app.actor
        quick = []
        if a.can("students.edit"):
            quick.append(btn("➕  New admission", "primary", slot=lambda: self._go("students", "add_student")))
        if a.can("fees.collect"):
            quick.append(btn("💰  Collect fee", "navy", slot=lambda: self._go("fees")))
        if a.can("attendance.mark"):
            quick.append(btn("🗓  Mark attendance", slot=lambda: self._go("attendance")))
        v.addWidget(page_header("Dashboard", "Live summary of your academy", *quick))
        f = QFrame(); f.setObjectName("card")
        fl = QHBoxLayout(f)
        self.session = QComboBox(); self.course = QComboBox()
        self.d1, self.d2 = OptDate(optional=False), OptDate(optional=False)
        self.d1.setDate(self.d1.date().addDays(1 - date.today().day))
        for lab, w in (("Session", self.session), ("Course", self.course), ("From", self.d1), ("To", self.d2)):
            fl.addWidget(QLabel(lab)); fl.addWidget(w)
        fl.addWidget(btn("Apply", "primary", slot=self.refresh))
        fl.addStretch(1)
        v.addWidget(f)
        self.flow = _Flow()
        self.k = {n: Kpi(t, c) for n, t, c in (
            ("active", "Active students", "#0b2545"), ("new", "New admissions", "#13a89e"), ("present", "Present today", "#16a34a"), ("absent", "Absent today", "#dc2626"),
            ("today", "Collected today", "#16a34a"), ("month", "Collected (range)", "#2563eb"), ("out", "Outstanding fees", "#dc2626"), ("adv", "Advance balance", "#d97706"))}
        for key in ("active", "new", "present", "absent", "today", "month", "out", "adv"):
            self.flow.add(self.k[key])
        v.addWidget(self.flow)
        row = _Flow(430)
        self.by_course, self.by_class = _Bars("Students by course"), _Bars("Students by class")
        row.add(self.by_course); row.add(self.by_class)
        v.addWidget(row)
        self.recent_pay = DataTable([("Receipt", lambda r: r["receipt_no"] or "-", "text"), ("Date", "pay_date", "date"), ("Student", "name", "text"), ("Amount", "amount_paise", "money"), ("Mode", "mode", "text")],
                                    page_size=8, empty="No fee transactions yet.")
        self.upcoming = DataTable([("Due", "due_date", "date"), ("Student", "name", "text"), ("Installment", "label", "text"), ("Due amount", "due", "money")], page_size=8, empty="No installments due in the next 14 days.")
        self.adm = DataTable([("Adm. No.", "admission_no", "text"), ("Student", "name", "text"), ("Date", "admission_date", "date")], page_size=8, empty="No admissions yet.")
        self.upcoming.color_fn = lambda r: "#fee2e2" if r.get("overdue") else None
        tabs = _Flow(430)
        for title, t in (("Recent fee transactions", self.recent_pay), ("Upcoming & overdue installments", self.upcoming), ("Recent admissions", self.adm)):
            box = QFrame(); box.setObjectName("card")
            bl = QVBoxLayout(box); bl.addWidget(label(title, "h2")); bl.addWidget(t)
            box.setMinimumHeight(300)
            tabs.add(box)
        self.tabs_flow = tabs
        v.addWidget(tabs)
        v.addStretch(1)

    def _go(self, mod, method=None):
        page = self.app.window.go_to_module(mod)
        if page and method:
            getattr(page, method)()

    def refresh(self):
        db = self.app.db
        cur = (self.session.currentData(), self.course.currentData())
        fill_combo(self.session, [(s["name"], s["id"]) for s in catalog.sessions(db, False)], "All sessions", current=cur[0])
        fill_combo(self.course, [(c["name"], c["id"]) for c in catalog.courses(db)], "All courses", current=cur[1])
        ok, d = safe(self, reports.dashboard, db, self.app.actor, session_id=self.session.currentData(), date_from=self.d1.iso(), date_to=self.d2.iso(), course_id=self.course.currentData())
        if not ok:
            return
        k = self.k
        k["active"].set(d["active_students"])
        k["new"].set(d["new_admissions"], f"{d['range'][0]} → {d['range'][1]}")
        at = d["attendance_today"]
        k["present"].set("-" if at is None else at["present"], "" if at is None else f"{at['marked']} marked")
        k["absent"].set("-" if at is None else at["absent"])
        for key in ("today", "month", "out", "adv"):
            val = d[{"today": "today_collection", "month": "month_collection", "out": "outstanding", "adv": "advance"}[key]]
            k[key].set("—" if val is None else money.fmt(val, "Rs."), "no permission" if val is None else "")
        self.by_course.set(d["by_course"])
        self.by_class.set(d["by_class"])
        self.recent_pay.set_rows(d["recent_payments"] or [])
        self.upcoming.set_rows(d["upcoming"] or [])
        self.adm.set_rows(d["recent_admissions"])
