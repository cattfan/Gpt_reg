CURRENT_VERSION = 6

DDL_SCHEMA_VERSION = """\
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);
"""

DDL_SETTINGS = """\
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

DDL_JOBS = """\
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    combo TEXT NOT NULL,
    mail_mode TEXT NOT NULL DEFAULT 'outlook',
    reg_mode TEXT NOT NULL DEFAULT 'browser',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    password TEXT,
    session_path TEXT,
    fingerprint_seed TEXT,
    fingerprint_profile TEXT,
    fingerprint_data TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
"""

DDL_JOB_LOGS = """\
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    line TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""

# v4 — tab "Check acc": đăng nhập HTTP rồi đọc plan tài khoản. Tách khỏi bảng
# `jobs` vì vòng đời khác hẳn (không đăng ký, không session file, chạy nhanh và
# nhiều). `combo` giữ nguyên dòng dán vào để retry; `plan` là plan_type thô
# (free/plus/team/…), `plan_detail` là subscription_plan (chatgptplusplan…).
DDL_CHECKS = """\
CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    combo TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    plan TEXT,
    plan_detail TEXT,
    has_subscription INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    deactivated INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    seconds REAL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
"""

ALL_DDL = [
    DDL_SCHEMA_VERSION,
    DDL_SETTINGS,
    DDL_JOBS,
    "CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    DDL_JOB_LOGS,
    "CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);",
    DDL_CHECKS,
    "CREATE INDEX IF NOT EXISTS idx_checks_status ON checks(status);",
]

# Cột thêm sau v1. `CREATE TABLE IF NOT EXISTS` không đụng tới bảng đã tồn tại,
# nên DB cũ cần ALTER TABLE riêng. Mỗi entry: (bảng, tên cột, khai báo).
ADD_COLUMNS: list[tuple[str, str, str]] = [
    ("jobs", "mfa_activated", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "browser_seconds", "REAL"),
    ("jobs", "http_seconds", "REAL"),
    ("jobs", "mfa_seconds", "REAL"),
    # v3 — thời điểm OpenAI CHẤP NHẬN đăng ký (user/register trả 200). Từ mốc
    # này account đã tồn tại trên server dù flow có hỏng ở bước sau; chạy lại
    # phải đi đường ĐĂNG NHẬP chứ không phải đăng ký lại.
    ("jobs", "registered_at", "REAL"),
    # v5 - persistent fingerprint identity; nullable de migrate DB cu va lazy-backfill.
    ("jobs", "fingerprint_seed", "TEXT"),
    ("jobs", "fingerprint_profile", "TEXT"),
    ("jobs", "fingerprint_data", "TEXT"),
]


DATA_MIGRATIONS: dict[int, tuple[str, ...]] = {
    6: ("DELETE FROM settings WHERE key = 'web.auth_token'",),
}
