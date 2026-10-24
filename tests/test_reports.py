from datetime import date, timedelta

import pytest
from helpers import make_user

from app.security.permissions import PermissionDenied, ValidationError
from app.services import attendance, catalog, customfields, fees, reports, students
from app.utils.money import to_paise as R


@pytest.fixture()
def world(ctx, admin, course_id):
    sess = catalog.current_session_id(ctx.db)
    batch = catalog.save_batch(ctx.db, admin, course_id=course_id, session_id=sess, name="Morn")
    ids = []
    for n, mob in (("Zoya Khan", "9000000011"), ("Amit Das", "9000000012"), ("Neha Roy", "9000000013")):
        sid = students.create(ctx.db, admin, ctx.vault, {"name": n, "mobile": mob}, aadhaar="123412341234")
        eid = fees.enroll_with_fees(ctx.db, admin, sid, course_id, batch_id=batch, course_fee=R(6000), admission_fee=0, registration_fee=0, installments=2,
                                    first_due=date.today() - timedelta(days=3), every_months=1)
        ids.append((sid, eid))
    fees.collect_payment(ctx.db, admin, ids[0][0], R(3000), "Cash", enrollment_id=ids[0][1])
    fees.collect_payment(ctx.db, admin, ids[1][0], R(1000), "UPI", enrollment_id=ids[1][1], reference="U9")
    fees.collect_payment(ctx.db, admin, ids[2][0], R(500), "Cash", as_advance=R(500))
    return {"ids": ids, "batch": batch, "course": course_id}


def test_every_report_runs_and_has_consistent_shape(ctx, admin, world):
    day, month = date.today().isoformat(), date.today().strftime("%Y-%m")
    params = {"date": day, "month": month, "student_id": world["ids"][0][0], "course_id": None, "batch_id": None}
    for rd in reports.available(admin):
        ds = reports.run(ctx.db, admin, rd.key, **{k: v for k, v in params.items() if k in rd.params})
        assert ds.columns and all(len(r) == len(ds.columns) for r in ds.rows), rd.key
        if ds.totals:
            assert len(ds.totals) == len(ds.columns), rd.key
    assert len(reports.REGISTRY) >= 25


def test_collection_numbers_match_database(ctx, admin, world):
    ds = reports.run(ctx.db, admin, "collection_daily", date=date.today().isoformat())
    assert ds.totals[8] == R(3000) + R(1000) + R(500) and len(ds.rows) == 3
    by_mode = {r[0]: r for r in reports.run(ctx.db, admin, "collection_by_mode").rows}
    assert by_mode["Cash"][4] == R(3500) and by_mode["UPI"][4] == R(1000)
    cash = reports.run(ctx.db, admin, "daily_cash", date=date.today().isoformat())
    assert cash.totals[4] == R(4500)


def test_outstanding_and_due_reports(ctx, admin, world):
    out = reports.run(ctx.db, admin, "outstanding")
    assert [r[1] for r in out.rows] == ["Neha Roy", "Amit Das", "Zoya Khan"] and out.totals[7] == R(18000) - R(4000)       # advance does not reduce it
    due = reports.run(ctx.db, admin, "installments_due")
    assert any(r[2] == "Amit Das" and r[8] == R(2000) for r in due.rows) and all(r[8] > 0 for r in due.rows)
    adv = reports.run(ctx.db, admin, "advance_balances")
    assert adv.rows == [["" + ctx.db.val("SELECT admission_no FROM students WHERE name='Neha Roy'"), "Neha Roy", "9000000013", R(500)]]


def test_reports_never_leak_aadhaar(ctx, admin, world):
    for rd in reports.available(admin):
        ds = reports.run(ctx.db, admin, rd.key, student_id=world["ids"][0][0], date=date.today().isoformat(), month=date.today().strftime("%Y-%m"))
        blob = " ".join(str(c) for r in ds.rows for c in r) + " ".join(c[0] for c in ds.columns)
        assert "123412341234" not in blob and "aadhaar" not in blob.lower()


