from datetime import date

import pytest
from helpers import make_user

from app.security.permissions import PermissionDenied, ValidationError
from app.services import attendance, backup, catalog, fees, students
from app.utils.money import to_paise as R


@pytest.fixture()
def enrolled(ctx, admin, student, course_id):
    eid = fees.enroll_with_fees(ctx.db, admin, student, course_id, course_fee=R(12000), admission_fee=R(500), registration_fee=R(300),
                                discount=R(1000), installments=3, first_due=date(2026, 11, 1), every_months=1, discount_reason="Sibling")
    return eid


def test_fee_structure_known_numbers(ctx, enrolled):
    s = fees.enrollment_summary(ctx.db, enrolled)
    assert s == {"gross": R(12800), "discount": R(1000), "scholarship": 0, "net": R(11800), "paid": 0, "outstanding": R(11800)}
    ins = fees.installments_status(ctx.db, enrolled)
    assert [i["amount"] for i in ins] == [R(800), 366666, 366666, 366668]           # remainder paise land on the last one
    assert sum(i["amount"] for i in ins) == s["net"]
    assert [i["due_date"] for i in ins[1:]] == ["2026-11-01", "2026-12-01", "2027-01-01"]


def test_partial_payments_installments_and_receipts(ctx, admin, student, enrolled):
    p1 = fees.collect_payment(ctx.db, admin, student, R(800), "Cash", enrollment_id=enrolled)
    ins = fees.installments_status(ctx.db, enrolled)
    assert ins[0]["status"] == "Paid" and ins[1]["paid"] == 0
    p2 = fees.collect_payment(ctx.db, admin, student, R(5000), "UPI", enrollment_id=enrolled, reference="UTR123")
    ins = fees.installments_status(ctx.db, enrolled)
    assert ins[1]["paid"] == 366666 and ins[2]["paid"] == R(5000) - 366666           # FIFO spill into next installment
    assert fees.enrollment_summary(ctx.db, enrolled)["outstanding"] == R(11800) - R(5800)
    r1, r2 = fees.get_payment(ctx.db, p1), fees.get_payment(ctx.db, p2)
    assert r1["receipt_no"] != r2["receipt_no"] and r1["txn_id"] != r2["txn_id"]
    assert r2["balance_before_paise"] == R(11800) - R(800) and r2["balance_after_paise"] == R(11800) - R(5800)
    # pay a specific later installment
    last = ins[3]["id"]
    fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled, installment_ids=[last])
    assert fees.installments_status(ctx.db, enrolled)[3]["paid"] == R(500)
    with pytest.raises(ValidationError):
        fees.collect_payment(ctx.db, admin, student, R(100000), "Cash", enrollment_id=enrolled)      # more than due, not advance
    for bad in (0, -5):
        with pytest.raises(ValidationError):
            fees.collect_payment(ctx.db, admin, student, bad, "Cash")
    with pytest.raises(ValidationError):
        fees.collect_payment(ctx.db, admin, student, R(10), "Bitcoin", enrollment_id=enrolled)


def test_full_payment_clears_balance_and_overpay_goes_to_advance_only_when_chosen(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(11800), "Bank Transfer", enrollment_id=enrolled)
    assert fees.enrollment_summary(ctx.db, enrolled)["outstanding"] == 0
    assert all(i["status"] == "Paid" for i in fees.installments_status(ctx.db, enrolled))
    with pytest.raises(ValidationError):
        fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled)           # nothing due
    pid = fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled, as_advance=R(500))
    assert fees.advance_balance(ctx.db, student) == R(500)
    assert fees.get_payment(ctx.db, pid)["allocated_paise"] == 0


