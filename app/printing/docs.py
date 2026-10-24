"""HTML builders for everything that gets printed: receipts (A4 + thermal), admission form / profile, attendance sheets,
report tables and ID cards. Every document reads SAVED records only."""
import os
from datetime import date, datetime
from html import escape

from app.printing.render import Doc
from app.security.crypto import mask
from app.services import attendance as att
from app.services import catalog, customfields, fees, reports, settings
from app.services import students as st
from app.utils import money


def e(v) -> str:
    return escape(str(v if v is not None else ""))


def rs(p) -> str:
    return money.fmt(int(p or 0), "Rs.")


def fdate(iso, db=None, with_time=False) -> str:
    if not iso:
        return ""
    fmt = settings.get(db, "date_format") if db is not None else "%d-%m-%Y"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return str(iso)
    return d.strftime(fmt + (" %I:%M %p" if with_time else ""))


def _header(db, compact=False, images=None) -> str:
    s = settings.get_all(db)
    images["logo"] = settings.logo_file(db)
    contact = " &nbsp;|&nbsp; ".join(e(x) for x in (s["address"], ("Ph: " + s["phone"]) if s["phone"] else "", s["email"], s["website"]) if x)
    if compact:
        return (f'<p align="center"><img src="logo" width="52" height="54"></p><p align="center"><b style="font-size:12pt;color:#0b2545">{e(s["institute_name"])}</b><br>'
                f'<span style="color:#13a89e"><b>{e(s["institute_sub"])}</b></span><br><span class="small muted">{contact}</span></p>')
    return (f'<table width="100%" cellspacing="0" cellpadding="3"><tr><td width="80"><img src="logo" width="72" height="75"></td>'
            f'<td><h1>{e(s["institute_name"])}</h1><h2>{e(s["institute_sub"])}</h2><span class="small muted">{contact}</span></td></tr></table>'
            f'<hr style="color:#0b2545">')


