"""Reports: pick a report, set filters, view, print / PDF / CSV / Excel."""
import os
from datetime import date

from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)
from PySide6.QtCore import Qt

from app.printing import docs, export, render
from app.services import catalog, reports as rp
from app.services import students as st
from app.ui.students import course_items
from app.ui.widgets import DataTable, OptDate, btn, data_of, fill_combo, info, label, page_header, safe, warn

PARAM_LABELS = {"session_id": "Session", "course_id": "Course / class", "batch_id": "Batch", "category_id": "Category", "status": "Status", "date": "Date",
                "month": "Month", "date_from": "From date", "date_to": "To date", "mode": "Payment mode", "student_id": "Student"}


class ReportsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.ds, self.widgets = app, None, {}
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Reports", "Student, finance and attendance reports from your live records"))
        sp = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(sp, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(230)
        self.tree.setMaximumWidth(330)
        sp.addWidget(self.tree)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(8, 0, 0, 0)
        self.title = label("Choose a report on the left", "h2")
        rv.addWidget(self.title)
        self.box = QFrame(); self.box.setObjectName("card")
        self.form = QFormLayout(self.box)
        rv.addWidget(self.box)
        self.run_b = btn("▶  Show report", "primary", slot=self.run)
        bar = QHBoxLayout()
        self.b_print = btn("🖨  Print / preview", "navy", slot=self.print_)
        self.b_pdf = btn("PDF", slot=self.to_pdf)
        self.b_csv = btn("CSV", slot=lambda: self.save_as("csv"))
        self.b_xlsx = btn("Excel", slot=lambda: self.save_as("xlsx"))
        for b in (self.run_b, self.b_print, self.b_pdf, self.b_csv, self.b_xlsx):
            bar.addWidget(b)
        bar.addStretch(1)
        rv.addLayout(bar)
        self.sub = label("", "muted")
        rv.addWidget(self.sub)
        self.table = DataTable([("", "x", "text")], page_size=200, search=True, empty="Choose a report and press “Show report”.")
        rv.addWidget(self.table, 1)
        sp.addWidget(right)
        sp.setStretchFactor(1, 1)
        exp = app.actor.can("reports.export")
        for b in (self.b_pdf, self.b_csv, self.b_xlsx):
            b.setVisible(exp)
        self.rd = None
        self.tree.currentItemChanged.connect(self._choose)
        self._filled = False

    def refresh(self):
        if self._filled:
            return
        self._filled = True
        groups = {}
        for r in sorted(rp.available(self.app.actor), key=lambda r: (r.group, r.title)):
            g = groups.get(r.group)
            if g is None:
                g = QTreeWidgetItem([r.group])
                f = g.font(0); f.setBold(True); g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[r.group] = g
            it = QTreeWidgetItem([r.title])
            it.setData(0, Qt.ItemDataRole.UserRole, r.key)
            g.addChild(it)
        self.tree.expandAll()
        self.set_buttons(False)

    def set_buttons(self, on):
        for b in (self.b_print, self.b_pdf, self.b_csv, self.b_xlsx):
            b.setEnabled(on)

    def _choose(self, cur, prev=None):
        key = cur.data(0, Qt.ItemDataRole.UserRole) if cur else None
        if not key:
            return
        self.rd = rp.REGISTRY[key]
        self.title.setText(self.rd.title)
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets = {}
        db = self.app.db
        for p in self.rd.params:
            w = self._make(p, db)
            if w is None:
                continue
            self.widgets[p] = w
            self.form.addRow(PARAM_LABELS.get(p, p), w)
        if self.rd.help:
            self.form.addRow(label(self.rd.help, "muted"))
        self.box.setVisible(bool(self.widgets) or bool(self.rd.help))
        self.ds = None
        self.set_buttons(False)
        self.table.set_rows([])
        self.sub.setText("")

    def _make(self, p, db):
        if p == "session_id":
            w = QComboBox(); fill_combo(w, [(s["name"], s["id"]) for s in catalog.sessions(db, False)], "All sessions"); return w
        if p == "course_id":
            w = QComboBox(); fill_combo(w, course_items(db), "All courses / classes"); return w
        if p == "batch_id":
            w = QComboBox(); fill_combo(w, [(f"{b['course']} — {b['name']}", b["id"]) for b in catalog.batches(db)], "All batches"); return w
        if p == "category_id":
            w = QComboBox(); fill_combo(w, [(c["name"], c["id"]) for c in catalog.categories(db)], "All categories"); return w
        if p == "status":
            w = QComboBox(); fill_combo(w, [(s, s) for s in st.STATUSES], "All statuses"); return w
        if p == "mode":
            w = QComboBox(); fill_combo(w, [(m, m) for m in catalog.payment_modes(db, False)], "All modes"); return w
        if p == "date":
            return OptDate(optional=False)
        if p in ("date_from", "date_to"):
            w = OptDate(optional=True)
            if p == "date_from":
                w.set_iso(date.today().replace(day=1).isoformat())
            return w
        if p == "month":
            w = QLineEdit(date.today().strftime("%Y-%m")); w.setPlaceholderText("YYYY-MM"); return w
        if p == "student_id":
            w = QComboBox(); w.setEditable(True); w.setMinimumWidth(320)
            w.lineEdit().setPlaceholderText("Type a name or admission no. and press Enter")
            w.lineEdit().returnPressed.connect(lambda ww=w: self._student_search(ww))
            return w
        return None

    def _student_search(self, w):
        t = w.currentText().strip()
        ok, rows = safe(self, st.search, self.app.db, self.app.actor, t, status=None, limit=30)
        if ok:
            w.clear()
            for r in rows:
                w.addItem(f"{r['name']} — {r['admission_no']}", r["id"])
            w.showPopup()

    def params(self):
        out = {}
        for p, w in self.widgets.items():
            if isinstance(w, OptDate):
                out[p] = w.iso()
            elif isinstance(w, QLineEdit):
                out[p] = w.text().strip() or None
            elif isinstance(w, QComboBox):
                out[p] = w.currentData()
        return out

    def run(self):
        if not self.rd:
            return warn(self, "Choose a report on the left.")
        ok, ds = safe(self, rp.run, self.app.db, self.app.actor, self.rd.key, **self.params())
        if not ok:
            return
        self.ds = ds
        self.title.setText(ds.title)
        self.sub.setText(ds.subtitle)
        self.table.columns = [(n, (lambda r, i=i: r[i]), k) for i, (n, k) in enumerate(ds.columns)]
        self.table.table.setColumnCount(len(ds.columns))
        self.table.table.setHorizontalHeaderLabels([n for n, _ in ds.columns])
        rows = [{i: v for i, v in enumerate(r)} for r in ds.rows]
        self.table.columns = [(n, (lambda r, i=i: r.get(i)), k) for i, (n, k) in enumerate(ds.columns)]
        self.table.set_rows(rows)
        self.table.empty = "No records for these filters."
        self.table.emptyl.setText("No records for these filters.")
        self.set_buttons(True)

    def print_(self):
        if self.ds:
            self.app.preview(self, docs.dataset(self.app.db, self.ds), self.ds.title, save_name=self.ds.title.replace(" ", "_"))

    def _name(self, ext):
        base = "".join(c if c.isalnum() else "_" for c in self.ds.title)[:50]
        return os.path.join(self.app.paths.exports, f"{base}_{date.today():%Y%m%d}.{ext}")

    def to_pdf(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save report as PDF", self._name("pdf"), "PDF (*.pdf)")
        if p:
            ok, _ = safe(self, render.to_pdf, docs.dataset(self.app.db, self.ds), p)
            if ok:
                info(self, f"Saved:\n{p}")

    def save_as(self, kind):
        p, _ = QFileDialog.getSaveFileName(self, f"Save report as {kind.upper()}", self._name(kind), f"{kind.upper()} (*.{kind})")
        if p:
            ok, _ = safe(self, (export.to_csv if kind == "csv" else export.to_xlsx), self.ds, p)
            if ok:
                info(self, f"Saved:\n{p}\n\nSensitive ID numbers are never included in reports.")
