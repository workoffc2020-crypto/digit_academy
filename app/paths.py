"""All file locations. Data lives OUTSIDE the install folder (Program Files is read-only for normal users and
re-installing must never touch student data)."""
import os
import sys
from dataclasses import dataclass


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def _default_root() -> str:
    env = os.environ.get("DIGIT_ACADEMY_DATA")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "DigitAcademy")


def default_backup_dir() -> str:
    env = os.environ.get("DIGIT_ACADEMY_BACKUPS")
    if env:
        return env
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "DigitAcademy Backups")


@dataclass
class Paths:
    root: str

    @property
    def db(self) -> str: return os.path.join(self.root, "data", "academy.db")
    @property
    def key(self) -> str: return os.path.join(self.root, "data", "secret.key")
    @property
    def photos(self) -> str: return os.path.join(self.root, "photos")
    @property
    def attachments(self) -> str: return os.path.join(self.root, "attachments")
    @property
    def logs(self) -> str: return os.path.join(self.root, "logs")
    @property
    def exports(self) -> str: return os.path.join(self.root, "exports")
    @property
    def logo_dir(self) -> str: return os.path.join(self.root, "branding")

    def ensure(self) -> "Paths":
        for p in (os.path.dirname(self.db), self.photos, self.attachments, self.logs, self.exports, self.logo_dir):
            os.makedirs(p, exist_ok=True)
        return self


def get_paths(root: str | None = None) -> Paths:
    return Paths(root or _default_root()).ensure()
