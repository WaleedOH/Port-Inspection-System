"""Database access. Uses SQLite: a single file, nothing to install.

Every query uses "?" placeholders, so user input can never be run as SQL.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .compare import DEFAULT_GREEN_THRESHOLD, DEFAULT_YELLOW_THRESHOLD

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vin_cache (vin TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    vin TEXT NOT NULL, vin_standard INTEGER, vin_check_digit_ok INTEGER, vin_notes TEXT,
    doc_make TEXT, doc_model TEXT, doc_year INTEGER,
    decoded_make TEXT, decoded_model TEXT, decoded_year INTEGER, mismatch_notes TEXT,
    seller TEXT, buyer TEXT, mileage INTEGER, incoterm TEXT,
    declared_price REAL NOT NULL, currency TEXT NOT NULL,
    exchange_rate REAL NOT NULL, base_currency TEXT NOT NULL, declared_base REAL,
    market_value REAL, market_low REAL, market_high REAL, market_source TEXT,
    diff_pct REAL, flag TEXT NOT NULL, flag_label TEXT NOT NULL, explanation TEXT,
    green_threshold REAL, yellow_threshold REAL,
    photo_file TEXT, vin_photo_file TEXT, notes TEXT,
    decision TEXT NOT NULL DEFAULT 'pending', decision_by TEXT, decision_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_vin ON checks(vin);
CREATE TABLE IF NOT EXISTS price_book (
    id INTEGER PRIMARY KEY,
    vin_pattern TEXT,                 -- VIN characters 1-8 + 10, when known
    make TEXT NOT NULL, model TEXT NOT NULL, trim TEXT, year INTEGER NOT NULL,
    make_key TEXT NOT NULL, model_key TEXT NOT NULL,   -- lower-case, letters and digits only
    msrp REAL NOT NULL,               -- manufacturer's price when new, in the base currency
    source TEXT, added_by TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_pattern ON price_book(vin_pattern);
CREATE INDEX IF NOT EXISTS idx_price_vehicle ON price_book(make_key, model_key, year);
"""

