"""Backups use SQLite's own backup API (safe while the app is open), are verified, and live OUTSIDE the install folder.
A backup is a .dabak zip: database + secret key + photos + attachments + branding + manifest with checksums."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta

from app import APP_ID, VERSION
from app.database.db import Database
from app.paths import Paths
from app.security.permissions import Actor, ValidationError, require
from app.services import settings
from app.services.audit import log

EXT = ".dabak"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _add_tree(z: zipfile.ZipFile, root: str, arc: str, manifest: dict):
    if not os.path.isdir(root):
        return
    for dp, _, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dp, fn)
            name = f"{arc}/{os.path.relpath(full, root).replace(os.sep, '/')}"
            z.write(full, name)
            manifest["files"][name] = _sha(full)


def create_backup(db: Database, paths: Paths, dest_dir: str, *, actor: Actor | None = None, label: str = "manual") -> dict:
    """Returns {path, size, ok, message}. `ok` is only True after the written file has been re-opened and verified."""
    if actor is not None:
        require(actor, "backup.manage")
    os.makedirs(dest_dir, exist_ok=True)
    name = f"DigitAcademy_{label}_{datetime.now():%Y%m%d_%H%M%S}{EXT}"
    final = os.path.join(dest_dir, name)
    tmpdir = tempfile.mkdtemp(prefix="da_bak_")
    try:
        snap = os.path.join(tmpdir, "academy.db")
        dst = sqlite3.connect(snap)
        try:
            db.conn.backup(dst)
        finally:
            dst.close()
        manifest = {"app": APP_ID, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"), "db_version": db.version(), "files": {}}
        part = final + ".part"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "academy.db")
            manifest["files"]["academy.db"] = _sha(snap)
            if os.path.isfile(paths.key):
                z.write(paths.key, "secret.key")
                manifest["files"]["secret.key"] = _sha(paths.key)
            _add_tree(z, paths.photos, "photos", manifest)
            _add_tree(z, paths.attachments, "attachments", manifest)
            _add_tree(z, paths.logo_dir, "branding", manifest)
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
        ok, msg = verify_backup(part)
        if not ok:
            os.remove(part)
            return {"path": None, "size": 0, "ok": False, "message": "Backup failed verification: " + msg}
        os.replace(part, final)
        res = {"path": final, "size": os.path.getsize(final), "ok": True, "message": "Backup created and verified."}
        if actor is not None:
            log(db, actor.id, "backup_create", "backup", name, f"{res['size']} bytes")
        return res
    except Exception as e:
        return {"path": None, "size": 0, "ok": False, "message": f"Backup failed: {e}"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def verify_backup(path: str) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                return False, "the file is damaged"
            names = set(z.namelist())
            if "manifest.json" not in names or "academy.db" not in names:
                return False, "this is not a Digit Academy backup"
            man = json.loads(z.read("manifest.json"))
            if man.get("app") != APP_ID:
                return False, "this backup belongs to a different application"
            for n, digest in man["files"].items():
                if n not in names:
                    return False, f"missing file {n}"
                h = hashlib.sha256()
                with z.open(n) as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != digest:
                    return False, f"checksum mismatch in {n}"
            tmp = tempfile.mkdtemp(prefix="da_chk_")
            try:
                z.extract("academy.db", tmp)
                con = sqlite3.connect(os.path.join(tmp, "academy.db"))
                try:
                    r = con.execute("PRAGMA integrity_check").fetchone()[0]
                    n_students = con.execute("SELECT COUNT(*) FROM students").fetchone()[0]
                finally:
                    con.close()
                if r != "ok":
                    return False, "the database inside is corrupt"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return True, f"OK ({n_students} students)"
    except zipfile.BadZipFile:
        return False, "this is not a valid backup file"
    except Exception as e:
        return False, str(e)


def list_backups(dest_dir: str) -> list[dict]:
    out = []
    if os.path.isdir(dest_dir):
        for fn in sorted(os.listdir(dest_dir), reverse=True):
            if fn.endswith(EXT):
                p = os.path.join(dest_dir, fn)
                out.append({"name": fn, "path": p, "size": os.path.getsize(p), "modified": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
    return out


def restore_backup(src: str, paths: Paths, db: Database | None, safety_dir: str) -> dict:
    """Verifies first, saves a copy of the CURRENT data, then replaces it. The caller must restart the app afterwards.
    Returns {ok, message, safety_copy}."""
    ok, msg = verify_backup(src)
    if not ok:
        return {"ok": False, "message": "Cannot restore: " + msg, "safety_copy": None}
    safety = None
    if db is not None:
        r = create_backup(db, paths, safety_dir, label="before_restore")
        if not r["ok"]:
            return {"ok": False, "message": "Could not save a safety copy of the current data first: " + r["message"], "safety_copy": None}
        safety = r["path"]
        db.close()
    stage = tempfile.mkdtemp(prefix="da_restore_")
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(stage)
        for p in (paths.db, paths.db + "-wal", paths.db + "-shm", paths.db + "-journal"):
            if os.path.exists(p):
                os.remove(p)
        os.makedirs(os.path.dirname(paths.db), exist_ok=True)
        shutil.copyfile(os.path.join(stage, "academy.db"), paths.db)
        if os.path.isfile(os.path.join(stage, "secret.key")):
            shutil.copyfile(os.path.join(stage, "secret.key"), paths.key)
        for sub, dest in (("photos", paths.photos), ("attachments", paths.attachments), ("branding", paths.logo_dir)):
            shutil.rmtree(dest, ignore_errors=True)
            if os.path.isdir(os.path.join(stage, sub)):
                shutil.copytree(os.path.join(stage, sub), dest)
            else:
                os.makedirs(dest, exist_ok=True)
        return {"ok": True, "message": "Backup restored. The application will restart.", "safety_copy": safety}
    except Exception as e:
        return {"ok": False, "message": f"Restore failed: {e}. Your previous data is in: {safety}", "safety_copy": safety}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def auto_backup_due(db: Database) -> bool:
    mode = settings.get(db, "auto_backup")
    if mode not in ("daily", "weekly"):
        return False
    last = settings.get(db, "last_auto_backup")
    if not last:
        return True
    try:
        t = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now() - t >= timedelta(days=1 if mode == "daily" else 7)


def run_auto_backup(db: Database, paths: Paths, keep: int = 30) -> dict | None:
    if not auto_backup_due(db):
        return None
    d = settings.effective_backup_dir(db)
    r = create_backup(db, paths, d, label="auto")
    if r["ok"]:
        settings.set_value(db, "last_auto_backup", datetime.now().isoformat(timespec="seconds"))
        autos = [b for b in list_backups(d) if "_auto_" in b["name"]]
        for old in autos[keep:]:
            try:
                os.remove(old["path"])
            except OSError:
                pass
    return r


def export_json(db: Database, actor: Actor, dest: str) -> str:
    """Portable export of the main tables (Aadhaar excluded)."""
    require(actor, "backup.manage")
    data = {}
    for t in ("students", "enrollments", "courses", "categories", "batches", "teachers", "fee_charges", "fee_adjustments", "installments", "payments",
              "payment_allocations", "attendance", "academic_sessions", "subjects"):
        rows = [dict(r) for r in db.q(f"SELECT * FROM {t}")]
        if t == "students":
            for r in rows:
                r.pop("aadhaar_enc", None)
        data[t] = rows
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log(db, actor.id, "export_json", "backup", os.path.basename(dest), "")
    return dest
