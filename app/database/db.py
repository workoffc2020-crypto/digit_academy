"""SQLite access: one connection, explicit transactions, versioned migrations (with an automatic safety copy
before any schema change on an existing database)."""
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from .schema import MIGRATIONS, latest_version


class DatabaseError(Exception):
    pass


class Database:
    def __init__(self, path: str, safety_dir: str | None = None):
        self.path = path
        self.safety_dir = safety_dir
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.migrated_from: int | None = None
        self.safety_copy: str | None = None
        self._migrate()

    # ---- schema versioning
    def version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self):
        cur = self.version()
        target = latest_version()
        if cur > target:
            raise DatabaseError("This database was created by a NEWER version of the application. Please install the latest version.")
        if cur == target:
            return
        if cur > 0:                                    # existing data: copy first, then change the schema
            self.migrated_from = cur
            self.safety_copy = self._safety_copy(cur)
        for i in range(cur, target):
            try:
                self.conn.executescript("BEGIN;\n" + MIGRATIONS[i] + f"\nPRAGMA user_version = {i + 1};\nCOMMIT;")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def _safety_copy(self, from_version: int) -> str | None:
        if not self.safety_dir:
            return None
        os.makedirs(self.safety_dir, exist_ok=True)
        dest = os.path.join(self.safety_dir, f"before_upgrade_v{from_version}_{datetime.now():%Y%m%d_%H%M%S}.db")
        dst = sqlite3.connect(dest)
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        return dest

    # ---- transactions (re-entrant: inner tx joins the outer one)
    @contextmanager
    def tx(self):
        with self._lock:
            if self._depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.conn
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("COMMIT")

    def q(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()):
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def val(self, sql: str, params: tuple | list = (), default=None):
        r = self.one(sql, params)
        return default if r is None or r[0] is None else r[0]

    def x(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def integrity_ok(self) -> bool:
        return self.val("PRAGMA integrity_check") == "ok"

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def next_counter(self, name: str) -> int:
        """Atomic, never-reused sequence (call inside a tx)."""
        self.x("INSERT INTO counters(name, value) VALUES (?, 0) ON CONFLICT(name) DO NOTHING", (name,))
        self.x("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
        return int(self.val("SELECT value FROM counters WHERE name = ?", (name,)))
