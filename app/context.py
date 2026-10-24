"""Wires paths + database + vault together and performs the first-run setup."""
import logging
import os
from dataclasses import dataclass
from datetime import date

from app.database.db import Database
from app.paths import Paths, get_paths
from app.security import auth
from app.security.crypto import Vault
from app.security.permissions import Actor
from app.services import catalog, settings


@dataclass
class Context:
    paths: Paths
    db: Database
    vault: Vault

    @classmethod
    def open(cls, root: str | None = None) -> "Context":
        paths = get_paths(root)
        db = Database(paths.db, safety_dir=os.path.join(paths.root, "upgrade_safety"))
        ctx = cls(paths, db, Vault(paths.key))
        settings.seed(db, with_catalog=not auth.has_users(db) or db.val("SELECT COUNT(*) FROM categories", default=0) == 0)
        if db.migrated_from is not None:
            logging.getLogger("digit_academy").info("Database upgraded from v%s; safety copy: %s", db.migrated_from, db.safety_copy)
        return ctx

    @property
    def needs_setup(self) -> bool:
        return not auth.has_users(self.db)

    def first_run_setup(self, *, institute_name: str, address: str, phone: str, email: str, admin_username: str, admin_password: str,
                        admin_full_name: str, session_name: str | None = None) -> Actor:
        from app.security.permissions import ValidationError
        if not self.needs_setup:
            raise ValidationError("The application is already set up.")
        if not institute_name.strip():
            raise ValidationError("Institute name is required.")
        with self.db.tx():
            uid = auth.create_user(self.db, None, admin_username, admin_password, "super_admin", admin_full_name or admin_username, first_run=True)
            actor = auth.actor_for(self.db, uid)
            settings.update(self.db, actor, {"institute_name": institute_name.strip(), "address": address.strip(), "phone": phone.strip(), "email": email.strip()})
            y = date.today().year if date.today().month >= 4 else date.today().year - 1
            catalog.add_session(self.db, actor, session_name or f"{y}-{str(y + 1)[2:]}", f"{y}-04-01", f"{y + 1}-03-31")
        return actor

    def close(self):
        self.db.close()
