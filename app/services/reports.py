"""Reports. Every report reads the live database and returns a Dataset (typed columns, raw values; money in paise).
Sensitive identity numbers (Aadhaar etc.) are never included in any report."""
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.database.db import Database
from app.security.permissions import Actor, ValidationError, require
from app.services import attendance as att
from app.services import customfields, fees

# column kinds: text | int | money | pct | date
Col = tuple[str, str]


@dataclass
class Dataset:
    title: str
    columns: list[Col]
    rows: list[list]
    subtitle: str = ""
    totals: list | None = None            # optional last row shown bold (same length as columns)
    notes: list[str] = field(default_factory=list)


@dataclass
class ReportDef:
    key: str
    title: str
    group: str
    params: list[str]
    needs: list[str]
    fn: object
    help: str = ""


REGISTRY: dict[str, ReportDef] = {}


def report(key, title, group, params, needs, help=""):
    def deco(fn):
        REGISTRY[key] = ReportDef(key, title, group, params, needs, fn, help)
        return fn
    return deco


def run(db: Database, actor: Actor, key: str, **params) -> Dataset:
    rd = REGISTRY.get(key)
    if rd is None:
        raise ValidationError("Unknown report.")
    require(actor, "reports.view")
    for p in rd.needs:
        require(actor, p)
    return rd.fn(db, actor, **{k: v for k, v in params.items() if k in rd.params})


def available(actor: Actor):
    return [r for r in REGISTRY.values() if actor.can("reports.view") and all(actor.can(p) for p in r.needs)]


def _d(v, default=None):
    return v or default


def _range_text(f, t):
    return f"{_fmt(f)} to {_fmt(t)}" if f or t else "All dates"


def _fmt(d):
    if not d:
        return "…"
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%m-%Y")


def _month_bounds(month: str):
    try:
        y, m = [int(x) for x in month.split("-")]
        last = calendar.monthrange(y, m)[1]
    except Exception:
        raise ValidationError("Choose a valid month.")
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}", last


def _student_filters(session_id, course_id, batch_id, category_id, status, kind=None):
    where, p = [], []
    if status:
        where.append("s.status=?"); p.append(status)
    if session_id:
        where.append("s.session_id=?"); p.append(session_id)
    if course_id or batch_id or category_id or kind:
        sub = "EXISTS (SELECT 1 FROM enrollments e JOIN courses c ON c.id=e.course_id WHERE e.student_id=s.id AND e.status='active'"
        if course_id: sub += " AND e.course_id=?"; p.append(course_id)
        if batch_id: sub += " AND e.batch_id=?"; p.append(batch_id)
        if category_id: sub += " AND c.category_id=?"; p.append(category_id)
        if kind: sub += " AND c.kind=?"; p.append(kind)
        where.append(sub + ")")
    return where, p


# ===================================================================== students
def _student_list(db, title, *, session_id=None, course_id=None, batch_id=None, category_id=None, status=None, kind=None, order="s.name", date_from=None, date_to=None):
    where, p = _student_filters(session_id, course_id, batch_id, category_id, status, kind)
    if date_from:
        where.append("s.admission_date>=?"); p.append(date_from)
    if date_to:
        where.append("s.admission_date<=?"); p.append(date_to)
    rows = db.q("SELECT s.admission_no, s.name, s.father_name, s.mobile, s.guardian_mobile, s.admission_date, s.status, "
                "(SELECT GROUP_CONCAT(c.name, ', ') FROM enrollments e JOIN courses c ON c.id=e.course_id WHERE e.student_id=s.id AND e.status='active') AS courses, "
                "(SELECT GROUP_CONCAT(b.name, ', ') FROM enrollments e JOIN batches b ON b.id=e.batch_id WHERE e.student_id=s.id AND e.status='active') AS batches "
                "FROM students s" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}", p)
    cols = [("Adm. No.", "text"), ("Student", "text"), ("Father", "text"), ("Mobile", "text"), ("Guardian mobile", "text"), ("Course / class", "text"), ("Batch", "text"),
            ("Admitted", "date"), ("Status", "text")]
    data = [[r["admission_no"], r["name"], r["father_name"], r["mobile"], r["guardian_mobile"], r["courses"] or "", r["batches"] or "", r["admission_date"], r["status"]] for r in rows]
    return Dataset(title, cols, data, f"{len(data)} students")


@report("students_register", "Complete student register", "Students", ["session_id", "status"], ["students.view"])
def students_register(db, actor, session_id=None, status=None):
    return _student_list(db, "Complete student register", session_id=session_id, status=status, order="s.id")