def test_advance_is_separate_then_explicitly_applied_without_double_counting(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(2000), "Cash", enrollment_id=enrolled, as_advance=R(2000))
    assert fees.advance_balance(ctx.db, student) == R(2000)
    assert fees.enrollment_summary(ctx.db, enrolled)["outstanding"] == R(11800)                      # advance does NOT reduce the balance by itself
    fees.apply_advance(ctx.db, admin, student, R(1200), enrollment_id=enrolled)
    assert fees.advance_balance(ctx.db, student) == R(800)
    s = fees.enrollment_summary(ctx.db, enrolled)
    assert s["paid"] == R(1200) and s["outstanding"] == R(10600)
    cash = ctx.db.val("SELECT SUM(amount_paise) FROM payments")
    assert cash == R(2000)                                                                           # applying advance moved no cash
    with pytest.raises(ValidationError):
        fees.apply_advance(ctx.db, admin, student, R(900), enrollment_id=enrolled)                   # only 800 left
    # a later normal payment on top
    fees.collect_payment(ctx.db, admin, student, R(1000), "Cash", enrollment_id=enrolled)
    assert fees.enrollment_summary(ctx.db, enrolled)["outstanding"] == R(9600)
    assert fees.advance_balance(ctx.db, student) == R(800)


def test_discount_rules_and_rebalancing(ctx, admin, student, enrolled):
    acct = make_user(ctx, admin, "acc", "accountant")
    with pytest.raises(ValidationError):                                                             # 1000 already of 12800 (7.8%); +1000 -> 15.6% > 10%
        fees.add_adjustment(ctx.db, acct, enrolled, "discount", R(1000), "x")
    fees.add_adjustment(ctx.db, acct, enrolled, "discount", R(200), "small")                          # 9.4% within the limit
    fees.add_adjustment(ctx.db, admin, enrolled, "scholarship", R(1000), "merit")                     # admin may exceed
    s = fees.enrollment_summary(ctx.db, enrolled)
    assert s["net"] == R(12800 - 1000 - 200 - 1000) and s["scholarship"] == R(1000)
    ins = fees.installments_status(ctx.db, enrolled)
    assert sum(i["amount"] for i in ins) == s["net"]                                                 # schedule stays equal to net payable
    with pytest.raises(ValidationError):
        fees.add_adjustment(ctx.db, admin, enrolled, "discount", R(50000), "too much")
    viewer = make_user(ctx, admin, "vw", "viewer")
    with pytest.raises(PermissionDenied):
        fees.add_adjustment(ctx.db, viewer, enrolled, "discount", R(1))
    fees.add_charge(ctx.db, admin, enrolled, "additional", "Exam fee", R(250))
    assert fees.enrollment_summary(ctx.db, enrolled)["net"] == s["net"] + R(250)
    assert sum(i["amount"] for i in fees.installments_status(ctx.db, enrolled)) == s["net"] + R(250)


def test_discount_cannot_make_student_overpaid(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(11800), "Cash", enrollment_id=enrolled)
    with pytest.raises(ValidationError):
        fees.add_adjustment(ctx.db, admin, enrolled, "discount", R(100), "late")
    assert fees.enrollment_summary(ctx.db, enrolled)["net"] == R(11800)                              # rolled back completely


def test_refunds(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(3000), "Cash", enrollment_id=enrolled)
    acct = make_user(ctx, admin, "acc", "accountant")
    with pytest.raises(PermissionDenied):
        fees.refund(ctx.db, acct, student, R(100), "Cash", from_advance=False, enrollment_id=enrolled, reason="x")
    with pytest.raises(ValidationError):
        fees.refund(ctx.db, admin, student, R(100), "Cash", from_advance=False, enrollment_id=enrolled, reason="")
    with pytest.raises(ValidationError):
        fees.refund(ctx.db, admin, student, R(3001), "Cash", from_advance=False, enrollment_id=enrolled, reason="too much")
    fees.refund(ctx.db, admin, student, R(1000), "Cash", from_advance=False, enrollment_id=enrolled, reason="left course")
    assert fees.enrollment_summary(ctx.db, enrolled)["paid"] == R(2000)
    assert fees.student_summary(ctx.db, student)["refunded"] == R(1000)
    fees.collect_payment(ctx.db, admin, student, R(700), "Cash", as_advance=R(700))
    fees.refund(ctx.db, admin, student, R(200), "Cash", from_advance=True, reason="returned")
    assert fees.advance_balance(ctx.db, student) == R(500)
    with pytest.raises(ValidationError):
        fees.refund(ctx.db, admin, student, R(600), "Cash", from_advance=True, reason="more than advance")


