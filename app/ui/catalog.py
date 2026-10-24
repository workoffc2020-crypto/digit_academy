"""Courses, classes, batches, teachers, subjects, sessions, payment modes, installment plans, attendance statuses - all editable."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from app.services import catalog as cat
from app.ui.fees import SimpleMoneyDialog as FormDialog
from app.ui.widgets import DataTable, MoneyEdit, OptDate, btn, confirm, fill_combo, info, label, page_header, safe, warn


class AdminList(QWidget):
    def __init__(self, app, columns, loader, *, add=None, edit=None, toggle=None, extras=(), hint=""):
        super().__init__()
        self.app, self.loader, self.add_fn, self.edit_fn, self.toggle_fn = app, loader, add, edit, toggle
        v = QVBoxLayout(self)
        if hint:
            v.addWidget(label(hint, "muted"))
        self.table = DataTable(columns, page_size=200, search=True, empty="Nothing added yet.")
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        if add:
            h.addWidget(btn("➕  Add", "primary", slot=lambda: self._do(add, None)))
        if edit:
            h.addWidget(btn("✏  Edit", slot=lambda: self._row(edit)))
            self.table.activated.connect(lambda r: self._row(edit))
        if toggle:
            self.tb = btn("Enable / disable", slot=lambda: self._row(toggle))
            h.addWidget(self.tb)
        for text, fn in extras:
            h.addWidget(btn(text, slot=lambda f=fn: self._row(f)))
        h.addStretch(1)
        v.addLayout(h)

    def _row(self, fn):
        r = self.table.current()
        if r is None:
            return warn(self, "Select a row first.")
        self._do(fn, r)

    def _do(self, fn, row):
        ok, _ = safe(self, fn, self, row)
        self.reload()

    def reload(self):
        ok, rows = safe(self, self.loader)
        if ok:
            self.table.set_rows([dict(r) for r in rows])


def _simple(parent, title, label_text, value=""):
    e = QLineEdit(value)
    d = FormDialog(parent, title, [("v", label_text, e)])
    return e.text() if d.exec() else None


class CatalogPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.db = app.db
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Courses & Batches", "Everything here is editable - no programming needed"))
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        T = lambda a, b, c="text": (a, b, c)
        a = app.actor
        self.lists = []

        def add(title, widget):
            self.tabs.addTab(widget, title.replace("&", "&&"))
            self.lists.append(widget)

        add("Courses & classes", AdminList(app, [T("Category", "category"), T("Name", "name"), T("Type", "kind"), T("Months", "duration_months", "int"), T("Course fee", "fee_paise", "money"),
                                                  T("Admission fee", "admission_fee_paise", "money"), T("Registration fee", "registration_fee_paise", "money"), T("Installments", "default_installments", "int"),
                                                  T("Active", lambda r: "Yes" if r["active"] else "No")],
                                          lambda: cat.courses(self.db, active_only=False), add=self.course_dialog, edit=self.course_dialog, toggle=self.toggle_course,
                                          hint="Set the real fees here. New admissions pick them up automatically (you can still change them per student)."))
        add("Batches", AdminList(app, [T("Course", "course"), T("Batch", "name"), T("Session", "session"), T("Timing", "timing"), T("Teacher", lambda r: r["teacher"] or "-"),
                                       T("Active", lambda r: "Yes" if r["active"] else "No")],
                                 lambda: cat.batches(self.db, active_only=False), add=self.batch_dialog, edit=self.batch_dialog,
                                 toggle=lambda p, r: cat.save_batch(self.db, a, id=r["id"], course_id=r["course_id"], session_id=r["session_id"], name=r["name"], timing=r["timing"],
                                                                    teacher_id=r["teacher_id"], active=not r["active"]),
                                 extras=[("🕘 Timetable", self.timetable_dialog)]))
        add("Teachers", AdminList(app, [T("Name", "name"), T("Phone", "phone"), T("Email", "email"), T("Active", lambda r: "Yes" if r["active"] else "No")],
                                  lambda: cat.teachers(self.db, False), add=self.teacher_dialog, edit=self.teacher_dialog,
                                  toggle=lambda p, r: cat.save_teacher(self.db, a, id=r["id"], name=r["name"], phone=r["phone"], email=r["email"], active=not r["active"])))
        add("Subjects", AdminList(app, [T("Subject", "name"), T("Active", lambda r: "Yes" if r["active"] else "No")], lambda: cat.subjects(self.db, False),
                                  add=lambda p, r: cat.add_subject(self.db, a, _simple(p, "New subject", "Subject name")) if True else None,
                                  toggle=lambda p, r: cat.set_subject_active(self.db, a, r["id"], not r["active"])))
        add("Sessions", AdminList(app, [T("Session", "name"), T("From", "start_date", "date"), T("To", "end_date", "date"), T("Current", lambda r: "✔" if r["id"] == cat.current_session_id(self.db) else ""),
                                        T("Active", lambda r: "Yes" if r["active"] else "No")],
                                  lambda: cat.sessions(self.db, False), add=self.session_dialog,
                                  toggle=lambda p, r: cat.set_session_active(self.db, a, r["id"], not r["active"]),
                                  extras=[("Make current session", lambda p, r: cat.set_current_session(self.db, a, r["id"]))]))
        add("Categories", AdminList(app, [T("Category", "name"), T("Active", lambda r: "Yes" if r["active"] else "No")], lambda: cat.categories(self.db, False),
                                    add=lambda p, r: cat.add_category(self.db, a, _simple(p, "New category", "Category name")),
                                    edit=lambda p, r: cat.rename_category(self.db, a, r["id"], _simple(p, "Rename category", "Category name", r["name"]) or r["name"]),
                                    toggle=lambda p, r: cat.set_category_active(self.db, a, r["id"], not r["active"])))
        add("Payment modes", AdminList(app, [T("Mode", "name"), T("Active", lambda r: "Yes" if r["active"] else "No")], lambda: self.db.q("SELECT * FROM payment_modes ORDER BY sort_order, name"),
                                       add=lambda p, r: cat.add_payment_mode(self.db, a, _simple(p, "New payment mode", "Name")),
                                       toggle=lambda p, r: cat.set_payment_mode_active(self.db, a, r["name"], not r["active"])))
        add("Fee categories", AdminList(app, [T("Name", "name"), T("Active", lambda r: "Yes" if r["active"] else "No")], lambda: self.db.q("SELECT * FROM fee_categories ORDER BY name"),
                                        add=lambda p, r: cat.add_fee_category(self.db, a, _simple(p, "New fee category", "Name")),
                                        toggle=lambda p, r: cat.set_fee_category_active(self.db, a, r["name"], not r["active"]),
                                        hint="Used for extra charges such as late fee, exam fee, study material."))
        add("Installment plans", AdminList(app, [T("Plan", "name"), T("Installments", "count", "int"), T("Every (months)", "every_months", "int"), T("Active", lambda r: "Yes" if r["active"] else "No")],
                                           lambda: cat.installment_templates(self.db, False), add=self.template_dialog, edit=self.template_dialog,
                                           toggle=lambda p, r: cat.set_template_active(self.db, a, r["id"], not r["active"])))
        if a.can("settings.manage"):
            add("Attendance statuses", AdminList(app, [T("Code", "code"), T("Label", "label"), T("Counts as present", "counts_present"), T("Counted in total", lambda r: "Yes" if r["counts_in_total"] else "No (e.g. holiday)"),
                                                       T("Active", lambda r: "Yes" if r["active"] else "No")],
                                                 lambda: cat.attendance_statuses(self.db, False), add=self.status_dialog, edit=self.status_dialog,
                                                 toggle=lambda p, r: cat.set_attendance_status_active(self.db, a, r["code"], not r["active"]),
                                                 hint="Present = 1, Half day = 0.5, Absent = 0. Holiday should not be counted in the total."))
        self.tabs.currentChanged.connect(lambda i: self.lists[i].reload())

    def refresh(self):
        for w in self.lists:
            w.reload()

    # ---- dialogs
    def course_dialog(self, parent, row):
        a, db = self.app.actor, self.db
        cats = QComboBox(); fill_combo(cats, [(c["name"], c["id"]) for c in cat.categories(db)], current=row["category_id"] if row else None)
        name = QLineEdit(row["name"] if row else "")
        kind = QComboBox(); fill_combo(kind, [("Class (school)", "class"), ("Course", "course")], current=row["kind"] if row else None)
        months = QSpinBox(); months.setRange(0, 120); months.setValue(row["duration_months"] if row else 0)
        fee, adm, reg = MoneyEdit(), MoneyEdit(), MoneyEdit()
        inst = QSpinBox(); inst.setRange(1, 36); inst.setValue(row["default_installments"] if row else 1)
        if row:
            fee.set_paise(row["fee_paise"]); adm.set_paise(row["admission_fee_paise"]); reg.set_paise(row["registration_fee_paise"])
        subs = QListWidget(); subs.setMaximumHeight(120); have = set(cat.course_subject_ids(db, row["id"])) if row else set()
        for s in cat.subjects(db):
            it = QListWidgetItem(s["name"]); it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if s["id"] in have else Qt.CheckState.Unchecked); it.setData(Qt.ItemDataRole.UserRole, s["id"]); subs.addItem(it)
        d = FormDialog(parent, "Course / class", [("c", "Category", cats), ("n", "Name *", name), ("k", "Type", kind), ("m", "Duration (months)", months), ("f", "Course fee (Rs.)", fee),
                                                  ("a", "Admission fee (Rs.)", adm), ("r", "Registration fee (Rs.)", reg), ("i", "Default installments", inst), ("s", "Subjects", subs)])
        if d.exec():
            ids = [subs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(subs.count()) if subs.item(i).checkState() == Qt.CheckState.Checked]
            cat.save_course(db, a, id=row["id"] if row else None, category_id=cats.currentData(), name=name.text(), kind=kind.currentData(), duration_months=months.value(),
                            fee_paise=fee.paise(), admission_fee_paise=adm.paise(), registration_fee_paise=reg.paise(), default_installments=inst.value(), subject_ids=ids,
                            active=bool(row["active"]) if row else True)

    def toggle_course(self, parent, r):
        cat.save_course(self.db, self.app.actor, id=r["id"], category_id=r["category_id"], name=r["name"], kind=r["kind"], duration_months=r["duration_months"], fee_paise=r["fee_paise"],
                        admission_fee_paise=r["admission_fee_paise"], registration_fee_paise=r["registration_fee_paise"], default_installments=r["default_installments"],
                        subject_ids=cat.course_subject_ids(self.db, r["id"]), active=not r["active"])

    def batch_dialog(self, parent, row):
        db = self.db
        course = QComboBox(); fill_combo(course, [(f"{c['category']} — {c['name']}", c["id"]) for c in cat.courses(db)], current=row["course_id"] if row else None)
        sess = QComboBox(); fill_combo(sess, [(s["name"], s["id"]) for s in cat.sessions(db)], current=(row["session_id"] if row else cat.current_session_id(db)))
        name, timing = QLineEdit(row["name"] if row else ""), QLineEdit(row["timing"] if row else "")
        timing.setPlaceholderText("e.g. 4:00 PM - 5:30 PM")
        teach = QComboBox(); fill_combo(teach, [(t["name"], t["id"]) for t in cat.teachers(db)], "— no teacher —", current=row["teacher_id"] if row else None)
        d = FormDialog(parent, "Batch", [("c", "Course / class *", course), ("s", "Session", sess), ("n", "Batch name *", name), ("t", "Timing", timing), ("q", "Teacher", teach)])
        if d.exec():
            cat.save_batch(db, self.app.actor, id=row["id"] if row else None, course_id=course.currentData(), session_id=sess.currentData(), name=name.text(), timing=timing.text(),
                           teacher_id=teach.currentData(), active=bool(row["active"]) if row else True)

    def teacher_dialog(self, parent, row):
        n, p, e = QLineEdit(row["name"] if row else ""), QLineEdit(row["phone"] if row else ""), QLineEdit(row["email"] if row else "")
        d = FormDialog(parent, "Teacher", [("n", "Name *", n), ("p", "Phone", p), ("e", "Email", e)])
        if d.exec():
            cat.save_teacher(self.db, self.app.actor, id=row["id"] if row else None, name=n.text(), phone=p.text(), email=e.text(), active=bool(row["active"]) if row else True)

    def session_dialog(self, parent, row):
        n = QLineEdit(); n.setPlaceholderText("e.g. 2027-28")
        a, b = OptDate(), OptDate()
        d = FormDialog(parent, "New academic session", [("n", "Session name *", n), ("a", "Starts", a), ("b", "Ends", b)])
        if d.exec():
            cat.add_session(self.db, self.app.actor, n.text(), a.iso(), b.iso())

    def template_dialog(self, parent, row):
        n = QLineEdit(row["name"] if row else "")
        c = QSpinBox(); c.setRange(1, 36); c.setValue(row["count"] if row else 3)
        e = QSpinBox(); e.setRange(0, 12); e.setValue(row["every_months"] if row else 1)
        d = FormDialog(parent, "Installment plan", [("n", "Plan name *", n), ("c", "Number of installments", c), ("e", "Months between installments", e)])
        if d.exec():
            cat.save_installment_template(self.db, self.app.actor, n.text(), c.value(), e.value(), id=row["id"] if row else None)

    def status_dialog(self, parent, row):
        code, lab = QLineEdit(row["code"] if row else ""), QLineEdit(row["label"] if row else "")
        code.setMaxLength(3)
        if row:
            code.setReadOnly(True)
        pres = QComboBox(); fill_combo(pres, [("Present (1)", 1.0), ("Half day (0.5)", 0.5), ("Absent (0)", 0.0)], current=row["counts_present"] if row else 1.0)
        tot = QCheckBox("Count this day in the total"); tot.setChecked(bool(row["counts_in_total"]) if row else True)
        col = QLineEdit(row["color"] if row else "#64748b")
        d = FormDialog(parent, "Attendance status", [("c", "Code (1-3 letters) *", code), ("l", "Label *", lab), ("p", "Counts as", pres), ("t", "", tot), ("k", "Colour (#hex)", col)])
        if d.exec():
            cat.save_attendance_status(self.db, self.app.actor, code.text(), lab.text(), pres.currentData(), tot.isChecked(), col.text() or "#64748b")

    def timetable_dialog(self, parent, row):
        db = self.db
        dlg = QDialog(parent); dlg.setWindowTitle(f"Timetable — {row['course']} / {row['name']}"); dlg.resize(620, 460)
        v = QVBoxLayout(dlg)
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        tbl = DataTable([("Day", lambda r: days[r["weekday"]], "text"), ("From", "start_time", "text"), ("To", "end_time", "text"), ("Subject", lambda r: r["subject"] or "-", "text"),
                         ("Teacher", lambda r: r["teacher"] or "-", "text")], empty="No timetable slots yet.")
        v.addWidget(tbl)

        def reload():
            tbl.set_rows([dict(r) for r in cat.timetable(db, row["id"])])

        def add_slot():
            wd = QComboBox(); fill_combo(wd, [(d, i) for i, d in enumerate(days)])
            a, b = QTimeEdit(), QTimeEdit(); a.setDisplayFormat("HH:mm"); b.setDisplayFormat("HH:mm")
            sub = QComboBox(); fill_combo(sub, [(s["name"], s["id"]) for s in cat.subjects(db)], "— any —")
            tch = QComboBox(); fill_combo(tch, [(t["name"], t["id"]) for t in cat.teachers(db)], "— batch teacher —")
            d = FormDialog(dlg, "Add slot", [("d", "Day", wd), ("a", "From", a), ("b", "To", b), ("s", "Subject", sub), ("t", "Teacher", tch)])
            if d.exec():
                safe(dlg, cat.save_timetable_slot, db, self.app.actor, row["id"], wd.currentData(), a.time().toString("HH:mm"), b.time().toString("HH:mm"), sub.currentData(), tch.currentData())
                reload()

        def del_slot():
            r = tbl.current()
            if r and confirm(dlg, "Delete this slot?", danger=True):
                safe(dlg, cat.delete_timetable_slot, db, self.app.actor, r["id"])
                reload()
        h = QHBoxLayout(); h.addWidget(btn("➕ Add slot", "primary", slot=add_slot)); h.addWidget(btn("Delete slot", "danger", slot=del_slot)); h.addStretch(1)
        h.addWidget(btn("Close", slot=dlg.accept))
        v.addLayout(h)
        reload()
        dlg.exec()