DEFAULT_SETTINGS = {
    "green_threshold": DEFAULT_GREEN_THRESHOLD,
    "yellow_threshold": DEFAULT_YELLOW_THRESHOLD,
    "base_currency": "USD",
    # How many units of base currency 1 unit of each currency is worth.
    # SAMPLE NUMBERS ONLY: replace them with your customs authority's official rates.
    "exchange_rates": {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0066,
                       "CAD": 0.73, "AED": 0.2723, "SAR": 0.2667, "KRW": 0.00072,
                       "CNY": 0.138},
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    conn = sqlite3.connect(config.data_dir() / "app.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first version. Existing databases get them on start-up.
NEW_COLUMNS = {
    "checks": {
        "doc_trim": "TEXT", "decoded_trim": "TEXT", "decoded_specs": "TEXT",
        "physical_check": "TEXT NOT NULL DEFAULT 'not_done'",
        "identity_review": "INTEGER NOT NULL DEFAULT 0",
    },
    # verified = 0 for prices found by online search that nobody has confirmed yet.
    "price_book": {"verified": "INTEGER NOT NULL DEFAULT 1"},
}


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, columns in NEW_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


# ---------- passwords and users ----------
def hash_password(password: str) -> str:
    """Passwords are stored as a slow, salted hash (scrypt), never as plain text."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_user(username: str, password: str, is_admin: bool = False) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, hash_password(password), int(is_admin), now()),
        )


def get_user(username: str):
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users():
    with connect() as conn:
        return conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()


def delete_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def count_users() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ---------- settings ----------
def get_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            settings[row["key"]] = json.loads(row["value"])
    return settings


def save_settings(values: dict) -> None:
    with connect() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )


# ---------- VIN cache ----------
def get_cached_vin(vin: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT data FROM vin_cache WHERE vin = ?", (vin,)).fetchone()
        return row["data"] if row else None


def cache_vin(vin: str, data: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO vin_cache (vin, data, fetched_at) VALUES (?,?,?)",
                     (vin, data, now()))


# ---------- checks ----------
def insert_check(values: dict) -> int:
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO checks ({columns}) VALUES ({marks})", list(values.values()))
        return cur.lastrowid


def get_check(check_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()


def list_checks(vin_query: str = "", flag: str = "", limit: int | None = 200):
    sql, params = "SELECT * FROM checks WHERE 1=1", []
    if vin_query:
        # Escape % and _ so they are searched literally.
        like = vin_query.upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " AND vin LIKE ? ESCAPE '\\'"
        params.append(f"%{like}%")
    if flag:
        sql += " AND flag = ?"
        params.append(flag)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def previous_checks_for_vin(vin: str, exclude_id: int | None = None):
    with connect() as conn:
        return conn.execute(
            "SELECT id, created_at, declared_price, currency, flag FROM checks "
            "WHERE vin = ? AND id != ? ORDER BY id DESC", (vin, exclude_id or 0)).fetchall()


def set_decision(check_id: int, decision: str, notes: str, username: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE checks SET decision = ?, notes = ?, decision_by = ?, decision_at = ? WHERE id = ?",
            (decision, notes, username, now(), check_id))


def photo_in_use(filename: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM checks WHERE photo_file = ? OR vin_photo_file = ?",
                            (filename, filename)).fetchone() is not None


# ---------- our own price book ----------
def _key(text: str | None) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def add_price(*, make: str, model: str, year: int, msrp: float, added_by: str,
              trim: str = "", vin_pattern: str | None = None, source: str = "",
              verified: bool = True) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO price_book (vin_pattern, make, model, trim, year, make_key, model_key, "
            "msrp, source, added_by, added_at, verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vin_pattern, make.strip(), model.strip(), trim.strip(), year, _key(make), _key(model),
             msrp, source.strip()[:500], added_by, now(), int(verified)))
        return cur.lastrowid


def find_price(vin_pattern: str | None, make: str, model: str, year: int, trim: str = ""):
    """Best price-book entry for a vehicle, newest first.

    1. Same VIN pattern: the same model, trim, engine and year. Most reliable.
    2. Same make, model and year, preferring the same trim."""
    with connect() as conn:
        if vin_pattern:
            row = conn.execute("SELECT *, 'VIN pattern' AS matched_by FROM price_book "
                               "WHERE vin_pattern = ? ORDER BY id DESC", (vin_pattern,)).fetchone()
            if row:
                return row
        if not (_key(make) and _key(model) and year):
            return None
        rows = conn.execute(
            "SELECT *, 'make, model and year' AS matched_by FROM price_book "
            "WHERE make_key = ? AND model_key = ? AND year = ? ORDER BY id DESC",
            (_key(make), _key(model), year)).fetchall()
        same_trim = [r for r in rows if _key(trim) and _key(r["trim"]) == _key(trim)]
        return (same_trim or rows or [None])[0]


def list_prices(query: str = "", limit: int = 300):
    like = f"%{_key(query)}%"
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM price_book WHERE make_key || model_key LIKE ? OR vin_pattern LIKE ? "
            "ORDER BY id DESC LIMIT ?", (like, f"%{query.strip().upper()}%", limit)).fetchall()


def count_prices() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM price_book").fetchone()[0]


def confirm_price(price_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE price_book SET verified = 1 WHERE id = ?", (price_id,))


def delete_price(price_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM price_book WHERE id = ?", (price_id,))


def peer_declarations(vin_pattern: str | None, exclude_id: int):
    """Earlier declarations for cars with the same VIN pattern (other VINs only)."""
    if not vin_pattern:
        return []
    with connect() as conn:
        return conn.execute(
            "SELECT id, vin, created_at, declared_base FROM checks "
            "WHERE substr(vin, 1, 8) || substr(vin, 10, 1) = ? AND length(vin) = 17 "
            "AND id != ? AND declared_base IS NOT NULL ORDER BY id DESC LIMIT 200",
            (vin_pattern, exclude_id)).fetchall()