def test_reversal_keeps_original_and_restores_balance(ctx, admin, student, enrolled):
    pid = fees.collect_payment(ctx.db, admin, student, R(2500), "Cash", enrollment_id=enrolled)
    receipt = fees.get_payment(ctx.db, pid)["receipt_no"]
    acct = make_user(ctx, admin, "acc", "accountant")
    with pytest.raises(PermissionDenied):
        fees.reverse_payment(ctx.db, acct, pid, "mistake")
    with pytest.raises(ValidationError):
        fees.reverse_payment(ctx.db, admin, pid, "")
    rid = fees.reverse_payment(ctx.db, admin, pid, "entered twice")
    assert fees.enrollment_summary(ctx.db, enrolled)["outstanding"] == R(11800)
    orig = fees.get_payment(ctx.db, pid)
    assert orig["status"] == "reversed" and orig["receipt_no"] == receipt and fees.get_payment(ctx.db, rid)["reversal_of"] == pid
    assert ctx.db.val("SELECT SUM(amount_paise) FROM payments") == 0                                 # cash nets to zero
    with pytest.raises(ValidationError):
        fees.reverse_payment(ctx.db, admin, pid, "again")                                            # cannot reverse twice
    pid2 = fees.collect_payment(ctx.db, admin, student, R(2500), "Cash", enrollment_id=enrolled, confirm_duplicate=True)
    assert fees.get_payment(ctx.db, pid2)["receipt_no"] not in (receipt, None)                       # number never reused
    data = fees.receipt_data(ctx.db, rid)
    assert data["reversal_of_receipt"] == receipt


def test_cannot_reverse_advance_already_used(ctx, admin, student, enrolled):
    pid = fees.collect_payment(ctx.db, admin, student, R(1000), "Cash", enrollment_id=enrolled, as_advance=R(1000))
    fees.apply_advance(ctx.db, admin, student, R(600), enrollment_id=enrolled)
    with pytest.raises(ValidationError):
        fees.reverse_payment(ctx.db, admin, pid, "oops")
    assert fees.get_payment(ctx.db, pid)["status"] == "posted"                                        # rolled back


def test_unique_receipts_and_duplicate_submission_guard(ctx, admin, student, enrolled):
    a = fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled, idem_key="form-1")
    b = fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled, idem_key="form-1")      # double click
    assert a == b and ctx.db.val("SELECT COUNT(*) FROM payments") == 1
    with pytest.raises(ValidationError):                                                                             # identical within seconds, no key
        fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled)
    nums = {fees.get_payment(ctx.db, fees.collect_payment(ctx.db, admin, student, R(100 + i), "Cash", enrollment_id=enrolled))["receipt_no"] for i in range(20)}
    assert len(nums) == 20


def test_failed_payment_leaves_no_partial_records(ctx, admin, student, enrolled, monkeypatch):
    before = ctx.db.val("SELECT COUNT(*) FROM payments"), ctx.db.val("SELECT COUNT(*) FROM payment_allocations"), ctx.db.val("SELECT value FROM counters WHERE name='receipt'", default=0)
    def boom(*a, **k):
        raise RuntimeError("disk error")
    monkeypatch.setattr(fees, "_finish_balance", boom)
    with pytest.raises(RuntimeError):
        fees.collect_payment(ctx.db, admin, student, R(500), "Cash", enrollment_id=enrolled)
    after = ctx.db.val("SELECT COUNT(*) FROM payments"), ctx.db.val("SELECT COUNT(*) FROM payment_allocations"), ctx.db.val("SELECT value FROM counters WHERE name='receipt'", default=0)
    assert before == after                                                                           # all-or-nothing, number not burned


def test_posted_transactions_cannot_be_edited_via_services(ctx):
    names = {n for n in dir(fees) if not n.startswith("_")}
    assert not any(n.startswith(("edit_payment", "update_payment", "delete_payment")) for n in names)


