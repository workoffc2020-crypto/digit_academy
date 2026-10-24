"""Permissions are checked in the SERVICE layer (not just by hiding buttons)."""
from dataclasses import dataclass, field

PERMISSIONS = {
    "students.view": "View students",
    "students.edit": "Add / edit students",
    "students.sensitive": "See full Aadhaar / ID numbers",
    "students.deactivate": "Deactivate / archive students",
    "fees.view": "View fee ledgers",
    "fees.collect": "Collect fees & print receipts",
    "fees.discount": "Give discounts (within limit)",
    "fees.discount_any": "Give discounts above the limit",
    "fees.charges": "Add / change fee charges & installments",
    "fees.refund": "Make refunds",
    "fees.reverse": "Reverse / cancel receipts",
    "attendance.view": "View attendance",
    "attendance.mark": "Mark attendance",
    "attendance.edit": "Correct saved attendance",
    "reports.view": "View reports",
    "reports.export": "Export reports (PDF / CSV / Excel)",
    "catalog.manage": "Manage courses, classes, batches, teachers",
    "fields.manage": "Design custom fields",
    "settings.manage": "Change settings",
    "users.manage": "Manage users & permissions",
    "backup.manage": "Backup & restore",
    "audit.view": "View audit log",
}

ROLES = {
    "super_admin": "Super Administrator",
    "admin": "Administrator",
    "accountant": "Accountant / Fee Collector",
    "teacher": "Teacher",
    "attendance_operator": "Attendance Operator",
    "viewer": "Read-only / Report Viewer",
}

_ALL = set(PERMISSIONS)
DEFAULT_ROLE_PERMS = {
    "super_admin": _ALL,
    "admin": _ALL - {"users.manage"} | {"users.manage"},   # admins manage users too; super admin can lock this down
    "accountant": {"students.view", "fees.view", "fees.collect", "fees.discount", "reports.view", "reports.export", "attendance.view"},
    "teacher": {"students.view", "attendance.view", "attendance.mark", "reports.view"},
    "attendance_operator": {"students.view", "attendance.view", "attendance.mark"},
    "viewer": {"students.view", "fees.view", "attendance.view", "reports.view"},
}


class PermissionDenied(Exception):
    pass


class ValidationError(Exception):
    pass


@dataclass
class Actor:
    id: int
    username: str
    role: str
    full_name: str = ""
    perms: set = field(default_factory=set)

    def can(self, perm: str) -> bool:
        return self.role == "super_admin" or perm in self.perms


def require(actor: Actor | None, perm: str):
    if actor is None or not actor.can(perm):
        raise PermissionDenied(f"You do not have permission: {PERMISSIONS.get(perm, perm)}")


SYSTEM = Actor(id=0, username="system", role="super_admin", full_name="System")
