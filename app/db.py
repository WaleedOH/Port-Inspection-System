"""Database access. All saved data lives in SQL tables defined below.

Two database engines are supported, with the same tables and the same SQL:
  - SQLite (default): a single file in the data folder, nothing to install.
  - PostgreSQL: used when DATABASE_URL is set. This is what makes data
    permanent on hosts without a disk, and it can be any PostgreSQL server
    (Supabase, Neon, or your organisation's own).

Every query uses "?" placeholders, so user input can never be run as SQL.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .compare import DEFAULT_GREEN_THRESHOLD, DEFAULT_YELLOW_THRESHOLD

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id {PK}, username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vin_cache (vin TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checks (
    id {PK},
    created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    vin TEXT NOT NULL, vin_standard INTEGER, vin_check_digit_ok INTEGER, vin_notes TEXT,
    doc_make TEXT, doc_model TEXT, doc_year INTEGER,
    decoded_make TEXT, decoded_model TEXT, decoded_year INTEGER, mismatch_notes TEXT,
    seller TEXT, buyer TEXT, mileage INTEGER, incoterm TEXT,
    declared_price {REAL} NOT NULL, currency TEXT NOT NULL,
    exchange_rate {REAL} NOT NULL, base_currency TEXT NOT NULL, declared_base {REAL},
    market_value {REAL}, market_low {REAL}, market_high {REAL}, market_source TEXT,
    diff_pct {REAL}, flag TEXT NOT NULL, flag_label TEXT NOT NULL, explanation TEXT,
    green_threshold {REAL}, yellow_threshold {REAL},
    photo_file TEXT, vin_photo_file TEXT, notes TEXT,
    decision TEXT NOT NULL DEFAULT 'pending', decision_by TEXT, decision_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_vin ON checks(vin);
CREATE TABLE IF NOT EXISTS price_book (
    id {PK},
    vin_pattern TEXT,                 -- VIN characters 1-8 + 10, when known
    make TEXT NOT NULL, model TEXT NOT NULL, trim TEXT, year INTEGER NOT NULL,
    make_key TEXT NOT NULL, model_key TEXT NOT NULL,   -- lower-case, letters and digits only
    msrp {REAL} NOT NULL,               -- manufacturer's price when new, in the base currency
    source TEXT, added_by TEXT NOT NULL, added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_pattern ON price_book(vin_pattern);
CREATE INDEX IF NOT EXISTS idx_price_vehicle ON price_book(make_key, model_key, year);
CREATE TABLE IF NOT EXISTS web_prices (
    make_key TEXT NOT NULL, model_key TEXT NOT NULL, year INTEGER NOT NULL,
    found INTEGER NOT NULL, low {REAL}, high {REAL}, site TEXT, url TEXT, fetched_at TEXT NOT NULL,
    PRIMARY KEY (make_key, model_key, year)
);
CREATE TABLE IF NOT EXISTS photos (
    name TEXT PRIMARY KEY, media_type TEXT NOT NULL, data {BLOB} NOT NULL, created_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "green_threshold": DEFAULT_GREEN_THRESHOLD,
    "yellow_threshold": DEFAULT_YELLOW_THRESHOLD,
    "base_currency": "USD",
    # Share of the new price kept at each age in years (0 = new). SAMPLE values:
    # replace with your customs authority's official depreciation schedule.
    "depreciation": [100, 82, 74, 66, 60, 54, 48, 44, 39, 35, 32, 29, 26, 23, 21, 19, 17, 15, 14, 12, 11, 10],
    "adjust_for_mileage": True,
    # How many units of base currency 1 unit of each currency is worth.
    # SAMPLE NUMBERS ONLY: replace them with your customs authority's official rates.
    "exchange_rates": {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0066,
                       "CAD": 0.73, "AED": 0.2723, "SAR": 0.2667, "KRW": 0.00072,
                       "CNY": 0.138},
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_postgres() -> bool:
    return bool(config.database_url())


class _Postgres:
    """Makes a PostgreSQL connection look like the SQLite one used everywhere
    else: '?' placeholders, and rows you can read by column name."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, params=()):
        return self.raw.execute(sql.replace("%", "%%").replace("?", "%s"), list(params))


_local = threading.local()   # one PostgreSQL connection per worker thread, reused


def _postgres():
    import psycopg  # imported here so the app runs without it when using SQLite
    from psycopg.rows import dict_row

    raw = getattr(_local, "raw", None)
    if raw is None or raw.closed or getattr(_local, "url", None) != config.database_url():
        # prepare_threshold=None keeps it compatible with connection poolers (e.g. Supabase's).
        raw = psycopg.connect(config.database_url(), row_factory=dict_row, prepare_threshold=None,
                              connect_timeout=10)
        _local.raw, _local.url = raw, config.database_url()
    return raw


@contextmanager
def connect():
    if is_postgres():
        raw = _postgres()
        try:
            yield _Postgres(raw)
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:       # the connection itself is broken: drop it, reconnect next time
                raw.close()
            raise
        return
    conn = sqlite3.connect(config.data_dir() / "app.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _schema() -> str:
    """The same tables for both engines; only three type names differ."""
    if is_postgres():
        return SCHEMA.format(PK="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                             REAL="DOUBLE PRECISION", BLOB="BYTEA")
    return SCHEMA.format(PK="INTEGER PRIMARY KEY", REAL="REAL", BLOB="BLOB")


def _columns(conn, table: str) -> set[str]:
    if is_postgres():
        rows = conn.execute("SELECT column_name AS name FROM information_schema.columns "
                            "WHERE table_schema = current_schema() AND table_name = ?", (table,))
    else:
        rows = conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in rows}


def _insert(conn, table: str, values: dict) -> int:
    """Insert one row and return its new id (works on both engines)."""
    values = {k: int(v) if isinstance(v, bool) else v for k, v in values.items()}
    marks = ", ".join("?" for _ in values)
    row = conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({marks}) RETURNING id",
                       list(values.values())).fetchone()
    return row["id"]