def test_write_off_and_ledger(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(3000), "Cash", enrollment_id=enrolled)
    fees.write_off_balance(ctx.db, admin, enrolled, "left academy")
    s = fees.enrollment_summary(ctx.db, enrolled)
    assert s["outstanding"] == 0 and s["net"] == R(3000)
    led = fees.ledger(ctx.db, admin, student)
    assert led[-1]["balance"] == 0 and any(r["type"] == "Payment" for r in led)


def test_daily_closing_by_mode(ctx, admin, student, enrolled):
    fees.collect_payment(ctx.db, admin, student, R(1000), "Cash", enrollment_id=enrolled)
    fees.collect_payment(ctx.db, admin, student, R(2000), "UPI", enrollment_id=enrolled, reference="U1")
    fees.refund(ctx.db, admin, student, R(300), "Cash", from_advance=False, enrollment_id=enrolled, reason="adj")
    rows = {r["mode"]: r for r in fees.daily_closing(ctx.db, admin, date.today().isoformat())}
    assert rows["Cash"]["received"] == R(1000) and rows["Cash"]["paid_out"] == R(300) and rows["Cash"]["net"] == R(700) and rows["UPI"]["net"] == R(2000)


# ------------------------------------------------------------------ attendance
@pytest.fixture()
def batch(ctx, admin, course_id):
    return catalog.save_batch(ctx.db, admin, course_id=course_id, session_id=catalog.current_session_id(ctx.db), name="Eve", timing="5 PM")


@pytest.fixture()
def three(ctx, admin, course_id, batch):
    ids = []
    for n in ("A One", "B Two", "C Three"):
        sid = students.create(ctx.db, admin, ctx.vault, {"name": n})
        students.add_enrollment(ctx.db, admin, sid, course_id, batch_id=batch)
        ids.append(sid)
    return ids


def test_attendance_mark_correct_percentage(ctx, admin, course_id, batch, three):
    day = date.today().isoformat()
    reg = attendance.register(ctx.db, admin, day, course_id, batch)
    assert [r["name"] for r in reg] == ["A One", "B Two", "C Three"] and all(r["status"] == "" for r in reg)
    r = attendance.save(ctx.db, admin, day, course_id, batch, None, [(three[0], "P", ""), (three[1], "A", "sick"), (three[2], "L", "")])
    assert r == {"created": 3, "changed": 0, "unchanged": 0}
    again = attendance.save(ctx.db, admin, day, course_id, batch, None, [(three[0], "P", "")])
    assert again["unchanged"] == 1 and ctx.db.val("SELECT COUNT(*) FROM attendance") == 3            # no duplicate rows
    op = make_user(ctx, admin, "op", "attendance_operator")
    with pytest.raises(PermissionDenied):
        attendance.save(ctx.db, op, day, course_id, batch, None, [(three[1], "P", "")])             # correcting needs authority
    attendance.save(ctx.db, admin, day, course_id, batch, None, [(three[1], "P", "came late")])
    assert ctx.db.val("SELECT COUNT(*) FROM attendance_audit") == 1
    aud = ctx.db.one("SELECT * FROM attendance_audit")
    assert (aud["old_status"], aud["new_status"]) == ("A", "P")
    with pytest.raises(ValidationError):
        attendance.save(ctx.db, admin, "2999-01-01", course_id, batch, None, [(three[0], "P", "")])
    with pytest.raises(ValidationError):
        attendance.save(ctx.db, admin, day, course_id, batch, None, [(three[0], "ZZ", "")])
    outsider = students.create(ctx.db, admin, ctx.vault, {"name": "Outsider"})
    with pytest.raises(ValidationError):
        attendance.save(ctx.db, admin, day, course_id, batch, None, [(outsider, "P", "")])