@report("students_course", "Course-wise student list", "Students", ["course_id", "category_id", "session_id", "status"], ["students.view"])
def students_course(db, actor, course_id=None, category_id=None, session_id=None, status="Active"):
    return _student_list(db, "Course-wise student list", course_id=course_id, category_id=category_id, session_id=session_id, status=status, kind="course" if not course_id else None,
                         order="courses, s.name")


@report("students_class", "Class-wise student list", "Students", ["course_id", "session_id", "status"], ["students.view"])
def students_class(db, actor, course_id=None, session_id=None, status="Active"):
    return _student_list(db, "Class-wise student list", course_id=course_id, session_id=session_id, status=status, kind="class" if not course_id else None, order="courses, s.name")


@report("students_batch", "Batch-wise student list", "Students", ["course_id", "batch_id", "session_id", "status"], ["students.view"])
def students_batch(db, actor, course_id=None, batch_id=None, session_id=None, status="Active"):
    return _student_list(db, "Batch-wise student list", course_id=course_id, batch_id=batch_id, session_id=session_id, status=status, order="batches, s.name")


@report("new_admissions", "New admissions", "Students", ["date_from", "date_to"], ["students.view"])
def new_admissions(db, actor, date_from=None, date_to=None):
    ds = _student_list(db, "New admissions", date_from=date_from, date_to=date_to, order="s.admission_date DESC, s.id DESC")
    ds.subtitle = f"{_range_text(date_from, date_to)} — {len(ds.rows)} students"
    return ds


@report("students_status", "Active / inactive students", "Students", ["status", "session_id"], ["students.view"])
def students_status(db, actor, status=None, session_id=None):
    ds = _student_list(db, "Students by status", status=status, session_id=session_id, order="s.status, s.name")
    return ds


@report("course_completion", "Course completion", "Students", ["date_from", "date_to", "course_id"], ["students.view"])
def course_completion(db, actor, date_from=None, date_to=None, course_id=None):
    where, p = ["e.status='completed'"], []
    if date_from: where.append("e.completed_on>=?"); p.append(date_from)
    if date_to: where.append("e.completed_on<=?"); p.append(date_to)
    if course_id: where.append("e.course_id=?"); p.append(course_id)
    rows = db.q("SELECT s.admission_no, s.name, c.name AS course, e.start_date, e.completed_on, e.certificate_status FROM enrollments e JOIN students s ON s.id=e.student_id "
                "JOIN courses c ON c.id=e.course_id WHERE " + " AND ".join(where) + " ORDER BY e.completed_on DESC", p)
    return Dataset("Course completion", [("Adm. No.", "text"), ("Student", "text"), ("Course", "text"), ("Started", "date"), ("Completed", "date"), ("Certificate", "text")],
                   [[r["admission_no"], r["name"], r["course"], r["start_date"], r["completed_on"], r["certificate_status"].title()] for r in rows], f"{len(rows)} completions")


