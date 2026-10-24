"""Students and enrollments."""
import os
import re
import uuid
from datetime import date, datetime

from app.database.db import Database
from app.security.crypto import Vault, mask
from app.security.permissions import Actor, ValidationError, require
from app.services import catalog, customfields, settings
from app.services.audit import log

STATUSES = ["Active", "Inactive", "Completed", "Transferred", "Withdrawn"]
GENDERS = ["", "Male", "Female", "Other"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_FIELDS = ["name", "dob", "gender", "father_name", "mother_name", "guardian_name", "guardian_relation", "mobile", "guardian_mobile",
           "email", "address", "locality", "city", "district", "state", "pin", "alt_id_type", "alt_id_no", "emergency_name",
           "emergency_phone", "session_id", "admission_date", "education_category", "current_school", "prev_qualification", "board", "remarks"]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _digits(s: str) -> str:
    return re.sub(r"[\s\-+()]", "", s or "")


def _phone(label: str, v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    d = _digits(v)
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    if not (d.isdigit() and len(d) == 10):
        raise ValidationError(f"{label} must be a 10-digit mobile number.")
    return d


def _clean(data: dict) -> dict:
    d = {k: (data.get(k) if data.get(k) is not None else "") for k in _FIELDS}
    for k in d:
        if isinstance(d[k], str):
            d[k] = d[k].strip()
    if not d["name"]:
        raise ValidationError("Student name is required.")
    d["mobile"] = _phone("Student mobile", d["mobile"])
    d["guardian_mobile"] = _phone("Guardian mobile", d["guardian_mobile"])
    d["emergency_phone"] = _phone("Emergency phone", d["emergency_phone"]) if d["emergency_phone"] else ""
    if d["email"] and not _EMAIL.match(d["email"]):
        raise ValidationError("Email address is not valid.")
    if d["pin"] and not re.fullmatch(r"\d{6}", d["pin"]):
        raise ValidationError("PIN code must be 6 digits.")
    for k in ("dob", "admission_date"):
        if d[k]:
            try:
                dt = datetime.strptime(d[k], "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError("Dates must be valid.")
            if k == "dob" and dt > date.today():
                raise ValidationError("Date of birth cannot be in the future.")
        else:
            d[k] = None
    if d["gender"] not in GENDERS:
        raise ValidationError("Invalid gender.")
    d["session_id"] = d["session_id"] or None
    return d


def age_of(dob: str | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    b = datetime.strptime(dob, "%Y-%m-%d").date()
    t = today or date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def _aadhaar(vault: Vault, raw: str):
    raw = _digits(raw or "")
    if not raw:
        return "", ""
    if not (raw.isdigit() and len(raw) == 12):
        raise ValidationError("Aadhaar number must be exactly 12 digits (or leave it empty - it is optional).")
    return vault.encrypt(raw), raw[-4:]


def find_possible_duplicates(db: Database, name: str, dob: str | None = None, mobile: str = "", exclude_id: int | None = None):
    """Only a WARNING - brothers/sisters legitimately share parents, phone and address."""
    name = (name or "").strip()
    if not name:
        return []
    rows = db.q("SELECT id, student_code, admission_no, name, dob, mobile, father_name FROM students WHERE lower(name)=lower(?) AND id<>?", (name, exclude_id or 0))
    out = []
    for r in rows:
        if (dob and r["dob"] == dob) or (mobile and r["mobile"] == _digits(mobile)):
            out.append(r)
    return out


def create(db: Database, actor: Actor, vault: Vault, data: dict, *, aadhaar: str = "", custom: dict | None = None, photo_src: str | None = None,
           photos_dir: str | None = None, admission_no: str | None = None) -> int:
    require(actor, "students.edit")
    d = _clean(data)
    enc, last4 = _aadhaar(vault, aadhaar)
    with db.tx():
        cv = customfields.validate(db, "student", custom or {}, actor)
        if admission_no:
            admission_no = admission_no.strip()
            if db.one("SELECT 1 FROM students WHERE admission_no=?", (admission_no,)):
                raise ValidationError(f"Admission number {admission_no} already exists.")
        else:
            admission_no = _next_admission_no(db)
        seq = db.next_counter("student_code")
        code = f"STU-{seq:06d}"
        if d["session_id"] is None:
            d["session_id"] = catalog.current_session_id(db)
        if not d["admission_date"]:
            d["admission_date"] = date.today().isoformat()
        cols = ["student_code", "admission_no", "aadhaar_enc", "aadhaar_last4", "status", "created_at", "updated_at", "created_by"] + _FIELDS
        vals = [code, admission_no, enc, last4, "Active", _now(), _now(), actor.id] + [d[k] for k in _FIELDS]
        sid = db.x(f"INSERT INTO students({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals).lastrowid
        customfields.save_values(db, "student", sid, cv)
        if photo_src and photos_dir:
            db.x("UPDATE students SET photo_path=? WHERE id=?", (save_photo(photo_src, photos_dir, sid), sid))
        log(db, actor.id, "student_create", "student", sid, f"{code} {d['name']} adm={admission_no}")
    return sid


def _next_admission_no(db: Database) -> str:
    fmt = settings.get(db, "admission_fmt")
    for _ in range(1000):                      # skip numbers that were typed in manually
        n = settings.format_number(fmt, db.next_counter("admission_no"), date.today())
        if not db.one("SELECT 1 FROM students WHERE admission_no=?", (n,)):
            return n
    raise ValidationError("Could not generate a free admission number.")


def update(db: Database, actor: Actor, vault: Vault, student_id: int, data: dict, *, aadhaar: str | None = None, custom: dict | None = None,
           photo_src: str | None = None, photos_dir: str | None = None, admission_no: str | None = None):
    require(actor, "students.edit")
    old = db.one("SELECT * FROM students WHERE id=?", (student_id,))
    if old is None:
        raise ValidationError("Student not found.")
    d = _clean(data)
    with db.tx():
        sets = {k: d[k] for k in _FIELDS}
        if admission_no is not None and admission_no.strip() != old["admission_no"]:
            admission_no = admission_no.strip()
            if not admission_no:
                raise ValidationError("Admission number cannot be empty.")
            if db.one("SELECT 1 FROM students WHERE admission_no=? AND id<>?", (admission_no, student_id)):
                raise ValidationError(f"Admission number {admission_no} already exists.")
            sets["admission_no"] = admission_no
        if aadhaar is not None:                       # None = leave unchanged, '' = clear
            if not actor.can("students.sensitive"):
                raise ValidationError("You do not have permission to change Aadhaar details.")
            sets["aadhaar_enc"], sets["aadhaar_last4"] = _aadhaar(vault, aadhaar)
        existing = customfields.values_for(db, "student", student_id)
        cv = customfields.validate(db, "student", custom or {}, actor, existing=existing)
        sets["updated_at"] = _now()
        db.x(f"UPDATE students SET {','.join(k + '=?' for k in sets)} WHERE id=?", list(sets.values()) + [student_id])
        customfields.save_values(db, "student", student_id, cv)
        if photo_src and photos_dir:
            db.x("UPDATE students SET photo_path=? WHERE id=?", (save_photo(photo_src, photos_dir, student_id), student_id))
        changed = [k for k in _FIELDS if (old[k] or "") != (d[k] or "")]
        log(db, actor.id, "student_edit", "student", student_id, "changed: " + ",".join(changed))


def save_photo(src: str, photos_dir: str, student_id: int) -> str:
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((600, 600))
            os.makedirs(photos_dir, exist_ok=True)
            dest = os.path.join(photos_dir, f"student_{student_id}_{uuid.uuid4().hex[:6]}.jpg")
            im.save(dest, "JPEG", quality=88)
    except Exception:
        raise ValidationError("This photo could not be read. Use a JPG or PNG image.")
    return dest


def set_status(db: Database, actor: Actor, student_id: int, status: str, reason: str = ""):
    require(actor, "students.deactivate" if status != "Active" else "students.edit")
    if status not in STATUSES:
        raise ValidationError("Invalid status.")
    with db.tx():
        old = db.val("SELECT status FROM students WHERE id=?", (student_id,))
        db.x("UPDATE students SET status=?, updated_at=? WHERE id=?", (status, _now(), student_id))
        if status in ("Inactive", "Withdrawn", "Transferred"):
            db.x("UPDATE enrollments SET status=? WHERE student_id=? AND status='active'",
                 ({"Inactive": "withdrawn", "Withdrawn": "withdrawn", "Transferred": "transferred"}[status], student_id))
        log(db, actor.id, "student_status", "student", student_id, f"{old} -> {status} {reason}")


def delete_if_unused(db: Database, actor: Actor, student_id: int):
    """Hard delete is only allowed for a student entered by mistake (no fees, no attendance)."""
    require(actor, "students.deactivate")
    used = (db.val("SELECT COUNT(*) FROM payments WHERE student_id=?", (student_id,), 0) + db.val("SELECT COUNT(*) FROM attendance WHERE student_id=?", (student_id,), 0)
            + db.val("SELECT COUNT(*) FROM fee_charges WHERE student_id=?", (student_id,), 0))
    if used:
        raise ValidationError("This student has fee or attendance history and cannot be deleted. Use Deactivate instead - the history is kept.")
    with db.tx():
        for t in ("enrollment_subjects",):
            db.x("DELETE FROM enrollment_subjects WHERE enrollment_id IN (SELECT id FROM enrollments WHERE student_id=?)", (student_id,))
        db.x("DELETE FROM installments WHERE student_id=?", (student_id,))
        db.x("DELETE FROM enrollments WHERE student_id=?", (student_id,))
        db.x("DELETE FROM custom_values WHERE entity_id=? AND field_id IN (SELECT id FROM custom_fields WHERE module='student')", (student_id,))
        db.x("DELETE FROM students WHERE id=?", (student_id,))
        log(db, actor.id, "student_delete", "student", student_id, "entered by mistake, no history")


def get(db: Database, actor: Actor, vault: Vault, student_id: int, *, reveal_sensitive: bool = False) -> dict:
    require(actor, "students.view")
    r = db.one("SELECT * FROM students WHERE id=?", (student_id,))
    if r is None:
        raise ValidationError("Student not found.")
    d = dict(r)
    d["age"] = age_of(d["dob"])
    full = vault.decrypt(d.pop("aadhaar_enc", ""))
    d["aadhaar_masked"] = mask(full) if full else ""
    d["aadhaar"] = full if (reveal_sensitive and actor.can("students.sensitive")) else ""
    if reveal_sensitive and actor.can("students.sensitive") and full:
        log(db, actor.id, "aadhaar_view", "student", student_id, "full number shown")
    if not actor.can("students.sensitive") and d["alt_id_no"]:
        d["alt_id_no"] = mask(d["alt_id_no"])
    d["custom"] = customfields.values_for(db, "student", student_id, actor)
    d["enrollments"] = enrollments(db, student_id)
    return d


def search(db: Database, actor: Actor, text: str = "", *, course_id=None, batch_id=None, session_id=None, status=None, category_id=None, limit=500, offset=0):
    require(actor, "students.view")
    where, p = ["1=1"], []
    t = (text or "").strip()
    if t:
        like = f"%{t}%"
        where.append("(s.name LIKE ? OR s.admission_no LIKE ? OR s.student_code LIKE ? OR s.mobile LIKE ? OR s.guardian_mobile LIKE ? OR s.father_name LIKE ?"
                     " OR EXISTS (SELECT 1 FROM payments py WHERE py.student_id=s.id AND py.receipt_no LIKE ?))")
        p += [like] * 7
    if status:
        where.append("s.status=?"); p.append(status)
    if session_id:
        where.append("s.session_id=?"); p.append(session_id)
    if course_id or batch_id or category_id:
        sub = "EXISTS (SELECT 1 FROM enrollments e JOIN courses c ON c.id=e.course_id WHERE e.student_id=s.id AND e.status='active'"
        if course_id:
            sub += " AND e.course_id=?"; p.append(course_id)
        if batch_id:
            sub += " AND e.batch_id=?"; p.append(batch_id)
        if category_id:
            sub += " AND c.category_id=?"; p.append(category_id)
        where.append(sub + ")")
    sql = ("SELECT s.id, s.student_code, s.admission_no, s.name, s.father_name, s.mobile, s.guardian_mobile, s.status, s.admission_date, s.photo_path, "
           "(SELECT GROUP_CONCAT(c.name || COALESCE(' / ' || b.name, ''), ', ') FROM enrollments e JOIN courses c ON c.id=e.course_id LEFT JOIN batches b ON b.id=e.batch_id "
           " WHERE e.student_id=s.id AND e.status='active') AS courses FROM students s WHERE " + " AND ".join(where) + " ORDER BY s.id DESC LIMIT ? OFFSET ?")
    return db.q(sql, p + [limit, offset])


def count(db: Database, status: str | None = None) -> int:
    return db.val("SELECT COUNT(*) FROM students" + (" WHERE status=?" if status else ""), (status,) if status else (), 0)


# ---------------- enrollments
def enrollments(db: Database, student_id: int):
    return db.q("SELECT e.*, c.name AS course, c.kind, k.name AS category, b.name AS batch, b.timing AS batch_timing, t.name AS teacher, s.name AS session "
                "FROM enrollments e JOIN courses c ON c.id=e.course_id JOIN categories k ON k.id=c.category_id LEFT JOIN batches b ON b.id=e.batch_id "
                "LEFT JOIN teachers t ON t.id=b.teacher_id LEFT JOIN academic_sessions s ON s.id=e.session_id WHERE e.student_id=? ORDER BY e.id", (student_id,))


def enrollment_subject_ids(db: Database, enrollment_id: int):
    return [r["subject_id"] for r in db.q("SELECT subject_id FROM enrollment_subjects WHERE enrollment_id=?", (enrollment_id,))]


def add_enrollment(db: Database, actor: Actor, student_id: int, course_id: int, *, batch_id=None, session_id=None, start_date=None, subject_ids=()) -> int:
    require(actor, "students.edit")
    c = catalog.course(db, course_id)
    if c is None:
        raise ValidationError("Course not found.")
    if batch_id:
        b = db.one("SELECT course_id FROM batches WHERE id=?", (batch_id,))
        if b is None or b["course_id"] != course_id:
            raise ValidationError("The batch does not belong to the selected course.")
    if db.one("SELECT 1 FROM enrollments WHERE student_id=? AND course_id=? AND session_id IS ? AND status='active'", (student_id, course_id, session_id)):
        raise ValidationError("The student is already enrolled in this course for this session.")
    with db.tx():
        eid = db.x("INSERT INTO enrollments(student_id,course_id,batch_id,session_id,status,start_date,created_at) VALUES (?,?,?,?, 'active', ?, ?)",
                   (student_id, course_id, batch_id, session_id or catalog.current_session_id(db), start_date or date.today().isoformat(), _now())).lastrowid
        db.conn.executemany("INSERT OR IGNORE INTO enrollment_subjects(enrollment_id,subject_id) VALUES (?,?)", [(eid, s) for s in subject_ids])
        log(db, actor.id, "enroll", "enrollment", eid, f"student {student_id} -> {c['name']}")
    return eid


def change_batch(db: Database, actor: Actor, enrollment_id: int, batch_id):
    require(actor, "students.edit")
    e = db.one("SELECT * FROM enrollments WHERE id=?", (enrollment_id,))
    if batch_id:
        b = db.one("SELECT course_id FROM batches WHERE id=?", (batch_id,))
        if b is None or b["course_id"] != e["course_id"]:
            raise ValidationError("The batch does not belong to this course.")
    db.x("UPDATE enrollments SET batch_id=? WHERE id=?", (batch_id, enrollment_id))
    log(db, actor.id, "batch_change", "enrollment", enrollment_id, f"batch {e['batch_id']} -> {batch_id}")


def set_enrollment_status(db: Database, actor: Actor, enrollment_id: int, status: str, *, certificate: str | None = None):
    require(actor, "students.edit")
    if status not in ("active", "completed", "withdrawn", "transferred"):
        raise ValidationError("Invalid status.")
    with db.tx():
        db.x("UPDATE enrollments SET status=?, completed_on=? WHERE id=?", (status, date.today().isoformat() if status == "completed" else None, enrollment_id))
        if certificate is not None:
            db.x("UPDATE enrollments SET certificate_status=? WHERE id=?", (certificate, enrollment_id))
        log(db, actor.id, "enrollment_status", "enrollment", enrollment_id, status)
