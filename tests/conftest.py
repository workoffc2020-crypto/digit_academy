import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def ctx(tmp_path):
    """A brand-new database in a temp folder - tests never touch real academy data."""
    from app.context import Context
    c = Context.open(str(tmp_path / "data"))
    c.first_run_setup(institute_name="Test Academy", address="Main Road", phone="9876543210", email="a@b.com",
                      admin_username="admin", admin_password="secret1", admin_full_name="Admin User", session_name="2026-27")
    yield c
    c.close()


@pytest.fixture()
def admin(ctx):
    from app.security import auth
    return auth.login(ctx.db, "admin", "secret1")


@pytest.fixture()
def student(ctx, admin):
    from app.services import students
    sid = students.create(ctx.db, admin, ctx.vault, {"name": "Asha Verma", "mobile": "9876501234", "father_name": "Ram Verma", "dob": "2010-05-04"})
    return sid


@pytest.fixture()
def course_id(ctx):
    return ctx.db.val("SELECT id FROM courses WHERE name='Python'")
