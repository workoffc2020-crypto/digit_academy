"""Students: list, add/edit form (with course & fees), profile, forms/ID cards."""
import uuid
from datetime import date, timedelta

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from app.printing import docs
from app.security.permissions import ValidationError
from app.services import attendance as att
from app.services import catalog, fees
from app.services import students as svc
from app.ui.forms import CustomFieldsForm
from app.ui.widgets import (DataTable, MoneyEdit, OptDate, btn, confirm, data_of, fill_combo, info, label, logo_pixmap, page_header, safe, warn)
from app.utils import money


def _scroll(widget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.Shape.NoFrame)
    s.setWidget(widget)
    return s


def course_items(db, kind=None):
    return [(f"{c['category']}  —  {c['name']}", c["id"]) for c in catalog.courses(db, kind=kind)]


# ===================================================================== course & fees panel
class EnrollPanel(QWidget):
    """Choose course, batch, subjects, fees, discount, installments and an optional first payment."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.idem = uuid.uuid4().hex
        db = app.db
        form = QFormLayout(self)
        form.setSpacing(8)
        self.course = QComboBox()
        fill_combo(self.course, course_items(db), "— choose a course / class —")
        self.batch = QComboBox()
        self.subjects = QListWidget()
        self.subjects.setFixedHeight(96)
        self.subjects.setFlow(QListWidget.Flow.LeftToRight)
        self.subjects.setWrapping(True)
        self.course_fee, self.adm_fee, self.reg_fee = MoneyEdit(), MoneyEdit(), MoneyEdit()
        self.discount, self.schol = MoneyEdit(), MoneyEdit()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason for discount / scholarship (optional)")
        self.template = QComboBox()
        fill_combo(self.template, [(f"{t['name']}", (t["count"], t["every_months"])) for t in catalog.installment_templates(db)])
        self.first_due = OptDate(optional=False)
        self.first_due.setDate(self.first_due.date())
        self.start = OptDate(optional=False)
        self.net = QLabel("Net payable: Rs. 0.00")
        self.net.setObjectName("h2")
        self.paid_now, self.mode, self.ref = MoneyEdit(), QComboBox(), QLineEdit()
        fill_combo(self.mode, [(m, m) for m in catalog.payment_modes(db)])
        self.ref.setPlaceholderText("UPI / cheque / transaction reference")
        form.addRow("Course / class *", self.course)
        form.addRow("Batch", self.batch)
        form.addRow("Subjects", self.subjects)
        form.addRow("Starting date", self.start)
        form.addRow(label("<b>Fees</b>"))
        form.addRow("Course fee (Rs.)", self.course_fee)
        form.addRow("Admission fee (Rs.)", self.adm_fee)
        form.addRow("Registration fee (Rs.)", self.reg_fee)
        form.addRow("Discount (Rs.)", self.discount)
        form.addRow("Scholarship / concession (Rs.)", self.schol)
        form.addRow("Reason", self.reason)
        form.addRow(self.net)
        form.addRow(label("<b>Installments</b>"))
        form.addRow("Installment plan", self.template)
        form.addRow("First due date", self.first_due)
        form.addRow(label("<b>Money received now (optional)</b>"))
        form.addRow("Amount received (Rs.)", self.paid_now)
        form.addRow("Payment mode", self.mode)
        form.addRow("Reference", self.ref)
        self.course.currentIndexChanged.connect(self._course_changed)
        for w in (self.course_fee, self.adm_fee, self.reg_fee, self.discount, self.schol):
            w.textChanged.connect(self._net)

    def _course_changed(self):
        cid = data_of(self.course)
        self.subjects.clear()
        if not cid:
            fill_combo(self.batch, [], "No batch")
            return
        c = catalog.course(self.app.db, cid)
        sid = catalog.current_session_id(self.app.db)
        fill_combo(self.batch, [(f"{b['name']}  {b['timing'] or ''}".strip(), b["id"]) for b in catalog.batches(self.app.db, course_id=cid, session_id=sid)], "No batch (general)")
        have = set(catalog.course_subject_ids(self.app.db, cid))
        for s in catalog.subjects(self.app.db):
            it = QListWidgetItem(s["name"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if s["id"] in have else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.subjects.addItem(it)
        self.course_fee.set_paise(c["fee_paise"])
        self.adm_fee.set_paise(c["admission_fee_paise"])
        self.reg_fee.set_paise(c["registration_fee_paise"])
        n = c["default_installments"] or 1
        for i in range(self.template.count()):
            if self.template.itemData(i)[0] == n:
                self.template.setCurrentIndex(i)
                break
        self._net()

    def _net(self):
        try:
            gross = self.course_fee.paise() + self.adm_fee.paise() + self.reg_fee.paise()
            self.net.setText(f"Net payable: {money.fmt(gross - self.discount.paise() - self.schol.paise(), 'Rs.')}")
        except ValueError:
            pass

    def chosen(self) -> bool:
        return bool(data_of(self.course))

    def enroll(self, student_id: int):
        """Creates the enrolment (+fees, installments, optional first payment). Call inside a transaction. Returns (eid, payment_id|None)."""
        db, actor = self.app.db, self.app.actor
        cid = data_of(self.course)
        if not cid:
            raise ValidationError("Choose a course / class.")
        count, every = self.template.currentData() or (1, 1)
        subs = [self.subjects.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.subjects.count()) if self.subjects.item(i).checkState() == Qt.CheckState.Checked]
        due = date.fromisoformat(self.first_due.iso())
        eid = fees.enroll_with_fees(db, actor, student_id, cid, batch_id=data_of(self.batch), subject_ids=subs, course_fee=self.course_fee.paise(),
                                    admission_fee=self.adm_fee.paise(), registration_fee=self.reg_fee.paise(), discount=self.discount.paise(),
                                    scholarship=self.schol.paise(), discount_reason=self.reason.text(), installments=count, first_due=due,
                                    every_months=every, start_date=self.start.iso())
        pid = None
        if self.paid_now.paise() > 0:
            pid = fees.collect_payment(db, actor, student_id, self.paid_now.paise(), self.mode.currentData(), enrollment_id=eid, reference=self.ref.text(),
                                       idem_key=self.idem)
        return eid, pid


class AddCourseDialog(QDialog):
    def __init__(self, app, parent, student_id):
        super().__init__(parent)
        self.app, self.sid, self.payment_id = app, student_id, None
        self.setWindowTitle("Add course / class")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        self.panel = EnrollPanel(app)
        v.addWidget(_scroll(self.panel), 1)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(btn("Cancel", slot=self.reject))
        h.addWidget(btn("Add course & fees", "primary", slot=self.save))
        v.addLayout(h)

    def save(self):
        def go():
            with self.app.db.tx():
                return self.panel.enroll(self.sid)
        ok, r = safe(self, go)
        if ok:
            self.payment_id = r[1]
            self.accept()


# ===================================================================== add / edit
class StudentDialog(QDialog):
    def __init__(self, app, parent, student_id=None):
        super().__init__(parent)
        self.app, self.sid = app, student_id
        self.photo_src = None
        self.created_payment = None
        self.setWindowTitle("Edit student" if student_id else "New admission")
        self.resize(820, 700)
        db = app.db
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        E = QLineEdit
        self.f = {k: E() for k in ("name", "father_name", "mother_name", "guardian_name", "guardian_relation", "mobile", "guardian_mobile", "email", "locality", "city",
                                   "district", "state", "pin", "alt_id_type", "alt_id_no", "emergency_name", "emergency_phone", "current_school", "prev_qualification", "board")}
        self.address = QPlainTextEdit()
        self.address.setFixedHeight(60)
        self.remarks = QPlainTextEdit()
        self.remarks.setFixedHeight(60)
        self.dob = OptDate()
        self.adm_date = OptDate(default_today=True)
        self.gender = QComboBox()
        fill_combo(self.gender, [(g or "—", g) for g in svc.GENDERS])
        self.session = QComboBox()
        fill_combo(self.session, [(s["name"], s["id"]) for s in catalog.sessions(db)], current=catalog.current_session_id(db))
        self.edu = QComboBox()
        self.edu.setEditable(True)
        self.edu.addItems([""] + [c["name"] for c in catalog.categories(db)])
        self.adm_no = QLineEdit()
        self.adm_no.setPlaceholderText("Leave blank for automatic number")
        self.aadhaar = QLineEdit()
        self.aadhaar.setPlaceholderText("12 digits - optional" if app.actor.can("students.sensitive") else "Restricted")
        self.aadhaar.setEnabled(app.actor.can("students.sensitive"))
        self.f["mobile"].setPlaceholderText("10-digit mobile")
        self.f["guardian_mobile"].setPlaceholderText("10-digit mobile")
        self.f["pin"].setPlaceholderText("6 digits")
        self.photo_lbl = QLabel("No photo")
        self.photo_lbl.setFixedSize(120, 140)
        self.photo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_lbl.setStyleSheet("border:1px dashed #94a3b8;border-radius:8px")

        t1 = QWidget(); g = QGridLayout(t1)
        form = QFormLayout(); form.setSpacing(8)
        form.addRow("Student name *", self.f["name"]); form.addRow("Date of birth", self.dob); form.addRow("Gender", self.gender)
        form.addRow("Father's name", self.f["father_name"]); form.addRow("Mother's name", self.f["mother_name"])
        form.addRow("Guardian's name", self.f["guardian_name"]); form.addRow("Guardian relationship", self.f["guardian_relation"])
        form.addRow("Admission number", self.adm_no)
        g.addLayout(form, 0, 0)
        pv = QVBoxLayout(); pv.addWidget(self.photo_lbl); pv.addWidget(btn("Choose photo…", slot=self.pick_photo)); pv.addStretch(1)
        g.addLayout(pv, 0, 1, alignment=Qt.AlignmentFlag.AlignTop)
        g.setColumnStretch(0, 1)
        self.tabs.addTab(_scroll(t1), "Personal")

        t2 = QWidget(); f2 = QFormLayout(t2); f2.setSpacing(8)
        f2.addRow("Student mobile", self.f["mobile"]); f2.addRow("Guardian mobile", self.f["guardian_mobile"]); f2.addRow("Email", self.f["email"])
        f2.addRow("Address", self.address); f2.addRow("Village / locality", self.f["locality"]); f2.addRow("City", self.f["city"]); f2.addRow("District", self.f["district"])
        f2.addRow("State", self.f["state"]); f2.addRow("PIN code", self.f["pin"])
        f2.addRow("Emergency contact name", self.f["emergency_name"]); f2.addRow("Emergency contact phone", self.f["emergency_phone"])
        self.tabs.addTab(_scroll(t2), "Contact && address")

        t3 = QWidget(); f3 = QFormLayout(t3); f3.setSpacing(8)
        f3.addRow("Aadhaar number", self.aadhaar)
        f3.addRow(label("Aadhaar is optional, stored encrypted and shown masked everywhere except to users with special permission.", "muted"))
        f3.addRow("Other ID type", self.f["alt_id_type"]); f3.addRow("Other ID number", self.f["alt_id_no"])
        f3.addRow("Admission date", self.adm_date); f3.addRow("Academic session", self.session); f3.addRow("Education category", self.edu)
        f3.addRow("Current school / institution", self.f["current_school"]); f3.addRow("Previous qualification", self.f["prev_qualification"])
        f3.addRow("Board / examination", self.f["board"]); f3.addRow("Remarks", self.remarks)
        self.tabs.addTab(_scroll(t3), "ID && academic")

        self.custom = CustomFieldsForm(app, "student")
        if not self.custom.empty:
            self.tabs.addTab(_scroll(self.custom), "Additional info")
        self.panel = None
        self.enroll_chk = None
        if student_id is None and app.actor.can("fees.charges"):
            box = QWidget(); bv = QVBoxLayout(box)
            self.enroll_chk = QCheckBox("Enrol in a course / class now and create the fee structure")
            self.enroll_chk.setChecked(True)
            bv.addWidget(self.enroll_chk)
            self.panel = EnrollPanel(app)
            bv.addWidget(self.panel)
            self.enroll_chk.toggled.connect(self.panel.setEnabled)
            self.tabs.addTab(_scroll(box), "Course && fees")
        h = QHBoxLayout(); h.addStretch(1)
        h.addWidget(btn("Cancel", slot=self.reject))
        h.addWidget(btn("Save student", "primary", slot=self.save))
        v.addLayout(h)
        if student_id:
            self._load()

    def pick_photo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Student photograph", "", "Images (*.jpg *.jpeg *.png *.bmp)")
        if p:
            self.photo_src = p
            self._show_photo(p)

    def _show_photo(self, p):
        pm = QPixmap(p)
        if not pm.isNull():
            self.photo_lbl.setPixmap(pm.scaled(120, 140, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _load(self):
        ok, s = safe(self, svc.get, self.app.db, self.app.actor, self.app.vault, self.sid)
        if not ok:
            self.reject()
            return
        for k, w in self.f.items():
            w.setText(s.get(k) or "")
        self.address.setPlainText(s["address"] or "")
        self.remarks.setPlainText(s["remarks"] or "")
        self.dob.set_iso(s["dob"]); self.adm_date.set_iso(s["admission_date"])
        self.gender.setCurrentIndex(max(self.gender.findData(s["gender"]), 0))
        self.session.setCurrentIndex(max(self.session.findData(s["session_id"]), 0))
        self.edu.setCurrentText(s["education_category"] or "")
        self.adm_no.setText(s["admission_no"])
        if self.aadhaar.isEnabled():
            self.aadhaar.setPlaceholderText(f"Current: {s['aadhaar_masked'] or 'none'}  (leave empty to keep)")
        if s["photo_path"]:
            self._show_photo(s["photo_path"])
        self.custom.set_values(s["custom"])

    def data(self) -> dict:
        d = {k: w.text() for k, w in self.f.items()}
        d.update(address=self.address.toPlainText(), remarks=self.remarks.toPlainText(), dob=self.dob.iso() or "", admission_date=self.adm_date.iso() or "",
                 gender=self.gender.currentData() or "", session_id=self.session.currentData(), education_category=self.edu.currentText())
        return d

    def save(self):
        app = self.app
        d = self.data()
        if not d["name"].strip():
            self.tabs.setCurrentIndex(0)
            warn(self, "Student name is required.")
            return
        dups = svc.find_possible_duplicates(app.db, d["name"], d["dob"] or None, d["mobile"], self.sid)
        if dups:
            names = "\n".join(f"• {r['name']} ({r['admission_no']}) father: {r['father_name'] or '-'}" for r in dups[:5])
            if not confirm(self, f"A student with the same name and date of birth / mobile already exists:\n\n{names}\n\nBrothers and sisters are allowed. Save anyway?", "Possible duplicate", "Save anyway"):
                return

        def go():
            custom = self.custom.values()
            adm = self.adm_no.text().strip() or None
            with app.db.tx():
                if self.sid:
                    svc.update(app.db, app.actor, app.vault, self.sid, d, aadhaar=(self.aadhaar.text() if self.aadhaar.text().strip() else None), custom=custom,
                               photo_src=self.photo_src, photos_dir=app.paths.photos, admission_no=adm)
                    return self.sid, None
                sid = svc.create(app.db, app.actor, app.vault, d, aadhaar=self.aadhaar.text(), custom=custom, photo_src=self.photo_src, photos_dir=app.paths.photos,
                                 admission_no=adm)
                pid = None
                if self.enroll_chk is not None and self.enroll_chk.isChecked() and self.panel.chosen():
                    _, pid = self.panel.enroll(sid)
                return sid, pid
        ok, r = safe(self, go)
        if ok:
            self.sid, self.created_payment = r
            self.accept()


# ===================================================================== profile
class ProfileDialog(QDialog):
    def __init__(self, app, parent, student_id):
        super().__init__(parent)
        self.app, self.sid = app, student_id
        self.setWindowTitle("Student profile")
        self.resize(980, 720)
        self.v = QVBoxLayout(self)
        self.build()

    def build(self):
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        app, actor = self.app, self.app.actor
        ok, s = safe(self, svc.get, app.db, actor, app.vault, self.sid)
        if not ok:
            self.reject()
            return
        self.s = s
        top = QFrame(); top.setObjectName("card")
        th = QHBoxLayout(top)
        ph = QLabel(); ph.setFixedSize(84, 96); ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = QPixmap(s["photo_path"]) if s["photo_path"] else QPixmap()
        if pm.isNull():
            pm = logo_pixmap(app.db, 84)
        ph.setPixmap(pm.scaled(84, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        th.addWidget(ph)
        info_l = QVBoxLayout()
        info_l.addWidget(label(f"{s['name']}", "h1"))
        age = f"{s['age']} yrs" if s["age"] is not None else ""
        info_l.addWidget(label(f"Adm. No. <b>{s['admission_no']}</b> &nbsp; ID {s['student_code']} &nbsp; Status <b>{s['status']}</b> &nbsp; {age} {s['gender']}", "muted"))
        info_l.addWidget(label(f"Father: {s['father_name'] or '-'} &nbsp; Mobile: {s['mobile'] or '-'} &nbsp; Guardian mobile: {s['guardian_mobile'] or '-'}"))
        info_l.addWidget(label(", ".join(x for x in (s["address"], s["locality"], s["city"], s["district"], s["state"], s["pin"]) if x) or "No address"))
        if s["aadhaar_masked"]:
            info_l.addWidget(label(f"Aadhaar: {s['aadhaar_masked']}", "muted"))
        th.addLayout(info_l, 1)
        self.v.addWidget(top)
        bar = QHBoxLayout()
        if actor.can("students.edit"):
            bar.addWidget(btn("✏ Edit", "primary", slot=self.edit))
            bar.addWidget(btn("➕ Add course", slot=self.add_course))
        bar.addWidget(btn("🖨 Admission form", slot=lambda: self.print_form(False)))
        bar.addWidget(btn("📄 Full profile", slot=lambda: self.print_form(True)))
        bar.addWidget(btn("🪪 ID card", slot=self.id_card))
        if actor.can("fees.collect"):
            bar.addWidget(btn("💰 Collect fee", "navy", slot=self.collect))
        if actor.can("students.deactivate"):
            bar.addWidget(btn("Deactivate" if s["status"] == "Active" else "Activate", "danger" if s["status"] == "Active" else "primary", slot=self.toggle))
        bar.addStretch(1)
        self.v.addLayout(bar)
        tabs = QTabWidget()
        self.v.addWidget(tabs, 1)
        enr = DataTable([("Course / class", "course", "text"), ("Batch", lambda r: r["batch"] or "-", "text"), ("Teacher", lambda r: r["teacher"] or "-", "text"),
                         ("Session", lambda r: r["session"] or "-", "text"), ("Started", "start_date", "date"), ("Status", "status", "text"),
                         ("Net fee", "net", "money"), ("Paid", "paid", "money"), ("Outstanding", "out", "money")], empty="Not enrolled in any course yet.")
        rows = []
        for e in s["enrollments"]:
            d = dict(e)
            if actor.can("fees.view"):
                sm = fees.enrollment_summary(app.db, e["id"])
                d.update(net=sm["net"], paid=sm["paid"], out=sm["outstanding"])
            rows.append(d)
        enr.set_rows(rows)
        tabs.addTab(enr, "Courses && fees")
        if actor.can("fees.view"):
            sm = fees.student_summary(app.db, self.sid)
            pay = DataTable([("Receipt", lambda r: r["receipt_no"] or r["txn_id"], "text"), ("Date", "pay_date", "date"), ("Course", lambda r: r["course"] or "-", "text"),
                             ("Type", "kind", "text"), ("Amount", "amount_paise", "money"), ("Mode", "mode", "text"), ("Status", "status", "status")], empty="No payments yet.")
            pay.set_rows([dict(r) for r in fees.payments_for_student(app.db, actor, self.sid)])
            w = QWidget(); l = QVBoxLayout(w)
            l.addWidget(label(f"Net payable <b>{money.fmt(sm['net'], 'Rs.')}</b> &nbsp; Received <b>{money.fmt(sm['paid'], 'Rs.')}</b> &nbsp; "
                              f"Outstanding <b>{money.fmt(sm['outstanding'], 'Rs.')}</b> &nbsp; Advance held <b>{money.fmt(sm['advance'], 'Rs.')}</b>"))
            l.addWidget(pay, 1)
            tabs.addTab(w, "Payments")
            led = DataTable([("Date", lambda r: r["when"][:10], "date"), ("Type", "type", "text"), ("Details", "text", "text"), ("Receipt", "ref", "text"),
                             ("Charges", "debit", "money"), ("Paid / credited", "credit", "money"), ("Balance", "balance", "money")])
            led.set_rows(fees.ledger(app.db, actor, self.sid))
            tabs.addTab(led, "Fee ledger")
        if actor.can("attendance.view"):
            pc = att.percentage(app.db, self.sid)
            ah = DataTable([("Date", "att_date", "date"), ("Status", "label", "text"), ("Batch", "batch", "text"), ("Remark", "remark", "text")], empty="No attendance recorded.")
            ah.set_rows([dict(r) for r in att.history_for_student(app.db, actor, self.sid)])
            w = QWidget(); l = QVBoxLayout(w)
            l.addWidget(label(f"Overall attendance: <b>{'-' if pc is None else f'{pc:.1f}%'}</b>"))
            l.addWidget(ah, 1)
            tabs.addTab(w, "Attendance")
        cf = CustomFieldsForm(app, "student")
        if not cf.empty:
            cf.set_values(s["custom"])
            cf.setEnabled(False)
            tabs.addTab(_scroll(cf), "Additional info")

    def edit(self):
        d = StudentDialog(self.app, self, self.sid)
        if d.exec():
            self.build()
            self.app.broadcast()

    def add_course(self):
        d = AddCourseDialog(self.app, self, self.sid)
        if d.exec():
            self.build()
            if d.payment_id:
                self.show_receipt(d.payment_id)
            self.app.broadcast()

    def show_receipt(self, pid):
        size = self.app.db.val("SELECT value FROM settings WHERE key='receipt_size'", default="A4")
        self.app.preview(self, {"A4": docs.receipt(self.app.db, pid, "A4"), "Thermal 80 mm": docs.receipt(self.app.db, pid, "THERMAL80")}, "Receipt",
                         "Thermal 80 mm" if size.upper().startswith("T") else "A4", f"receipt_{pid}")

    def print_form(self, full):
        ok, d = safe(self, docs.admission_form, self.app.db, self.app.vault, self.app.actor, self.sid, full)
        if ok:
            self.app.preview(self, d, "Profile" if full else "Admission form", save_name=f"{'profile' if full else 'admission'}_{self.s['admission_no']}")

    def id_card(self):
        ok, d = safe(self, docs.id_cards, self.app.db, self.app.vault, self.app.actor, [self.sid])
        if ok:
            self.app.preview(self, d, "ID card", save_name=f"idcard_{self.s['admission_no']}")

    def collect(self):
        self.accept()
        page = self.app.window.go_to_module("fees")
        if page:
            page.open_student(self.sid)

    def toggle(self):
        new = "Inactive" if self.s["status"] == "Active" else "Active"
        if confirm(self, f"Mark {self.s['name']} as {new}?\n\nFee and attendance history is always kept.", "Change status", "Yes, change"):
            ok, _ = safe(self, svc.set_status, self.app.db, self.app.actor, self.sid, new, "")
            if ok:
                self.build()
                self.app.broadcast()


# ===================================================================== list page
class StudentsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)
        adds = []
        if app.actor.can("students.edit"):
            adds.append(btn("➕  New admission", "primary", slot=self.add_student))
        v.addWidget(page_header("Students", "Search, admit and manage students", *adds))
        card = QFrame(); card.setObjectName("card")
        fl = QGridLayout(card)
        self.q = QLineEdit(); self.q.setPlaceholderText("🔎  Search name, admission no., mobile, father, receipt no.")
        self.q.setClearButtonEnabled(True)
        self.status = QComboBox(); fill_combo(self.status, [(s, s) for s in svc.STATUSES], "All statuses", current="Active")
        self.course = QComboBox(); self.batch = QComboBox(); self.session = QComboBox()
        fl.addWidget(self.q, 0, 0, 1, 4)
        fl.addWidget(self.status, 1, 0); fl.addWidget(self.course, 1, 1); fl.addWidget(self.batch, 1, 2); fl.addWidget(self.session, 1, 3)
        v.addWidget(card)
        self.table = DataTable([("Adm. No.", "admission_no", "text"), ("Student", "name", "text"), ("Father", "father_name", "text"), ("Mobile", "mobile", "text"),
                                ("Course / batch", "courses", "text"), ("Status", "status", "status"), ("Admitted", "admission_date", "date")],
                               page_size=100, empty="No students found. Click “New admission” to add the first student.")
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.b_view = btn("👁  Open profile", "navy", slot=self.open_profile)
        self.b_edit = btn("✏  Edit", slot=self.edit)
        self.b_form = btn("🖨  Admission form", slot=self.print_form)
        self.b_id = btn("🪪  ID card", slot=self.id_card)
        self.b_ids = btn("🪪  ID cards for this list", slot=self.id_cards_all)
        for b in (self.b_view, self.b_edit, self.b_form, self.b_id, self.b_ids):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.reload)
        self.q.textChanged.connect(lambda: self.timer.start(300))
        for c in (self.status, self.batch, self.session):
            c.currentIndexChanged.connect(lambda _=0: self.reload())
        self.course.currentIndexChanged.connect(self._course)
        self.table.activated.connect(lambda r: self.open_profile())
        self.table.selected.connect(self._sel)
        self._filled = False

    def refresh(self):
        db = self.app.db
        cur = (self.course.currentData(), self.session.currentData())
        fill_combo(self.course, course_items(db), "All courses / classes", current=cur[0])
        fill_combo(self.session, [(s["name"], s["id"]) for s in catalog.sessions(db, False)], "All sessions", current=cur[1])
        self._course(reload=False)
        self.reload()

    def _course(self, *_, reload=True):
        cid = self.course.currentData()
        fill_combo(self.batch, [(f"{b['name']}", b["id"]) for b in catalog.batches(self.app.db, course_id=cid)] if cid else [], "All batches")
        if reload:
            self.reload()

    def reload(self):
        ok, rows = safe(self, svc.search, self.app.db, self.app.actor, self.q.text(), course_id=self.course.currentData(), batch_id=self.batch.currentData(),
                        session_id=self.session.currentData(), status=self.status.currentData(), limit=3000)
        if ok:
            self.table.set_rows(rows)
        self._sel(None)

    def _sel(self, r):
        r = r or self.table.current()
        for b in (self.b_view, self.b_edit, self.b_form, self.b_id):
            b.setEnabled(r is not None)
        self.b_edit.setEnabled(r is not None and self.app.actor.can("students.edit"))
        self.b_ids.setEnabled(bool(self.table.rows()))

    def add_student(self):
        d = StudentDialog(self.app, self)
        if d.exec():
            self.reload()
            if d.created_payment:
                size = self.app.db.val("SELECT value FROM settings WHERE key='receipt_size'", default="A4")
                self.app.preview(self, {"A4": docs.receipt(self.app.db, d.created_payment, "A4"), "Thermal 80 mm": docs.receipt(self.app.db, d.created_payment, "THERMAL80")},
                                 "Receipt", "Thermal 80 mm" if size.upper().startswith("T") else "A4", f"receipt_{d.created_payment}")
            if confirm(self, "Student saved. Print the admission form now?", "Saved", "Print form"):
                self.app.preview(self, docs.admission_form(self.app.db, self.app.vault, self.app.actor, d.sid), "Admission form", save_name="admission_form")
            self.app.broadcast()

    def open_profile(self):
        r = self.table.current()
        if r:
            ProfileDialog(self.app, self, r["id"]).exec()
            self.reload()

    def edit(self):
        r = self.table.current()
        if r and StudentDialog(self.app, self, r["id"]).exec():
            self.reload()

    def print_form(self):
        r = self.table.current()
        if r:
            ok, d = safe(self, docs.admission_form, self.app.db, self.app.vault, self.app.actor, r["id"])
            if ok:
                self.app.preview(self, d, "Admission form", save_name=f"admission_{r['admission_no']}")

    def id_card(self):
        r = self.table.current()
        if r:
            ok, d = safe(self, docs.id_cards, self.app.db, self.app.vault, self.app.actor, [r["id"]])
            if ok:
                self.app.preview(self, d, "ID card", save_name=f"idcard_{r['admission_no']}")

    def id_cards_all(self):
        ids = [r["id"] for r in self.table.rows()][:300]
        if ids and confirm(self, f"Prepare ID cards for {len(ids)} student(s) in this list?"):
            ok, d = safe(self, docs.id_cards, self.app.db, self.app.vault, self.app.actor, ids)
            if ok:
                self.app.preview(self, d, "ID cards", save_name="idcards")
