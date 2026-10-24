"""Login, first-run setup and the lock screen."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QVBoxLayout

from app import APP_NAME
from app.security import auth
from app.security.permissions import ValidationError
from app.ui.widgets import btn, label, logo_pixmap, safe


class _Card(QDialog):
    def __init__(self, db, title, sub, width=420):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setModal(True)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(10)
        lg = QLabel()
        lg.setPixmap(logo_pixmap(db, 96))
        lg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(lg)
        t = label(title, "h1")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s = label(sub, "muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(t)
        outer.addWidget(s)
        self.form = QFormLayout()
        self.form.setSpacing(9)
        outer.addLayout(self.form)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#dc2626;font-weight:600")
        self.err.setWordWrap(True)
        outer.addWidget(self.err)
        self.row = QHBoxLayout()
        outer.addLayout(self.row)


class LoginDialog(_Card):
    def __init__(self, ctx, username: str = "", locked: bool = False):
        super().__init__(ctx.db, "Screen locked" if locked else "Welcome back", "Enter your password to continue" if locked else "Sign in to Digit Academy")
        self.ctx, self.actor = ctx, None
        self.user = QLineEdit(username)
        self.user.setPlaceholderText("Username")
        self.user.setReadOnly(locked)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("Password")
        self.form.addRow("Username", self.user)
        self.form.addRow("Password", self.pw)
        ok = btn("Sign in", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._go)
        cancel = btn("Exit" if not locked else "Log out")
        cancel.clicked.connect(self.reject)
        self.row.addWidget(cancel)
        self.row.addWidget(ok, 1)
        (self.pw if username else self.user).setFocus()
        self.pw.returnPressed.connect(self._go)

    def _go(self):
        try:
            self.actor = auth.login(self.ctx.db, self.user.text(), self.pw.text())
        except auth.LoginError as ex:
            self.err.setText(str(ex))
            self.pw.clear()
            self.pw.setFocus()
            return
        self.accept()


class SetupDialog(_Card):
    def __init__(self, ctx):
        super().__init__(ctx.db, "First-time setup", "Set up your institute and the Super Administrator account.", 560)
        self.ctx, self.actor = ctx, None
        self.name = QLineEdit("Digit Academy")
        self.addr = QLineEdit()
        self.phone = QLineEdit()
        self.email = QLineEdit()
        self.full = QLineEdit()
        self.user = QLineEdit("admin")
        self.p1, self.p2 = QLineEdit(), QLineEdit()
        for p in (self.p1, self.p2):
            p.setEchoMode(QLineEdit.EchoMode.Password)
        for lab, w in (("Institute name *", self.name), ("Address", self.addr), ("Phone", self.phone), ("Email", self.email), ("Your full name", self.full),
                       ("Admin username *", self.user), ("Password * (min 6)", self.p1), ("Repeat password *", self.p2)):
            self.form.addRow(lab, w)
        note = label("You can change the logo, address and all other details later in Settings. Keep this password safe - it cannot be recovered if forgotten "
                     "(another Super Administrator can reset it).", "muted")
        ok = btn("Create & start", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._go)
        self.row.addWidget(ok, 1)
        self.layout().insertWidget(self.layout().count() - 1, note)

    def _go(self):
        if self.p1.text() != self.p2.text():
            self.err.setText("The two passwords do not match.")
            return
        try:
            self.actor = self.ctx.first_run_setup(institute_name=self.name.text(), address=self.addr.text(), phone=self.phone.text(), email=self.email.text(),
                                                  admin_username=self.user.text(), admin_password=self.p1.text(), admin_full_name=self.full.text())
        except ValidationError as ex:
            self.err.setText(str(ex))
            return
        self.accept()