def test_report_permissions(ctx, admin, world):
    teacher = make_user(ctx, admin, "t", "teacher")
    with pytest.raises(PermissionDenied):
        reports.run(ctx.db, teacher, "outstanding")
    assert reports.run(ctx.db, teacher, "att_daily").columns
    assert all("fees.view" not in r.needs for r in reports.available(teacher))
    nobody = make_user(ctx, admin, "o", "attendance_operator")
    with pytest.raises(PermissionDenied):
        reports.run(ctx.db, nobody, "students_register")


def test_attendance_reports(ctx, admin, world):
    day = date.today().isoformat()
    sids = [i for i, _ in world["ids"]]
    attendance.save(ctx.db, admin, day, world["course"], world["batch"], None, [(sids[0], "P", ""), (sids[1], "A", "ill"), (sids[2], "A", "")])
    pc = reports.run(ctx.db, admin, "att_percentage", course_id=world["course"], batch_id=world["batch"])
    assert {r[1]: r[5] for r in pc.rows} == {"Zoya Khan": 100.0, "Amit Das": 0.0, "Neha Roy": 0.0}
    ab = reports.run(ctx.db, admin, "att_absent")
    assert {r[1] for r in ab.rows} == {"Amit Das", "Neha Roy"}
    mon = reports.run(ctx.db, admin, "att_monthly", month=date.today().strftime("%Y-%m"), course_id=world["course"], batch_id=world["batch"])
    row = next(r for r in mon.rows if r[0] == "Zoya Khan")
    assert row[int(day[8:])] == "P" and row[-3] == 1 and row[-1] == 100.0
    dd = reports.run(ctx.db, admin, "att_daily", date=day)
    assert len(dd.rows) == 3
    bw = reports.run(ctx.db, admin, "att_batch", batch_id=world["batch"])
    assert bw.rows[0][2] == 3 and bw.rows[0][4] == pytest.approx(33.33, abs=0.01)
    assert reports.run(ctx.db, admin, "att_student", student_id=sids[1]).rows[0][1] == "Absent"


def test_filters_and_custom_field_report(ctx, admin, world):
    other = catalog.courses(ctx.db)[0]["id"]
    assert reports.run(ctx.db, admin, "students_batch", batch_id=world["batch"]).rows.__len__() == 3
    assert reports.run(ctx.db, admin, "students_course", course_id=other).rows == []
    students.set_status(ctx.db, admin, world["ids"][2][0], "Inactive")
    assert len(reports.run(ctx.db, admin, "students_status", status="Inactive").rows) == 1
    assert len(reports.run(ctx.db, admin, "students_register").rows) == 3
    f = customfields.save_field(ctx.db, admin, module="student", label="Transport", field_type="dropdown", choices=["Bus", "Van"])
    students.update(ctx.db, admin, ctx.vault, world["ids"][0][0], {"name": "Zoya Khan", "mobile": "9000000011"}, custom={"transport": "Bus"})
    ds = reports.run(ctx.db, admin, "custom_fields")
    assert ds.columns[-1][0] == "Transport" and [r[-1] for r in ds.rows if r[1] == "Zoya Khan"] == ["Bus"]
    with pytest.raises(ValidationError):
        reports.run(ctx.db, admin, "student_ledger")
    with pytest.raises(ValidationError):
        reports.run(ctx.db, admin, "collection_monthly", month="garbage")


def test_dashboard_numbers_are_real(ctx, admin, world):
    empty = reports.dashboard(ctx.db, admin)
    assert empty["active_students"] == 3 and empty["new_admissions"] == 3
    assert empty["today_collection"] == R(4500) and empty["month_collection"] == R(4500)
    assert empty["outstanding"] == R(14000) and empty["advance"] == R(500)
    assert empty["upcoming_count"] == 2 and empty["by_course"][0] == ("Python", 3)
    assert len(empty["recent_payments"]) == 3
    t = make_user(ctx, admin, "tt", "teacher")
    d = reports.dashboard(ctx.db, t)
    assert d["outstanding"] is None and d["today_collection"] is None and d["active_students"] == 3


def test_empty_database_dashboard_is_zero_not_fake(ctx, admin):
    d = reports.dashboard(ctx.db, admin)
    assert d["active_students"] == 0 and d["today_collection"] == 0 and d["outstanding"] == 0 and d["recent_payments"] == [] and d["upcoming"] == []
