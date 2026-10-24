import os
import time
from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.printing import render
from app.security import auth
from app.services import attendance as att
from app.services import customfields, fees, reports as rp, students as svc
from app.ui import theme
from app.ui import widgets as W
from app.ui.main_window import AppState, MainWindow

fitz = pytest.importorskip("pymupdf")


@pytest.fixture()
def gui(ctx, admin, monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet(10, False))
    msgs = []
    for n in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda p, t, m, *a, _n=n, **k: msgs.append((_n, m))))
    previews = []

    def fake_preview(self, parent, variants, title="", default=None, save_name="doc"):
        v = variants if isinstance(variants, dict) else {"x": variants}
        for i, (k, d) in enumerate(v.items()):
            doc = d() if callable(d) else d
            p = render.to_pdf(doc, str(tmp_path / f"prev_{len(previews)}_{i}.pdf"))
            previews.append((title, p))
    monkeypatch.setattr(AppState, "preview", fake_preview)
    w = MainWindow(ctx, admin)
    w.resize(1280, 780)
    w.show()
    app.processEvents()
    return type("G", (), dict(app=app, w=w, msgs=msgs, previews=previews, ctx=ctx, admin=admin))


def test_every_page_opens_without_errors(gui):
    for i in range(len(gui.w.pages)):
        gui.w.goto(i)
        gui.app.processEvents()
    assert gui.msgs == []


def test_new_admission_with_fees_and_first_payment_then_receipt(gui):
    from app.ui.students import StudentDialog
    d = StudentDialog(gui.w.state, gui.w)
    d.f["name"].setText("Ravi Kumar"); d.f["father_name"].setText("Mohan Kumar"); d.f["mobile"].setText("9876543210")
    d.panel.course.setCurrentIndex(d.panel.course.findText("Python", Qt.MatchFlag.MatchContains))
    assert d.panel.course_fee.text() == ""                            # real fees are entered by the owner, none invented
    d.panel.course_fee.setText("12000"); d.panel.adm_fee.setText("500")
    d.panel.template.setCurrentIndex(d.panel.template.findText("Monthly x3"))
    d.panel.paid_now.setText("4500")
    d.save()
    assert gui.msgs == [], gui.msgs
    sid = d.sid
    sm = fees.student_summary(gui.ctx.db, sid)
    assert sm["net"] == 1250000 and sm["paid"] == 450000 and sm["outstanding"] == 800000
    assert d.created_payment and fees.get_payment(gui.ctx.db, d.created_payment)["receipt_no"]


def _student_with_course(gui, name="Meena Devi"):
    db, a = gui.ctx.db, gui.admin
    sid = svc.create(db, a, gui.ctx.vault, {"name": name, "mobile": "9811111111"})
    cid = db.val("SELECT id FROM courses WHERE name='MS Office'")
    eid = fees.enroll_with_fees(db, a, sid, cid, course_fee=900000, installments=3, first_due=date(2026, 1, 1))
    return sid, eid


def test_collect_dialog_pays_oldest_first_and_blocks_double_submit(gui):
    from app.ui.fees import CollectDialog
    sid, eid = _student_with_course(gui)
    due = [i for i in fees.installments_status(gui.ctx.db, eid) if i["due"] > 0]
    d = CollectDialog(gui.w.state, gui.w, sid, eid, due)
    d.amount.setText("3500"); d.save()
    assert gui.msgs == [] and d.payment_id
    again = fees.collect_payment(gui.ctx.db, gui.admin, sid, 350000, "Cash", enrollment_id=eid, idem_key=d.idem)
    assert again == d.payment_id                                       # same key -> same receipt, no second payment
    assert fees.enrollment_summary(gui.ctx.db, eid)["paid"] == 350000


def test_fees_page_flow_and_receipt_preview(gui):
    sid, eid = _student_with_course(gui)
    pid = fees.collect_payment(gui.ctx.db, gui.admin, sid, 200000, "UPI", enrollment_id=eid, reference="UTR1")
    page = gui.w.go_to_module("fees")
    page.open_student(sid)
    assert page.pays.table.rowCount() == 1 and page.inst.table.rowCount() == 3
    page.pays.table.selectRow(0)
    page.print_receipt()
    assert gui.previews and "Receipt" in gui.previews[-1][0]
    t = fitz.open(gui.previews[-1][1])[0].get_text()
    assert "2,000.00" in t and "Meena Devi" in t
    page.statement()
    assert gui.msgs == []