def test_attendance_percentage_math_and_holidays(ctx, admin, course_id, batch, three):
    sid = three[0]
    for i, st in enumerate(["P", "P", "A", "L", "H", "LV", "P", "A", "P", "P"]):
        d = f"2026-08-{i + 1:02d}"
        ctx.db.x("INSERT INTO attendance(student_id,att_date,batch_id,subject_id,status,marked_at) VALUES (?,?,?,0,?,'x')", (sid, d, batch, st))
    # counted days: 9 (holiday excluded). present weight: 5 x P + 1 x L = 6 ; A,A,LV = 0  -> 6/9
    assert attendance.percentage(ctx.db, sid) == round(600 / 9, 2)
    assert attendance.percentage(ctx.db, sid, "2026-08-01", "2026-08-02") == 100.0
    assert attendance.percentage(ctx.db, three[1]) is None
    catalog.save_attendance_status(ctx.db, admin, "HD", "Half day", 0.5, True)
    ctx.db.x("INSERT INTO attendance(student_id,att_date,batch_id,subject_id,status,marked_at) VALUES (?,?,?,0,'HD','x')", (three[1], "2026-08-01", batch))
    assert attendance.percentage(ctx.db, three[1]) == 50.0


def test_withdrawn_students_not_listed(ctx, admin, course_id, batch, three):
    students.set_status(ctx.db, admin, three[2], "Withdrawn")
    names = [r["name"] for r in attendance.register(ctx.db, admin, date.today().isoformat(), course_id, batch)]
    assert names == ["A One", "B Two"]


# ------------------------------------------------------------------ backup
def test_backup_verify_and_restore_roundtrip(ctx, admin, student, enrolled, tmp_path):
    fees.collect_payment(ctx.db, admin, student, R(800), "Cash", enrollment_id=enrolled)
    (tmp_path / "ph.jpg").write_bytes(b"jpegdata")
    import os, shutil
    shutil.copy(tmp_path / "ph.jpg", os.path.join(ctx.paths.photos, "p1.jpg"))
    bdir = str(tmp_path / "bk")
    r = backup.create_backup(ctx.db, ctx.paths, bdir, actor=admin)
    assert r["ok"] and os.path.getsize(r["path"]) == r["size"] and backup.verify_backup(r["path"])[0]
    assert len(backup.list_backups(bdir)) == 1
    # change data after the backup, then restore
    students.create(ctx.db, admin, ctx.vault, {"name": "Added After Backup"})
    assert students.count(ctx.db) == 2
    res = backup.restore_backup(r["path"], ctx.paths, ctx.db, str(tmp_path / "safety"))
    assert res["ok"] and os.path.isfile(res["safety_copy"])
    from app.context import Context
    c2 = Context.open(os.path.dirname(os.path.dirname(ctx.paths.db)))
    try:
        assert students.count(c2.db) == 1 and c2.db.val("SELECT SUM(amount_paise) FROM payments") == R(800)
        assert os.path.isfile(os.path.join(c2.paths.photos, "p1.jpg"))
    finally:
        c2.close()


def test_corrupt_or_foreign_backup_rejected(ctx, tmp_path):
    bad = tmp_path / "x.dabak"
    bad.write_bytes(b"not a zip")
    assert backup.verify_backup(str(bad))[0] is False
    res = backup.restore_backup(str(bad), ctx.paths, ctx.db, str(tmp_path / "s"))
    assert not res["ok"] and "Cannot restore" in res["message"]
    assert ctx.db.val("SELECT COUNT(*) FROM users") == 1                                              # current data untouched, db still open
    import zipfile
    r = backup.create_backup(ctx.db, ctx.paths, str(tmp_path / "bk"))
    tampered = str(tmp_path / "t.dabak")
    with zipfile.ZipFile(r["path"]) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            zout.writestr(n, data[:-10] + b"X" * 10 if n == "academy.db" else data)
    assert backup.verify_backup(tampered)[0] is False


def test_auto_backup_schedule(ctx, admin, tmp_path):
    from app.services import settings
    settings.update(ctx.db, admin, {"backup_dir": str(tmp_path / "auto"), "auto_backup": "daily"})
    assert backup.auto_backup_due(ctx.db)
    r = backup.run_auto_backup(ctx.db, ctx.paths)
    assert r and r["ok"] and not backup.auto_backup_due(ctx.db)
    assert backup.run_auto_backup(ctx.db, ctx.paths) is None
    settings.update(ctx.db, admin, {"auto_backup": "off"})
    settings.set_value(ctx.db, "last_auto_backup", "")
    assert not backup.auto_backup_due(ctx.db)