# Columns added after the first version. Existing databases get them on start-up.
NEW_COLUMNS = {
    "checks": {
        "doc_trim": "TEXT", "decoded_trim": "TEXT", "decoded_specs": "TEXT",
        "physical_check": "TEXT NOT NULL DEFAULT 'not_done'",
        "identity_review": "INTEGER NOT NULL DEFAULT 0",
        "physical_details": "TEXT",
    },
    # verified = 0 for prices found by online search that nobody has confirmed yet.
    "price_book": {"verified": "INTEGER NOT NULL DEFAULT 1"},
}


def init() -> None:
    with connect() as conn:
        for statement in _schema().split(";"):
            if statement.strip():
                conn.execute(statement)
        for table, columns in NEW_COLUMNS.items():
            existing = _columns(conn, table)
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
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


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
        conn.execute("INSERT INTO vin_cache (vin, data, fetched_at) VALUES (?,?,?) "
                     "ON CONFLICT(vin) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
                     (vin, data, now()))


# ---------- checks ----------
def insert_check(values: dict) -> int:
    with connect() as conn:
        return _insert(conn, "checks", values)


def get_check(check_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()


def list_checks(vin_query: str = "", flag: str = "", limit: int | None = 200, awaiting: bool = False):
    sql, params = "SELECT * FROM checks WHERE 1=1", []
    if awaiting:   # needs review and nobody has decided yet
        sql += " AND decision = 'pending' AND (flag IN ('YELLOW', 'RED') OR identity_review = 1)"
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


def save_photo(name: str, media_type: str, data: bytes) -> None:
    """Photos are kept in the database too, so they survive on any host."""
    with connect() as conn:
        conn.execute("INSERT INTO photos (name, media_type, data, created_at) VALUES (?,?,?,?)",
                     (name, media_type, data, now()))


def get_photo(name: str):
    with connect() as conn:
        row = conn.execute("SELECT media_type, data FROM photos WHERE name = ?", (name,)).fetchone()
        return (row["media_type"], bytes(row["data"])) if row else None


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
        return _insert(conn, "price_book", {
            "vin_pattern": vin_pattern, "make": make.strip(), "model": model.strip(), "trim": trim.strip(),
            "year": year, "make_key": _key(make), "model_key": _key(model), "msrp": msrp,
            "source": source.strip()[:500], "added_by": added_by, "added_at": now(), "verified": verified})


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
        return conn.execute("SELECT COUNT(*) AS n FROM price_book").fetchone()["n"]


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


# ---------- patterns across checks ----------
def party_history(column: str, name: str, exclude_id: int) -> dict | None:
    """How often the same seller or buyer appeared before, and how often those
    checks needed review. A repeat pattern is a risk signal, not proof."""
    assert column in ("seller", "buyer")
    if not (name or "").strip():
        return None
    with connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(CASE WHEN flag IN ('YELLOW', 'RED') OR identity_review = 1 THEN 1 ELSE 0 END), 0) AS flagged "
            f"FROM checks WHERE lower(trim({column})) = ? AND id != ?",
            (name.strip().lower(), exclude_id)).fetchone()
    return {"total": row["total"], "flagged": row["flagged"]} if row["total"] else None


def summary() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN flag IN ('YELLOW', 'RED') THEN 1 ELSE 0 END), 0) AS price_review, "
            "COALESCE(SUM(identity_review), 0) AS identity_review, "
            "COALESCE(SUM(CASE WHEN decision = 'pending' AND (flag IN ('YELLOW', 'RED') OR identity_review = 1) "
            "THEN 1 ELSE 0 END), 0) AS awaiting FROM checks").fetchone()
    return dict(row)


# ---------- prices found by the automatic web lookup ----------
def get_web_price(make_key: str, model_key: str, year: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM web_prices WHERE make_key = ? AND model_key = ? AND year = ?",
                            (make_key, model_key, year)).fetchone()


def save_web_price(make_key: str, model_key: str, year: int, price) -> None:
    """price is a WebPrice, or None to remember that nothing was found."""
    values = ((1, price.low, price.high, price.site, price.url, price.fetched_at) if price
              else (0, None, None, None, None, now()))
    with connect() as conn:
        conn.execute(
            "INSERT INTO web_prices (make_key, model_key, year, found, low, high, site, url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(make_key, model_key, year) DO UPDATE SET "
            "found = excluded.found, low = excluded.low, high = excluded.high, site = excluded.site, "
            "url = excluded.url, fetched_at = excluded.fetched_at",
            (make_key, model_key, year, *values))


def list_web_prices(limit: int = 200):
    with connect() as conn:
        return conn.execute("SELECT * FROM web_prices WHERE found = 1 ORDER BY fetched_at DESC LIMIT ?",
                            (limit,)).fetchall()


def delete_web_price(make_key: str, model_key: str, year: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM web_prices WHERE make_key = ? AND model_key = ? AND year = ?",
                     (make_key, model_key, year))
