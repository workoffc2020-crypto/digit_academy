"""Settings + seed data. Everything configurable lives in the database so no source edits are ever needed."""
import os
import shutil
from datetime import date

from app.database.db import Database
from app.paths import default_backup_dir
from app.security.permissions import DEFAULT_ROLE_PERMS, Actor, ValidationError, require
from app.services.audit import log

DEFAULTS = {
    "institute_name": "Digit Academy", "institute_sub": "Charitable Trust", "address": "", "phone": "", "email": "", "website": "",
    "logo_path": "", "receipt_footer": "Thank you. Fees once paid are not refundable.", "receipt_terms": "",
    "admission_fmt": "DA{YY}{SEQ:04}", "receipt_fmt": "DA/{FY}/{SEQ:05}", "date_format": "%d-%m-%Y",
    "receipt_size": "A4", "default_printer": "", "currency": "INR", "discount_limit_percent": "10",
    "lock_minutes": "15", "backup_dir": "", "auto_backup": "daily", "last_auto_backup": "", "theme": "light",
    "font_size": "10", "language": "en", "show_aadhaar_on_forms": "0", "current_session_id": "",
}

DEFAULT_MODES = ["Cash", "UPI", "Bank Transfer", "Debit/Credit Card", "Cheque"]
DEFAULT_FEE_CATEGORIES = ["Late fee", "Exam fee", "Study material", "Certificate fee", "Other"]
DEFAULT_STATUSES = [("P", "Present", 1.0, 1, "#16a34a"), ("A", "Absent", 0.0, 1, "#dc2626"), ("L", "Late", 1.0, 1, "#d97706"),
                    ("LV", "Leave", 0.0, 1, "#2563eb"), ("H", "Holiday", 0.0, 0, "#64748b")]
DEFAULT_TEMPLATES = [("Full payment (1)", 1, 0), ("Monthly x3", 3, 1), ("Monthly x6", 6, 1), ("Quarterly x4", 4, 3), ("Monthly x12", 12, 1)]
SEED_CATEGORIES = {
    "School Tuition (Class 1-10)": [("Class %d" % i, "class") for i in range(1, 11)],
    "Senior Secondary (Class 11-12)": [("Class 11", "class"), ("Class 12", "class")],
    "Computer Courses": [("Basic Computer", "course"), ("Advanced Computer", "course"), ("MS Office", "course"), ("Advanced Excel", "course"),
                         ("Tally & Accounting", "course"), ("Programming", "course"), ("Python", "course"), ("Digital Marketing", "course"),
                         ("Graphic Design", "course")],
    "English Speaking": [("Spoken English", "course")],
    "Professional & Vocational": [],
    "Short-term Skill Development": [],
    "Government Exam Preparation": [],
    "Other Courses": [],
}


def get(db: Database, key: str) -> str:
    r = db.one("SELECT value FROM settings WHERE key = ?", (key,))
    return r["value"] if r else DEFAULTS.get(key, "")


def get_all(db: Database) -> dict:
    d = dict(DEFAULTS)
    for r in db.q("SELECT key, value FROM settings"):
        d[r["key"]] = r["value"]
    return d


