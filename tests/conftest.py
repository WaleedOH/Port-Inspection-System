import os
import tempfile

# Use a throw-away data folder so tests never touch real data.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ivc-test-")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password-123"
os.environ["DOCUMENT_READER"] = "browser-ocr"
os.environ["PRICING_PROVIDER"] = "mock"

# Tests must never touch a real database, so DATABASE_URL is ignored here.
# To run the tests on PostgreSQL, point TEST_DATABASE_URL at a throw-away
# database whose name contains "test". It is emptied at the start of the run.
os.environ.pop("DATABASE_URL", None)
_test_db = os.environ.get("TEST_DATABASE_URL")
if _test_db:
    import psycopg

    with psycopg.connect(_test_db, autocommit=True) as _conn:
        _name = _conn.execute("SELECT current_database()").fetchone()[0]
        if "test" not in _name:
            raise SystemExit(f"Refusing to empty database '{_name}': its name does not contain 'test'.")
        _conn.execute("DROP SCHEMA public CASCADE")
        _conn.execute("CREATE SCHEMA public")
    os.environ["DATABASE_URL"] = _test_db
