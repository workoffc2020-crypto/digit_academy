import pytest
from helpers import make_user

from app.security import auth
from app.security.permissions import PermissionDenied, ValidationError
from app.services import catalog, customfields, settings, students
from app.utils import money


def test_money_helpers():
    assert money.to_paise("1,250.50") == 125050 and money.to_paise("₹ 99") == 9900 and money.to_paise("") == 0
    with pytest.raises(ValueError):
        money.to_paise("abc")
    assert money.fmt(12345678) == "₹ 1,23,456.78" and money.fmt(-5000) == "-₹ 50.00"
    assert money.amount_in_words(125050) == "Rupees One Thousand Two Hundred Fifty and Fifty Paise Only"
    assert money.amount_in_words(100000 * 100) == "Rupees One Lakh Only"
    assert money.amount_in_words(0) == "Rupees Zero Only"


def test_first_run_login_and_lockout(ctx):
    assert not ctx.needs_setup
    with pytest.raises(ValidationError):
        ctx.first_run_setup(institute_name="x", address="", phone="", email="", admin_username="b", admin_password="secret1", admin_full_name="")
    assert auth.login(ctx.db, "ADMIN", "secret1").role == "super_admin"        # username is case-insensitive
    for _ in range(5):
        with pytest.raises(auth.LoginError):
            auth.login(ctx.db, "admin", "wrong")
    with pytest.raises(auth.LoginError) as e:
        auth.login(ctx.db, "admin", "secret1")                                   # locked even with the right password
    assert "Too many" in str(e.value)
    assert ctx.db.val("SELECT password_hash FROM users").startswith("scrypt$")   # never plaintext


def test_student_create_edit_persist_and_duplicate_admission(ctx, admin):
    sid = students.create(ctx.db, admin, ctx.vault, {"name": "Ravi Kumar", "mobile": "+91 98765 43210", "dob": "2009-01-15"}, aadhaar="1234 5678 9012")
    s = students.get(ctx.db, admin, ctx.vault, sid)
    assert s["mobile"] == "9876543210" and s["admission_no"].startswith("DA26") and s["student_code"] == "STU-000001"
    assert s["aadhaar_masked"] == "XXXXXXXX9012" and s["aadhaar"] == ""
    assert students.get(ctx.db, admin, ctx.vault, sid, reveal_sensitive=True)["aadhaar"] == "123456789012"
    assert "123456789012" not in str(ctx.db.val("SELECT aadhaar_enc FROM students"))          # encrypted at rest
    with pytest.raises(ValidationError):
        students.create(ctx.db, admin, ctx.vault, {"name": "X"}, admission_no=s["admission_no"])
    sid2 = students.create(ctx.db, admin, ctx.vault, {"name": "Y"}, admission_no="CUSTOM-1")
    with pytest.raises(ValidationError):
        students.update(ctx.db, admin, ctx.vault, sid2, {"name": "Y"}, admission_no=s["admission_no"])
    students.update(ctx.db, admin, ctx.vault, sid, {"name": "Ravi K. Sharma", "mobile": "9876543210", "city": "Jaipur"})
    assert students.get(ctx.db, admin, ctx.vault, sid)["name"] == "Ravi K. Sharma"
    assert students.get(ctx.db, admin, ctx.vault, sid)["aadhaar_masked"] == "XXXXXXXX9012"    # update without aadhaar keeps it
    for bad in ({"name": ""}, {"name": "A", "mobile": "123"}, {"name": "A", "email": "nope"}, {"name": "A", "pin": "12"}, {"name": "A", "dob": "2999-01-01"}):
        with pytest.raises(ValidationError):
            students.create(ctx.db, admin, ctx.vault, bad)
    with pytest.raises(ValidationError):
        students.create(ctx.db, admin, ctx.vault, {"name": "A"}, aadhaar="123")


def test_data_survives_reopen(tmp_path):
    from app.context import Context
    root = str(tmp_path / "d")
    c = Context.open(root)
    a = c.first_run_setup(institute_name="T", address="", phone="", email="", admin_username="admin", admin_password="secret1", admin_full_name="")
    sid = students.create(c.db, a, c.vault, {"name": "Persist Me", "mobile": "9999999999"}, aadhaar="111122223333")
    c.close()
    c2 = Context.open(root)
    assert not c2.needs_setup
    a2 = auth.login(c2.db, "admin", "secret1")
    s = students.get(c2.db, a2, c2.vault, sid, reveal_sensitive=True)
    assert s["name"] == "Persist Me" and s["aadhaar"] == "111122223333"            # key file persisted too
    c2.close()


