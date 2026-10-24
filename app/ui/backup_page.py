"""Backup & restore. A backup is only reported as successful after the written file has been re-opened and verified."""
import os
from datetime import datetime

from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.services import backup as B
from app.services import settings as S
from app.ui.widgets import DataTable, btn, confirm, fill_combo, info, label, page_header, safe, warn


class BackupPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Backup & Restore", "Protect your students' and fee records", btn("🛡  Back up now", "primary", slot=self.backup_now)))
        card = QFrame(); card.setObjectName("card")
        c = QVBoxLayout(card)
        self.dir_lbl = label("")
        c.addWidget(self.dir_lbl)
        r = QHBoxLayout()
        r.addWidget(btn("Choose backup folder…", slot=self.choose_dir))
        r.addWidget(QLabel("Automatic backup:"))
        self.auto = QComboBox(); fill_combo(self.auto, [("Every day (when the app closes)", "daily"), ("Every week", "weekly"), ("Off", "off")])
        self.auto.activated.connect(self.set_auto)
        r.addWidget(self.auto)
        r.addStretch(1)
        c.addLayout(r)
        c.addWidget(label("Backups are saved OUTSIDE the program folder, so re-installing never erases them. Copy the backup folder to a pen-drive or another drive regularly. "
                          "The backup contains the encryption key for Aadhaar numbers - keep it private.", "muted"))
        v.addWidget(card)
        self.table = DataTable([("Backup file", "name", "text"), ("Created", lambda r: r["modified"].replace("T", " "), "text"), ("Size (KB)", lambda r: r["size"] // 1024, "int")],
                               empty="No backups in this folder yet. Click “Back up now”.")
        v.addWidget(self.table, 1)
        b = QHBoxLayout()
        b.addWidget(btn("✔  Verify selected", slot=self.verify))
        b.addWidget(btn("♻  Restore selected…", "danger", slot=self.restore))
        b.addWidget(btn("Restore from file…", slot=lambda: self.restore(True)))
        b.addWidget(btn("Export data (JSON)", slot=self.export))
        b.addStretch(1)
        v.addLayout(b)

    def refresh(self):
        d = S.effective_backup_dir(self.app.db)
        self.dir_lbl.setText(f"Backup folder: <b>{d}</b>")
        mode = S.get(self.app.db, "auto_backup") or "off"
        self.auto.setCurrentIndex(max(self.auto.findData(mode), 0))
        self.table.set_rows(B.list_backups(d))

    def choose_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Choose backup folder", S.effective_backup_dir(self.app.db))
        if p:
            safe(self, S.update, self.app.db, self.app.actor, {"backup_dir": p})
            self.refresh()

    def set_auto(self, *_):
        safe(self, S.update, self.app.db, self.app.actor, {"auto_backup": self.auto.currentData()})

    def backup_now(self):
        ok, r = safe(self, B.create_backup, self.app.db, self.app.paths, S.effective_backup_dir(self.app.db), actor=self.app.actor)
        if ok:
            (info if r["ok"] else warn)(self, (f"Backup created and verified.\n\n{r['path']}\n{r['size'] // 1024} KB" if r["ok"] else f"Backup could NOT be verified:\n{r['message']}"))
            self.refresh()

    def verify(self):
        r = self.table.current()
        if r:
            ok, res = B.verify_backup(r["path"])
            (info if ok else warn)(self, "This backup is complete and readable." if ok else f"Problem with this backup:\n{res}")

    def restore(self, pick=False):
        path = None
        if pick:
            path, _ = QFileDialog.getOpenFileName(self, "Choose backup file", S.effective_backup_dir(self.app.db), f"Backups (*{B.EXT})")
        else:
            r = self.table.current()
            path = r["path"] if r else None
        if not path:
            return warn(self, "Select a backup first.")
        ok, why = B.verify_backup(path)
        if not ok:
            return warn(self, f"This backup is invalid and was NOT used:\n{why}")
        if not confirm(self, "Restoring REPLACES all current data with this backup.\n\nA safety copy of the current data is saved first. The program will then close - open it again.",
                       "Restore backup", "Yes, restore", danger=True):
            return
        safe_dir = os.path.join(self.app.paths.root, "restore_safety")
        self.app.db.close()
        res = B.restore_backup(path, self.app.paths, None, safe_dir)
        info(self, res["message"] + "\n\nThe program will now close. Please open it again.")
        QApplication.quit()
        os._exit(0)

    def export(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export data", os.path.join(self.app.paths.exports, f"digit_academy_export_{datetime.now():%Y%m%d}.json"), "JSON (*.json)")
        if p:
            ok, _ = safe(self, B.export_json, self.app.db, self.app.actor, p)
            if ok:
                info(self, f"Exported (Aadhaar numbers excluded):\n{p}")
