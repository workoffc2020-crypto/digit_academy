"""Digit Academy Management System - entry point.   `--selftest` checks the PACKAGED program headlessly (used by the build)."""
import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler


def _logging(ctx_root: str):
    os.makedirs(os.path.join(ctx_root, "logs"), exist_ok=True)
    h = RotatingFileHandler(os.path.join(ctx_root, "logs", "app.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    lg = logging.getLogger("digit_academy")
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    sys.excepthook = lambda t, v, tb: lg.error("Unhandled", exc_info=(t, v, tb))
    return lg


def selftest() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    log, ok = [], True
    try:
        with tempfile.TemporaryDirectory() as t:
            from datetime import date
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
            from PySide6.QtWidgets import QMessageBox

            def _boom(parent, title, text, *a, **k):
                raise RuntimeError(f"{title}: {text}")
            QMessageBox.warning = staticmethod(_boom)                 # a dialog in a headless test must FAIL, never hang
            QMessageBox.critical = staticmethod(_boom)
            from app.context import Context
            from app.printing import docs, render
            from app.security import auth
            from app.services import backup, fees, students
            ctx = Context.open(os.path.join(t, "data"))
            ctx.first_run_setup(institute_name="Self Test", address="", phone="", email="", admin_username="admin", admin_password="secret1", admin_full_name="A", session_name="2026-27")
            a = auth.login(ctx.db, "admin", "secret1")
            sid = students.create(ctx.db, a, ctx.vault, {"name": "Test Student", "mobile": "9876501234"}, aadhaar="123412341234")
            cid = ctx.db.val("SELECT id FROM courses WHERE name='Python'")
            eid = fees.enroll_with_fees(ctx.db, a, sid, cid, course_fee=300000, installments=2, first_due=date.today())
            pid = fees.collect_payment(ctx.db, a, sid, 100000, "Cash", enrollment_id=eid)
            pdf = render.to_pdf(docs.receipt(ctx.db, pid, "A4"), os.path.join(t, "r.pdf"))
            log.append(f"receipt pdf: {os.path.getsize(pdf)} bytes")
            bk = backup.create_backup(ctx.db, ctx.paths, os.path.join(t, "bk"), actor=a)
            log.append(f"backup verified: {bk['ok']}")
            from PySide6 import QtPdf, QtPdfWidgets  # noqa: F401
            from app.ui.main_window import MainWindow
            w = MainWindow(ctx, a)
            for i in range(len(w.pages)):
                w.goto(i)
            log.append(f"all {len(w.pages)} pages opened")
            ok = os.path.getsize(pdf) > 3000 and bk["ok"] and len(w.pages) >= 8
            ctx.close()
    except Exception as ex:                       # noqa: BLE001
        ok = False
        log.append(f"ERROR: {ex!r}")
    print("PASS" if ok else "FAIL", *log, sep="\n")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app import APP_NAME
    from app.context import Context
    from app.database.db import DatabaseError
    from app.paths import resource_path
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DigitAcademy.Management.1")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(resource_path("assets/app.ico")))
    from app.paths import _default_root
    log = _logging(_default_root())
    try:
        ctx = Context.open()
    except DatabaseError as ex:
        QMessageBox.critical(None, APP_NAME, str(ex))
        sys.exit(1)
    except Exception as ex:                       # noqa: BLE001
        log.exception("startup failed")
        QMessageBox.critical(None, APP_NAME, f"The program could not start:\n{ex}\n\nYour data was not changed. Details: {_default_root()}\\logs")
        sys.exit(1)
    from app.ui import theme
    from app.ui.login import LoginDialog, SetupDialog
    from app.ui.main_window import MainWindow
    from app.services import settings
    app.setStyleSheet(theme.stylesheet(int(settings.get(ctx.db, "font_size") or 10), settings.get(ctx.db, "theme") == "dark"))
    actor = None
    if ctx.needs_setup:
        dlg = SetupDialog(ctx)
        if not dlg.exec():
            sys.exit(0)
        actor = dlg.actor
    else:
        dlg = LoginDialog(ctx)
        if not dlg.exec():
            sys.exit(0)
        actor = dlg.actor
    win = MainWindow(ctx, actor)
    win.apply_theme()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
