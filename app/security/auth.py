"""Password hashing (scrypt from the standard library), login with lockout, user management."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta

from app.database.db import Database
from .permissions import Actor, ROLES, ValidationError, require, DEFAULT_ROLE_PERMS
from app.services.audit import log

MIN_PASSWORD = 6
MAX_FAILED = 5
LOCK_MINUTES = 5
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(h).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_perms(db: Database, role: str) -> set:
    return {r["permission"] for r in db.q("SELECT permission FROM role_permissions WHERE role = ?", (role,))}


def actor_for(db: Database, user_id: int) -> Actor:
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    return Actor(id=u["id"], username=u["username"], role=u["role"], full_name=u["full_name"], perms=load_perms(db, u["role"]))


class LoginError(Exception):
    pass


def login(db: Database, username: str, password: str) -> Actor:
    u = db.one("SELECT * FROM users WHERE username = ?", (username.strip(),))
    generic = LoginError("Wrong username or password.")
    if u is None:
        verify_password(password, hash_password("x"))     # keep timing similar
        log(db, None, "login_failed", "user", username.strip(), "unknown user")
        raise generic
    if not u["active"]:
        raise LoginError("This account is disabled. Ask the administrator.")
    if u["locked_until"] and u["locked_until"] > _now():
        raise LoginError(f"Too many wrong attempts. Try again after {u['locked_until'][11:16]}.")
    if not verify_password(password, u["password_hash"]):
        n = u["failed_attempts"] + 1
        locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds") if n >= MAX_FAILED else None
        db.x("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?", (0 if locked else n, locked, u["id"]))
        log(db, u["id"], "login_failed", "user", u["id"], f"attempt {n}")
        raise generic
    db.x("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login = ? WHERE id = ?", (_now(), u["id"]))
    log(db, u["id"], "login", "user", u["id"], "")
    return actor_for(db, u["id"])


def check_password_policy(password: str):
    if len(password) < MIN_PASSWORD:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD} characters.")


def has_users(db: Database) -> bool:
    return db.val("SELECT COUNT(*) FROM users", default=0) > 0


def create_user(db: Database, actor: Actor | None, username: str, password: str, role: str, full_name: str = "", *, first_run: bool = False) -> int:
    if not first_run:
        require(actor, "users.manage")
        if role == "super_admin" and actor.role != "super_admin":
            raise ValidationError("Only a Super Administrator can create another Super Administrator.")
    username = username.strip()
    if not username or " " in username:
        raise ValidationError("Username is required and cannot contain spaces.")
    if role not in ROLES:
        raise ValidationError("Unknown role.")
    check_password_policy(password)
    if db.one("SELECT 1 FROM users WHERE username = ?", (username,)):
        raise ValidationError("This username already exists.")
    with db.tx():
        cur = db.x("INSERT INTO users(username, full_name, password_hash, role, active, created_at) VALUES (?,?,?,?,1,?)",
                   (username, full_name.strip(), hash_password(password), role, _now()))
        log(db, actor.id if actor else cur.lastrowid, "user_create", "user", cur.lastrowid, f"{username} ({role})")
    return cur.lastrowid


def change_password(db: Database, actor: Actor, user_id: int, new_password: str, old_password: str | None = None):
    own = actor.id == user_id
    if not own:
        require(actor, "users.manage")
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if u is None:
        raise ValidationError("User not found.")
    if u["role"] == "super_admin" and actor.role != "super_admin":
        raise ValidationError("Only a Super Administrator can change this password.")
    if own and old_password is not None and not verify_password(old_password, u["password_hash"]):
        raise ValidationError("Current password is wrong.")
    check_password_policy(new_password)
    db.x("UPDATE users SET password_hash = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?", (hash_password(new_password), user_id))
    log(db, actor.id, "password_change", "user", user_id, "")


def set_user_active(db: Database, actor: Actor, user_id: int, active: bool):
    require(actor, "users.manage")
    if user_id == actor.id and not active:
        raise ValidationError("You cannot disable your own account.")
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if u["role"] == "super_admin" and not active and db.val("SELECT COUNT(*) FROM users WHERE role='super_admin' AND active=1") <= 1:
        raise ValidationError("At least one active Super Administrator is required.")
    db.x("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
    log(db, actor.id, "user_enable" if active else "user_disable", "user", user_id, u["username"])


def set_user_role(db: Database, actor: Actor, user_id: int, role: str):
    require(actor, "users.manage")
    if role not in ROLES:
        raise ValidationError("Unknown role.")
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if (role == "super_admin" or u["role"] == "super_admin") and actor.role != "super_admin":
        raise ValidationError("Only a Super Administrator can change Super Administrator roles.")
    if u["role"] == "super_admin" and role != "super_admin" and db.val("SELECT COUNT(*) FROM users WHERE role='super_admin' AND active=1") <= 1:
        raise ValidationError("At least one active Super Administrator is required.")
    db.x("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    log(db, actor.id, "user_role", "user", user_id, f"{u['username']}: {u['role']} -> {role}")


def set_role_permissions(db: Database, actor: Actor, role: str, perms: set):
    require(actor, "users.manage")
    if role == "super_admin":
        raise ValidationError("The Super Administrator always has every permission.")
    from .permissions import PERMISSIONS
    perms = {p for p in perms if p in PERMISSIONS}
    if role == "admin" and "users.manage" not in perms and actor.role != "super_admin":
        raise ValidationError("Only a Super Administrator can remove user-management from Administrators.")
    with db.tx():
        db.x("DELETE FROM role_permissions WHERE role = ?", (role,))
        db.conn.executemany("INSERT INTO role_permissions(role, permission) VALUES (?,?)", [(role, p) for p in sorted(perms)])
        log(db, actor.id, "role_permissions", "role", role, ",".join(sorted(perms)))


def list_users(db: Database, actor: Actor):
    require(actor, "users.manage")
    return db.q("SELECT id, username, full_name, role, active, last_login, locked_until FROM users ORDER BY username")


def reset_role_defaults(db: Database, role: str):
    db.x("DELETE FROM role_permissions WHERE role = ?", (role,))
    db.conn.executemany("INSERT INTO role_permissions(role, permission) VALUES (?,?)", [(role, p) for p in sorted(DEFAULT_ROLE_PERMS[role])])
