"""All settings that come from the environment live here.

Secrets (passwords, API keys) are NEVER written in the code. They are read
from environment variables, which you set on your computer or hosting service.
"""
import os
import secrets
from pathlib import Path


def data_dir() -> Path:
    """Folder holding the database and the uploaded photos."""
    path = Path(os.environ.get("DATA_DIR", "data"))
    (path / "photos").mkdir(parents=True, exist_ok=True)
    return path


def secret_key() -> str:
    """Key used to sign login cookies. Taken from SECRET_KEY, or generated
    once and kept in the data folder (readable only by the app's user)."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    key_file = data_dir() / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
        key_file.chmod(0o600)
    return key_file.read_text().strip()


def cookie_secure() -> bool:
    """Set COOKIE_SECURE=1 when the app is served over HTTPS (any real deployment)."""
    return os.environ.get("COOKIE_SECURE", "0") == "1"


DOCUMENT_READER = lambda: os.environ.get("DOCUMENT_READER", "browser-ocr")  # noqa: E731
PRICING_PROVIDER = lambda: os.environ.get("PRICING_PROVIDER", "mock")  # noqa: E731
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB per photo