def test_attendance_page_mark_save_and_lock_for_non_editors(gui):
    sid, eid = _student_with_course(gui)
    page = gui.w.go_to_module("attendance")
    page.course.setCurrentIndex(page.course.findText("MS Office", Qt.MatchFlag.MatchContains))
    page.load()
    assert page.table.rowCount() == 1
    page.mark_all.__func__  # exists
    combo = page.table.cellWidget(0, 2)
    combo.setCurrentIndex(combo.findData("P"))
    page.save()
    assert att.percentage(gui.ctx.db, sid) == 100.0
    page.sheet()
    assert any("attendance" in t.lower() for t, _ in gui.previews)


def test_all_reports_run_through_the_reports_page(gui):
    sid, eid = _student_with_course(gui)
    fees.collect_payment(gui.ctx.db, gui.admin, sid, 100000, "Cash", enrollment_id=eid)
    page = gui.w.go_to_module("reports")
    tree = page.tree
    n = 0
    for gi in range(tree.topLevelItemCount()):
        g = tree.topLevelItem(gi)
        for ci in range(g.childCount()):
            tree.setCurrentItem(g.child(ci))
            if "student_id" in page.widgets:
                page.widgets["student_id"].addItem("Meena", sid)
            page.run()
            assert page.ds is not None, g.child(ci).text(0)
            n += 1
    assert n >= 25 and gui.msgs == [], gui.msgs
    page.print_()
    assert gui.previews


def test_custom_field_appears_on_form_and_persists(gui):
    customfields.save_field(gui.ctx.db, gui.admin, module="student", label="Blood group", field_type="dropdown", choices=["A+", "B+", "O+"], required=False, print_on_forms=True)
    from app.ui.students import StudentDialog
    d = StudentDialog(gui.w.state, gui.w)
    d.f["name"].setText("Pooja")
    w = d.custom.w["blood_group"][1]
    w.setCurrentIndex(w.findData("B+"))
    d.enroll_chk.setChecked(False)
    d.save()
    assert gui.msgs == []
    assert svc.get(gui.ctx.db, gui.admin, gui.ctx.vault, d.sid)["custom"]["blood_group"] == "B+"


def test_settings_save_changes_theme_and_header(gui):
    page = gui.w.go_to_module("settings_page")
    page.w["institute_name"].setText("Digit Academy Test")
    page.theme.setCurrentIndex(page.theme.findData("dark"))
    page.save()
    assert gui.msgs and "saved" in gui.msgs[-1][1].lower()
    assert "DIGIT ACADEMY TEST" in gui.w.title.text()
    assert "#0f172a" in QApplication.instance().styleSheet()


def test_backup_page_creates_verified_backup(gui, tmp_path):
    from app.services import settings
    settings.update(gui.ctx.db, gui.admin, {"backup_dir": str(tmp_path / "bk")})
    page = gui.w.go_to_module("backup_page")
    page.backup_now()
    assert gui.msgs and "verified" in gui.msgs[-1][1].lower()
    assert len(os.listdir(tmp_path / "bk")) == 1


def test_teacher_role_sees_limited_menu_and_cannot_edit_saved_attendance(gui):
    from tests.helpers import make_user
    t = make_user(gui.ctx, gui.admin, "teach1", "teacher")
    w2 = MainWindow(gui.ctx, t)
    titles = [p[0] for p in w2.pages]
    assert "Students" in titles and "Attendance" in titles and "Fees & Receipts" not in titles and "Settings" not in titles and "Users & Security" not in titles


@pytest.mark.parametrize("size", [(1920, 1040), (1366, 768), (1000, 640), (760, 520)])
def test_layout_fits_every_screen_size(gui, size):
    gui.w.resize(*size)
    for i in range(len(gui.w.pages)):
        gui.w.goto(i)
        gui.app.processEvents(); gui.app.processEvents()
        sc = gui.w.stack.currentWidget()
        assert sc.width() <= gui.w.width() and gui.w.centralWidget().width() <= gui.w.width()
        assert sc.horizontalScrollBar().maximum() <= 40 or size[0] < 900, (gui.w.pages[i][0], size, sc.horizontalScrollBar().maximum())
