"""Administrator-defined fields. Values live in a generic table (no ALTER TABLE, no generated code), so renaming or
deactivating a field never destroys data."""
import json
import os
import re
import shutil
import uuid
from datetime import date, datetime

from app.database.db import Database
from app.security.permissions import Actor, ValidationError, require
from app.services.audit import log

FIELD_TYPES = {
    "text": "Short text", "longtext": "Long text", "int": "Integer", "decimal": "Decimal / currency", "date": "Date",
    "dropdown": "Dropdown", "multiselect": "Multi-select", "checkbox": "Checkbox", "yesno": "Yes / No",
    "email": "Email", "phone": "Phone", "url": "URL", "file": "File attachment",
}
MODULES = {"student": "Student", "course": "Course / class", "batch": "Batch", "teacher": "Teacher", "payment": "Fee payment"}
FILE_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
FILE_MAX = 5 * 1024 * 1024
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return s or "field"


def save_field(db: Database, actor: Actor, *, id=None, module, label, field_type, required=False, default_value="", max_length=None,
               choices=(), section="Additional information", visible_roles=(), editable_roles=(), print_on_forms=False, active=True) -> int:
    require(actor, "fields.manage")
    label = (label or "").strip()
    if not label:
        raise ValidationError("Field label is required.")
    if module not in MODULES or field_type not in FIELD_TYPES:
        raise ValidationError("Unknown module or field type.")
    choices = [c.strip() for c in choices if c and c.strip()]
    if field_type in ("dropdown", "multiselect") and not choices:
        raise ValidationError("Add at least one choice for a dropdown / multi-select field.")
    if max_length is not None and max_length <= 0:
        raise ValidationError("Maximum length must be positive.")
    with db.tx():
        if id is None:
            key = _slug(label)
            base, n = key, 2
            while db.one("SELECT 1 FROM custom_fields WHERE module=? AND field_key=?", (module, key)):
                key = f"{base}_{n}"; n += 1
            order = db.val("SELECT COALESCE(MAX(sort_order),0)+1 FROM custom_fields WHERE module=?", (module,))
            id = db.x("INSERT INTO custom_fields(module,field_key,label,field_type,required,default_value,max_length,choices,section,sort_order,visible_roles,editable_roles,print_on_forms,active,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (module, key, label, field_type, int(required), default_value or "", max_length, json.dumps(choices), section or "Additional information", order,
                       ",".join(visible_roles), ",".join(editable_roles), int(print_on_forms), int(active), datetime.now().isoformat(timespec="seconds"))).lastrowid
            log(db, actor.id, "field_add", "custom_field", id, f"{module}.{label}")
        else:
            old = db.one("SELECT * FROM custom_fields WHERE id=?", (id,))
            if old is None:
                raise ValidationError("Field not found.")
            if old["field_type"] != field_type and db.val("SELECT COUNT(*) FROM custom_values WHERE field_id=?", (id,), 0) > 0:
                raise ValidationError("The type of a field that already holds data cannot be changed. Add a new field instead.")
            db.x("UPDATE custom_fields SET label=?,field_type=?,required=?,default_value=?,max_length=?,choices=?,section=?,visible_roles=?,editable_roles=?,print_on_forms=?,active=? WHERE id=?",
                 (label, field_type, int(required), default_value or "", max_length, json.dumps(choices), section or "Additional information",
                  ",".join(visible_roles), ",".join(editable_roles), int(print_on_forms), int(active), id))
            log(db, actor.id, "field_edit", "custom_field", id, label)
    return id


def set_order(db, actor, ordered_ids):
    require(actor, "fields.manage")
    with db.tx():
        for i, fid in enumerate(ordered_ids):
            db.x("UPDATE custom_fields SET sort_order=? WHERE id=?", (i, fid))


def set_active(db, actor, field_id, active):
    """'Delete' = deactivate: stored values are kept and come back if the field is re-enabled."""
    require(actor, "fields.manage")
    db.x("UPDATE custom_fields SET active=? WHERE id=?", (1 if active else 0, field_id))
    log(db, actor.id, "field_enable" if active else "field_disable", "custom_field", field_id, "")


def fields(db: Database, module: str, actor: Actor | None = None, *, include_inactive=False, for_edit=False):
    rows = db.q("SELECT * FROM custom_fields WHERE module=?" + ("" if include_inactive else " AND active=1") + " ORDER BY section, sort_order, id", (module,))
    out = []
    for r in rows:
        d = dict(r)
        d["choices"] = json.loads(d["choices"] or "[]")
        if actor is not None:
            roles = [x for x in (d["editable_roles"] if for_edit else d["visible_roles"]).split(",") if x]
            vis = [x for x in d["visible_roles"].split(",") if x]
            if actor.role != "super_admin":
                if vis and actor.role not in vis:
                    continue
                if for_edit and roles and actor.role not in roles:
                    d["readonly"] = True
        out.append(d)
    return out