@report("custom_fields", "Custom-field report (students)", "Students", ["status", "course_id"], ["students.view"], "Shows every active custom student field you created.")
def custom_field_report(db, actor, status=None, course_id=None):
    flds = customfields.fields(db, "student", actor)
    where, p = _student_filters(None, course_id, None, None, status)
    sts = db.q("SELECT s.id, s.admission_no, s.name FROM students s" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.name", p)
    vals = {}
    for r in db.q("SELECT field_id, entity_id, value FROM custom_values"):
        vals[(r["field_id"], r["entity_id"])] = r["value"]
    cols = [("Adm. No.", "text"), ("Student", "text")] + [(f["label"], "text") for f in flds]
    rows = [[s["admission_no"], s["name"]] + [customfields.display_value(f, vals.get((f["id"], s["id"]), "")) for f in flds] for s in sts]
    ds = Dataset("Custom-field report", cols, rows, f"{len(rows)} students")
    if not flds:
        ds.notes.append("No custom student fields have been created yet (Settings → Field Designer).")
    return ds


# ===================================================================== finance
def _collection_rows(db, date_from, date_to, mode=None, cashier_id=None):
    where, p = ["p.kind IN ('payment','refund','reversal')"], []
    if date_from: where.append("p.pay_date>=?"); p.append(date_from)
    if date_to: where.append("p.pay_date<=?"); p.append(date_to)
    if mode: where.append("p.mode=?"); p.append(mode)
    if cashier_id: where.append("p.cashier_id=?"); p.append(cashier_id)
    return db.q("SELECT p.*, s.name AS student, s.admission_no, (SELECT c.name FROM enrollments e JOIN courses c ON c.id=e.course_id WHERE e.id=p.enrollment_id) AS course "
                "FROM payments p JOIN students s ON s.id=p.student_id WHERE " + " AND ".join(where) + " ORDER BY p.pay_date, p.id", p)


def _collection_ds(db, title, f, t, mode=None, cashier_id=None):
    rows = _collection_rows(db, f, t, mode, cashier_id)
    cols = [("Date", "date"), ("Receipt", "text"), ("Student", "text"), ("Adm. No.", "text"), ("Course", "text"), ("Type", "text"), ("Mode", "text"), ("Reference", "text"),
            ("Amount", "money"), ("Cashier", "text")]
    kinds = {"payment": "Payment", "refund": "Refund", "reversal": "Reversal"}
    data = [[r["pay_date"], r["receipt_no"] or "—", r["student"], r["admission_no"], r["course"] or "", kinds[r["kind"]] + (" (cancelled)" if r["status"] == "reversed" else ""),
             r["mode"], r["reference"], r["amount_paise"], r["cashier_name"]] for r in rows]
    total = sum(r["amount_paise"] for r in rows)
    return Dataset(title, cols, data, _range_text(f, t), totals=["", "", "", "", "", "", "", "Net total", total, ""])


@report("collection_daily", "Daily fee collection", "Finance", ["date"], ["fees.view"])
def collection_daily(db, actor, date=None):
    day = date or datetime.now().date().isoformat()
    return _collection_ds(db, "Daily fee collection", day, day)


@report("collection_monthly", "Monthly fee collection", "Finance", ["month"], ["fees.view"])
def collection_monthly(db, actor, month=None):
    month = month or datetime.now().strftime("%Y-%m")
    f, t, _ = _month_bounds(month)
    return _collection_ds(db, "Monthly fee collection", f, t)


@report("collection_range", "Fee collection (date range)", "Finance", ["date_from", "date_to", "mode"], ["fees.view"])
def collection_range(db, actor, date_from=None, date_to=None, mode=None):
    return _collection_ds(db, "Fee collection", date_from, date_to, mode)


@report("collection_by_mode", "Payment-mode-wise collection", "Finance", ["date_from", "date_to"], ["fees.view"])
def collection_by_mode(db, actor, date_from=None, date_to=None):
    where, p = ["kind IN ('payment','refund','reversal')"], []
    if date_from: where.append("pay_date>=?"); p.append(date_from)
    if date_to: where.append("pay_date<=?"); p.append(date_to)
    rows = db.q("SELECT mode, SUM(CASE WHEN amount_paise>0 THEN amount_paise ELSE 0 END) AS rec, SUM(CASE WHEN amount_paise<0 THEN -amount_paise ELSE 0 END) AS out, "
                "SUM(amount_paise) AS net, COUNT(*) AS n FROM payments WHERE " + " AND ".join(where) + " GROUP BY mode ORDER BY net DESC", p)
    data = [[r["mode"], r["n"], r["rec"], r["out"], r["net"]] for r in rows]
    tot = ["Total", sum(r[1] for r in data), sum(r[2] for r in data), sum(r[3] for r in data), sum(r[4] for r in data)]
    return Dataset("Payment-mode-wise collection", [("Mode", "text"), ("Entries", "int"), ("Received", "money"), ("Paid out / reversed", "money"), ("Net", "money")], data,
                   _range_text(date_from, date_to), totals=tot)


@report("student_ledger", "Student fee ledger", "Finance", ["student_id"], ["fees.view"])
def student_ledger(db, actor, student_id=None):
    if not student_id:
        raise ValidationError("Choose a student.")
    s = db.one("SELECT name, admission_no FROM students WHERE id=?", (student_id,))
    if s is None:
        raise ValidationError("Student not found.")
    led = fees.ledger(db, actor, student_id)
    sm = fees.student_summary(db, student_id)
    rows = [[r["when"][:10], r["type"], r["text"], r["ref"], r["debit"], r["credit"], r["balance"]] for r in led]
    ds = Dataset(f"Fee ledger — {s['name']} ({s['admission_no']})", [("Date", "date"), ("Type", "text"), ("Details", "text"), ("Receipt / Txn", "text"), ("Charges", "money"),
                 ("Paid / credited", "money"), ("Balance", "money")], rows, f"Outstanding {sm['outstanding'] / 100:,.2f}   Advance held {sm['advance'] / 100:,.2f}")
    ds.totals = ["", "", "", "Total", sum(r[4] for r in rows), sum(r[5] for r in rows), sm["outstanding"]]
    return ds


@report("outstanding", "Outstanding fee report", "Finance", ["session_id", "course_id", "batch_id"], ["fees.view"])
def outstanding(db, actor, session_id=None, course_id=None, batch_id=None):
    where, p = ["s.status IN ('Active','Inactive','Completed')"], []
    if session_id: where.append("e.session_id=?"); p.append(session_id)
    if course_id: where.append("e.course_id=?"); p.append(course_id)
    if batch_id: where.append("e.batch_id=?"); p.append(batch_id)
    out = []
    for r in db.q("SELECT e.id AS eid, s.admission_no, s.name, s.mobile, s.guardian_mobile, c.name AS course, COALESCE(b.name,'') AS batch FROM enrollments e JOIN students s ON s.id=e.student_id "
                  "JOIN courses c ON c.id=e.course_id LEFT JOIN batches b ON b.id=e.batch_id WHERE " + " AND ".join(where) + " ORDER BY s.name", p):
        sm = fees.enrollment_summary(db, r["eid"])
        if sm["outstanding"] > 0:
            nxt = next((i for i in fees.installments_status(db, r["eid"]) if i["due"] > 0), None)
            out.append([r["admission_no"], r["name"], r["mobile"] or r["guardian_mobile"], r["course"], r["batch"], sm["net"], sm["paid"], sm["outstanding"],
                        nxt["due_date"] if nxt else None])
    out.sort(key=lambda x: -x[7])
    tot = ["", "Total", "", "", "", sum(x[5] for x in out), sum(x[6] for x in out), sum(x[7] for x in out), None]
    return Dataset("Outstanding fee report", [("Adm. No.", "text"), ("Student", "text"), ("Mobile", "text"), ("Course", "text"), ("Batch", "text"), ("Net payable", "money"),
                   ("Paid", "money"), ("Outstanding", "money"), ("Next due", "date")], out, f"{len(out)} accounts with balance", totals=tot)


@report("installments_due", "Installment due report", "Finance", ["date_from", "date_to"], ["fees.view"], "Unpaid installments due in the range (leave the start empty to include all overdue ones).")
def installments_due(db, actor, date_from=None, date_to=None):
    date_to = date_to or (date.today() + timedelta(days=15)).isoformat()
    where, p = ["i.due_date<=?"], [date_to]
    if date_from: where.append("i.due_date>=?"); p.append(date_from)
    rows = []
    for r in db.q("SELECT i.*, s.admission_no, s.name, s.mobile, s.guardian_mobile, c.name AS course FROM installments i JOIN students s ON s.id=i.student_id JOIN enrollments e ON e.id=i.enrollment_id "
                  "JOIN courses c ON c.id=e.course_id WHERE s.status='Active' AND e.status='active' AND " + " AND ".join(where) + " ORDER BY i.due_date, s.name", p):
        paid = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payment_allocations WHERE installment_id=?", (r["id"],), 0)
        due = r["amount_paise"] - paid
        if due > 0:
            days = (date.today() - date.fromisoformat(r["due_date"])).days
            rows.append([r["due_date"], r["admission_no"], r["name"], r["mobile"] or r["guardian_mobile"], r["course"], r["label"], r["amount_paise"], paid, due,
                         f"Overdue {days} d" if days > 0 else "Due"])
    return Dataset("Installment due report", [("Due date", "date"), ("Adm. No.", "text"), ("Student", "text"), ("Mobile", "text"), ("Course", "text"), ("Installment", "text"),
                   ("Amount", "money"), ("Paid", "money"), ("Due", "money"), ("Status", "text")], rows, f"Up to {_fmt(date_to)} — {len(rows)} installments",
                   totals=["", "", "", "", "", "Total", sum(r[6] for r in rows), sum(r[7] for r in rows), sum(r[8] for r in rows), ""])


@report("advance_balances", "Advance balance report", "Finance", [], ["fees.view"])
def advance_balances(db, actor):
    rows = db.q("SELECT s.admission_no, s.name, s.mobile, SUM(p.advance_paise) AS adv FROM payments p JOIN students s ON s.id=p.student_id GROUP BY s.id HAVING adv <> 0 ORDER BY adv DESC")
    data = [[r["admission_no"], r["name"], r["mobile"], r["adv"]] for r in rows]
    return Dataset("Advance balance report", [("Adm. No.", "text"), ("Student", "text"), ("Mobile", "text"), ("Advance held", "money")], data, f"{len(data)} students",
                   totals=["", "", "Total", sum(r[3] for r in data)])


@report("discounts", "Discounts & concessions", "Finance", ["date_from", "date_to"], ["fees.view"])
def discounts(db, actor, date_from=None, date_to=None):
    where, p = ["a.voided=0"], []
    if date_from: where.append("substr(a.created_at,1,10)>=?"); p.append(date_from)
    if date_to: where.append("substr(a.created_at,1,10)<=?"); p.append(date_to)
    rows = db.q("SELECT a.*, s.admission_no, s.name, c.name AS course, u.username AS approver FROM fee_adjustments a JOIN students s ON s.id=a.student_id JOIN enrollments e ON e.id=a.enrollment_id "
                "JOIN courses c ON c.id=e.course_id LEFT JOIN users u ON u.id=a.approved_by WHERE " + " AND ".join(where) + " ORDER BY a.created_at DESC", p)
    data = [[r["created_at"][:10], r["admission_no"], r["name"], r["course"], r["kind"].title(), r["amount_paise"], r["reason"], r["approver"] or ""] for r in rows]
    return Dataset("Discounts and concessions", [("Date", "date"), ("Adm. No.", "text"), ("Student", "text"), ("Course", "text"), ("Kind", "text"), ("Amount", "money"),
                   ("Reason", "text"), ("Approved by", "text")], data, _range_text(date_from, date_to), totals=["", "", "", "", "Total", sum(r[5] for r in data), "", ""])


@report("refunds_reversals", "Refunds & reversals", "Finance", ["date_from", "date_to"], ["fees.view"])
def refunds_reversals(db, actor, date_from=None, date_to=None):
    where, p = ["p.kind IN ('refund','reversal')"], []
    if date_from: where.append("p.pay_date>=?"); p.append(date_from)
    if date_to: where.append("p.pay_date<=?"); p.append(date_to)
    rows = db.q("SELECT p.*, s.name, s.admission_no, o.receipt_no AS orig FROM payments p JOIN students s ON s.id=p.student_id LEFT JOIN payments o ON o.id=p.reversal_of "
                "WHERE " + " AND ".join(where) + " ORDER BY p.id DESC", p)
    data = [[r["pay_date"], r["kind"].title(), r["admission_no"], r["name"], r["orig"] or r["receipt_no"] or "", r["amount_paise"], r["reason"], r["cashier_name"]] for r in rows]
    return Dataset("Refunds and reversals", [("Date", "date"), ("Type", "text"), ("Adm. No.", "text"), ("Student", "text"), ("Original receipt", "text"), ("Amount", "money"),
                   ("Reason", "text"), ("By", "text")], data, _range_text(date_from, date_to))


@report("cashier_wise", "Cashier-wise collection", "Finance", ["date_from", "date_to"], ["fees.view"])
def cashier_wise(db, actor, date_from=None, date_to=None):
    where, p = ["kind IN ('payment','refund','reversal')"], []
    if date_from: where.append("pay_date>=?"); p.append(date_from)
    if date_to: where.append("pay_date<=?"); p.append(date_to)
    rows = db.q("SELECT cashier_name, COUNT(*) AS n, SUM(CASE WHEN amount_paise>0 THEN amount_paise ELSE 0 END) AS rec, SUM(amount_paise) AS net FROM payments WHERE "
                + " AND ".join(where) + " GROUP BY cashier_id ORDER BY net DESC", p)
    data = [[r["cashier_name"], r["n"], r["rec"], r["net"]] for r in rows]
    return Dataset("Cashier-wise collection", [("Cashier", "text"), ("Entries", "int"), ("Received", "money"), ("Net", "money")], data, _range_text(date_from, date_to),
                   totals=["Total", sum(r[1] for r in data), sum(r[2] for r in data), sum(r[3] for r in data)])


@report("receipt_register", "Receipt register", "Finance", ["date_from", "date_to"], ["fees.view"])
def receipt_register(db, actor, date_from=None, date_to=None):
    where, p = ["p.receipt_no IS NOT NULL"], []
    if date_from: where.append("p.pay_date>=?"); p.append(date_from)
    if date_to: where.append("p.pay_date<=?"); p.append(date_to)
    rows = db.q("SELECT p.*, s.name, s.admission_no FROM payments p JOIN students s ON s.id=p.student_id WHERE " + " AND ".join(where) + " ORDER BY p.id", p)
    data = [[r["receipt_no"], r["pay_date"], r["admission_no"], r["name"], r["mode"], r["amount_paise"], "Cancelled" if r["status"] == "reversed" else "Valid"] for r in rows]
    valid = sum(r[5] for r in data if r[6] == "Valid")
    return Dataset("Receipt register", [("Receipt", "text"), ("Date", "date"), ("Adm. No.", "text"), ("Student", "text"), ("Mode", "text"), ("Amount", "money"), ("Status", "text")],
                   data, _range_text(date_from, date_to), totals=["", "", "", "", "Valid total", valid, ""])


@report("daily_cash", "Daily cash summary (closing)", "Finance", ["date"], ["fees.view"], "Compare this with the cash, UPI and bank entries you actually hold.")
def daily_cash(db, actor, date=None):
    day = date or datetime.now().date().isoformat()
    rows = fees.daily_closing(db, actor, day)
    data = [[r["mode"], r["n"], r["received"], r["paid_out"], r["net"]] for r in rows]
    return Dataset("Daily cash summary", [("Mode", "text"), ("Entries", "int"), ("Received", "money"), ("Refunded / reversed", "money"), ("Net to be in hand", "money")], data, _fmt(day),
                   totals=["Total", sum(r[1] for r in data), sum(r[2] for r in data), sum(r[3] for r in data), sum(r[4] for r in data)])


# ===================================================================== attendance
def _att_students(db, course_id, batch_id, kind=None):
    where, p = ["e.status='active'", "s.status='Active'"], []
    if course_id: where.append("e.course_id=?"); p.append(course_id)
    if batch_id: where.append("e.batch_id=?"); p.append(batch_id)
    if kind: where.append("c.kind=?"); p.append(kind)
    return db.q("SELECT DISTINCT s.id, s.name, s.admission_no, c.name AS course, COALESCE(b.name,'') AS batch, e.batch_id FROM enrollments e JOIN students s ON s.id=e.student_id "
                "JOIN courses c ON c.id=e.course_id LEFT JOIN batches b ON b.id=e.batch_id WHERE " + " AND ".join(where) + " ORDER BY c.name, b.name, s.name", p)


@report("att_daily", "Daily attendance", "Attendance", ["date", "course_id", "batch_id"], ["attendance.view"])
def att_daily(db, actor, date=None, course_id=None, batch_id=None):
    day = date or datetime.now().date().isoformat()
    where, p = ["a.att_date=?"], [day]
    if course_id: where.append("e.course_id=?"); p.append(course_id)
    if batch_id: where.append("a.batch_id=?"); p.append(batch_id)
    rows = db.q("SELECT DISTINCT s.admission_no, s.name, c.name AS course, COALESCE(b.name,'') AS batch, st.label, a.remark FROM attendance a JOIN students s ON s.id=a.student_id "
                "JOIN attendance_statuses st ON st.code=a.status JOIN enrollments e ON e.student_id=s.id AND e.status='active' JOIN courses c ON c.id=e.course_id "
                "LEFT JOIN batches b ON b.id=a.batch_id WHERE " + " AND ".join(where) + " ORDER BY c.name, b.name, s.name", p)
    data = [[r["admission_no"], r["name"], r["course"], r["batch"], r["label"], r["remark"]] for r in rows]
    return Dataset("Daily attendance", [("Adm. No.", "text"), ("Student", "text"), ("Course / class", "text"), ("Batch", "text"), ("Status", "text"), ("Remark", "text")], data,
                   f"{_fmt(day)} — {len(data)} marked")


@report("att_monthly", "Monthly attendance register", "Attendance", ["month", "course_id", "batch_id"], ["attendance.view"])
def att_monthly(db, actor, month=None, course_id=None, batch_id=None):
    month = month or datetime.now().strftime("%Y-%m")
    f, t, last = _month_bounds(month)
    studs = _att_students(db, course_id, batch_id)
    marks = {}
    for r in db.q("SELECT student_id, att_date, status FROM attendance WHERE att_date BETWEEN ? AND ?", (f, t)):
        marks[(r["student_id"], int(r["att_date"][8:]))] = r["status"]
    cols = [("Student", "text")] + [(str(d), "text") for d in range(1, last + 1)] + [("P", "int"), ("A", "int"), ("%", "pct")]
    rows = []
    for s in studs:
        cells = [marks.get((s["id"], d), "") for d in range(1, last + 1)]
        pct = att.percentage(db, s["id"], f, t)
        pres = sum(1 for c in cells if c in ("P", "L"))
        absn = sum(1 for c in cells if c == "A")
        rows.append([s["name"]] + cells + [pres, absn, pct])
    return Dataset("Monthly attendance register", cols, rows, datetime.strptime(month, "%Y-%m").strftime("%B %Y") + f" — {len(rows)} students",
                   notes=["P present, A absent, L late, LV leave, H holiday"])


@report("att_student", "Student-wise attendance", "Attendance", ["student_id", "date_from", "date_to"], ["attendance.view"])
def att_student(db, actor, student_id=None, date_from=None, date_to=None):
    if not student_id:
        raise ValidationError("Choose a student.")
    s = db.one("SELECT name, admission_no FROM students WHERE id=?", (student_id,))
    where, p = ["a.student_id=?"], [student_id]
    if date_from: where.append("a.att_date>=?"); p.append(date_from)
    if date_to: where.append("a.att_date<=?"); p.append(date_to)
    rows = db.q("SELECT a.att_date, st.label, a.remark, COALESCE(b.name,'') AS batch FROM attendance a JOIN attendance_statuses st ON st.code=a.status LEFT JOIN batches b ON b.id=a.batch_id "
                "WHERE " + " AND ".join(where) + " ORDER BY a.att_date", p)
    pct = att.percentage(db, student_id, date_from, date_to)
    return Dataset(f"Attendance — {s['name']} ({s['admission_no']})", [("Date", "date"), ("Status", "text"), ("Batch", "text"), ("Remark", "text")],
                   [[r["att_date"], r["label"], r["batch"], r["remark"]] for r in rows], f"{_range_text(date_from, date_to)} — attendance {pct if pct is not None else '—'}%")


def _att_summary(db, title, course_id, batch_id, date_from, date_to, kind=None, group="course"):
    studs = _att_students(db, course_id, batch_id, kind)
    groups: dict = {}
    for s in studs:
        key = (s["course"], s["batch"]) if group == "batch" else (s["course"], "")
        g = groups.setdefault(key, {"n": 0, "pcts": [], "marked": 0})
        g["n"] += 1
        pc = att.percentage(db, s["id"], date_from, date_to)
        if pc is not None:
            g["pcts"].append(pc)
    rows = []
    for (c, b), g in sorted(groups.items()):
        avg = round(sum(g["pcts"]) / len(g["pcts"]), 2) if g["pcts"] else None
        rows.append([c, b, g["n"], len(g["pcts"]), avg])
    return Dataset(title, [("Course / class", "text"), ("Batch", "text"), ("Students", "int"), ("With records", "int"), ("Average attendance", "pct")], rows, _range_text(date_from, date_to))


@report("att_course", "Course-wise attendance", "Attendance", ["course_id", "date_from", "date_to"], ["attendance.view"])
def att_course(db, actor, course_id=None, date_from=None, date_to=None):
    return _att_summary(db, "Course-wise attendance", course_id, None, date_from, date_to, kind="course")


@report("att_class", "Class-wise attendance", "Attendance", ["course_id", "date_from", "date_to"], ["attendance.view"])
def att_class(db, actor, course_id=None, date_from=None, date_to=None):
    return _att_summary(db, "Class-wise attendance", course_id, None, date_from, date_to, kind="class")


@report("att_batch", "Batch-wise attendance", "Attendance", ["course_id", "batch_id", "date_from", "date_to"], ["attendance.view"])
def att_batch(db, actor, course_id=None, batch_id=None, date_from=None, date_to=None):
    return _att_summary(db, "Batch-wise attendance", course_id, batch_id, date_from, date_to, group="batch")


@report("att_absent", "Absence list", "Attendance", ["date_from", "date_to", "course_id", "batch_id"], ["attendance.view"])
def att_absent(db, actor, date_from=None, date_to=None, course_id=None, batch_id=None):
    where, p = ["st.counts_present=0", "st.counts_in_total=1"], []
    if date_from: where.append("a.att_date>=?"); p.append(date_from)
    if date_to: where.append("a.att_date<=?"); p.append(date_to)
    if batch_id: where.append("a.batch_id=?"); p.append(batch_id)
    if course_id: where.append("EXISTS (SELECT 1 FROM enrollments e WHERE e.student_id=s.id AND e.course_id=?)"); p.append(course_id)
    rows = db.q("SELECT s.admission_no, s.name, s.mobile, s.guardian_mobile, COUNT(*) AS n, MAX(a.att_date) AS last FROM attendance a JOIN students s ON s.id=a.student_id "
                "JOIN attendance_statuses st ON st.code=a.status WHERE " + " AND ".join(where) + " GROUP BY s.id ORDER BY n DESC, s.name", p)
    return Dataset("Absence list", [("Adm. No.", "text"), ("Student", "text"), ("Mobile", "text"), ("Guardian mobile", "text"), ("Days absent", "int"), ("Last absent", "date")],
                   [[r["admission_no"], r["name"], r["mobile"], r["guardian_mobile"], r["n"], r["last"]] for r in rows], _range_text(date_from, date_to))


@report("att_percentage", "Attendance percentage", "Attendance", ["course_id", "batch_id", "date_from", "date_to"], ["attendance.view"])
def att_percentage(db, actor, course_id=None, batch_id=None, date_from=None, date_to=None):
    rows = []
    for s in _att_students(db, course_id, batch_id):
        pc = att.percentage(db, s["id"], date_from, date_to)
        days = db.val("SELECT COUNT(*) FROM attendance a JOIN attendance_statuses st ON st.code=a.status WHERE a.student_id=? AND st.counts_in_total=1"
                      + (" AND a.att_date>=?" if date_from else "") + (" AND a.att_date<=?" if date_to else ""), [s["id"]] + [x for x in (date_from, date_to) if x], 0)
        rows.append([s["admission_no"], s["name"], s["course"], s["batch"], days, pc])
    rows.sort(key=lambda r: (r[5] is None, r[5] if r[5] is not None else 0))
    return Dataset("Attendance percentage", [("Adm. No.", "text"), ("Student", "text"), ("Course / class", "text"), ("Batch", "text"), ("Days counted", "int"), ("Attendance", "pct")],
                   rows, _range_text(date_from, date_to))


# ===================================================================== dashboard
def dashboard(db: Database, actor: Actor, *, session_id=None, date_from=None, date_to=None, course_id=None, batch_id=None) -> dict:
    """Live numbers only - nothing is hard-coded. Finance tiles are None when the user may not view fees."""
    today = date.today()
    month_from = today.replace(day=1).isoformat()
    f, t = date_from or month_from, date_to or today.isoformat()
    where, p = _student_filters(session_id, course_id, batch_id, None, "Active")
    base = "SELECT COUNT(*) FROM students s" + (" WHERE " + " AND ".join(where) if where else "")
    d = {"active_students": db.val(base, p, 0)}
    w2, p2 = _student_filters(session_id, course_id, batch_id, None, None)
    w2 += ["s.admission_date>=?", "s.admission_date<=?"]; p2 += [f, t]
    d["new_admissions"] = db.val("SELECT COUNT(*) FROM students s WHERE " + " AND ".join(w2), p2, 0)
    d["range"] = (f, t)
    d["by_course"] = [(r["name"], r["n"]) for r in db.q("SELECT c.name, COUNT(DISTINCT e.student_id) AS n FROM enrollments e JOIN courses c ON c.id=e.course_id JOIN students s ON s.id=e.student_id "
                                                         "WHERE e.status='active' AND s.status='Active' AND c.kind='course'" + (" AND e.session_id=?" if session_id else "") + " GROUP BY c.id ORDER BY n DESC, c.name LIMIT 12",
                                                         [session_id] if session_id else [])]
    d["by_class"] = [(r["name"], r["n"]) for r in db.q("SELECT c.name, COUNT(DISTINCT e.student_id) AS n FROM enrollments e JOIN courses c ON c.id=e.course_id JOIN students s ON s.id=e.student_id "
                                                        "WHERE e.status='active' AND s.status='Active' AND c.kind='class'" + (" AND e.session_id=?" if session_id else "") + " GROUP BY c.id ORDER BY c.sort_order LIMIT 14",
                                                        [session_id] if session_id else [])]
    d["attendance_today"] = att.today_summary(db, today.isoformat()) if actor.can("attendance.view") else None
    d["recent_admissions"] = [dict(r) for r in db.q("SELECT id, name, admission_no, admission_date FROM students ORDER BY id DESC LIMIT 6")]
    if actor.can("fees.view"):
        d["today_collection"] = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE pay_date=? AND kind IN ('payment','refund','reversal')", (today.isoformat(),), 0)
        d["month_collection"] = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE pay_date BETWEEN ? AND ? AND kind IN ('payment','refund','reversal')", (month_from, today.isoformat()), 0)
        out = 0
        for e in db.q("SELECT e.id FROM enrollments e JOIN students s ON s.id=e.student_id WHERE s.status='Active' AND e.status='active'"
                      + (" AND e.session_id=?" if session_id else "") + (" AND e.course_id=?" if course_id else "") + (" AND e.batch_id=?" if batch_id else ""),
                      [x for x in (session_id, course_id, batch_id) if x]):
            out += max(fees.enrollment_summary(db, e["id"])["outstanding"], 0)
        d["outstanding"] = out
        d["advance"] = db.val("SELECT COALESCE(SUM(advance_paise),0) FROM payments", default=0)
        d["recent_payments"] = [dict(r) for r in db.q("SELECT p.id, p.receipt_no, p.amount_paise, p.mode, p.pay_date, p.kind, p.status, s.name FROM payments p JOIN students s ON s.id=p.student_id "
                                                        "WHERE p.kind IN ('payment','refund') ORDER BY p.id DESC LIMIT 8")]
        soon = (today + timedelta(days=14)).isoformat()
        up = []
        for r in db.q("SELECT i.id, i.due_date, i.amount_paise, i.label, s.name, s.id AS sid FROM installments i JOIN students s ON s.id=i.student_id JOIN enrollments e ON e.id=i.enrollment_id "
                      "WHERE s.status='Active' AND e.status='active' AND i.due_date<=? ORDER BY i.due_date LIMIT 80", (soon,)):
            paid = db.val("SELECT COALESCE(SUM(amount_paise),0) FROM payment_allocations WHERE installment_id=?", (r["id"],), 0)
            if r["amount_paise"] - paid > 0:
                up.append({"name": r["name"], "due_date": r["due_date"], "due": r["amount_paise"] - paid, "label": r["label"], "overdue": r["due_date"] < today.isoformat(), "student_id": r["sid"]})
        d["upcoming"] = up[:8]
        d["upcoming_count"] = len(up)
    else:
        for k in ("today_collection", "month_collection", "outstanding", "advance", "recent_payments", "upcoming", "upcoming_count"):
            d[k] = None
    return d