def _kv(rows, cols=2) -> str:
    """rows: [(label, value)] laid out in `cols` pairs per line."""
    out = ['<table width="100%" cellspacing="0" cellpadding="4" border="1" style="border-color:#cbd5e1">']
    for i in range(0, len(rows), cols):
        out.append("<tr>")
        for lab, val in rows[i:i + cols]:
            out.append(f'<td width="{int(18 if cols == 2 else 12)}%" bgcolor="#eef4fb"><b>{e(lab)}</b></td><td width="{int(32 if cols == 2 else 21)}%">{e(val)}</td>')
        for _ in range(cols - len(rows[i:i + cols])):
            out.append('<td bgcolor="#eef4fb"></td><td></td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


# ===================================================================== receipts
_TITLES = {"payment": "FEE RECEIPT", "refund": "REFUND VOUCHER", "reversal": "RECEIPT REVERSAL NOTE", "advance_applied": "ADVANCE ADJUSTMENT VOUCHER"}


def receipt(db, payment_id: int, size: str = "A4") -> Doc:
    d = fees.receipt_data(db, payment_id)
    p, s, enr = d["payment"], d["student"], d["enrollment"]
    cfg = settings.get_all(db)
    images = {}
    title = _TITLES[p["kind"]]
    if p["kind"] == "payment" and p["advance_paise"] and not p["allocated_paise"]:
        title = "ADVANCE RECEIPT"
    status = ""
    if p["status"] == "reversed":
        status = f'CANCELLED - reversed ({e(d["reversed_by_txn"])})'
    elif p["kind"] == "reversal":
        status = f'Reverses receipt {e(d["reversal_of_receipt"])}'
    guardian = s["father_name"] or s["guardian_name"]
    course = enr["course"] if enr else ""
    batch = ((enr["batch"] or "") + (f' ({enr["timing"]})' if enr and enr.get("timing") else "")) if enr else ""
    no = p["receipt_no"] or p["txn_id"]
    amt = p["amount_paise"]
    words = money.amount_in_words(abs(amt))
    rows = [("Receipt No.", no), ("Date & time", fdate(p["paid_at"], db, True)), ("Student ID", s["student_code"]), ("Admission No.", s["admission_no"]),
            ("Student", s["name"]), ("Father / guardian", guardian), ("Course / class", course), ("Batch", batch),
            ("Payment mode", p["mode"] if p["kind"] != "advance_applied" else "Advance balance"), ("Reference", p["reference"]),
            ("Received by", p["cashier_name"]), ("Transaction ID", p["txn_id"])]
    alloc = "".join(f'<tr><td>{e(a["label"] or "Fee")} - {e(a["course"])} (due {e(fdate(a["due_date"], db))})</td><td align="right">{e(rs(a["amount_paise"]))}</td></tr>' for a in d["allocations"])
    if p["advance_paise"] > 0:
        alloc += f'<tr><td>Advance received (kept on account)</td><td align="right">{e(rs(p["advance_paise"]))}</td></tr>'
    elif p["advance_paise"] < 0 and p["kind"] == "advance_applied":
        alloc += f'<tr><td>Taken from advance balance</td><td align="right">{e(rs(-p["advance_paise"]))}</td></tr>'
    if not alloc:
        alloc = '<tr><td colspan="2" class="muted">-</td></tr>'
    bb = rs(p["balance_before_paise"]) if p["balance_before_paise"] is not None else ""
    ba = rs(p["balance_after_paise"]) if p["balance_after_paise"] is not None else ""
    note = f'<p class="small"><b>Note:</b> {e(p["note"] or p["reason"])}</p>' if (p["note"] or p["reason"]) else ""
    footer = f'<p class="small muted" align="center">{e(cfg["receipt_footer"])}</p>' + (f'<p class="small muted">{e(cfg["receipt_terms"])}</p>' if cfg["receipt_terms"] else "")
    stamp = f'<p align="center"><b style="color:#dc2626">{status}</b></p>' if status else ""
    if size == "THERMAL80":
        body = _header(db, True, images) + f'<p align="center"><b>{title}</b></p>' + stamp
        body += ('<table width="100%" cellspacing="0" cellpadding="1">'
                 + "".join(f'<tr><td class="small muted" width="38%">{e(k)}</td><td class="small"><b>{e(v)}</b></td></tr>' for k, v in
                           [("Receipt", no), ("Date", fdate(p["paid_at"], db, True)), ("Adm No", s["admission_no"]), ("Student", s["name"]), ("Course", course),
                            ("Mode", p["mode"]), ("Ref", p["reference"]), ("By", p["cashier_name"])] if v)
                 + "</table><hr>")
        body += f'<table width="100%" cellspacing="0" cellpadding="1">{alloc}</table><hr>'
        body += (f'<p><span class="big">Amount: {e(rs(amt))}</span><br><span class="small">{e(words)}</span></p>'
                 f'<p class="small">Balance before: {e(bb)}<br>Balance after: <b>{e(ba)}</b></p>' + note + footer)
        return Doc(f"<html><body>{body}</body></html>", images, "THERMAL80", False, f"Receipt {no}")
    body = _header(db, False, images) + f'<p align="center"><span class="big" style="color:#0b2545">{title}</span></p>' + stamp + _kv(rows)
    body += ('<h3>Payment details</h3><table width="100%" cellspacing="0" cellpadding="4" border="1" style="border-color:#cbd5e1">'
             '<tr><th align="left">Description</th><th align="right" width="22%">Amount</th></tr>' + alloc + "</table>")
    body += ('<br><table width="100%" cellspacing="0" cellpadding="4"><tr><td width="55%" valign="top">'
             f'<span class="muted">Balance before this payment:</span> <b>{e(bb)}</b><br><span class="muted">Balance remaining:</span> <b>{e(ba)}</b></td>'
             f'<td align="right" valign="top"><span class="muted">Amount</span><br><span class="big">{e(rs(amt))}</span></td></tr></table>')
    body += f'<p><b>Amount in words:</b> {e(words)}</p>' + note
    body += ('<br><br><table width="100%"><tr><td width="50%" class="small muted">Received by: <b>' + e(p["cashier_name"]) + '</b></td>'
             '<td align="right" class="small muted">__________________________<br>Authorised signature</td></tr></table>')
    body += "<hr>" + footer
    return Doc(f"<html><body>{body}</body></html>", images, "A4", False, f"Receipt {no}")


# ===================================================================== admission form / profile
def admission_form(db, vault, actor, student_id: int, full_profile: bool = False) -> Doc:
    s = st.get(db, actor, vault, student_id)
    cfg = settings.get_all(db)
    images = {}
    hdr = _header(db, False, images)
    photo = ""
    if s["photo_path"] and os.path.isfile(s["photo_path"]):
        images["photo"] = s["photo_path"]
        photo = '<img src="photo" width="90" height="110">'
    else:
        photo = '<table width="90" height="110" border="1" cellspacing="0" style="border-color:#94a3b8"><tr><td align="center" class="small muted">Photo</td></tr></table>'
    title = "STUDENT PROFILE" if full_profile else "ADMISSION FORM"
    top = (f'<table width="100%"><tr><td valign="middle"><span class="big" style="color:#0b2545">{title}</span><br>'
           f'<b>Admission No:</b> {e(s["admission_no"])} &nbsp; <b>Student ID:</b> {e(s["student_code"])}<br>'
           f'<b>Date:</b> {e(fdate(s["admission_date"], db))} &nbsp; <b>Status:</b> {e(s["status"])}</td><td align="right" width="100">{photo}</td></tr></table>')
    personal = [("Student name", s["name"]), ("Date of birth", fdate(s["dob"], db) + (f' (age {s["age"]})' if s["age"] is not None else "")), ("Gender", s["gender"]),
                ("Father's name", s["father_name"]), ("Mother's name", s["mother_name"]), ("Guardian", (s["guardian_name"] + (f' ({s["guardian_relation"]})' if s["guardian_relation"] else "")).strip()),
                ("Student mobile", s["mobile"]), ("Guardian mobile", s["guardian_mobile"]), ("Email", s["email"]), ("Emergency contact", f'{s["emergency_name"]} {s["emergency_phone"]}'.strip()),
                ("Address", s["address"]), ("Locality", s["locality"]), ("City", s["city"]), ("District", s["district"]), ("State", s["state"]), ("PIN", s["pin"])]
    if cfg["show_aadhaar_on_forms"] == "1" and s["aadhaar_masked"]:
        personal.append(("Aadhaar (masked)", s["aadhaar_masked"]))
    acad = [("Education category", s["education_category"]), ("Current school", s["current_school"]), ("Previous qualification", s["prev_qualification"]), ("Board / exam", s["board"])]
    body = hdr + top + "<h3>Personal &amp; contact details</h3>" + _kv(personal) + "<h3>Academic background</h3>" + _kv(acad)
    enr_rows = ""
    for en in s["enrollments"]:
        sm = fees.enrollment_summary(db, en["id"]) if actor.can("fees.view") else None
        subs = ", ".join(r["name"] for r in db.q("SELECT sb.name FROM enrollment_subjects es JOIN subjects sb ON sb.id=es.subject_id WHERE es.enrollment_id=?", (en["id"],)))
        enr_rows += (f'<tr><td>{e(en["course"])}</td><td>{e(en["batch"] or "")} {e(en["batch_timing"] or "")}</td><td>{e(en["teacher"] or "")}</td><td>{e(subs)}</td><td>{e(en["session"] or "")}</td><td>{e(en["status"])}</td>'
                     + (f'<td align="right">{e(rs(sm["net"]))}</td>' if sm else "") + "</tr>")
    if enr_rows:
        body += ('<h3>Course / class enrolment</h3><table width="100%" cellspacing="0" cellpadding="4" border="1" style="border-color:#cbd5e1"><tr><th>Course / class</th><th>Batch &amp; timing</th><th>Teacher</th>'
                 '<th>Subjects</th><th>Session</th><th>Status</th>' + ("<th>Net fee</th>" if actor.can("fees.view") else "") + "</tr>" + enr_rows + "</table>")
    cf = [(f["label"], customfields.display_value(f, s["custom"].get(f["field_key"], ""))) for f in customfields.fields(db, "student", actor) if f["print_on_forms"] or full_profile]
    cf = [(a, b) for a, b in cf if b != "" or not full_profile]
    if cf:
        body += "<h3>Additional information</h3>" + _kv(cf)
    if full_profile:
        if actor.can("fees.view"):
            sm = fees.student_summary(db, student_id)
            body += "<h3>Fee summary</h3>" + _kv([("Total fee", rs(sm["gross"])), ("Discount / scholarship", rs(sm["discount"] + sm["scholarship"])), ("Net payable", rs(sm["net"])),
                                                  ("Received", rs(sm["paid"])), ("Outstanding", rs(sm["outstanding"])), ("Advance held", rs(sm["advance"]))])
        if actor.can("attendance.view"):
            pc = att.percentage(db, student_id)
            body += "<h3>Attendance</h3>" + _kv([("Overall attendance", "-" if pc is None else f"{pc:.1f}%")])
    if s["remarks"]:
        body += f'<p><b>Remarks:</b> {e(s["remarks"])}</p>'
    if not full_profile:
        body += ('<p class="small muted">I declare that the information given above is true. I agree to follow the rules of the institute and to pay the fees on time.</p>'
                 '<br><br><table width="100%"><tr><td class="small muted">__________________<br>Student / guardian signature</td>'
                 '<td align="right" class="small muted">__________________<br>Authorised signatory</td></tr></table>')
    return Doc(f"<html><body>{body}</body></html>", images, "A4", False, f"{title} {s['admission_no']}")


# ===================================================================== report tables
def _cell(kind, v, db):
    if v is None or v == "":
        return ""
    if kind == "money":
        return e(money.fmt(int(v), ""))
    if kind == "pct":
        return "-" if v is None else f"{float(v):.1f}%"
    if kind == "date":
        return e(fdate(str(v), db))
    return e(v)


def dataset(db, ds, *, landscape=None) -> Doc:
    images = {}
    wide = len(ds.columns) > 6 if landscape is None else landscape
    head = "".join(f'<th{" align=right" if k in ("money", "int", "pct") else " align=left"}>{e(n)}</th>' for n, k in ds.columns)
    rows = []
    for r in ds.rows:
        tds = "".join(f'<td{" align=right" if k in ("money", "int", "pct") else ""}>{_cell(k, v, db)}</td>' for (n, k), v in zip(ds.columns, r))
        rows.append(f"<tr>{tds}</tr>")
    if ds.totals:
        tds = "".join(f'<td class="tot"{" align=right" if k in ("money", "int", "pct") else ""}><b>{_cell(k, v, db)}</b></td>' for (n, k), v in zip(ds.columns, ds.totals))
        rows.append(f"<tr>{tds}</tr>")
    body = (_header(db, False, images) + f'<p><span class="big" style="color:#0b2545">{e(ds.title)}</span><br><span class="muted">{e(ds.subtitle)}</span></p>'
            f'<table width="100%" cellspacing="0" cellpadding="3" border="1" style="border-color:#cbd5e1"><tr>{head}</tr>{"".join(rows)}</table>'
            + "".join(f'<p class="small muted">{e(n)}</p>' for n in ds.notes)
            + f'<p class="small muted">{len(ds.rows)} row(s) &nbsp;|&nbsp; Printed {datetime.now():%d-%m-%Y %I:%M %p}</p>')
    return Doc(f"<html><body>{body}</body></html>", images, "A4", wide, ds.title)


# ===================================================================== attendance sheets
def attendance_sheet(db, actor, course_id: int, batch_id: int | None, on_date: str, *, blank_month: bool = False, month: str | None = None) -> Doc:
    images = {}
    c = catalog.course(db, course_id)
    b = db.one("SELECT name, timing FROM batches WHERE id=?", (batch_id,)) if batch_id else None
    studs = att.enrolled_students(db, course_id, batch_id, on_date)
    meta = f'<b>Course / class:</b> {e(c["name"])} &nbsp; <b>Batch:</b> {e(b["name"] if b else "-")} {e(b["timing"] if b else "")}'
    if blank_month:
        days = 31
        head = "".join(f"<th>{i}</th>" for i in range(1, days + 1))
        rows = "".join(f'<tr><td>{i}</td><td>{e(s["admission_no"])}</td><td>{e(s["name"])}</td>' + "<td></td>" * days + "</tr>" for i, s in enumerate(studs, 1))
        rows += "".join(f'<tr><td>{i}</td><td></td><td>&nbsp;</td>' + "<td></td>" * days + "</tr>" for i in range(len(studs) + 1, len(studs) + 4))
        body = (_header(db, False, images) + f'<p><span class="big" style="color:#0b2545">ATTENDANCE REGISTER</span> &nbsp; <b>Month:</b> {e(month or "")}<br>{meta}</p>'
                f'<table width="100%" cellspacing="0" cellpadding="2" border="1" style="border-color:#94a3b8"><tr><th>#</th><th>Adm. No.</th><th align="left" width="22%">Student</th>{head}</tr>{rows}</table>'
                '<p class="small muted">P = Present &nbsp; A = Absent &nbsp; L = Late &nbsp; LV = Leave &nbsp; H = Holiday</p>')
        return Doc(f"<html><body>{body}</body></html>", images, "A4", True, "Attendance register")
    rows = "".join(f'<tr><td>{i}</td><td>{e(s["admission_no"])}</td><td>{e(s["name"])}</td><td width="14%"></td><td width="14%"></td><td width="22%"></td></tr>' for i, s in enumerate(studs, 1))
    body = (_header(db, False, images) + f'<p><span class="big" style="color:#0b2545">DAILY ATTENDANCE SHEET</span> &nbsp; <b>Date:</b> {e(fdate(on_date, db))}<br>{meta}</p>'
            f'<table width="100%" cellspacing="0" cellpadding="6" border="1" style="border-color:#94a3b8"><tr><th>#</th><th>Adm. No.</th><th align="left">Student</th>'
            f'<th>Present / Absent</th><th>Signature</th><th>Remarks</th></tr>{rows}</table>'
            f'<p class="small muted">Total students: {len(studs)} &nbsp; | &nbsp; Teacher signature: ____________________</p>')
    return Doc(f"<html><body>{body}</body></html>", images, "A4", False, "Attendance sheet")


# ===================================================================== ID cards
def id_cards(db, vault, actor, student_ids: list[int]) -> Doc:
    images = {"logo": settings.logo_file(db)}
    cfg = settings.get_all(db)
    parts = []
    for n, sid in enumerate(student_ids):
        s = st.get(db, actor, vault, sid)
        key = f"photo{n}"
        if s["photo_path"] and os.path.isfile(s["photo_path"]):
            images[key] = s["photo_path"]
            ph = f'<img src="{key}" width="62" height="76">'
        else:
            ph = '<table width="62" border="1" cellspacing="0"><tr><td height="76" align="center" class="small muted">Photo</td></tr></table>'
        enr = s["enrollments"][0] if s["enrollments"] else None
        course = f'{enr["course"]}' if enr else ""
        batch = (enr["batch"] or "") if enr else ""
        brk = ' style="page-break-before:always"' if n else ""
        parts.append(
            f'<table width="316" height="186" cellspacing="0" cellpadding="0"{brk}>'
            f'<tr><td colspan="3" bgcolor="#0b2545" height="36"><table width="100%"><tr><td width="44"><img src="logo" width="30" height="31"></td>'
            f'<td><b style="color:#ffffff;font-size:10pt">{e(cfg["institute_name"])}</b><br><span style="color:#5eead4;font-size:7pt">{e(cfg["institute_sub"])}</span></td></tr></table></td></tr>'
            f'<tr><td width="84" align="center" valign="middle">{ph}</td><td valign="middle"><b style="font-size:11pt;color:#0b2545">{e(s["name"])}</b><br>'
            f'<span class="small">Adm. No: <b>{e(s["admission_no"])}</b><br>Course: {e(course)}<br>Batch: {e(batch)}<br>Mobile: {e(s["guardian_mobile"] or s["mobile"])}</span></td><td width="2"><span style="font-size:88pt;color:#ffffff">&nbsp;</span></td></tr>'
            f'<tr><td colspan="3" bgcolor="#13a89e" height="20" align="center"><span style="color:#ffffff;font-size:6.5pt">{e(cfg["address"])} {e(("Ph: " + cfg["phone"]) if cfg["phone"] else "")}</span></td></tr></table>')
    return Doc("<html><body>" + "".join(parts) + "</body></html>", images, "CARD", False, "ID cards")


# ===================================================================== due-fee reminders (text only; nothing is sent automatically)
def reminder_text(db, name: str, due_rows: list) -> str:
    cfg = settings.get_all(db)
    lines = [f"Dear Parent/Student ({name}),", "", f"This is a gentle reminder from {cfg['institute_name']} about the fee installment(s) due:"]
    for r in due_rows:
        lines.append(f"- {r['label'] or 'Fee'}: {money.fmt(r['due'], 'Rs.')} (due {fdate(r['due_date'], db)})")
    lines += ["", "Please pay at the earliest. Ignore this message if you have already paid.", "Thank you.", cfg["institute_name"] + (f" | {cfg['phone']}" if cfg["phone"] else "")]
    return "\n".join(lines)