def set_value(db: Database, key: str, value: str):
    db.x("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def update(db: Database, actor: Actor, values: dict):
    require(actor, "settings.manage")
    for k, v in values.items():
        if k not in DEFAULTS:
            raise ValidationError(f"Unknown setting: {k}")
    if "discount_limit_percent" in values:
        try:
            if not (0 <= float(values["discount_limit_percent"]) <= 100):
                raise ValueError
        except ValueError:
            raise ValidationError("Discount limit must be between 0 and 100 percent.")
    for k in ("admission_fmt", "receipt_fmt"):
        if k in values:
            try:
                format_number(values[k], 1, date.today())
            except Exception:
                raise ValidationError("Invalid number format. Use tokens like {YY} {FY} {SEQ:05}")
            if "{SEQ" not in values[k]:
                raise ValidationError("The number format must contain {SEQ:05} so every number is unique.")
    with db.tx():
        for k, v in values.items():
            set_value(db, k, v)
        log(db, actor.id, "settings_update", "settings", "", ", ".join(sorted(values)))


def fy_label(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y % 100:02d}-{(y + 1) % 100:02d}"


def format_number(fmt: str, seq: int, d: date) -> str:
    """Tokens: {YY} {YYYY} {FY} (Indian financial year 25-26) {SEQ} / {SEQ:05}."""
    import re
    out = fmt.replace("{YYYY}", f"{d.year}").replace("{YY}", f"{d.year % 100:02d}").replace("{FY}", fy_label(d))
    m = re.search(r"\{SEQ(?::(\d+))?\}", out)
    if m:
        width = int(m.group(1)) if m.group(1) else 1
        out = out[:m.start()] + str(seq).zfill(width) + out[m.end():]
    return out


def logo_file(db: Database | None) -> str:
    """The logo to print/show: the one chosen in Settings, else the bundled Digit Academy logo."""
    from app.paths import resource_path
    if db is not None:
        p = get(db, "logo_path")
        if p and os.path.isfile(p):
            return p
    return resource_path("assets/logo.png")


def set_logo(db: Database, actor: Actor, src: str, branding_dir: str):
    require(actor, "settings.manage")
    from PIL import Image
    try:
        with Image.open(src) as im:
            im.verify()
    except Exception:
        raise ValidationError("This file is not a valid image.")
    ext = os.path.splitext(src)[1].lower() or ".png"
    dest = os.path.join(branding_dir, "logo" + ext)
    os.makedirs(branding_dir, exist_ok=True)
    shutil.copyfile(src, dest)
    with db.tx():
        set_value(db, "logo_path", dest)
        log(db, actor.id, "logo_change", "settings", "", os.path.basename(src))
    return dest


def effective_backup_dir(db: Database) -> str:
    return get(db, "backup_dir") or default_backup_dir()


def seed(db: Database, *, with_catalog: bool = True):
    """Idempotent defaults. Fees are seeded as 0 - the owner enters the real fees."""
    with db.tx():
        for k, v in DEFAULTS.items():
            db.x("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", (k, v))
        for i, m in enumerate(DEFAULT_MODES):
            db.x("INSERT OR IGNORE INTO payment_modes(name, sort_order) VALUES (?,?)", (m, i))
        for c in DEFAULT_FEE_CATEGORIES:
            db.x("INSERT OR IGNORE INTO fee_categories(name) VALUES (?)", (c,))
        for i, (code, label, pres, tot, col) in enumerate(DEFAULT_STATUSES):
            db.x("INSERT OR IGNORE INTO attendance_statuses(code,label,counts_present,counts_in_total,color,sort_order) VALUES (?,?,?,?,?,?)",
                 (code, label, pres, tot, col, i))
        for n, c, e in DEFAULT_TEMPLATES:
            db.x("INSERT OR IGNORE INTO installment_templates(name,count,every_months) VALUES (?,?,?)", (n, c, e))
        if db.val("SELECT COUNT(*) FROM role_permissions", default=0) == 0:
            for role, perms in DEFAULT_ROLE_PERMS.items():
                db.conn.executemany("INSERT INTO role_permissions(role, permission) VALUES (?,?)", [(role, p) for p in sorted(perms)])
        if with_catalog and db.val("SELECT COUNT(*) FROM categories", default=0) == 0:
            for i, (cat, items) in enumerate(SEED_CATEGORIES.items()):
                cid = db.x("INSERT INTO categories(name, sort_order) VALUES (?,?)", (cat, i)).lastrowid
                for j, (nm, kind) in enumerate(items):
                    db.x("INSERT INTO courses(category_id, name, kind, sort_order) VALUES (?,?,?,?)", (cid, nm, kind, j))
            for s in ("English", "Hindi", "Mathematics", "Science", "Social Science", "Computer", "Accountancy", "Physics", "Chemistry", "Biology"):
                db.x("INSERT OR IGNORE INTO subjects(name) VALUES (?)", (s,))
