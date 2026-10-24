from datetime import datetime

from app.database.db import Database


def log(db: Database, user_id, action: str, entity: str = "", entity_id="", detail: str = ""):
    name = None
    if user_id:
        r = db.one("SELECT username FROM users WHERE id = ?", (user_id,))
        name = r["username"] if r else None
    db.x("INSERT INTO audit_log(ts, user_id, username, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?,?)",
         (datetime.now().isoformat(timespec="seconds"), user_id, name, action, entity, str(entity_id), detail[:2000]))


def recent(db: Database, actor, limit: int = 500, text: str = ""):
    from app.security.permissions import require
    require(actor, "audit.view")
    like = f"%{text}%"
    return db.q("SELECT * FROM audit_log WHERE (? = '' OR action LIKE ? OR detail LIKE ? OR username LIKE ?) ORDER BY id DESC LIMIT ?",
                (text, like, like, like, limit))