def _check_value(f: dict, raw):
    """Returns the normalised text to store (or '' for empty). Raises ValidationError with a clear message."""
    t = f["field_type"]
    label = f["label"]
    if raw is None:
        raw = ""
    if isinstance(raw, (list, tuple)):
        raw = json.dumps([str(x) for x in raw]) if raw else ""
    if isinstance(raw, bool):
        raw = "1" if raw else "0"
    s = str(raw).strip()
    if s == "":
        if f["required"]:
            raise ValidationError(f"'{label}' is required.")
        return ""
    ml = f.get("max_length")
    if ml and t in ("text", "longtext", "email", "phone", "url") and len(s) > ml:
        raise ValidationError(f"'{label}' can have at most {ml} characters.")
    if t == "int":
        try:
            return str(int(s))
        except ValueError:
            raise ValidationError(f"'{label}' must be a whole number.")
    if t == "decimal":
        from app.utils.money import to_paise
        try:
            return f"{to_paise(s) / 100:.2f}"
        except ValueError:
            raise ValidationError(f"'{label}' must be a number.")
    if t == "date":
        try:
            return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
        except ValueError:
            raise ValidationError(f"'{label}' must be a valid date.")
    if t == "dropdown":
        if s not in f["choices"]:
            raise ValidationError(f"'{label}': choose one of the listed options.")
        return s
    if t == "multiselect":
        try:
            vals = json.loads(s)
        except Exception:
            vals = [x.strip() for x in s.split(",") if x.strip()]
        if any(v not in f["choices"] for v in vals):
            raise ValidationError(f"'{label}': choose only listed options.")
        return json.dumps(vals)
    if t in ("checkbox", "yesno"):
        return "1" if s.lower() in ("1", "true", "yes", "y") else "0"
    if t == "email":
        if not _EMAIL.match(s):
            raise ValidationError(f"'{label}' must be a valid email address.")
        return s
    if t == "phone":
        digits = re.sub(r"[\s\-+()]", "", s)
        if not digits.isdigit() or not 7 <= len(digits) <= 15:
            raise ValidationError(f"'{label}' must be a valid phone number.")
        return s
    if t == "url":
        if not re.match(r"^https?://\S+\.\S+$", s):
            raise ValidationError(f"'{label}' must be a web address starting with http:// or https://")
        return s
    return s


def validate(db: Database, module: str, values: dict, actor: Actor | None = None, *, existing: dict | None = None) -> dict:
    """values: {field_key: raw}. Returns {field_id: normalised}. Fields the actor may not edit are left untouched."""
    out = {}
    existing = existing or {}
    for f in fields(db, module, actor, for_edit=True):
        if f.get("readonly"):
            continue
        key = f["field_key"]
        if key in values:
            raw = values[key]
        elif key in existing:
            raw = existing[key]
        else:
            raw = f["default_value"]
        if f["field_type"] == "file":
            out[f["id"]] = str(raw or "")                  # already-stored path; new files go through attach_file()
            if f["required"] and not out[f["id"]]:
                raise ValidationError(f"'{f['label']}' is required.")
            continue
        out[f["id"]] = _check_value(f, raw)
    return out


def save_values(db: Database, module: str, entity_id: int, normalised: dict):
    for fid, val in normalised.items():
        if val == "":
            db.x("DELETE FROM custom_values WHERE field_id=? AND entity_id=?", (fid, entity_id))
        else:
            db.x("INSERT INTO custom_values(field_id,entity_id,value) VALUES (?,?,?) ON CONFLICT(field_id,entity_id) DO UPDATE SET value=excluded.value", (fid, entity_id, val))


def values_for(db: Database, module: str, entity_id: int, actor: Actor | None = None) -> dict:
    out = {}
    stored = {r["field_id"]: r["value"] for r in db.q("SELECT field_id, value FROM custom_values WHERE entity_id=?", (entity_id,))}
    for f in fields(db, module, actor):
        out[f["field_key"]] = stored.get(f["id"], "")
    return out


def attach_file(attachments_dir: str, src: str) -> str:
    ext = os.path.splitext(src)[1].lower()
    if ext not in FILE_EXT:
        raise ValidationError("Allowed attachment types: " + ", ".join(sorted(FILE_EXT)))
    if not os.path.isfile(src):
        raise ValidationError("File not found.")
    if os.path.getsize(src) > FILE_MAX:
        raise ValidationError("Attachment is larger than 5 MB.")
    os.makedirs(attachments_dir, exist_ok=True)
    dest = os.path.join(attachments_dir, uuid.uuid4().hex + ext)
    shutil.copyfile(src, dest)
    return dest


def display_value(f: dict, value: str) -> str:
    if value in ("", None):
        return ""
    t = f["field_type"]
    if t in ("checkbox", "yesno"):
        return "Yes" if value == "1" else "No"
    if t == "multiselect":
        try:
            return ", ".join(json.loads(value))
        except Exception:
            return value
    if t == "date":
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%d-%m-%Y")
        except ValueError:
            return value
    if t == "file":
        return os.path.basename(value)
    return value
