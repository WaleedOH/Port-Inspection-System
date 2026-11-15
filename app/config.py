"""All settings that come from the environment live here.

Secrets (passwords, API keys) are NEVER written in the code. They are read
from environment variables, which you set on your computer or hosting service.
"""
import os
import secrets
import tempfile
from pathlib import Path


_fallback_dir: Path | None = None


def data_dir() -> Path:
    """Folder holding the database and the uploaded photos.

    Some hosts (Vercel and other "serverless" platforms) do not allow writing
    next to the code. There the app falls back to the temporary folder so it
    can still start, but that folder is wiped regularly: see is_ephemeral().
    """
    global _fallback_dir
    path = _fallback_dir or Path(os.environ.get("DATA_DIR", "data"))
    try:
        (path / "photos").mkdir(parents=True, exist_ok=True)
    except OSError:
        _fallback_dir = path = Path(tempfile.gettempdir()) / "import-value-checker"
        (path / "photos").mkdir(parents=True, exist_ok=True)
    return path


def is_ephemeral() -> bool:
    """True when saved data will NOT survive (temporary storage is in use)."""
    data_dir()
    return _fallback_dir is not None or bool(os.environ.get("VERCEL"))


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
    default = "1" if os.environ.get("VERCEL") else "0"  # Vercel is always HTTPS
    return os.environ.get("COOKIE_SECURE", default) == "1"


DOCUMENT_READER = lambda: os.environ.get("DOCUMENT_READER", "browser-ocr")  # noqa: E731
PRICING_PROVIDER = lambda: os.environ.get("PRICING_PROVIDER", "mock")  # noqa: E731
# "offline" = the app's own vehicle database (default, no internet needed)
# "online"  = ask the NHTSA service every time
# "both"    = own database first, NHTSA only when it has no details
VIN_DECODER = lambda: os.environ.get("VIN_DECODER", "offline")  # noqa: E731
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB per photo