def test_duplicate_warning_and_search(ctx, admin):
    a = students.create(ctx.db, admin, ctx.vault, {"name": "Sita Devi", "mobile": "9000000001", "dob": "2011-02-02"})
    assert students.find_possible_duplicates(ctx.db, "sita devi", "2011-02-02")[0]["id"] == a
    assert students.find_possible_duplicates(ctx.db, "Sita Devi", "2012-01-01", "9000000009") == []          # warning only on real overlap
    students.create(ctx.db, admin, ctx.vault, {"name": "Sita Devi", "mobile": "9000000001", "dob": "2011-02-02"})   # still allowed
    assert len(students.search(ctx.db, admin, "Sita")) == 2
    assert len(students.search(ctx.db, admin, "9000000001")) == 2
    assert students.search(ctx.db, admin, "nobody") == []


def test_permissions_enforced_in_services(ctx, admin, student):
    teacher = make_user(ctx, admin, "t1", "teacher")
    viewer = make_user(ctx, admin, "v1", "viewer")
    with pytest.raises(PermissionDenied):
        students.create(ctx.db, teacher, ctx.vault, {"name": "No"})
    with pytest.raises(PermissionDenied):
        settings.update(ctx.db, teacher, {"phone": "1"})
    with pytest.raises(PermissionDenied):
        catalog.add_subject(ctx.db, viewer, "Art")
    assert students.get(ctx.db, teacher, ctx.vault, student)["aadhaar"] == ""            # teachers never see full Aadhaar
    with pytest.raises(PermissionDenied):
        auth.create_user(ctx.db, teacher, "x1", "abcdef", "viewer")
    plain_admin = make_user(ctx, admin, "adm2", "admin")
    with pytest.raises(ValidationError):
        auth.create_user(ctx.db, plain_admin, "boss", "abcdef", "super_admin")             # only a super admin can make one
    with pytest.raises(ValidationError):
        auth.set_user_role(ctx.db, plain_admin, ctx.db.val("SELECT id FROM users WHERE username='admin'"), "viewer")
    with pytest.raises(ValidationError):
        auth.set_user_active(ctx.db, admin, admin.id, False)                               # cannot lock yourself out
    auth.set_role_permissions(ctx.db, admin, "teacher", {"students.view"})
    teacher = auth.actor_for(ctx.db, teacher.id)
    assert not teacher.can("attendance.mark")


def test_custom_fields_validation_persistence_and_visibility(ctx, admin, student):
    f_int = customfields.save_field(ctx.db, admin, module="student", label="Siblings", field_type="int")
    f_dd = customfields.save_field(ctx.db, admin, module="student", label="Transport", field_type="dropdown", choices=["Bus", "Van"], required=True)
    f_sec = customfields.save_field(ctx.db, admin, module="student", label="Blood group", field_type="text", visible_roles=["admin"])
    with pytest.raises(ValidationError):
        customfields.save_field(ctx.db, admin, module="student", label="Bad", field_type="dropdown", choices=[])
    with pytest.raises(ValidationError):
        students.update(ctx.db, admin, ctx.vault, student, {"name": "Asha Verma"}, custom={"siblings": "two", "transport": "Bus"})
    with pytest.raises(ValidationError):
        students.update(ctx.db, admin, ctx.vault, student, {"name": "Asha Verma"}, custom={"siblings": "2"})          # required dropdown missing
    students.update(ctx.db, admin, ctx.vault, student, {"name": "Asha Verma"}, custom={"siblings": "2", "transport": "Van", "blood_group": "O+"})
    assert students.get(ctx.db, admin, ctx.vault, student)["custom"] == {"siblings": "2", "transport": "Van", "blood_group": "O+"}
    acct = make_user(ctx, admin, "acc", "accountant")
    assert "blood_group" not in students.get(ctx.db, acct, ctx.vault, student)["custom"]                 # hidden from that role
    customfields.save_field(ctx.db, admin, id=f_int, module="student", label="No. of siblings", field_type="int")  # rename keeps data
    assert students.get(ctx.db, admin, ctx.vault, student)["custom"]["siblings"] == "2"
    with pytest.raises(ValidationError):
        customfields.save_field(ctx.db, admin, id=f_int, module="student", label="No. of siblings", field_type="text")
    customfields.set_active(ctx.db, admin, f_int, False)
    assert "siblings" not in students.get(ctx.db, admin, ctx.vault, student)["custom"]
    customfields.set_active(ctx.db, admin, f_int, True)
    assert students.get(ctx.db, admin, ctx.vault, student)["custom"]["siblings"] == "2"                 # data came back


