"""Fees: collect money, installments, advance, refunds, reversals, receipts register, daily closing, due reminders."""
import uuid
from datetime import date, timedelta
from urllib.parse import quote

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget)

from app.printing import docs
from app.security.permissions import ValidationError
from app.services import catalog, fees, reports, settings
from app.services import students as st
from app.services.reports import Dataset
from app.ui.widgets import (DataTable, Kpi, MoneyEdit, OptDate, btn, confirm, data_of, fill_combo, info, label, page_header, safe, warn)
from app.utils import money


def show_receipt(app, parent, pid):
    size = settings.get(app.db, "receipt_size")
    app.preview(parent, {"A4": lambda: docs.receipt(app.db, pid, "A4"), "Thermal 80 mm": lambda: docs.receipt(app.db, pid, "THERMAL80")}, "Receipt",
                "Thermal 80 mm" if size.upper().startswith("T") else "A4", f"receipt_{pid}")


# ===================================================================== dialogs
class CollectDialog(QDialog):
    def __init__(self, app, parent, student_id, enrollment_id, due_installments):
        super().__init__(parent)
        self.app, self.sid, self.eid = app, student_id, enrollment_id
        self.idem = uuid.uuid4().hex
        self.payment_id = None
        self.setWindowTitle("Collect fee")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        f = QFormLayout()
        f.setSpacing(8)
        total_due = sum(i["due"] for i in due_installments)
        self.amount = MoneyEdit()
        full = btn("Full due")
        full.clicked.connect(lambda: self.amount.set_paise(total_due))
        r = QHBoxLayout(); r.addWidget(self.amount, 1); r.addWidget(full)
        self.mode = QComboBox(); fill_combo(self.mode, [(m, m) for m in catalog.payment_modes(app.db)])
        self.ref = QLineEdit(); self.ref.setPlaceholderText("UPI id / cheque no. / bank reference")
        self.note = QLineEdit()
        self.date = OptDate(optional=False)
        self.date.setEnabled(app.actor.can("fees.charges"))
        self.date.setToolTip("Only authorised staff can back-date a payment.")
        self.adv = MoneyEdit()
        self.adv.setPlaceholderText("0.00 — keep this part as advance")
        f.addRow("Amount received (Rs.) *", r)
        f.addRow("Payment mode *", self.mode)
        f.addRow("Reference", self.ref)
        f.addRow("Payment date", self.date)
        f.addRow("Of which: advance (Rs.)", self.adv)
        f.addRow("Note", self.note)
        v.addLayout(f)
        v.addWidget(label(f"Total due now: <b>{money.fmt(total_due, 'Rs.')}</b>. Tick installments to pay a specific one; otherwise the oldest due is paid first."))
        self.inst = QListWidget()
        self.inst.setMaximumHeight(130)
        for i in due_installments:
            it = QListWidgetItem(f"{i['label'] or 'Installment ' + str(i['seq'])}   due {i['due_date']}   —   {money.fmt(i['due'], 'Rs.')}   ({i['status']})")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, i["id"])
            self.inst.addItem(it)
        v.addWidget(self.inst)
        h = QHBoxLayout(); h.addStretch(1)
        h.addWidget(btn("Cancel", slot=self.reject))
        self.ok = btn("Save payment & receipt", "primary", slot=self.save)
        h.addWidget(self.ok)
        v.addLayout(h)

    def save(self):
        ids = [self.inst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.inst.count()) if self.inst.item(i).checkState() == Qt.CheckState.Checked]
        self.ok.setEnabled(False)                                    # no double-click double payment
        ok, pid = safe(self, lambda: fees.collect_payment(
            self.app.db, self.app.actor, self.sid, self.amount.paise(), self.mode.currentData(), enrollment_id=self.eid, installment_ids=ids or None,
            as_advance=self.adv.paise(), reference=self.ref.text(), note=self.note.text(), idem_key=self.idem, pay_date=self.date.iso()))
        if ok:
            self.payment_id = pid
            self.accept()
        else:
            self.ok.setEnabled(True)


