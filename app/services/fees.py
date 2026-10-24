"""Fee accounting. Integer paise everywhere.

Model
  net payable   = sum(active charges) - sum(active discounts/scholarships)
  paid          = sum(payment_allocations) of the enrollment's installments      (signed)
  outstanding   = net payable - paid
  advance       = sum(payments.advance_paise) of the student                      (signed; NOT counted against outstanding)
Payments form a SIGNED ledger: payment +, reversal -, refund -, 'advance_applied' moves money from advance to fees (cash 0).
A posted row is never edited or deleted: mistakes are corrected by a reversal row that points to the original.
"""
import calendar
import uuid
from datetime import date, datetime, timedelta

from app.database.db import Database
from app.security.permissions import Actor, ValidationError, require
from app.services import catalog, settings, students as stu
from app.services.audit import log


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def split_amount(total: int, n: int) -> list[int]:
    """Equal installments in whole paise; the last one absorbs the rounding remainder."""
    if n < 1:
        raise ValidationError("At least one installment is required.")
    base = total // n
    parts = [base] * n
    parts[-1] += total - base * n
    return parts


def schedule_dates(first_due: date, n: int, every_months: int) -> list[date]:
    return [add_months(first_due, i * every_months) for i in range(n)]


# ---------------------------------------------------------------- balances
def gross_charges(db: Database, eid: int) -> int:
    return db.val("SELECT COALESCE(SUM(amount_paise),0) FROM fee_charges WHERE enrollment_id=? AND voided=0", (eid,), 0)


def total_adjustments(db: Database, eid: int, kind: str | None = None) -> int:
    return db.val("SELECT COALESCE(SUM(amount_paise),0) FROM fee_adjustments WHERE enrollment_id=? AND voided=0" + (" AND kind=?" if kind else ""),
                  (eid, kind) if kind else (eid,), 0)


def net_payable(db: Database, eid: int) -> int:
    return gross_charges(db, eid) - total_adjustments(db, eid)


def paid_amount(db: Database, eid: int) -> int:
    return db.val("SELECT COALESCE(SUM(a.amount_paise),0) FROM payment_allocations a JOIN installments i ON i.id=a.installment_id WHERE i.enrollment_id=?", (eid,), 0)


def enrollment_summary(db: Database, eid: int) -> dict:
    gross = gross_charges(db, eid)
    disc = total_adjustments(db, eid, "discount")
    schol = total_adjustments(db, eid, "scholarship")
    paid = paid_amount(db, eid)
    net = gross - disc - schol
    return {"gross": gross, "discount": disc, "scholarship": schol, "net": net, "paid": paid, "outstanding": net - paid}


def advance_balance(db: Database, student_id: int) -> int:
    return db.val("SELECT COALESCE(SUM(advance_paise),0) FROM payments WHERE student_id=?", (student_id,), 0)


def student_summary(db: Database, student_id: int) -> dict:
    tot = {"gross": 0, "discount": 0, "scholarship": 0, "net": 0, "paid": 0, "outstanding": 0}
    for e in db.q("SELECT id FROM enrollments WHERE student_id=?", (student_id,)):
        s = enrollment_summary(db, e["id"])
        for k in tot:
            tot[k] += s[k]
    tot["advance"] = advance_balance(db, student_id)
    tot["refunded"] = -db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE student_id=? AND kind='refund'", (student_id,), 0)
    tot["received"] = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE student_id=? AND kind IN ('payment','reversal','refund')", (student_id,), 0)
    return tot