def test_course_batch_enrollment_rules(ctx, admin, student, course_id):
    sess = catalog.current_session_id(ctx.db)
    t = catalog.save_teacher(ctx.db, admin, name="Mr. Singh")
    b = catalog.save_batch(ctx.db, admin, course_id=course_id, session_id=sess, name="Morning", timing="9-10 AM", teacher_id=t)
    with pytest.raises(ValidationError):
        catalog.save_batch(ctx.db, admin, course_id=course_id, session_id=sess, name="Morning")
    eid = students.add_enrollment(ctx.db, admin, student, course_id, batch_id=b, session_id=sess)
    with pytest.raises(ValidationError):
        students.add_enrollment(ctx.db, admin, student, course_id, batch_id=b, session_id=sess)         # no double enrolment
    other = catalog.courses(ctx.db)[0]["id"]
    with pytest.raises(ValidationError):
        students.add_enrollment(ctx.db, admin, student, other, batch_id=b, session_id=sess)             # batch belongs to another course
    assert students.get(ctx.db, admin, ctx.vault, student)["enrollments"][0]["teacher"] == "Mr. Singh"
    assert len(students.search(ctx.db, admin, batch_id=b)) == 1
    new_course = catalog.save_course(ctx.db, admin, category_id=catalog.categories(ctx.db)[0]["id"], name="Brand New Course", fee_paise=500000)
    assert catalog.course(ctx.db, new_course)["fee_paise"] == 500000                                    # no source edit needed
    with pytest.raises(ValidationError):
        catalog.save_course(ctx.db, admin, category_id=catalog.categories(ctx.db)[0]["id"], name="Brand New Course")


def test_student_with_history_cannot_be_deleted(ctx, admin, student):
    students.delete_if_unused(ctx.db, admin, ctx.db.val("SELECT id FROM students WHERE id <> ?", (student,), 0) or student) if False else None
    from app.services import fees
    cid = catalog.save_course(ctx.db, admin, category_id=catalog.categories(ctx.db)[0]["id"], name="C", fee_paise=100000)
    fees.enroll_with_fees(ctx.db, admin, student, cid)
    with pytest.raises(ValidationError):
        students.delete_if_unused(ctx.db, admin, student)
    students.set_status(ctx.db, admin, student, "Inactive")
    assert students.get(ctx.db, admin, ctx.vault, student)["status"] == "Inactive"
    mistake = students.create(ctx.db, admin, ctx.vault, {"name": "Typo"})
    students.delete_if_unused(ctx.db, admin, mistake)
    assert ctx.db.one("SELECT 1 FROM students WHERE id=?", (mistake,)) is None


def test_settings_validation_and_number_formats(ctx, admin):
    with pytest.raises(ValidationError):
        settings.update(ctx.db, admin, {"receipt_fmt": "NOSEQ"})
    with pytest.raises(ValidationError):
        settings.update(ctx.db, admin, {"discount_limit_percent": "150"})
    settings.update(ctx.db, admin, {"phone": "0141-123", "receipt_fmt": "R-{FY}-{SEQ:04}"})
    assert settings.get(ctx.db, "phone") == "0141-123"
    from datetime import date
    assert settings.format_number("DA/{FY}/{SEQ:05}", 7, date(2026, 3, 31)) == "DA/25-26/00007"
    assert settings.format_number("DA/{FY}/{SEQ:05}", 7, date(2026, 4, 1)) == "DA/26-27/00007"
    assert settings.logo_file(ctx.db).endswith("logo.png")


def test_migration_makes_safety_copy_and_blocks_newer_db(tmp_path):
    from app.database import schema
    from app.database.db import Database, DatabaseError
    p = str(tmp_path / "x.db")
    d = Database(p, safety_dir=str(tmp_path / "safe"))
    d.x("INSERT INTO settings(key,value) VALUES ('k','v')")
    d.close()
    schema.MIGRATIONS.append("ALTER TABLE settings ADD COLUMN note TEXT DEFAULT '';")
    try:
        d2 = Database(p, safety_dir=str(tmp_path / "safe"))
        assert d2.migrated_from == 1 and d2.safety_copy and d2.val("SELECT value FROM settings WHERE key='k'") == "v"
        d2.close()
        schema.MIGRATIONS.pop()
        with pytest.raises(DatabaseError):
            Database(p)
    finally:
        if len(schema.MIGRATIONS) > 1:
            schema.MIGRATIONS.pop()
