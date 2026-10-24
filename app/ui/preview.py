"""Print preview (real PDF of what will be printed), Print, Save as PDF."""
import os
import tempfile

from PySide6.QtCore import QMarginsF, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout

from app.printing import render
from app.services import settings
from app.ui.widgets import btn, info, safe


class PreviewDialog(QDialog):
    """variants: {label: Doc or callable->Doc}. Several variants (e.g. A4 / Thermal) show a switcher."""

    def __init__(self, app, parent, variants: dict, title="Print preview", default=None, save_name="document"):
        super().__init__(parent)
        self.app, self.variants, self.save_name = app, variants, save_name
        self.setWindowTitle(title)
        self.resize(900, 760)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Layout:"))
        self.combo = QComboBox()
        self.combo.addItems(list(variants))
        if default in variants:
            self.combo.setCurrentText(default)
        self.combo.setVisible(len(variants) > 1)
        top.addWidget(self.combo)
        top.addStretch(1)
        self.print_b = btn("🖨  Print…", "primary", slot=self.do_print)
        self.save_b = btn("💾  Save as PDF…", slot=self.do_save)
        self.open_b = btn("Open PDF", slot=self.do_open)
        close = btn("Close", slot=self.accept)
        for b in (self.print_b, self.save_b, self.open_b, close):
            top.addWidget(b)
        v.addLayout(top)
        self.pdf = QPdfDocument(self)
        self.view = QPdfView()
        self.view.setDocument(self.pdf)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        v.addWidget(self.view, 1)
        self._tmp = None
        self.combo.currentTextChanged.connect(lambda _=None: self.load())
        self.load()

    def doc(self):
        d = self.variants[self.combo.currentText()]
        return d() if callable(d) else d

    def load(self):
        ok, path = safe(self, lambda: render.temp_pdf(self.doc()))
        if ok:
            old, self._tmp = self._tmp, path
            self.pdf.load(path)
            if old:
                try:
                    os.remove(old)
                except OSError:
                    pass

    def do_print(self):
        printer = render.make_printer(settings.get(self.app.db, "default_printer"))
        dlg = QPrintDialog(printer, self)
        if dlg.exec():
            ok, _ = safe(self, render.print_doc, self.doc(), printer)
            if ok:
                info(self, "Sent to the printer.")

    def do_save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF", os.path.join(self.app.paths.exports, self.save_name.replace("/", "-") + ".pdf"), "PDF (*.pdf)")
        if path:
            ok, _ = safe(self, render.to_pdf, self.doc(), path)
            if ok:
                info(self, f"Saved:\n{path}")

    def do_open(self):
        if self._tmp:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._tmp))

    def done(self, r):
        self.pdf.close()
        if self._tmp:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
        super().done(r)
