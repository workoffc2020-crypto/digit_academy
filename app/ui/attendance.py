"""Attendance register: pick date / class / batch, mark, save, print sheets."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from app.printing import docs
from app.services import attendance as att
from app.services import catalog
from app.ui.students import course_items
from app.ui.widgets import OptDate, btn, confirm, data_of, fill_combo, info, label, page_header, safe, warn


class AttendancePage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Attendance", "Mark daily attendance for a class or batch"))
        card = QFrame(); card.setObjectName("card")
        g = QGridLayout(card)
        self.date = OptDate(optional=False)
        self.course, self.batch = QComboBox(), QComboBox()
        self.course.setMinimumWidth(240)
        g.addWidget(QLabel("Date"), 0, 0); g.addWidget(self.date, 0, 1)
        g.addWidget(QLabel("Course / class"), 0, 2); g.addWidget(self.course, 0, 3)
        g.addWidget(QLabel("Batch"), 0, 4); g.addWidget(self.batch, 0, 5)
        g.addWidget(btn("Load students", "primary", slot=self.load), 0, 6)
        g.setColumnStretch(3, 1); g.setColumnStretch(5, 1)
        v.addWidget(card)
        self.info = label("", "muted")
        v.addWidget(self.info)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Adm. No.", "Student", "Status", "Remark"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table, 1)
        b = QHBoxLayout()
        self.b_pres = btn("All present", slot=lambda: self.mark_all("P"))
        self.b_abs = btn("All absent", slot=lambda: self.mark_all("A"))
        self.b_save = btn("💾  Save attendance", "primary", slot=self.save)
        self.b_sheet = btn("🖨  Daily sheet", slot=self.sheet)
        self.b_reg = btn("🖨  Blank monthly register", slot=self.register)
        for x in (self.b_pres, self.b_abs, self.b_save, self.b_sheet, self.b_reg):
            b.addWidget(x)
        b.addStretch(1)
        v.addLayout(b)
        can_mark = app.actor.can("attendance.mark")
        for x in (self.b_pres, self.b_abs, self.b_save):
            x.setVisible(can_mark)
        self.course.currentIndexChanged.connect(self._course)
        self.statuses = []

    def refresh(self):
        db = self.app.db
        self.statuses = [dict(s) for s in catalog.attendance_statuses(db)]
        cur = self.course.currentData()
        fill_combo(self.course, course_items(db), "— choose course / class —", current=cur)
        self._course()

    def _course(self, *_):
        cid = self.course.currentData()
        sid = catalog.current_session_id(self.app.db)
        fill_combo(self.batch, [(f"{b['name']}  {b['timing'] or ''}".strip(), b["id"]) for b in catalog.batches(self.app.db, course_id=cid, session_id=sid)] if cid else [], "No batch (general)")

    def load(self):
        cid = self.course.currentData()
        if not cid:
            return warn(self, "Choose a course / class first.")
        ok, rows = safe(self, att.register, self.app.db, self.app.actor, self.date.iso(), cid, self.batch.currentData())
        if not ok:
            return
        self.rows = rows
        can_edit = self.app.actor.can("attendance.edit")
        can_mark = self.app.actor.can("attendance.mark")
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r["admission_no"]))
            self.table.setItem(i, 1, QTableWidgetItem(r["name"]))
            combo = QComboBox()
            combo.addItem("— not marked —", "")
            for s in self.statuses:
                combo.addItem(f"{s['code']} — {s['label']}", s["code"])
            combo.setCurrentIndex(max(combo.findData(r["status"]), 0))
            combo.setEnabled(can_mark and (not r["saved"] or can_edit))
            if r["saved"] and not can_edit:
                combo.setToolTip("Already saved. Only an authorised person can correct it.")
            combo.currentIndexChanged.connect(lambda _=0, c=combo: self._tint(c))
            self.table.setCellWidget(i, 2, combo)
            self._tint(combo)
            rem = QLineEdit(r["remark"])
            rem.setEnabled(can_mark and (not r["saved"] or can_edit))
            self.table.setCellWidget(i, 3, rem)
        saved = sum(1 for r in rows if r["saved"])
        self.info.setText(f"{len(rows)} student(s) • {saved} already saved for this date." if rows else "No students are enrolled in this class / batch.")

    def _tint(self, combo):
        col = next((s["color"] for s in self.statuses if s["code"] == combo.currentData()), None)
        combo.setStyleSheet(f"QComboBox {{ border: 2px solid {col}; font-weight: 700; }}" if col else "")

    def mark_all(self, code):
        if self.rows and confirm(self, f"Mark ALL {len(self.rows)} students as {'present' if code == 'P' else 'absent'}?"):
            for i in range(self.table.rowCount()):
                c = self.table.cellWidget(i, 2)
                if c.isEnabled():
                    c.setCurrentIndex(c.findData(code))

    def save(self):
        recs = []
        for i, r in enumerate(self.rows):
            c = self.table.cellWidget(i, 2)
            if c.isEnabled():
                recs.append((r["student_id"], c.currentData() or "", self.table.cellWidget(i, 3).text()))
        if not any(x[1] for x in recs):
            return warn(self, "Mark at least one student first.")
        ok, res = safe(self, att.save, self.app.db, self.app.actor, self.date.iso(), self.course.currentData(), self.batch.currentData(), None, recs)
        if ok:
            info(self, f"Attendance saved.\n\nNew: {res['created']}   Corrected: {res['changed']}   Unchanged: {res['unchanged']}")
            self.load()

    def _doc(self, **kw):
        cid = self.course.currentData()
        if not cid:
            warn(self, "Choose a course / class first.")
            return None
        return docs.attendance_sheet(self.app.db, self.app.actor, cid, self.batch.currentData(), self.date.iso(), **kw)

    def sheet(self):
        d = self._doc()
        if d:
            self.app.preview(self, d, "Daily attendance sheet", save_name="attendance_sheet")

    def register(self):
        d = self._doc(blank_month=True, month=self.date.date().toString("MMMM yyyy"))
        if d:
            self.app.preview(self, d, "Monthly attendance register", save_name="attendance_register")