def installments_status(db: Database, eid: int, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for r in db.q("SELECT * FROM installments WHERE enrollment_id=? ORDER BY due_date, seq", (eid,)):
        paid = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payment_allocations WHERE installment_id=?", (r["id"],), 0)
        due = r["amount_paise"] - paid
        if due <= 0:
            st = "Paid"
        elif r["due_date"] < today.isoformat():
            st = "Overdue"
        elif paid > 0:
            st = "Partial"
        else:
            st = "Pending"
        out.append({"id": r["id"], "seq": r["seq"], "label": r["label"], "due_date": r["due_date"], "amount": r["amount_paise"], "paid": paid, "due": max(due, 0), "status": st})
    return out


# ---------------------------------------------------------------- enrolment with fees
def _discount_limit_check(db: Database, actor: Actor, gross: int, total_discount: int):
    if total_discount <= 0:
        return
    if actor.can("fees.discount_any"):
        return
    if not actor.can("fees.discount"):
        raise ValidationError("You do not have permission to give a discount.")
    limit = float(settings.get(db, "discount_limit_percent") or 0)
    if gross <= 0 or total_discount * 100 > gross * limit:
        raise ValidationError(f"Discounts above {limit:g}% of the fee need an administrator's approval.")


def enroll_with_fees(db: Database, actor: Actor, student_id: int, course_id: int, *, batch_id=None, session_id=None, subject_ids=(),
                     course_fee: int | None = None, admission_fee: int | None = None, registration_fee: int | None = None,
                     discount: int = 0, scholarship: int = 0, discount_reason: str = "", installments: int | None = None,
                     first_due: date | None = None, every_months: int = 1, start_date: str | None = None) -> int:
    """Enrol a student and create their fee structure in one atomic step. Defaults come from the course."""
    require(actor, "students.edit")
    require(actor, "fees.charges")
    c = catalog.course(db, course_id)
    if c is None:
        raise ValidationError("Course not found.")
    course_fee = c["fee_paise"] if course_fee is None else course_fee
    admission_fee = c["admission_fee_paise"] if admission_fee is None else admission_fee
    registration_fee = c["registration_fee_paise"] if registration_fee is None else registration_fee
    installments = installments or c["default_installments"] or 1
    if min(course_fee, admission_fee, registration_fee, discount, scholarship) < 0:
        raise ValidationError("Amounts cannot be negative.")
    gross = course_fee + admission_fee + registration_fee
    if discount + scholarship > gross:
        raise ValidationError("Discount and scholarship cannot be more than the total fee.")
    upfront = admission_fee + registration_fee
    tuition_net = course_fee - discount - scholarship
    if tuition_net < 0:
        raise ValidationError("Discount and scholarship cannot be more than the course fee (admission/registration fees are due separately).")
    _discount_limit_check(db, actor, gross, discount + scholarship)
    first_due = first_due or date.today()
    with db.tx():
        eid = stu.add_enrollment(db, actor, student_id, course_id, batch_id=batch_id, session_id=session_id, subject_ids=subject_ids, start_date=start_date)
        for kind, desc, amt in (("course", f"{c['name']} fee", course_fee), ("admission", "Admission fee", admission_fee), ("registration", "Registration fee", registration_fee)):
            if amt > 0:
                db.x("INSERT INTO fee_charges(student_id,enrollment_id,kind,description,amount_paise,created_at,created_by) VALUES (?,?,?,?,?,?,?)",
                     (student_id, eid, kind, desc, amt, _now(), actor.id))
        for kind, amt in (("discount", discount), ("scholarship", scholarship)):
            if amt > 0:
                db.x("INSERT INTO fee_adjustments(student_id,enrollment_id,kind,amount_paise,reason,approved_by,created_at) VALUES (?,?,?,?,?,?,?)",
                     (student_id, eid, kind, amt, discount_reason, actor.id, _now()))
        seq = 1
        if upfront > 0:
            db.x("INSERT INTO installments(enrollment_id,student_id,seq,label,due_date,amount_paise) VALUES (?,?,?,?,?,?)",
                 (eid, student_id, seq, "Admission & registration", (start_date or date.today().isoformat()), upfront))
            seq += 1
        if tuition_net > 0 or seq == 1:
            amounts = split_amount(tuition_net, installments)
            for i, (amt, dt) in enumerate(zip(amounts, schedule_dates(first_due, installments, every_months)), 1):
                db.x("INSERT INTO installments(enrollment_id,student_id,seq,label,due_date,amount_paise) VALUES (?,?,?,?,?,?)",
                     (eid, student_id, seq, f"Installment {i} of {installments}" if installments > 1 else "Course fee", dt.isoformat(), amt))
                seq += 1
        log(db, actor.id, "fee_structure", "enrollment", eid, f"gross={gross} disc={discount} schol={scholarship} inst={installments}")
    return eid


def rebalance_installments(db: Database, eid: int):
    """Keep sum(installments) == net payable after a charge/discount change. Paid money is never taken back."""
    net = net_payable(db, eid)
    insts = db.q("SELECT * FROM installments WHERE enrollment_id=? ORDER BY due_date, seq", (eid,))
    total = sum(r["amount_paise"] for r in insts)
    diff = net - total
    if diff == 0:
        return
    paid = {r["id"]: db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payment_allocations WHERE installment_id=?", (r["id"],), 0) for r in insts}
    if diff > 0:
        target = next((r for r in reversed(insts) if r["amount_paise"] - paid[r["id"]] > 0), None)
        if target is not None:
            db.x("UPDATE installments SET amount_paise = amount_paise + ? WHERE id=?", (diff, target["id"]))
        else:
            sid = db.val("SELECT student_id FROM enrollments WHERE id=?", (eid,))
            seq = db.val("SELECT COALESCE(MAX(seq),0)+1 FROM installments WHERE enrollment_id=?", (eid,))
            db.x("INSERT INTO installments(enrollment_id,student_id,seq,label,due_date,amount_paise) VALUES (?,?,?,?,?,?)",
                 (eid, sid, seq, "Additional charges", date.today().isoformat(), diff))
    else:
        need = -diff
        for r in reversed(insts):
            if need == 0:
                break
            take = min(r["amount_paise"] - paid[r["id"]], need)
            if take > 0:
                db.x("UPDATE installments SET amount_paise = amount_paise - ? WHERE id=?", (take, r["id"]))
                need -= take
        if need > 0:
            raise ValidationError("This change would make the student overpaid. Refund the extra money first.")
        for r in insts:                                   # tidy up empty unpaid installments (keep at least one)
            n_left = db.val("SELECT COUNT(*) FROM installments WHERE enrollment_id=?", (eid,))
            cur = db.one("SELECT amount_paise FROM installments WHERE id=?", (r["id"],))
            if n_left > 1 and cur and cur["amount_paise"] == 0 and paid[r["id"]] == 0:
                db.x("DELETE FROM installments WHERE id=?", (r["id"],))


def add_charge(db: Database, actor: Actor, eid: int, kind: str, description: str, amount: int) -> int:
    require(actor, "fees.charges")
    if amount <= 0:
        raise ValidationError("Charge amount must be more than zero.")
    if not (description or "").strip():
        raise ValidationError("Description is required.")
    with db.tx():
        e = db.one("SELECT student_id FROM enrollments WHERE id=?", (eid,))
        cid = db.x("INSERT INTO fee_charges(student_id,enrollment_id,kind,description,amount_paise,created_at,created_by) VALUES (?,?,?,?,?,?,?)",
                   (e["student_id"], eid, kind, description.strip(), amount, _now(), actor.id)).lastrowid
        rebalance_installments(db, eid)
        log(db, actor.id, "charge_add", "enrollment", eid, f"{description} {amount}")
    return cid


def void_charge(db: Database, actor: Actor, charge_id: int, reason: str):
    require(actor, "fees.charges")
    if not (reason or "").strip():
        raise ValidationError("A reason is required.")
    with db.tx():
        c = db.one("SELECT * FROM fee_charges WHERE id=?", (charge_id,))
        if c is None or c["voided"]:
            raise ValidationError("Charge not found.")
        db.x("UPDATE fee_charges SET voided=1, void_reason=? WHERE id=?", (reason.strip(), charge_id))
        if net_payable(db, c["enrollment_id"]) < paid_amount(db, c["enrollment_id"]):
            raise ValidationError("Cancelling this charge would leave the student overpaid. Refund the extra money first.")
        rebalance_installments(db, c["enrollment_id"])
        log(db, actor.id, "charge_void", "enrollment", c["enrollment_id"], f"{c['description']} {c['amount_paise']}: {reason}")


def add_adjustment(db: Database, actor: Actor, eid: int, kind: str, amount: int, reason: str = "") -> int:
    if kind not in ("discount", "scholarship"):
        raise ValidationError("Kind must be discount or scholarship.")
    require(actor, "fees.discount")
    if amount <= 0:
        raise ValidationError("Amount must be more than zero.")
    with db.tx():
        e = db.one("SELECT student_id FROM enrollments WHERE id=?", (eid,))
        gross = gross_charges(db, eid)
        _discount_limit_check(db, actor, gross, total_adjustments(db, eid) + amount)
        aid = db.x("INSERT INTO fee_adjustments(student_id,enrollment_id,kind,amount_paise,reason,approved_by,created_at) VALUES (?,?,?,?,?,?,?)",
                   (e["student_id"], eid, kind, amount, reason.strip(), actor.id, _now())).lastrowid
        if net_payable(db, eid) < paid_amount(db, eid):
            raise ValidationError("This discount would leave the student overpaid. Make a refund instead.")
        if net_payable(db, eid) < 0:
            raise ValidationError("Discount cannot be more than the total fee.")
        rebalance_installments(db, eid)
        log(db, actor.id, "adjustment_add", "enrollment", eid, f"{kind} {amount} {reason}")
    return aid


def void_adjustment(db: Database, actor: Actor, adj_id: int, reason: str):
    require(actor, "fees.discount_any")
    if not (reason or "").strip():
        raise ValidationError("A reason is required.")
    with db.tx():
        a = db.one("SELECT * FROM fee_adjustments WHERE id=?", (adj_id,))
        if a is None or a["voided"]:
            raise ValidationError("Adjustment not found.")
        db.x("UPDATE fee_adjustments SET voided=1, void_reason=? WHERE id=?", (reason.strip(), adj_id))
        rebalance_installments(db, a["enrollment_id"])
        log(db, actor.id, "adjustment_void", "enrollment", a["enrollment_id"], f"{a['kind']} {a['amount_paise']}: {reason}")


def write_off_balance(db: Database, actor: Actor, eid: int, reason: str) -> int:
    """For a student who leaves: waive what is still unpaid (recorded as an approved discount)."""
    require(actor, "fees.discount_any")
    out = enrollment_summary(db, eid)["outstanding"]
    if out <= 0:
        raise ValidationError("Nothing is outstanding.")
    return add_adjustment(db, actor, eid, "discount", out, f"Balance written off: {reason}")


# ---------------------------------------------------------------- payments
def _alloc_fifo(db: Database, eid_list: list[int], amount: int) -> list[tuple[int, int]]:
    out, left = [], amount
    for eid in eid_list:
        for i in installments_status(db, eid):
            if left <= 0:
                break
            take = min(i["due"], left)
            if take > 0:
                out.append((i["id"], take))
                left -= take
    return out if left == 0 else out


def _balance_total(db: Database, student_id: int) -> int:
    return student_summary(db, student_id)["outstanding"]


def _new_txn_id() -> str:
    return "TXN-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6].upper()


def _insert_payment(db: Database, actor: Actor, *, student_id, enrollment_id, kind, amount, allocated, advance, mode, reference, pay_date, reason="", note="",
                    reversal_of=None, status="posted", idem_key=None, receipt=True) -> int:
    before = _balance_total(db, student_id)
    receipt_no = None
    if receipt:
        receipt_no = settings.format_number(settings.get(db, "receipt_fmt"), db.next_counter("receipt"), date.fromisoformat(pay_date))
    pid = db.x("INSERT INTO payments(txn_id,receipt_no,idem_key,student_id,enrollment_id,kind,amount_paise,allocated_paise,advance_paise,mode,reference,paid_at,pay_date,"
               "cashier_id,cashier_name,status,reversal_of,reason,note,created_at,balance_before_paise) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (_new_txn_id(), receipt_no, idem_key, student_id, enrollment_id, kind, amount, allocated, advance, mode, (reference or "").strip(), _now(), pay_date,
                actor.id, actor.full_name or actor.username, status, reversal_of, reason, note, _now(), before)).lastrowid
    return pid


def _finish_balance(db: Database, pid: int, student_id: int):
    db.x("UPDATE payments SET balance_after_paise=? WHERE id=?", (_balance_total(db, student_id), pid))


def collect_payment(db: Database, actor: Actor, student_id: int, amount: int, mode: str, *, enrollment_id: int | None = None,
                    installment_ids: list[int] | None = None, as_advance: int = 0, reference: str = "", note: str = "", idem_key: str | None = None,
                    pay_date: str | None = None, confirm_duplicate: bool = False) -> int:
    """Record money received.

    * Allocation: to the given installments (in that order), otherwise oldest-due first within the enrollment
      (or all the student's enrollments when none is given).
    * `as_advance` is the part of `amount` the family wants to keep as ADVANCE (not applied to any fee).
    * Money that cannot be allocated and is not marked as advance is rejected - nothing is silently parked.
    """
    require(actor, "fees.collect")
    if not isinstance(amount, int) or amount <= 0:
        raise ValidationError("Payment amount must be more than zero.")
    if as_advance < 0 or as_advance > amount:
        raise ValidationError("Advance part must be between zero and the amount received.")
    if mode not in catalog.payment_modes(db):
        raise ValidationError("Choose a valid payment mode.")
    today = date.today().isoformat()
    if pay_date and pay_date != today:
        require(actor, "fees.charges")
        if pay_date > today:
            raise ValidationError("Payment date cannot be in the future.")
    pay_date = pay_date or today
    if idem_key:
        dup = db.one("SELECT id FROM payments WHERE idem_key=?", (idem_key,))
        if dup:
            return dup["id"]
    with db.tx():
        if db.one("SELECT 1 FROM students WHERE id=?", (student_id,)) is None:
            raise ValidationError("Student not found.")
        since = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
        if not confirm_duplicate and not idem_key and db.one(
                "SELECT 1 FROM payments WHERE student_id=? AND amount_paise=? AND mode=? AND kind='payment' AND created_at>=?", (student_id, amount, mode, since)):
            raise ValidationError("An identical payment was recorded a few seconds ago. If this is really a second payment, confirm it.")
        to_fees = amount - as_advance
        allocs: list[tuple[int, int]] = []
        if to_fees > 0:
            if installment_ids:
                left = to_fees
                for iid in installment_ids:
                    row = next((i for e in db.q("SELECT id FROM enrollments WHERE student_id=?", (student_id,)) for i in installments_status(db, e["id"]) if i["id"] == iid), None)
                    if row is None:
                        raise ValidationError("Selected installment does not belong to this student.")
                    take = min(row["due"], left)
                    if take > 0:
                        allocs.append((iid, take)); left -= take
                if left > 0:
                    raise ValidationError("The amount is more than the selected installments need. Pick more installments or record the extra as advance.")
            else:
                eids = [enrollment_id] if enrollment_id else [e["id"] for e in db.q("SELECT id FROM enrollments WHERE student_id=? ORDER BY id", (student_id,))]
                allocs = _alloc_fifo(db, eids, to_fees)
                got = sum(a for _, a in allocs)
                if got < to_fees:
                    raise ValidationError(f"Only {got / 100:.2f} is due. Reduce the amount or record the extra {(to_fees - got) / 100:.2f} as an advance.")
        allocated = sum(a for _, a in allocs)
        pid = _insert_payment(db, actor, student_id=student_id, enrollment_id=enrollment_id, kind="payment", amount=amount, allocated=allocated,
                              advance=amount - allocated, mode=mode, reference=reference, pay_date=pay_date, note=note, idem_key=idem_key)
        for iid, a in allocs:
            db.x("INSERT INTO payment_allocations(payment_id,installment_id,amount_paise) VALUES (?,?,?)", (pid, iid, a))
        _finish_balance(db, pid, student_id)
        log(db, actor.id, "payment", "payment", pid, f"student {student_id} amount {amount} {mode} adv {amount - allocated}")
    return pid


def apply_advance(db: Database, actor: Actor, student_id: int, amount: int, *, enrollment_id: int | None = None, installment_ids: list[int] | None = None) -> int:
    """Move money from the student's advance balance onto fees that are due (explicit step, zero cash movement)."""
    require(actor, "fees.collect")
    if amount <= 0:
        raise ValidationError("Amount must be more than zero.")
    with db.tx():
        if amount > advance_balance(db, student_id):
            raise ValidationError(f"Only {advance_balance(db, student_id) / 100:.2f} advance is available.")
        eids = [enrollment_id] if enrollment_id else [e["id"] for e in db.q("SELECT id FROM enrollments WHERE student_id=? ORDER BY id", (student_id,))]
        if installment_ids:
            allocs, left = [], amount
            for iid in installment_ids:
                row = next((i for e in eids for i in installments_status(db, e) if i["id"] == iid), None)
                if row is None:
                    raise ValidationError("Selected installment is not available.")
                take = min(row["due"], left)
                if take > 0:
                    allocs.append((iid, take)); left -= take
        else:
            allocs = _alloc_fifo(db, eids, amount)
        got = sum(a for _, a in allocs)
        if got != amount:
            raise ValidationError(f"Only {got / 100:.2f} is due, so that is the most advance that can be applied.")
        pid = _insert_payment(db, actor, student_id=student_id, enrollment_id=enrollment_id, kind="advance_applied", amount=0, allocated=amount, advance=-amount,
                              mode="Advance", reference="", pay_date=date.today().isoformat(), receipt=False)
        for iid, a in allocs:
            db.x("INSERT INTO payment_allocations(payment_id,installment_id,amount_paise) VALUES (?,?,?)", (pid, iid, a))
        _finish_balance(db, pid, student_id)
        log(db, actor.id, "advance_apply", "payment", pid, f"student {student_id} {amount}")
    return pid


def refund(db: Database, actor: Actor, student_id: int, amount: int, mode: str, *, from_advance: bool, enrollment_id: int | None = None, reason: str = "",
           reference: str = "") -> int:
    require(actor, "fees.refund")
    if amount <= 0:
        raise ValidationError("Refund amount must be more than zero.")
    if not (reason or "").strip():
        raise ValidationError("A reason is required for a refund.")
    if mode not in catalog.payment_modes(db):
        raise ValidationError("Choose a valid payment mode.")
    with db.tx():
        if from_advance:
            if amount > advance_balance(db, student_id):
                raise ValidationError(f"Only {advance_balance(db, student_id) / 100:.2f} advance is available to refund.")
            pid = _insert_payment(db, actor, student_id=student_id, enrollment_id=enrollment_id, kind="refund", amount=-amount, allocated=0, advance=-amount,
                                  mode=mode, reference=reference, pay_date=date.today().isoformat(), reason=reason.strip())
            _finish_balance(db, pid, student_id)
        else:
            if not enrollment_id:
                raise ValidationError("Choose the course the refund is for.")
            if amount > paid_amount(db, enrollment_id):
                raise ValidationError(f"Only {paid_amount(db, enrollment_id) / 100:.2f} has been paid for this course.")
            pid = _insert_payment(db, actor, student_id=student_id, enrollment_id=enrollment_id, kind="refund", amount=-amount, allocated=-amount, advance=0,
                                  mode=mode, reference=reference, pay_date=date.today().isoformat(), reason=reason.strip())
            left = amount
            for i in reversed(installments_status(db, enrollment_id)):          # take back from the latest installments first
                take = min(i["paid"], left)
                if take > 0:
                    db.x("INSERT INTO payment_allocations(payment_id,installment_id,amount_paise) VALUES (?,?,?)", (pid, i["id"], -take))
                    left -= take
            _finish_balance(db, pid, student_id)
        log(db, actor.id, "refund", "payment", pid, f"student {student_id} {amount} {'advance' if from_advance else 'fees'}: {reason}")
    return pid


def reverse_payment(db: Database, actor: Actor, payment_id: int, reason: str) -> int:
    """Controlled cancellation. The original receipt stays on record (status 'reversed'); a mirror entry cancels its effect."""
    require(actor, "fees.reverse")
    if not (reason or "").strip():
        raise ValidationError("A reason is required to cancel a receipt.")
    with db.tx():
        p = db.one("SELECT * FROM payments WHERE id=?", (payment_id,))
        if p is None:
            raise ValidationError("Receipt not found.")
        if p["status"] != "posted" or p["kind"] == "reversal":
            raise ValidationError("This entry has already been cancelled or cannot be cancelled.")
        rid = _insert_payment(db, actor, student_id=p["student_id"], enrollment_id=p["enrollment_id"], kind="reversal", amount=-p["amount_paise"],
                              allocated=-p["allocated_paise"], advance=-p["advance_paise"], mode=p["mode"], reference=p["reference"],
                              pay_date=date.today().isoformat(), reason=reason.strip(), note=f"Reversal of {p['receipt_no'] or p['txn_id']}",
                              reversal_of=payment_id, status="reversal", receipt=False)
        for a in db.q("SELECT * FROM payment_allocations WHERE payment_id=?", (payment_id,)):
            db.x("INSERT INTO payment_allocations(payment_id,installment_id,amount_paise) VALUES (?,?,?)", (rid, a["installment_id"], -a["amount_paise"]))
        db.x("UPDATE payments SET status='reversed' WHERE id=?", (payment_id,))
        # safety: reversing must not leave negative advance or over-refunded installments
        if advance_balance(db, p["student_id"]) < 0:
            raise ValidationError("The advance from this receipt has already been used or refunded. Cancel that entry first.")
        bad = db.one("SELECT i.id FROM installments i WHERE i.student_id=? AND (SELECT COALESCE(SUM(amount_paise),0) FROM payment_allocations WHERE installment_id=i.id) < 0", (p["student_id"],))
        if bad:
            raise ValidationError("This entry cannot be cancelled because later refunds depend on it. Cancel the refund first.")
        _finish_balance(db, rid, p["student_id"])
        log(db, actor.id, "payment_reverse", "payment", payment_id, f"{p['receipt_no']}: {reason}")
    return rid


# ---------------------------------------------------------------- queries
def get_payment(db: Database, payment_id: int):
    return db.one("SELECT * FROM payments WHERE id=?", (payment_id,))


def payments_for_student(db: Database, actor: Actor, student_id: int):
    require(actor, "fees.view")
    return db.q("SELECT p.*, c.name AS course FROM payments p LEFT JOIN enrollments e ON e.id=p.enrollment_id LEFT JOIN courses c ON c.id=e.course_id "
                "WHERE p.student_id=? ORDER BY p.id DESC", (student_id,))


def find_receipts(db: Database, actor: Actor, text: str = "", date_from: str | None = None, date_to: str | None = None, limit: int = 300):
    require(actor, "fees.view")
    where, p = ["p.kind <> 'advance_applied'"], []
    if text:
        like = f"%{text.strip()}%"
        where.append("(p.receipt_no LIKE ? OR p.txn_id LIKE ? OR p.reference LIKE ? OR s.name LIKE ? OR s.admission_no LIKE ? OR s.mobile LIKE ?)")
        p += [like] * 6
    if date_from:
        where.append("p.pay_date >= ?"); p.append(date_from)
    if date_to:
        where.append("p.pay_date <= ?"); p.append(date_to)
    return db.q("SELECT p.*, s.name AS student, s.admission_no FROM payments p JOIN students s ON s.id=p.student_id WHERE " + " AND ".join(where)
                + " ORDER BY p.id DESC LIMIT ?", p + [limit])


def ledger(db: Database, actor: Actor, student_id: int) -> list[dict]:
    """Chronological statement: charges/discounts/payments with a running outstanding balance."""
    require(actor, "fees.view")
    rows = []
    for c in db.q("SELECT c.*, k.name AS course FROM fee_charges c JOIN enrollments e ON e.id=c.enrollment_id JOIN courses k ON k.id=e.course_id WHERE c.student_id=? AND c.voided=0", (student_id,)):
        rows.append({"when": c["created_at"], "type": "Charge", "text": f"{c['description']} ({c['course']})", "debit": c["amount_paise"], "credit": 0, "ref": ""})
    for a in db.q("SELECT * FROM fee_adjustments WHERE student_id=? AND voided=0", (student_id,)):
        rows.append({"when": a["created_at"], "type": a["kind"].title(), "text": a["reason"] or a["kind"].title(), "debit": 0, "credit": a["amount_paise"], "ref": ""})
    for p in db.q("SELECT * FROM payments WHERE student_id=?", (student_id,)):
        if p["allocated_paise"]:
            label = {"payment": "Payment", "refund": "Refund", "reversal": "Reversal", "advance_applied": "Advance applied"}[p["kind"]]
            rows.append({"when": p["created_at"], "type": label, "text": f"{p['mode']} {p['reference']}".strip(), "debit": 0, "credit": p["allocated_paise"], "ref": p["receipt_no"] or p["txn_id"]})
        elif p["advance_paise"]:
            label = "Advance received" if p["advance_paise"] > 0 else "Advance returned"
            rows.append({"when": p["created_at"], "type": label, "text": f"{p['mode']} {p['reference']}".strip(), "debit": 0, "credit": 0, "ref": p["receipt_no"] or p["txn_id"], "advance": p["advance_paise"]})
    rows.sort(key=lambda r: r["when"])
    bal = 0
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["balance"] = bal
    return rows


def receipt_data(db: Database, payment_id: int) -> dict:
    """Everything needed to print a saved receipt. Reads ONLY saved records."""
    p = db.one("SELECT * FROM payments WHERE id=?", (payment_id,))
    if p is None:
        raise ValidationError("Receipt not found.")
    s = db.one("SELECT * FROM students WHERE id=?", (p["student_id"],))
    enr = None
    if p["enrollment_id"]:
        enr = db.one("SELECT e.id, c.name AS course, b.name AS batch, b.timing FROM enrollments e JOIN courses c ON c.id=e.course_id LEFT JOIN batches b ON b.id=e.batch_id WHERE e.id=?", (p["enrollment_id"],))
    allocs = db.q("SELECT a.amount_paise, i.label, i.due_date, c.name AS course FROM payment_allocations a JOIN installments i ON i.id=a.installment_id "
                  "JOIN enrollments e ON e.id=i.enrollment_id JOIN courses c ON c.id=e.course_id WHERE a.payment_id=? ORDER BY i.due_date, i.seq", (payment_id,))
    orig = db.one("SELECT receipt_no FROM payments WHERE id=?", (p["reversal_of"],)) if p["reversal_of"] else None
    return {"payment": dict(p), "student": dict(s), "enrollment": dict(enr) if enr else None, "allocations": [dict(a) for a in allocs],
            "reversal_of_receipt": orig["receipt_no"] if orig else None,
            "reversed_by_txn": (db.val("SELECT txn_id FROM payments WHERE reversal_of=?", (payment_id,)) if p["status"] == "reversed" else None)}


def daily_closing(db: Database, actor: Actor, day: str) -> list[dict]:
    """Cash-drawer style summary by payment mode for one day (signed: refunds/reversals reduce it)."""
    require(actor, "fees.view")
    rows = db.q("SELECT mode, SUM(CASE WHEN amount_paise>0 THEN amount_paise ELSE 0 END) AS received, "
                "SUM(CASE WHEN amount_paise<0 THEN -amount_paise ELSE 0 END) AS paid_out, SUM(amount_paise) AS net, COUNT(*) AS n "
                "FROM payments WHERE pay_date=? AND kind IN ('payment','refund','reversal') GROUP BY mode ORDER BY mode", (day,))
    return [dict(r) for r in rows]
