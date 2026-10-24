"""Sessions, categories, classes/courses, subjects, teachers, batches, payment modes, installment templates,
attendance statuses. Items are deactivated, never hard-deleted when in use."""
from app.database.db import Database
from app.security.permissions import Actor, ValidationError, require
from app.services.audit import log


def _name(v: str, what: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValidationError(f"{what} name is required.")
    return v


def _lookup(db: Database, table: str):
    return db.q(f"SELECT * FROM {table} ORDER BY 1")


# ---------- sessions
def add_session(db, actor, name, start=None, end=None) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Session")
    if db.one("SELECT 1 FROM academic_sessions WHERE name = ?", (name,)):
        raise ValidationError("This session already exists.")
    with db.tx():
        sid = db.x("INSERT INTO academic_sessions(name,start_date,end_date) VALUES (?,?,?)", (name, start, end)).lastrowid
        if not db.val("SELECT value FROM settings WHERE key='current_session_id'"):
            db.x("INSERT INTO settings(key,value) VALUES ('current_session_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(sid),))
        log(db, actor.id, "session_add", "session", sid, name)
    return sid


def sessions(db, active_only=True):
    return db.q("SELECT * FROM academic_sessions" + (" WHERE active=1" if active_only else "") + " ORDER BY COALESCE(start_date,'') DESC, id DESC")


def current_session_id(db) -> int | None:
    v = db.val("SELECT value FROM settings WHERE key='current_session_id'")
    if v and str(v).isdigit() and db.one("SELECT 1 FROM academic_sessions WHERE id=?", (int(v),)):
        return int(v)
    r = db.one("SELECT id FROM academic_sessions WHERE active=1 ORDER BY id DESC")
    return r["id"] if r else None


def set_current_session(db, actor, sid):
    require(actor, "catalog.manage")
    db.x("INSERT INTO settings(key,value) VALUES ('current_session_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(sid),))


def set_session_active(db, actor, sid, active):
    require(actor, "catalog.manage")
    db.x("UPDATE academic_sessions SET active=? WHERE id=?", (1 if active else 0, sid))


# ---------- categories
def add_category(db, actor, name) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Category")
    if db.one("SELECT 1 FROM categories WHERE name = ?", (name,)):
        raise ValidationError("This category already exists.")
    with db.tx():
        cid = db.x("INSERT INTO categories(name, sort_order) VALUES (?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM categories))", (name,)).lastrowid
        log(db, actor.id, "category_add", "category", cid, name)
    return cid


def rename_category(db, actor, cid, name):
    require(actor, "catalog.manage")
    name = _name(name, "Category")
    if db.one("SELECT 1 FROM categories WHERE name = ? AND id <> ?", (name, cid)):
        raise ValidationError("This category already exists.")
    db.x("UPDATE categories SET name=? WHERE id=?", (name, cid))


def set_category_active(db, actor, cid, active):
    require(actor, "catalog.manage")
    db.x("UPDATE categories SET active=? WHERE id=?", (1 if active else 0, cid))


def categories(db, active_only=True):
    return db.q("SELECT * FROM categories" + (" WHERE active=1" if active_only else "") + " ORDER BY sort_order, name")


# ---------- courses / classes
def save_course(db, actor, *, id=None, category_id, name, kind="course", duration_months=0, fee_paise=0, admission_fee_paise=0,
                registration_fee_paise=0, default_installments=1, subject_ids=(), active=True) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Course / class")
    if kind not in ("class", "course"):
        raise ValidationError("Kind must be class or course.")
    for v, w in ((fee_paise, "Fee"), (admission_fee_paise, "Admission fee"), (registration_fee_paise, "Registration fee")):
        if v < 0:
            raise ValidationError(f"{w} cannot be negative.")
    if default_installments < 1 or duration_months < 0:
        raise ValidationError("Installments must be at least 1 and duration cannot be negative.")
    dup = db.one("SELECT 1 FROM courses WHERE category_id=? AND name=? AND id<>?", (category_id, name, id or 0))
    if dup:
        raise ValidationError("This course already exists in the category.")
    with db.tx():
        if id is None:
            id = db.x("INSERT INTO courses(category_id,name,kind,duration_months,fee_paise,admission_fee_paise,registration_fee_paise,default_installments,active,sort_order) "
                      "VALUES (?,?,?,?,?,?,?,?,?,(SELECT COALESCE(MAX(sort_order),0)+1 FROM courses))",
                      (category_id, name, kind, duration_months, fee_paise, admission_fee_paise, registration_fee_paise, default_installments, 1 if active else 0)).lastrowid
            act = "course_add"
        else:
            db.x("UPDATE courses SET category_id=?,name=?,kind=?,duration_months=?,fee_paise=?,admission_fee_paise=?,registration_fee_paise=?,default_installments=?,active=? WHERE id=?",
                 (category_id, name, kind, duration_months, fee_paise, admission_fee_paise, registration_fee_paise, default_installments, 1 if active else 0, id))
            act = "course_edit"
        db.x("DELETE FROM course_subjects WHERE course_id=?", (id,))
        db.conn.executemany("INSERT OR IGNORE INTO course_subjects(course_id,subject_id) VALUES (?,?)", [(id, s) for s in subject_ids])
        log(db, actor.id, act, "course", id, f"{name} fee={fee_paise}")
    return id


def courses(db, category_id=None, active_only=True, kind=None):
    sql = ("SELECT c.*, k.name AS category FROM courses c JOIN categories k ON k.id=c.category_id WHERE 1=1"
           + (" AND c.active=1 AND k.active=1" if active_only else "") + (" AND c.category_id=?" if category_id else "") + (" AND c.kind=?" if kind else "")
           + " ORDER BY k.sort_order, c.sort_order, c.name")
    p = [x for x in (category_id, kind) if x]
    return db.q(sql, p)


def course(db, cid):
    return db.one("SELECT c.*, k.name AS category FROM courses c JOIN categories k ON k.id=c.category_id WHERE c.id=?", (cid,))


def course_subject_ids(db, cid):
    return [r["subject_id"] for r in db.q("SELECT subject_id FROM course_subjects WHERE course_id=?", (cid,))]


# ---------- subjects / teachers
def add_subject(db, actor, name) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Subject")
    r = db.one("SELECT id FROM subjects WHERE name=?", (name,))
    if r:
        raise ValidationError("This subject already exists.")
    return db.x("INSERT INTO subjects(name) VALUES (?)", (name,)).lastrowid


def subjects(db, active_only=True):
    return db.q("SELECT * FROM subjects" + (" WHERE active=1" if active_only else "") + " ORDER BY name")


def set_subject_active(db, actor, sid, active):
    require(actor, "catalog.manage")
    db.x("UPDATE subjects SET active=? WHERE id=?", (1 if active else 0, sid))


def save_teacher(db, actor, *, id=None, name, phone="", email="", active=True) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Teacher")
    with db.tx():
        if id is None:
            id = db.x("INSERT INTO teachers(name,phone,email,active) VALUES (?,?,?,?)", (name, phone.strip(), email.strip(), 1 if active else 0)).lastrowid
        else:
            db.x("UPDATE teachers SET name=?,phone=?,email=?,active=? WHERE id=?", (name, phone.strip(), email.strip(), 1 if active else 0, id))
        log(db, actor.id, "teacher_save", "teacher", id, name)
    return id


def teachers(db, active_only=True):
    return db.q("SELECT * FROM teachers" + (" WHERE active=1" if active_only else "") + " ORDER BY name")


# ---------- batches
def save_batch(db, actor, *, id=None, course_id, session_id, name, timing="", teacher_id=None, active=True) -> int:
    require(actor, "catalog.manage")
    name = _name(name, "Batch")
    if db.one("SELECT 1 FROM batches WHERE course_id=? AND session_id IS ? AND name=? AND id<>?", (course_id, session_id, name, id or 0)):
        raise ValidationError("A batch with this name already exists for this course and session.")
    with db.tx():
        if id is None:
            id = db.x("INSERT INTO batches(course_id,session_id,name,timing,teacher_id,active) VALUES (?,?,?,?,?,?)",
                      (course_id, session_id, name, timing.strip(), teacher_id, 1 if active else 0)).lastrowid
        else:
            db.x("UPDATE batches SET course_id=?,session_id=?,name=?,timing=?,teacher_id=?,active=? WHERE id=?",
                 (course_id, session_id, name, timing.strip(), teacher_id, 1 if active else 0, id))
        log(db, actor.id, "batch_save", "batch", id, name)
    return id


def batches(db, course_id=None, session_id=None, active_only=True):
    sql = ("SELECT b.*, c.name AS course, t.name AS teacher, s.name AS session FROM batches b JOIN courses c ON c.id=b.course_id "
           "LEFT JOIN teachers t ON t.id=b.teacher_id LEFT JOIN academic_sessions s ON s.id=b.session_id WHERE 1=1"
           + (" AND b.active=1" if active_only else "") + (" AND b.course_id=?" if course_id else "") + (" AND b.session_id=?" if session_id else "")
           + " ORDER BY c.name, b.name")
    return db.q(sql, [x for x in (course_id, session_id) if x])


def save_timetable_slot(db, actor, batch_id, weekday, start, end, subject_id=None, teacher_id=None) -> int:
    require(actor, "catalog.manage")
    if not 0 <= weekday <= 6:
        raise ValidationError("Weekday must be 0 (Mon) to 6 (Sun).")
    return db.x("INSERT INTO timetable(batch_id,weekday,start_time,end_time,subject_id,teacher_id) VALUES (?,?,?,?,?,?)",
                (batch_id, weekday, start, end, subject_id, teacher_id)).lastrowid


def timetable(db, batch_id):
    return db.q("SELECT tt.*, s.name AS subject, t.name AS teacher FROM timetable tt LEFT JOIN subjects s ON s.id=tt.subject_id "
                "LEFT JOIN teachers t ON t.id=tt.teacher_id WHERE batch_id=? ORDER BY weekday, start_time", (batch_id,))


def delete_timetable_slot(db, actor, slot_id):
    require(actor, "catalog.manage")
    db.x("DELETE FROM timetable WHERE id=?", (slot_id,))


# ---------- lookups
def payment_modes(db, active_only=True):
    return [r["name"] for r in db.q("SELECT name FROM payment_modes" + (" WHERE active=1" if active_only else "") + " ORDER BY sort_order, name")]


def add_payment_mode(db, actor, name):
    require(actor, "settings.manage")
    name = _name(name, "Payment mode")
    db.x("INSERT INTO payment_modes(name, sort_order) VALUES (?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM payment_modes)) "
         "ON CONFLICT(name) DO UPDATE SET active=1", (name,))


def set_payment_mode_active(db, actor, name, active):
    require(actor, "settings.manage")
    if not active and len(payment_modes(db)) <= 1:
        raise ValidationError("At least one payment mode must stay active.")
    db.x("UPDATE payment_modes SET active=? WHERE name=?", (1 if active else 0, name))


def fee_categories(db, active_only=True):
    return [r["name"] for r in db.q("SELECT name FROM fee_categories" + (" WHERE active=1" if active_only else "") + " ORDER BY name")]


def add_fee_category(db, actor, name):
    require(actor, "settings.manage")
    db.x("INSERT INTO fee_categories(name) VALUES (?) ON CONFLICT(name) DO UPDATE SET active=1", (_name(name, "Fee category"),))


def set_fee_category_active(db, actor, name, active):
    require(actor, "settings.manage")
    db.x("UPDATE fee_categories SET active=? WHERE name=?", (1 if active else 0, name))


def installment_templates(db, active_only=True):
    return db.q("SELECT * FROM installment_templates" + (" WHERE active=1" if active_only else "") + " ORDER BY count, name")


def save_installment_template(db, actor, name, count, every_months, id=None):
    require(actor, "settings.manage")
    if count < 1 or every_months < 0:
        raise ValidationError("Count must be at least 1.")
    name = _name(name, "Schedule")
    if id is None:
        db.x("INSERT INTO installment_templates(name,count,every_months) VALUES (?,?,?)", (name, count, every_months))
    else:
        db.x("UPDATE installment_templates SET name=?,count=?,every_months=? WHERE id=?", (name, count, every_months, id))


def set_template_active(db, actor, tid, active):
    require(actor, "settings.manage")
    db.x("UPDATE installment_templates SET active=? WHERE id=?", (1 if active else 0, tid))


def attendance_statuses(db, active_only=True):
    return db.q("SELECT * FROM attendance_statuses" + (" WHERE active=1" if active_only else "") + " ORDER BY sort_order, code")


def save_attendance_status(db, actor, code, label, counts_present, counts_in_total, color="#64748b"):
    require(actor, "settings.manage")
    code = (code or "").strip().upper()
    if not code or len(code) > 3:
        raise ValidationError("Status code must be 1 to 3 letters.")
    if not (0 <= counts_present <= 1):
        raise ValidationError("'Counts as present' must be between 0 and 1 (0.5 = half day).")
    db.x("INSERT INTO attendance_statuses(code,label,counts_present,counts_in_total,color,sort_order) VALUES (?,?,?,?,?,(SELECT COALESCE(MAX(sort_order),0)+1 FROM attendance_statuses)) "
         "ON CONFLICT(code) DO UPDATE SET label=excluded.label, counts_present=excluded.counts_present, counts_in_total=excluded.counts_in_total, color=excluded.color, active=1",
         (code, _name(label, "Status"), float(counts_present), 1 if counts_in_total else 0, color))


def set_attendance_status_active(db, actor, code, active):
    require(actor, "settings.manage")
    if not active and len(attendance_statuses(db)) <= 2:
        raise ValidationError("Keep at least two attendance statuses.")
    db.x("UPDATE attendance_statuses SET active=? WHERE code=?", (1 if active else 0, code))
