"""Settings: institute details, logo, numbering, receipt, security, appearance."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox,
                               QVBoxLayout, QWidget)
from PySide6.QtPrintSupport import QPrinterInfo

from app.services import settings as S
from app.ui.widgets import btn, fill_combo, info, label, logo_pixmap, page_header, safe


class SettingsPage(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(page_header("Settings", "Institute details, numbering, receipts, security and appearance", btn("💾  Save settings", "primary", slot=self.save)))
        E = QLineEdit
        self.w = {k: E() for k in ("institute_name", "institute_sub", "address", "phone", "email", "website", "admission_fmt", "receipt_fmt")}
        self.footer = QPlainTextEdit(); self.footer.setFixedHeight(54)
        self.terms = QPlainTextEdit(); self.terms.setFixedHeight(54)
        self.date_fmt = QComboBox(); fill_combo(self.date_fmt, [("31-12-2026  (dd-mm-yyyy)", "%d-%m-%Y"), ("31/12/2026  (dd/mm/yyyy)", "%d/%m/%Y"), ("2026-12-31  (yyyy-mm-dd)", "%Y-%m-%d"), ("31 Dec 2026", "%d %b %Y")])
        self.size = QComboBox(); fill_combo(self.size, [("A4 sheet", "A4"), ("Thermal 80 mm roll", "THERMAL80")])
        self.printer = QComboBox(); fill_combo(self.printer, [(p, p) for p in QPrinterInfo.availablePrinterNames()], "System default printer")
        self.lock = QSpinBox(); self.lock.setRange(0, 240); self.lock.setSpecialValueText("Never"); self.lock.setSuffix(" min")
        self.disc = QLineEdit(); self.disc.setMaximumWidth(120)
        self.theme = QComboBox(); fill_combo(self.theme, [("Light", "light"), ("Dark", "dark")])
        self.font = QSpinBox(); self.font.setRange(8, 16); self.font.setSuffix(" pt")
        self.aad = QCheckBox("Print masked Aadhaar (XXXXXXXX1234) on admission forms")
        self.preview = label("", "muted")

        def group(title, rows):
            g = QGroupBox(title.replace("&", "&&")); f = QFormLayout(g); f.setSpacing(8)
            for lab, w in rows:
                f.addRow(lab, w)
            return g
        inst = group("Institute", [("Name *", self.w["institute_name"]), ("Sub-title (e.g. Charitable Trust)", self.w["institute_sub"]), ("Address", self.w["address"]),
                                   ("Phone", self.w["phone"]), ("Email", self.w["email"]), ("Website", self.w["website"])])
        logo = QGroupBox("Logo")
        lh = QHBoxLayout(logo)
        self.logo_lbl = QLabel(); lh.addWidget(self.logo_lbl)
        lv = QVBoxLayout(); lv.addWidget(label("Shown in the header, login screen, receipts, forms, ID cards and reports. Use a clear PNG or JPG.", "muted"))
        lv.addWidget(btn("Change logo…", slot=self.change_logo)); lh.addLayout(lv, 1)
        num = group("Numbering", [("Admission number format", self.w["admission_fmt"]), ("Receipt number format", self.w["receipt_fmt"]), ("", self.preview)])
        rec = group("Receipts & printing", [("Receipt footer", self.footer), ("Terms (optional)", self.terms), ("Default receipt size", self.size), ("Default printer", self.printer),
                                            ("Date format", self.date_fmt), ("", self.aad)])
        sec = group("Security & fees", [("Lock screen after inactivity", self.lock), ("Discount limit without admin approval (%)", self.disc)])
        app_ = group("Appearance", [("Theme", self.theme), ("Text size", self.font)])
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        for g in (inst, logo):
            col1.addWidget(g)
        for g in (num, rec, sec, app_):
            col2.addWidget(g)
        row = QHBoxLayout(); row.addLayout(col1, 1); row.addLayout(col2, 1)
        v.addLayout(row)
        v.addStretch(1)
        for k in ("admission_fmt", "receipt_fmt"):
            self.w[k].textChanged.connect(self._preview)

    def _preview(self):
        try:
            a = S.format_number(self.w["admission_fmt"].text(), 1, date.today())
            r = S.format_number(self.w["receipt_fmt"].text(), 1, date.today())
            self.preview.setText(f"Next numbers will look like:  {a}   and   {r}")
        except Exception:
            self.preview.setText("Invalid format - use {YY} {YYYY} {FY} and {SEQ:05}")

    def refresh(self):
        s = S.get_all(self.app.db)
        for k, w in self.w.items():
            w.setText(s[k])
        self.footer.setPlainText(s["receipt_footer"]); self.terms.setPlainText(s["receipt_terms"])
        for combo, key in ((self.date_fmt, "date_format"), (self.size, "receipt_size"), (self.printer, "default_printer"), (self.theme, "theme")):
            i = combo.findData(s[key]); combo.setCurrentIndex(max(i, 0))
        self.lock.setValue(int(s["lock_minutes"] or 0)); self.disc.setText(s["discount_limit_percent"]); self.font.setValue(int(s["font_size"] or 10))
        self.aad.setChecked(s["show_aadhaar_on_forms"] == "1")
        self.logo_lbl.setPixmap(logo_pixmap(self.app.db, 110))
        self._preview()

    def change_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose logo", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if p:
            ok, _ = safe(self, S.set_logo, self.app.db, self.app.actor, p, self.app.paths.logo_dir)
            if ok:
                self.logo_lbl.setPixmap(logo_pixmap(self.app.db, 110))
                self.app.window.rebuild_header_logo()

    def save(self):
        vals = {k: w.text() for k, w in self.w.items()}
        vals.update(receipt_footer=self.footer.toPlainText(), receipt_terms=self.terms.toPlainText(), date_format=self.date_fmt.currentData(), receipt_size=self.size.currentData(),
                    default_printer=self.printer.currentData() or "", lock_minutes=str(self.lock.value()), discount_limit_percent=self.disc.text() or "0",
                    theme=self.theme.currentData(), font_size=str(self.font.value()), show_aadhaar_on_forms="1" if self.aad.isChecked() else "0")
        if not vals["institute_name"].strip():
            return info(self, "Institute name is required.")
        ok, _ = safe(self, S.update, self.app.db, self.app.actor, vals)
        if ok:
            self.app.window.apply_theme()
            self.app.window._refresh_header()
            info(self, "Settings saved.")
