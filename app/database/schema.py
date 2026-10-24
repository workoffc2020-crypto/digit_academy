"""SQLite schema. Money is ALWAYS integer paise. Migrations: each entry is applied once, in order, and
PRAGMA user_version records how far the database has got. NEVER edit an applied migration - append a new one."""

MIGRATIONS: list[str] = []

MIGRATIONS.append("""
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);

CREATE TABLE users (
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, full_name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT, last_login TEXT, created_at TEXT NOT NULL);
CREATE TABLE role_permissions (role TEXT NOT NULL, permission TEXT NOT NULL, PRIMARY KEY (role, permission));
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, user_id INTEGER, username TEXT, action TEXT NOT NULL,
  entity TEXT, entity_id TEXT, detail TEXT);
CREATE INDEX ix_audit_ts ON audit_log(ts);

CREATE TABLE academic_sessions (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, start_date TEXT, end_date TEXT,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE courses (
  id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id), name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'course' CHECK (kind IN ('class','course')),
  duration_months INTEGER NOT NULL DEFAULT 0, fee_paise INTEGER NOT NULL DEFAULT 0 CHECK (fee_paise >= 0),
  admission_fee_paise INTEGER NOT NULL DEFAULT 0 CHECK (admission_fee_paise >= 0),
  registration_fee_paise INTEGER NOT NULL DEFAULT 0 CHECK (registration_fee_paise >= 0),
  default_installments INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0, UNIQUE (category_id, name));
CREATE TABLE subjects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE course_subjects (course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE, PRIMARY KEY (course_id, subject_id));
CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE batches (
  id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES courses(id), session_id INTEGER REFERENCES academic_sessions(id),
  name TEXT NOT NULL, timing TEXT DEFAULT '', teacher_id INTEGER REFERENCES teachers(id), active INTEGER NOT NULL DEFAULT 1,
  UNIQUE (course_id, session_id, name));
CREATE TABLE timetable (id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6), start_time TEXT, end_time TEXT, subject_id INTEGER REFERENCES subjects(id),
  teacher_id INTEGER REFERENCES teachers(id));

CREATE TABLE students (
  id INTEGER PRIMARY KEY, student_code TEXT NOT NULL UNIQUE, admission_no TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL, photo_path TEXT DEFAULT '', dob TEXT, gender TEXT DEFAULT '',
  father_name TEXT DEFAULT '', mother_name TEXT DEFAULT '', guardian_name TEXT DEFAULT '', guardian_relation TEXT DEFAULT '',
  mobile TEXT DEFAULT '', guardian_mobile TEXT DEFAULT '', email TEXT DEFAULT '',
  address TEXT DEFAULT '', locality TEXT DEFAULT '', city TEXT DEFAULT '', district TEXT DEFAULT '', state TEXT DEFAULT '', pin TEXT DEFAULT '',
  aadhaar_enc TEXT DEFAULT '', aadhaar_last4 TEXT DEFAULT '', alt_id_type TEXT DEFAULT '', alt_id_no TEXT DEFAULT '',
  emergency_name TEXT DEFAULT '', emergency_phone TEXT DEFAULT '',
  session_id INTEGER REFERENCES academic_sessions(id), admission_date TEXT, education_category TEXT DEFAULT '',
  current_school TEXT DEFAULT '', prev_qualification TEXT DEFAULT '', board TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active','Inactive','Completed','Transferred','Withdrawn')),
  remarks TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER);
CREATE INDEX ix_students_name ON students(name);
CREATE INDEX ix_students_mobile ON students(mobile);
CREATE INDEX ix_students_status ON students(status);

CREATE TABLE enrollments (
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id), course_id INTEGER NOT NULL REFERENCES courses(id),
  batch_id INTEGER REFERENCES batches(id), session_id INTEGER REFERENCES academic_sessions(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','withdrawn','transferred')),
  start_date TEXT, end_date TEXT, completed_on TEXT, certificate_status TEXT NOT NULL DEFAULT 'none', created_at TEXT NOT NULL);
CREATE INDEX ix_enr_student ON enrollments(student_id);
CREATE INDEX ix_enr_batch ON enrollments(batch_id);
CREATE TABLE enrollment_subjects (enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES subjects(id), PRIMARY KEY (enrollment_id, subject_id));

CREATE TABLE fee_charges (
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id), enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
  kind TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
  created_at TEXT NOT NULL, created_by INTEGER, voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT);
CREATE INDEX ix_charge_enr ON fee_charges(enrollment_id);
CREATE TABLE fee_adjustments (
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id), enrollment_id INTEGER NOT NULL REFERENCES enrollments(id),
  kind TEXT NOT NULL CHECK (kind IN ('discount','scholarship')), amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
  reason TEXT DEFAULT '', approved_by INTEGER, created_at TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT);
CREATE INDEX ix_adj_enr ON fee_adjustments(enrollment_id);
CREATE TABLE installments (
  id INTEGER PRIMARY KEY, enrollment_id INTEGER NOT NULL REFERENCES enrollments(id), student_id INTEGER NOT NULL REFERENCES students(id),
  seq INTEGER NOT NULL, label TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL, amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0));
CREATE INDEX ix_inst_enr ON installments(enrollment_id);
CREATE INDEX ix_inst_due ON installments(due_date);

-- Signed ledger: payment +, reversal -, refund -, advance applied = 0 cash.  amount = allocated + advance (always).
CREATE TABLE payments (
  id INTEGER PRIMARY KEY, txn_id TEXT NOT NULL UNIQUE, receipt_no TEXT UNIQUE, idem_key TEXT UNIQUE,
  student_id INTEGER NOT NULL REFERENCES students(id), enrollment_id INTEGER REFERENCES enrollments(id),
  kind TEXT NOT NULL CHECK (kind IN ('payment','refund','reversal','advance_applied')),
  amount_paise INTEGER NOT NULL, allocated_paise INTEGER NOT NULL DEFAULT 0, advance_paise INTEGER NOT NULL DEFAULT 0,
  mode TEXT NOT NULL DEFAULT '', reference TEXT DEFAULT '', paid_at TEXT NOT NULL, pay_date TEXT NOT NULL,
  cashier_id INTEGER, cashier_name TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','reversed','reversal')),
  reversal_of INTEGER REFERENCES payments(id), reason TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT NOT NULL,
  balance_before_paise INTEGER, balance_after_paise INTEGER,
  CHECK (amount_paise = allocated_paise + advance_paise));
CREATE INDEX ix_pay_student ON payments(student_id);
CREATE INDEX ix_pay_date ON payments(pay_date);
CREATE TABLE payment_allocations (
  id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id), installment_id INTEGER NOT NULL REFERENCES installments(id),
  amount_paise INTEGER NOT NULL CHECK (amount_paise <> 0));
CREATE INDEX ix_alloc_pay ON payment_allocations(payment_id);
CREATE INDEX ix_alloc_inst ON payment_allocations(installment_id);

CREATE TABLE payment_modes (name TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE fee_categories (name TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE installment_templates (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, count INTEGER NOT NULL CHECK (count >= 1),
  every_months INTEGER NOT NULL DEFAULT 1 CHECK (every_months >= 0), active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE attendance_statuses (code TEXT PRIMARY KEY, label TEXT NOT NULL, counts_present REAL NOT NULL DEFAULT 0,
  counts_in_total INTEGER NOT NULL DEFAULT 1, color TEXT DEFAULT '#64748b', sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE attendance (
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id), att_date TEXT NOT NULL,
  batch_id INTEGER NOT NULL DEFAULT 0, subject_id INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL REFERENCES attendance_statuses(code),
  remark TEXT DEFAULT '', marked_by INTEGER, marked_at TEXT NOT NULL, updated_by INTEGER, updated_at TEXT,
  UNIQUE (student_id, att_date, batch_id, subject_id));
CREATE INDEX ix_att_date ON attendance(att_date);
CREATE INDEX ix_att_batch ON attendance(batch_id, att_date);
CREATE TABLE attendance_audit (id INTEGER PRIMARY KEY, attendance_id INTEGER NOT NULL, old_status TEXT, new_status TEXT,
  old_remark TEXT, new_remark TEXT, changed_by INTEGER, changed_at TEXT NOT NULL);

CREATE TABLE custom_fields (
  id INTEGER PRIMARY KEY, module TEXT NOT NULL CHECK (module IN ('student','course','batch','teacher','payment')),
  field_key TEXT NOT NULL, label TEXT NOT NULL, field_type TEXT NOT NULL, required INTEGER NOT NULL DEFAULT 0,
  default_value TEXT DEFAULT '', max_length INTEGER, choices TEXT DEFAULT '', section TEXT DEFAULT 'Additional information',
  sort_order INTEGER NOT NULL DEFAULT 0, visible_roles TEXT DEFAULT '', editable_roles TEXT DEFAULT '', print_on_forms INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE (module, field_key));
CREATE TABLE custom_values (field_id INTEGER NOT NULL REFERENCES custom_fields(id), entity_id INTEGER NOT NULL, value TEXT,
  PRIMARY KEY (field_id, entity_id));
CREATE INDEX ix_cv_entity ON custom_values(entity_id);
""")


def latest_version() -> int:
    return len(MIGRATIONS)