class SimpleMoneyDialog(QDialog):
    """Amount + extra fields. spec: [(key, label, widget)]"""

    def __init__(self, parent, title, spec, text=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        if text:
            v.addWidget(label(text))
        f = QFormLayout()
        self.w = {}
        for key, lab, w in spec:
            f.addRow(lab, w)
            self.w[key] = w
        v.addLayout(f)
        h = QHBoxLayout(); h.addStretch(1)
        h.addWidget(btn("Cancel", slot=self.reject))
        h.addWidget(btn("Confirm", "primary", slot=self.accept))
        v.addLayout(h)


# ===================================================================== page
class FeesPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.sid, self.eid = app, None, None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Fees & Receipts", "Collect fees, installments, advance, refunds and receipts"))
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_collect(), "💰 Collect && ledger")
        self.tabs.addTab(self._build_register(), "🧾 Receipts register")
        if app.actor.can("fees.view"):
            self.tabs.addTab(self._build_closing(), "🔒 Daily closing")
            self.tabs.addTab(self._build_reminders(), "⏰ Due reminders")

    # ------------------------------------------------------------- collect tab
    def _build_collect(self):
        w = QWidget(); v = QVBoxLayout(w)
        sr = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("🔎  Find student: name, admission no., mobile, receipt no.")
        self.search.setClearButtonEnabled(True)
        self.matches = QComboBox(); self.matches.setMinimumWidth(320)
        sr.addWidget(self.search, 2); sr.addWidget(self.matches, 2)
        v.addLayout(sr)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self._find)
        self.search.textChanged.connect(lambda: self.timer.start(300))
        self.matches.activated.connect(self._pick)
        self.who = label("Choose a student to see the fee account.", "h2")
        v.addWidget(self.who)
        k = QGridLayout()
        self.k_net, self.k_paid, self.k_out, self.k_adv = Kpi("Net payable", "#0b2545"), Kpi("Received", "#16a34a"), Kpi("Outstanding", "#dc2626"), Kpi("Advance held", "#d97706")
        for i, kp in enumerate((self.k_net, self.k_paid, self.k_out, self.k_adv)):
            k.addWidget(kp, 0, i)
        v.addLayout(k)
        er = QHBoxLayout()
        er.addWidget(QLabel("Course / class:"))
        self.enr = QComboBox(); self.enr.setMinimumWidth(300)
        self.enr.currentIndexChanged.connect(self._enr_changed)
        er.addWidget(self.enr)
        er.addStretch(1)
        v.addLayout(er)
        self.inst = DataTable([("Installment", lambda r: r["label"] or f"#{r['seq']}", "text"), ("Due date", "due_date", "date"), ("Amount", "amount", "money"),
                               ("Paid", "paid", "money"), ("Due", "due", "money"), ("Status", "status", "status")], empty="No installments for this course.")
        v.addWidget(self.inst, 1)
        ab = QHBoxLayout()
        self.b_collect = btn("💰  Collect payment", "primary", slot=self.collect)
        self.b_adv = btn("Use advance", slot=self.use_advance)
        self.b_charge = btn("➕ Extra charge", slot=self.add_charge)
        self.b_disc = btn("🏷 Discount / scholarship", slot=self.add_discount)
        self.b_refund = btn("↩ Refund", "warn", slot=self.refund)
        self.b_stmt = btn("🖨 Statement", slot=self.statement)
        for b in (self.b_collect, self.b_adv, self.b_charge, self.b_disc, self.b_refund, self.b_stmt):
            ab.addWidget(b)
        ab.addStretch(1)
        v.addLayout(ab)
        self.pays = DataTable([("Receipt", lambda r: r["receipt_no"] or r["txn_id"], "text"), ("Date", "pay_date", "date"), ("Course", lambda r: r["course"] or "-", "text"),
                               ("Type", "kind", "text"), ("Amount", "amount_paise", "money"), ("Mode", "mode", "text"), ("Reference", "reference", "text"), ("Status", "status", "status")],
                              page_size=50, empty="No payments recorded yet.")
        v.addWidget(self.pays, 1)
        pb = QHBoxLayout()
        self.b_print = btn("🖨  Print / preview receipt", "navy", slot=self.print_receipt)
        self.b_rev = btn("✖  Cancel receipt (reversal)", "danger", slot=self.reverse)
        pb.addWidget(self.b_print); pb.addWidget(self.b_rev); pb.addStretch(1)
        v.addLayout(pb)
        a = self.app.actor
        self.b_collect.setVisible(a.can("fees.collect")); self.b_adv.setVisible(a.can("fees.collect")); self.b_charge.setVisible(a.can("fees.charges"))
        self.b_disc.setVisible(a.can("fees.discount") or a.can("fees.discount_any")); self.b_refund.setVisible(a.can("fees.refund")); self.b_rev.setVisible(a.can("fees.reverse"))
        self._enable()
        return w

    def refresh(self):
        if self.sid:
            self._load_student()
        self._reg_reload()
        if self.app.actor.can("fees.view"):
            self._closing_reload()
            self._rm_reload()

    def _find(self):
        t = self.search.text().strip()
        if len(t) < 2:
            fill_combo(self.matches, [])
            return
        ok, rows = safe(self, st.search, self.app.db, self.app.actor, t, status=None, limit=25)
        if ok:
            fill_combo(self.matches, [(f"{r['name']}  —  {r['admission_no']}  —  {r['mobile'] or r['guardian_mobile'] or ''}  [{r['status']}]", r["id"]) for r in rows],
                       "— choose from %d match(es) —" % len(rows) if rows else "— no match —")
            if len(rows) == 1:
                self.matches.setCurrentIndex(1)
                self._pick()

    def _pick(self, *_):
        sid = self.matches.currentData()
        if sid:
            self.open_student(sid)

    def open_student(self, sid):
        self.sid = sid
        self.tabs.setCurrentIndex(0)
        self._load_student()

    def _load_student(self):
        app = self.app
        ok, s = safe(self, st.get, app.db, app.actor, app.vault, self.sid)
        if not ok:
            return
        self.who.setText(f"{s['name']}  •  Adm. No. {s['admission_no']}  •  {s['mobile'] or s['guardian_mobile'] or 'no mobile'}  •  {s['status']}")
        sm = fees.student_summary(app.db, self.sid)
        self.k_net.set(money.fmt(sm["net"], "Rs."), f"Fee {money.fmt(sm['gross'], 'Rs.')} − discounts {money.fmt(sm['discount'] + sm['scholarship'], 'Rs.')}")
        self.k_paid.set(money.fmt(sm["paid"], "Rs."))
        self.k_out.set(money.fmt(sm["outstanding"], "Rs."))
        self.k_adv.set(money.fmt(sm["advance"], "Rs."), "unused advance money")
        cur = self.enr.currentData()
        fill_combo(self.enr, [(f"{e['course']}  ({e['status']})", e["id"]) for e in s["enrollments"]], current=cur)
        self.eid = self.enr.currentData()
        self._inst_load()
        self.pays.set_rows([dict(r) for r in fees.payments_for_student(app.db, app.actor, self.sid)])
        self._enable()

    def _enr_changed(self, *_):
        self.eid = self.enr.currentData()
        self._inst_load()
        self._enable()

    def _inst_load(self):
        rows = fees.installments_status(self.app.db, self.eid) if self.eid else []
        self.inst.set_rows(rows)

    def _enable(self):
        has = self.sid is not None
        for b in (self.b_collect, self.b_adv, self.b_charge, self.b_disc, self.b_refund, self.b_stmt):
            b.setEnabled(has)
        for b in (self.b_charge, self.b_disc):
            b.setEnabled(has and self.eid is not None)

    def _due(self):
        return [i for i in (fees.installments_status(self.app.db, self.eid) if self.eid else []) if i["due"] > 0]

    def collect(self):
        d = CollectDialog(self.app, self, self.sid, self.eid, self._due())
        if d.exec() and d.payment_id:
            self._load_student()
            show_receipt(self.app, self, d.payment_id)

    def use_advance(self):
        adv = fees.advance_balance(self.app.db, self.sid)
        if adv <= 0:
            return info(self, "This student has no advance balance.")
        amt = MoneyEdit(); amt.set_paise(min(adv, sum(i["due"] for i in self._due())))
        d = SimpleMoneyDialog(self, "Use advance", [("a", "Amount to apply (Rs.)", amt)],
                              f"Advance available: <b>{money.fmt(adv, 'Rs.')}</b>. It will be applied to the oldest due installments of the selected course. No cash moves.")
        if d.exec():
            ok, pid = safe(self, fees.apply_advance, self.app.db, self.app.actor, self.sid, amt.paise(), enrollment_id=self.eid)
            if ok:
                self._load_student()
                show_receipt(self.app, self, pid)

    def add_charge(self):
        cat = QComboBox(); fill_combo(cat, [(c, c) for c in catalog.fee_categories(self.app.db)])
        cat.setEditable(True)
        amt = MoneyEdit()
        d = SimpleMoneyDialog(self, "Extra charge", [("c", "Charge", cat), ("a", "Amount (Rs.)", amt)], "Adds an extra charge (late fee, exam fee, material...) to this course. It is added to the last installment.")
        if d.exec():
            ok, _ = safe(self, fees.add_charge, self.app.db, self.app.actor, self.eid, "additional", cat.currentText(), amt.paise())
            if ok:
                self._load_student()

    def add_discount(self):
        kind = QComboBox(); fill_combo(kind, [("Discount", "discount"), ("Scholarship / concession", "scholarship")])
        amt, why = MoneyEdit(), QLineEdit()
        d = SimpleMoneyDialog(self, "Discount / scholarship", [("k", "Type", kind), ("a", "Amount (Rs.)", amt), ("r", "Reason", why)],
                              "Discounts above the allowed percentage need an administrator.")
        if d.exec():
            ok, _ = safe(self, fees.add_adjustment, self.app.db, self.app.actor, self.eid, kind.currentData(), amt.paise(), why.text())
            if ok:
                self._load_student()

    def refund(self):
        src = QComboBox(); fill_combo(src, [("From fees paid for the selected course", False), ("From advance balance", True)])
        amt, why, ref = MoneyEdit(), QLineEdit(), QLineEdit()
        mode = QComboBox(); fill_combo(mode, [(m, m) for m in catalog.payment_modes(self.app.db)])
        d = SimpleMoneyDialog(self, "Refund", [("s", "Refund", src), ("a", "Amount (Rs.)", amt), ("m", "Paid back by", mode), ("r", "Reason *", why), ("f", "Reference", ref)],
                              "A refund creates its own voucher and is kept in the history.")
        if d.exec():
            ok, pid = safe(self, fees.refund, self.app.db, self.app.actor, self.sid, amt.paise(), mode.currentData(), from_advance=bool(src.currentData()),
                           enrollment_id=self.eid, reason=why.text(), reference=ref.text())
            if ok:
                self._load_student()
                show_receipt(self.app, self, pid)

    def statement(self):
        ok, ds = safe(self, reports.run, self.app.db, self.app.actor, "student_ledger", student_id=self.sid)
        if ok:
            self.app.preview(self, docs.dataset(self.app.db, ds), "Fee statement", save_name="fee_statement")

    def print_receipt(self):
        r = self.pays.current()
        if r:
            show_receipt(self.app, self, r["id"])

    def reverse(self):
        r = self.pays.current()
        if not r:
            return
        why = QLineEdit()
        d = SimpleMoneyDialog(self, "Cancel receipt", [("r", "Reason *", why)],
                              f"Receipt <b>{r['receipt_no'] or r['txn_id']}</b> will stay on record, marked CANCELLED, and a reversal entry is created. The balance is restored.")
        if d.exec() and confirm(self, "Cancel this receipt? This cannot be undone (a new receipt can be made afterwards).", danger=True, yes="Yes, cancel receipt"):
            ok, rid = safe(self, fees.reverse_payment, self.app.db, self.app.actor, r["id"], why.text())
            if ok:
                self._load_student()
                show_receipt(self.app, self, rid)

    # ------------------------------------------------------------- receipts register
    def _build_register(self):
        w = QWidget(); v = QVBoxLayout(w)
        h = QHBoxLayout()
        self.rq = QLineEdit(); self.rq.setPlaceholderText("Receipt no., student, admission no., mobile, reference…")
        self.rf, self.rt = OptDate(), OptDate()
        h.addWidget(self.rq, 2); h.addWidget(QLabel("From")); h.addWidget(self.rf); h.addWidget(QLabel("To")); h.addWidget(self.rt)
        v.addLayout(h)
        self.reg = DataTable([("Receipt", lambda r: r["receipt_no"] or r["txn_id"], "text"), ("Date", "pay_date", "date"), ("Student", "student", "text"), ("Adm. No.", "admission_no", "text"),
                              ("Type", "kind", "text"), ("Amount", "amount_paise", "money"), ("Mode", "mode", "text"), ("Cashier", "cashier_name", "text"), ("Status", "status", "status")],
                             page_size=100, empty="No receipts match.")
        v.addWidget(self.reg, 1)
        b = QHBoxLayout()
        b.addWidget(btn("🖨  Print / preview", "navy", slot=lambda: self._reg_print()))
        b.addWidget(btn("Open student", slot=lambda: self._reg_student()))
        b.addStretch(1)
        v.addLayout(b)
        self.rtimer = QTimer(self); self.rtimer.setSingleShot(True); self.rtimer.timeout.connect(self._reg_reload)
        self.rq.textChanged.connect(lambda: self.rtimer.start(300))
        self.rf.dateChanged.connect(lambda: self.rtimer.start(200)); self.rt.dateChanged.connect(lambda: self.rtimer.start(200))
        self.reg.activated.connect(lambda r: self._reg_print())
        return w

    def _reg_reload(self):
        if not self.app.actor.can("fees.view"):
            return
        ok, rows = safe(self, fees.find_receipts, self.app.db, self.app.actor, self.rq.text(), self.rf.iso(), self.rt.iso(), 2000)
        if ok:
            self.reg.set_rows([dict(r) for r in rows])

    def _reg_print(self):
        r = self.reg.current()
        if r:
            show_receipt(self.app, self, r["id"])

    def _reg_student(self):
        r = self.reg.current()
        if r:
            self.open_student(r["student_id"])

    # ------------------------------------------------------------- daily closing
    def _build_closing(self):
        w = QWidget(); v = QVBoxLayout(w)
        h = QHBoxLayout()
        self.cd = OptDate(optional=False)
        h.addWidget(QLabel("Closing date")); h.addWidget(self.cd); h.addStretch(1)
        v.addLayout(h)
        self.cl = DataTable([("Payment mode", "mode", "text"), ("Receipts", "n", "int"), ("Received", "received", "money"), ("Paid out (refunds / reversals)", "paid_out", "money"),
                             ("Expected net", "net", "money")], empty="No money movement on this date.")
        v.addWidget(self.cl)
        self.count_box = QFrame(); self.count_box.setObjectName("card")
        self.count_form = QFormLayout(self.count_box)
        v.addWidget(label("Enter what you actually counted / received per mode to see any difference:"))
        v.addWidget(self.count_box)
        self.cl_total = label("", "h2")
        v.addWidget(self.cl_total)
        b = QHBoxLayout()
        b.addWidget(btn("🖨  Print closing sheet", "navy", slot=self._closing_print)); b.addStretch(1)
        v.addLayout(b)
        v.addStretch(1)
        self.cd.dateChanged.connect(lambda: self._closing_reload())
        self.counted = {}
        return w

    def _closing_reload(self):
        rows = fees.daily_closing(self.app.db, self.app.actor, self.cd.iso())
        self.cl.set_rows(rows)
        while self.count_form.rowCount():
            self.count_form.removeRow(0)
        self.counted = {}
        for r in rows:
            e = MoneyEdit(); e.textChanged.connect(self._closing_diff)
            self.counted[r["mode"]] = (e, r["net"])
            self.count_form.addRow(f"{r['mode']} — expected {money.fmt(r['net'], 'Rs.')}", e)
        self._closing_diff()

    def _closing_diff(self):
        exp = sum(n for _, n in self.counted.values())
        got = sum(e.paise() for e, _ in self.counted.values() if e.text())
        any_in = any(e.text() for e, _ in self.counted.values())
        self.cl_total.setText(f"Expected total {money.fmt(exp, 'Rs.')}" + (f"   •   Counted {money.fmt(got, 'Rs.')}   •   Difference {money.fmt(got - exp, 'Rs.')}" if any_in else ""))

    def _closing_print(self):
        rows = fees.daily_closing(self.app.db, self.app.actor, self.cd.iso())
        data = []
        for r in rows:
            e, net = self.counted.get(r["mode"], (None, 0))
            c = e.paise() if e is not None and e.text() else None
            data.append([r["mode"], r["n"], r["received"], r["paid_out"], r["net"], c, None if c is None else c - r["net"]])
        ds = Dataset("Daily collection closing", [("Mode", "text"), ("Receipts", "int"), ("Received", "money"), ("Paid out", "money"), ("Expected net", "money"), ("Counted", "money"), ("Difference", "money")],
                     data, "Date: " + self.cd.date().toString("dd-MM-yyyy"), totals=["Total", sum(r[1] for r in data), sum(r[2] for r in data), sum(r[3] for r in data), sum(r[4] for r in data), None, None])
        ds.notes = ["Cashier signature: ____________________      Checked by: ____________________"]
        self.app.preview(self, docs.dataset(self.app.db, ds, landscape=False), "Closing sheet", save_name="daily_closing")

    # ------------------------------------------------------------- reminders
    def _build_reminders(self):
        w = QWidget(); v = QVBoxLayout(w)
        h = QHBoxLayout()
        self.rm_to = OptDate(optional=False)
        self.rm_to.setDate(self.rm_to.date().addDays(15))
        h.addWidget(QLabel("Show installments due up to")); h.addWidget(self.rm_to)
        h.addWidget(btn("Refresh", slot=self._rm_reload)); h.addStretch(1)
        v.addLayout(h)
        self.rm = DataTable([("Due date", "due_date", "date"), ("Adm. No.", "adm", "text"), ("Student", "name", "text"), ("Mobile", "mobile", "text"), ("Course", "course", "text"),
                             ("Installment", "label", "text"), ("Due", "due", "money"), ("Status", "state", "text")], page_size=100, search=True, empty="No installments are due.")
        v.addWidget(self.rm, 1)
        self.msg = QPlainTextEdit(); self.msg.setPlaceholderText("Select a student above to prepare a reminder message (nothing is sent automatically).")
        self.msg.setFixedHeight(120)
        v.addWidget(self.msg)
        b = QHBoxLayout()
        b.addWidget(btn("📋  Copy message", slot=lambda: QGuiApplication.clipboard().setText(self.msg.toPlainText())))
        b.addWidget(btn("WhatsApp (opens WhatsApp Web / app)", "primary", slot=self._rm_wa))
        b.addWidget(btn("🖨  Print list", slot=self._rm_print))
        b.addStretch(1)
        v.addLayout(b)
        self.rm.selected.connect(self._rm_sel)
        self.rm_to.dateChanged.connect(lambda: self._rm_reload())
        self._rm_rows = []
        return w

    def _rm_reload(self):
        ok, ds = safe(self, reports.run, self.app.db, self.app.actor, "installments_due", date_to=self.rm_to.iso())
        if not ok:
            return
        rows = [dict(due_date=r[0], adm=r[1], name=r[2], mobile=r[3], course=r[4], label=r[5], due=r[8], state=r[9]) for r in ds.rows]
        self._rm_rows = rows
        self.rm.color_fn = lambda r: "#fee2e2" if str(r["state"]).startswith("Overdue") else None
        self.rm.set_rows(rows)
        self._rm_ds = ds

    def _rm_sel(self, r):
        if not r:
            return
        mine = [x for x in self._rm_rows if x["adm"] == r["adm"]]
        self.msg.setPlainText(docs.reminder_text(self.app.db, r["name"], [dict(label=x["label"], due=x["due"], due_date=x["due_date"]) for x in mine]))

    def _rm_wa(self):
        r = self.rm.current()
        if not r or not r["mobile"]:
            return warn(self, "Select a student who has a mobile number.")
        num = "".join(ch for ch in r["mobile"] if ch.isdigit())[-10:]
        QDesktopServices.openUrl(QUrl(f"https://wa.me/91{num}?text={quote(self.msg.toPlainText())}"))

    def _rm_print(self):
        if hasattr(self, "_rm_ds"):
            self.app.preview(self, docs.dataset(self.app.db, self._rm_ds), "Installment due list", save_name="installments_due")
