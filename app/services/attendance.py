"""Attendance: one row per student / date / batch / subject, corrections are audited."""
from datetime import date, datetime

from app.database.db import Database
from app.security.permissions import Actor, PermissionDenied, ValidationError, require
from app.services.audit import log


def _now():
    return datetime.now().isoformat(timespec="seconds")


def enrolled_students(db: Database, course_id: int, batch_id: int | None, on_date: str):
    """Currently enrolled (active) students; withdrawn / completed students are not listed."""
    sql = ("SELECT s.id, s.name, s.admission_no, s.student_code FROM enrollments e JOIN students s ON s.id=e.student_id "
           "WHERE e.course_id=? AND e.status='active' AND s.status='Active' AND COALESCE(e.start_date,'0000') <= ? "
           + ("AND e.batch_id=? " if batch_id else "AND e.batch_id IS NULL ") + "ORDER BY s.name")
    return db.q(sql, [course_id, on_date] + ([batch_id] if batch_id else []))


def register(db: Database, actor: Actor, att_date: str, course_id: int, batch_id: int | None, subject_id: int | None = None) -> list[dict]:
    require(actor, "attendance.view")
    existing = {r["student_id"]: r for r in db.q("SELECT * FROM attendance WHERE att_date=? AND batch_id=? AND subject_id=?", (att_date, batch_id or 0, subject_id or 0))}
    out = []
    for s in enrolled_students(db, course_id, batch_id, att_date):
        e = existing.pop(s["id"], None)
        out.append({"student_id": s["id"], "name": s["name"], "admission_no": s["admission_no"], "status": e["status"] if e else "", "remark": e["remark"] if e else "",
                    "saved": e is not None})
    for sid, e in existing.items():                       # marked earlier but since moved out - still show so nothing is hidden
        s = db.one("SELECT name, admission_no FROM students WHERE id=?", (sid,))
        out.append({"student_id": sid, "name": s["name"], "admission_no": s["admission_no"], "status": e["status"], "remark": e["remark"], "saved": True})
    return out


def save(db: Database, actor: Actor, att_date: str, course_id: int, batch_id: int | None, subject_id: int | None, records: list[tuple]) -> dict:
    """records: [(student_id, status_code, remark)]. New entries need attendance.mark; changing saved ones needs attendance.edit."""
    require(actor, "attendance.mark")
    try:
        d = datetime.strptime(att_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("Invalid date.")
    if d > date.today():
        raise ValidationError("Attendance cannot be marked for a future date.")
    codes = {r["code"] for r in db.q("SELECT code FROM attendance_statuses WHERE active=1")}
    allowed = {s["id"] for s in enrolled_students(db, course_id, batch_id, att_date)}
    allowed |= {r["student_id"] for r in db.q("SELECT student_id FROM attendance WHERE att_date=? AND batch_id=? AND subject_id=?", (att_date, batch_id or 0, subject_id or 0))}
    created = changed = unchanged = 0
    with db.tx():
        for sid, status, remark in records:
            if not status:
                continue
            if status not in codes:
                raise ValidationError(f"Unknown attendance status '{status}'.")
            if sid not in allowed:
                raise ValidationError("A selected student is not enrolled in this class / batch.")
            old = db.one("SELECT * FROM attendance WHERE student_id=? AND att_date=? AND batch_id=? AND subject_id=?", (sid, att_date, batch_id or 0, subject_id or 0))
            remark = (remark or "").strip()
            if old is None:
                db.x("INSERT INTO attendance(student_id,att_date,batch_id,subject_id,status,remark,marked_by,marked_at) VALUES (?,?,?,?,?,?,?,?)",
                     (sid, att_date, batch_id or 0, subject_id or 0, status, remark, actor.id, _now()))
                created += 1
            elif old["status"] == status and (old["remark"] or "") == remark:
                unchanged += 1
            else:
                if not actor.can("attendance.edit"):
                    raise PermissionDenied("This attendance was already saved. Only an authorised person can correct it.")
                db.x("INSERT INTO attendance_audit(attendance_id,old_status,new_status,old_remark,new_remark,changed_by,changed_at) VALUES (?,?,?,?,?,?,?)",
                     (old["id"], old["status"], status, old["remark"], remark, actor.id, _now()))
                db.x("UPDATE attendance SET status=?, remark=?, updated_by=?, updated_at=? WHERE id=?", (status, remark, actor.id, _now(), old["id"]))
                changed += 1
        log(db, actor.id, "attendance_save", "attendance", f"{att_date}/{batch_id or 0}", f"new={created} changed={changed}")
    return {"created": created, "changed": changed, "unchanged": unchanged}


def history_for_student(db: Database, actor: Actor, student_id: int, limit: int = 400):
    require(actor, "attendance.view")
    return db.q("SELECT a.att_date, a.status, st.label, a.remark, COALESCE(b.name,'') AS batch FROM attendance a JOIN attendance_statuses st ON st.code=a.status "
                "LEFT JOIN batches b ON b.id=a.batch_id WHERE a.student_id=? ORDER BY a.att_date DESC LIMIT ?", (student_id, limit))


def percentage(db: Database, student_id: int, date_from: str | None = None, date_to: str | None = None, batch_id: int | None = None) -> float | None:
    """present-weight / counted days x 100. Holidays (not counted in total) are excluded. None when there are no counted days."""
    where, p = ["a.student_id=?"], [student_id]
    if date_from:
        where.append("a.att_date>=?"); p.append(date_from)
    if date_to:
        where.append("a.att_date<=?"); p.append(date_to)
    if batch_id is not None:
        where.append("a.batch_id=?"); p.append(batch_id)
    r = db.one("SELECT SUM(CASE WHEN st.counts_in_total=1 THEN st.counts_present ELSE 0 END) AS pres, "
               "SUM(st.counts_in_total) AS total FROM attendance a JOIN attendance_statuses st ON st.code=a.status WHERE " + " AND ".join(where), p)
    if not r or not r["total"]:
        return None
    return round(100.0 * (r["pres"] or 0) / r["total"], 2)


def today_summary(db: Database, day: str) -> dict:
    rows = db.q("SELECT a.status, st.counts_present, st.counts_in_total, COUNT(DISTINCT a.student_id) AS n FROM attendance a JOIN attendance_statuses st ON st.code=a.status "
                "WHERE a.att_date=? GROUP BY a.status", (day,))
    present = sum(r["n"] for r in rows if r["counts_present"] > 0)
    absent = sum(r["n"] for r in rows if r["counts_present"] == 0 and r["counts_in_total"] == 1)
    return {"present": present, "absent": absent, "marked": sum(r["n"] for r in rows)}
