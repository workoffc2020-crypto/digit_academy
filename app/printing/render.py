"""Turns a Doc (HTML + images) into a PDF, a printout or a preview using Qt only.
Qt shapes Hindi (Devanagari) correctly; FreeSans is bundled so it also works on PCs without a Hindi font."""
import os
import tempfile
from dataclasses import dataclass, field

from PySide6.QtCore import QMarginsF, QSizeF, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from app.paths import resource_path

DPI = 96
MM = DPI / 25.4
_fonts_ready = False
FONT_FAMILY = "FreeSans"


@dataclass
class Doc:
    html: str
    images: dict = field(default_factory=dict)      # resource name -> file path
    page: str = "A4"                                # A4 | THERMAL80 | CARD
    landscape: bool = False
    title: str = "Document"


def ensure_fonts():
    global _fonts_ready, FONT_FAMILY
    if _fonts_ready:
        return
    if QGuiApplication.instance() is None:
        QGuiApplication([])
    for f in ("FreeSans.ttf", "FreeSansBold.ttf"):
        p = resource_path(os.path.join("assets", "fonts", f))
        if os.path.isfile(p):
            fid = QFontDatabase.addApplicationFont(p)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                FONT_FAMILY = fams[0]
    _fonts_ready = True


CSS = """
body { font-family: '%s'; font-size: 10pt; color: #111827; }
h1 { font-size: 17pt; color: #0b2545; margin: 0; }
h2 { font-size: 11pt; color: #13a89e; margin: 0; }
h3 { font-size: 11pt; color: #0b2545; margin: 8px 0 3px 0; }
td, th { font-size: 9.5pt; }
th { background-color: #0b2545; color: #ffffff; }
.muted { color: #64748b; }
.small { font-size: 8pt; }
.tot { font-weight: bold; background-color: #eef4fb; }
.big { font-size: 13pt; font-weight: bold; }
"""


def _page_layout(doc: Doc, height_mm: float | None = None) -> QPageLayout:
    if doc.page == "THERMAL80":
        size = QPageSize(QSizeF(80, max(height_mm or 120, 60)), QPageSize.Unit.Millimeter)
        return QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(3, 3, 3, 3), QPageLayout.Unit.Millimeter)
    if doc.page == "CARD":
        size = QPageSize(QSizeF(85.6, 54), QPageSize.Unit.Millimeter)
        return QPageLayout(size, QPageLayout.Orientation.Landscape if False else QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    orient = QPageLayout.Orientation.Landscape if doc.landscape else QPageLayout.Orientation.Portrait
    return QPageLayout(QPageSize(QPageSize.PageSizeId.A4), orient, QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)


def _text_doc(doc: Doc, width_px: float, height_px: float | None = None) -> QTextDocument:
    ensure_fonts()
    td = QTextDocument()
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(10)
    td.setDefaultFont(f)
    td.setDefaultStyleSheet(CSS % FONT_FAMILY)
    for name, path in doc.images.items():
        img = QImage(path)
        if not img.isNull():
            td.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), img)
    td.setPageSize(QSizeF(width_px, height_px or 100000))
    td.setHtml(doc.html)
    td.setPageSize(QSizeF(width_px, height_px or 100000))
    return td


def _prepare(doc: Doc):
    """Returns (layout, QTextDocument sized for that layout)."""
    layout = _page_layout(doc)
    rect = layout.paintRect(QPageLayout.Unit.Millimeter)
    wpx = rect.width() * MM
    if doc.page == "THERMAL80":
        td = _text_doc(doc, wpx)
        h_mm = td.size().height() / MM + 8
        layout = _page_layout(doc, h_mm)
        rect = layout.paintRect(QPageLayout.Unit.Millimeter)
        td = _text_doc(doc, rect.width() * MM, rect.height() * MM)
    else:
        td = _text_doc(doc, wpx, rect.height() * MM)
    return layout, td


def to_pdf(doc: Doc, path: str) -> str:
    layout, td = _prepare(doc)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    w = QPdfWriter(path)
    w.setResolution(DPI)
    w.setPageLayout(layout)
    w.setTitle(doc.title)
    w.setCreator("Digit Academy Management System")
    td.print_(w)
    return path


def make_printer(printer_name: str = ""):
    info = QPrinterInfo.printerInfo(printer_name) if printer_name else QPrinterInfo.defaultPrinter()
    p = QPrinter(info, QPrinter.PrinterMode.ScreenResolution) if not info.isNull() else QPrinter(QPrinter.PrinterMode.ScreenResolution)
    return p


def print_doc(doc: Doc, printer: QPrinter) -> bool:
    layout, td = _prepare(doc)
    printer.setPageLayout(layout)
    printer.setDocName(doc.title)
    td.print_(printer)
    return True


def temp_pdf(doc: Doc) -> str:
    fd, p = tempfile.mkstemp(prefix="da_prev_", suffix=".pdf")
    os.close(fd)
    return to_pdf(doc, p)
