"""Users, roles & permissions, audit log."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLineEdit, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from app.security import auth
from app.security.permissions import DEFAULT_ROLE_PERMS, PERMISSIONS, ROLES
from app.services import audit
from app.ui.fees import SimpleMoneyDialog as FormDialog
from app.ui.widgets import DataTable, btn, confirm, fill_combo, info, label, page_header, safe, warn


class UsersPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        a = app.actor
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Users & Security", "Who can do what - checked in the program, not just hidden buttons"))
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        if a.can("users.manage"):
            self.tabs.addTab(self._users(), "👥 Users")
            self.tabs.addTab(self._roles(), "🔑 Roles && permissions")
        if a.can("audit.view"):
            self.tabs.addTab(self._audit(), "📜 Audit log")

    def refresh(self):
        if self.app.actor.can("users.manage"):
            self._load_users()
        if self.app.actor.can("audit.view"):
            self._load_audit()

    # ---- users
    def _users(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.ut = DataTable([("Username", "username", "text"), ("Full name", "full_name", "text"), ("Role", lambda r: ROLES.get(r["role"], r["role"]), "text"),
                             ("Status", lambda r: "Active" if r["active"] else "Disabled", "status"), ("Last login", lambda r: (r["last_login"] or "")[:16].replace("T", " "), "text")], empty="No users.")
        v.addWidget(self.ut, 1)
        h = QHBoxLayout()
        h.addWidget(btn("➕  Add user", "primary", slot=self.add_user))
        h.addWidget(btn("Change role", slot=self.change_role))
        h.addWidget(btn("Reset password", slot=self.reset_pw))
        h.addWidget(btn("Disable / enable", "warn", slot=self.toggle))
        h.addStretch(1)
        v.addLayout(h)
        return w

    def _load_users(self):
        ok, rows = safe(self, auth.list_users, self.app.db, self.app.actor)
        if ok:
            self.ut.set_rows([dict(r) for r in rows])

    def _role_combo(self, current=None):
        c = QComboBox()
        items = [(n, k) for k, n in ROLES.items() if k != "super_admin" or self.app.actor.role == "super_admin"]
        fill_combo(c, items, current=current)
        return c

    def add_user(self):
        u, n, p = QLineEdit(), QLineEdit(), QLineEdit()
        p.setEchoMode(QLineEdit.EchoMode.Password)
        role = self._role_combo("teacher")
        if FormDialog(self, "New user", [("u", "Username *", u), ("n", "Full name", n), ("p", "Password * (min 6)", p), ("r", "Role", role)]).exec():
            safe(self, auth.create_user, self.app.db, self.app.actor, u.text(), p.text(), role.currentData(), n.text())
            self._load_users()

    def change_role(self):
        r = self.ut.current()
        if r:
            role = self._role_combo(r["role"])
            if FormDialog(self, f"Role for {r['username']}", [("r", "Role", role)]).exec():
                safe(self, auth.set_user_role, self.app.db, self.app.actor, r["id"], role.currentData())
                self._load_users()

    def reset_pw(self):
        r = self.ut.current()
        if r:
            p = QLineEdit(); p.setEchoMode(QLineEdit.EchoMode.Password)
            if FormDialog(self, f"New password for {r['username']}", [("p", "New password (min 6)", p)]).exec():
                ok, _ = safe(self, auth.change_password, self.app.db, self.app.actor, r["id"], p.text())
                if ok:
                    info(self, "Password changed.")

    def toggle(self):
        r = self.ut.current()
        if r:
            safe(self, auth.set_user_active, self.app.db, self.app.actor, r["id"], not r["active"])
            self._load_users()

    # ---- roles
    def _roles(self):
        w = QWidget(); v = QVBoxLayout(w)
        h = QHBoxLayout()
        h.addWidget(label("Role:"))
        self.role = QComboBox(); fill_combo(self.role, [(n, k) for k, n in ROLES.items() if k != "super_admin"])
        self.role.currentIndexChanged.connect(lambda _=0: self._load_role())
        h.addWidget(self.role); h.addStretch(1)
        v.addLayout(h)
        v.addWidget(label("The Super Administrator always has every permission. Tick what each role may do.", "muted"))
        sc = QScrollArea(); sc.setWidgetResizable(True)
        inner = QWidget(); g = QGridLayout(inner)
        self.checks = {}
        for i, (k, n) in enumerate(PERMISSIONS.items()):
            c = QCheckBox(n)
            self.checks[k] = c
            g.addWidget(c, i // 2, i % 2)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        b = QHBoxLayout()
        b.addWidget(btn("💾  Save permissions", "primary", slot=self.save_role))
        b.addWidget(btn("Reset to defaults", slot=self.reset_role))
        b.addStretch(1)
        v.addLayout(b)
        self._load_role()
        return w

    def _load_role(self):
        perms = auth.load_perms(self.app.db, self.role.currentData())
        for k, c in self.checks.items():
            c.setChecked(k in perms)

    def save_role(self):
        perms = {k for k, c in self.checks.items() if c.isChecked()}
        ok, _ = safe(self, auth.set_role_permissions, self.app.db, self.app.actor, self.role.currentData(), perms)
        if ok:
            info(self, "Saved. Users get the new permissions the next time they sign in.")

    def reset_role(self):
        if confirm(self, "Reset this role to the standard permissions?"):
            with self.app.db.tx():
                auth.reset_role_defaults(self.app.db, self.role.currentData())
            self._load_role()

    # ---- audit
    def _audit(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.aq = QLineEdit(); self.aq.setPlaceholderText("Filter by action, user or detail…")
        self.aq.returnPressed.connect(self._load_audit)
        v.addWidget(self.aq)
        self.at = DataTable([("When", lambda r: r["ts"].replace("T", " "), "text"), ("User", lambda r: r["username"] or "-", "text"), ("Action", "action", "text"),
                             ("Item", lambda r: f"{r['entity'] or ''} {r['entity_id'] or ''}".strip(), "text"), ("Detail", "detail", "text")], page_size=200, empty="No entries.")
        v.addWidget(self.at, 1)
        return w

    def _load_audit(self):
        ok, rows = safe(self, audit.recent, self.app.db, self.app.actor, 3000, self.aq.text().strip())
        if ok:
            self.at.set_rows([dict(r) for r in rows])
