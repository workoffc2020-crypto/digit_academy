"""Shared ReportLab helpers: fonts (Latin + Devanagari), branded header, number formatting for print."""
import os

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, Table, TableStyle

from app.paths import resource_path
from app.services import settings
from app.utils import money

NAVY = colors.HexColor("#0b2545")
TEAL = colors.HexColor("#13a89e")
GREY = colors.HexColor("#64748b")
LIGHT = colors.HexColor("#eef4fb")
FONT, FONT_B = "DA-Sans", "DA-Sans-Bold"
_registered = False


def register_fonts():
    """FreeSans (bundled) draws English AND Hindi names correctly; Helvetica is only a last resort."""
    global FONT, FONT_B, _registered
    if _registered:
        return
    try:
        pdfmetrics.registerFont(TTFont("DA-Sans", resource_path("assets/fonts/FreeSans.ttf")))
        pdfmetrics.registerFont(TTFont("DA-Sans-Bold", resource_path("assets/fonts/FreeSansBold.ttf")))
        pdfmetrics.registerFontFamily("DA-Sans", normal="DA-Sans", bold="DA-Sans-Bold", italic="DA-Sans", boldItalic="DA-Sans-Bold")
    except Exception:
        FONT, FONT_B = "Helvetica", "Helvetica-Bold"
    _registered = True


def rs(paise: int) -> str:
    return money.fmt(paise, "Rs.")


def style(name="n", size=9, bold=False, color=colors.black, align=0, leading=None, **kw) -> ParagraphStyle:
    register_fonts()
    return ParagraphStyle(name, fontName=FONT_B if bold else FONT, fontSize=size, leading=leading or size * 1.25, textColor=color, alignment=align, **kw)


def esc(text) -> str:
    return (str(text if text is not None else "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def date_text(iso, fmt="%d-%m-%Y") -> str:
    if not iso:
        return ""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except ValueError:
        return str(iso)


def header_block(db, width: float, compact: bool = False):
    """Logo + institute name + address/contact as a flowable table."""
    register_fonts()
    s = settings.get_all(db)
    logo = Image(settings.logo_file(db), width=(14 if compact else 22) * mm, height=(14 if compact else 22) * mm, kind="proportional")
    lines = [Paragraph(esc(s["institute_name"]), style("h1", 13 if compact else 18, True, NAVY, 1 if compact else 0))]
    if s["institute_sub"]:
        lines.append(Paragraph(esc(s["institute_sub"]), style("h2", 8 if compact else 10, True, TEAL, 1 if compact else 0)))
    contact = " | ".join(x for x in (s["address"], ("Ph: " + s["phone"]) if s["phone"] else "", s["email"], s["website"]) if x)
    if contact:
        lines.append(Paragraph(esc(contact), style("h3", 7 if compact else 8, False, GREY, 1 if compact else 0)))
    if compact:
        t = Table([[logo], [lines]], colWidths=[width])
        t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1)]))
        return t
    t = Table([[logo, lines]], colWidths=[26 * mm, width - 26 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, NAVY), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t
