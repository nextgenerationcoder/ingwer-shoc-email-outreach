import sqlite3
from contextlib import contextmanager

from . import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    schema_path = __file__.replace("db.py", "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    with get_connection() as conn:
        needs_copy = _rename_outdated_form_targets(conn)
        conn.executescript(schema)
        if needs_copy:
            _copy_form_targets_old(conn)


def _rename_outdated_form_targets(conn: sqlite3.Connection) -> bool:
    """CREATE TABLE IF NOT EXISTS in schema.sql only creates tables that
    don't exist yet -- it never adds new columns or relaxes a CHECK
    constraint on a table left over from an older version of this app in
    an existing database file. Rename it out of the way so schema.sql
    recreates it fresh; the caller copies the old rows back in afterward."""
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "form_targets" not in tables:
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(form_targets)").fetchall()}
    if "screenshot" in cols:
        return False  # already current

    conn.execute("ALTER TABLE form_targets RENAME TO form_targets_old")
    return True


def _copy_form_targets_old(conn: sqlite3.Connection) -> None:
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(form_targets_old)").fetchall()}
    new_cols = {r[1] for r in conn.execute("PRAGMA table_info(form_targets)").fetchall()}
    shared = ", ".join(sorted(old_cols & new_cols))
    conn.execute(f"INSERT INTO form_targets ({shared}) SELECT {shared} FROM form_targets_old")
    conn.execute("DROP TABLE form_targets_old")


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default=None):
    init_db()
    with connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    init_db()
    with connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> dict:
    init_db()
    with connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
