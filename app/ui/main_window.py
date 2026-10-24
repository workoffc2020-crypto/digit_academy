"""Main window: header with logo, permission-aware sidebar, pages, inactivity lock, remembered size, auto backup."""
import logging
import time

from PySide6.QtCore import QEvent, QSettings, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout,
                               QWidget)

from app import APP_NAME
from app.paths import resource_path
from app.services import backup, catalog, settings
from app.ui import theme
from app.ui.login import LoginDialog
from app.ui.preview import PreviewDialog
from app.ui.widgets import btn, confirm, info, logo_pixmap, safe

log = logging.getLogger("digit_academy")


class AppState:
    """Everything a page needs. Pages never open files or databases themselves."""

    def __init__(self, ctx, actor, window=None):
        self.ctx, self.actor, self.window = ctx, actor, window

    db = property(lambda s: s.ctx.db)
    vault = property(lambda s: s.ctx.vault)
    paths = property(lambda s: s.ctx.paths)

    def preview(self, parent, variants, title="Print preview", default=None, save_name="document"):
        if not isinstance(variants, dict):
            variants = {"Document": variants}
        PreviewDialog(self, parent, variants, title, default, save_name).exec()

    def broadcast(self):
        if self.window:
            self.window.refresh_current()


# (title, icon, module, class, permission)
PAGES = [
    ("Dashboard", "🏠", "dashboard", "DashboardPage", None),
    ("Students", "🎓", "students", "StudentsPage", "students.view"),
    ("Fees & Receipts", "💰", "fees", "FeesPage", "fees.view"),
    ("Attendance", "🗓", "attendance", "AttendancePage", "attendance.view"),
    ("Reports", "📊", "reports", "ReportsPage", "reports.view"),
    ("Courses & Batches", "📚", "catalog", "CatalogPage", "catalog.manage"),
    ("Custom Fields", "🧩", "fields", "FieldsPage", "fields.manage"),
    ("Users & Security", "🔐", "users", "UsersPage", ("users.manage", "audit.view")),
    ("Settings", "⚙", "settings_page", "SettingsPage", "settings.manage"),
    ("Backup & Restore", "🛡", "backup_page", "BackupPage", "backup.manage"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx, actor, app_qt=None):
        super().__init__()
        self.state = AppState(ctx, actor, self)
        self.actor = actor
        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(QIcon(resource_path("assets/app.ico")))
        self.setMinimumSize(760, 520)
        self.settings_store = QSettings("DigitAcademy", "Management")
        self._last_input = time.time()
        self._locked = False
        self.pages, self.nav, self._loaded = [], [], {}
        root = QWidget()
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._header())
        body = QHBoxLayout()
        body.setSpacing(0)
        col.addLayout(body, 1)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        sv = QVBoxLayout(self.sidebar)
        sv.setContentsMargins(0, 10, 0, 10)
        sv.setSpacing(2)
        body.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self._build_pages(sv)
        self.pwhint = QLabel("v1.0")
        sv.addStretch(1)
        self._restore()
        self.goto(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._idle_check)
        self.timer.start(15000)
        QApplication.instance().installEventFilter(self)

    # ---- layout
    def _header(self):
        h = QFrame()
        h.setObjectName("header")
        h.setFixedHeight(66)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(14, 4, 14, 4)
        lg = self.header_logo = QLabel()
        lg.setPixmap(logo_pixmap(self.state.db, 54))
        lg.setStyleSheet("background:#ffffff;border-radius:27px;padding:2px")
        lay.addWidget(lg)
        t = QVBoxLayout()
        t.setSpacing(0)
        self.title = QLabel(settings.get(self.state.db, "institute_name").upper())
        self.title.setObjectName("htitle")
        self.sub = QLabel("Management System  •  " + (settings.get(self.state.db, "institute_sub") or ""))
        self.sub.setObjectName("hsub")
        t.addWidget(self.title)
        t.addWidget(self.sub)
        lay.addLayout(t, 1)
        self.session_lbl = QLabel("")
        self.user_lbl = QLabel(f"👤 {self.actor.full_name or self.actor.username}  ({self.actor.role.replace('_', ' ')})")
        lay.addWidget(self.session_lbl)
        lay.addSpacing(14)
        lay.addWidget(self.user_lbl)
        self.lock_b = btn("🔒 Lock", slot=self.lock)
        self.out_b = btn("Log out", slot=self.close)
        for b in (self.lock_b, self.out_b):
            b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.12);color:#fff;border:1px solid rgba(255,255,255,0.3)}QPushButton:hover{background:rgba(255,255,255,0.25)}")
            lay.addWidget(b)
        self._refresh_header()
        return h

    def _refresh_header(self):
        s = catalog.current_session_id(self.state.db)
        name = self.state.db.val("SELECT name FROM academic_sessions WHERE id=?", (s,), "-") if s else "-"
        self.session_lbl.setText(f"Session {name}")
        self.title.setText(settings.get(self.state.db, "institute_name").upper())
        self.sub.setText("Management System  •  " + (settings.get(self.state.db, "institute_sub") or ""))

    def _build_pages(self, sv):
        for title, icon, mod, cls, perm in PAGES:
            perms = (perm,) if isinstance(perm, str) else (perm or ())
            if perms and not any(self.actor.can(p) for p in perms):
                continue
            b = QPushButton(f"  {icon}   {title}".replace("&", "&&"))
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            i = len(self.pages)
            b.clicked.connect(lambda _=False, k=i: self.goto(k))
            sv.addWidget(b)
            self.nav.append(b)
            self.pages.append((title, icon, mod, cls))
            ph = QWidget()
            ph.setObjectName("page")
            self.stack.addWidget(ph)

    def _ensure(self, i):
        if i in self._loaded:
            return self._loaded[i]
        title, icon, mod, cls = self.pages[i]
        import importlib
        m = importlib.import_module(f"app.ui.{mod}")
        page = getattr(m, cls)(self.state)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        page.setObjectName("page")
        sc.setWidget(page)
        old = self.stack.widget(i)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.insertWidget(i, sc)
        self._loaded[i] = page
        return page

    def goto(self, i):
        for k, b in enumerate(self.nav):
            b.setChecked(k == i)
        page = self._ensure(i)
        self.stack.setCurrentIndex(i)
        ok, _ = safe(self, page.refresh)

    def go_to_module(self, mod):
        for i, p in enumerate(self.pages):
            if p[2] == mod:
                self.goto(i)
                return self._loaded[i]
        return None

    def refresh_current(self):
        i = self.stack.currentIndex()
        if i in self._loaded:
            safe(self, self._loaded[i].refresh)
        self._refresh_header()

    def resizeEvent(self, e):
        compact = self.width() < 1020
        for b, (title, icon, mod, cls) in zip(self.nav, self.pages):
            b.setText(f"  {icon}" if compact else f"  {icon}   {title}".replace("&", "&&"))
            b.setToolTip(title)
        self.sidebar.setFixedWidth(58 if compact else 218)
        self.user_lbl.setVisible(self.width() > 900)
        self.session_lbl.setVisible(self.width() > 760)
        super().resizeEvent(e)

    def apply_theme(self):
        st = settings.get_all(self.state.db)
        try:
            pt = int(st["font_size"] or 10)
        except ValueError:
            pt = 10
        QApplication.instance().setStyleSheet(theme.stylesheet(pt, st["theme"] == "dark"))

    def rebuild_header_logo(self):
        self.header_logo.setPixmap(logo_pixmap(self.state.db, 54))

    # ---- remember window
    def _restore(self):
        g = self.settings_store.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        else:
            self.resize(1280, 780)

    # ---- inactivity lock
    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.MouseMove, QEvent.Type.Wheel):
            self._last_input = time.time()
        return False

    def _idle_check(self):
        try:
            minutes = int(settings.get(self.state.db, "lock_minutes") or 0)
        except ValueError:
            minutes = 0
        if minutes > 0 and not self._locked and time.time() - self._last_input > minutes * 60:
            self.lock()

    def lock(self):
        if self._locked:
            return
        self._locked = True
        self.centralWidget().setEnabled(False)
        while True:
            dlg = LoginDialog(self.state.ctx, self.actor.username, locked=True)
            if dlg.exec() and dlg.actor and dlg.actor.id == self.actor.id:
                break
            if not dlg.result():
                self._locked = False
                self.centralWidget().setEnabled(True)
                self.close()
                return
        self.centralWidget().setEnabled(True)
        self._locked = False
        self._last_input = time.time()

    # ---- closing
    def closeEvent(self, ev):
        self.settings_store.setValue("geometry", self.saveGeometry())
        try:
            r = backup.run_auto_backup(self.state.db, self.state.paths)
            if r and not r["ok"]:
                log.warning("auto backup failed: %s", r["message"])
        except Exception:
            log.exception("auto backup")
        self.timer.stop()
        super().closeEvent(ev)
