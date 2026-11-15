import os
import tempfile

# Use a throw-away data folder so tests never touch real data.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ivc-test-")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password-123"
os.environ["DOCUMENT_READER"] = "browser-ocr"
os.environ["PRICING_PROVIDER"] = "mock"
