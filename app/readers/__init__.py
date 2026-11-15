"""Choose the document reader from the DOCUMENT_READER environment variable.

To add another reader (PaddleOCR, a cloud OCR service, ...): write a class
with a read() method like the ones here and add it to READERS.
"""
from .. import config
from .base import DocumentReader, ExtractedFields, Photo
from .browser_ocr import BrowserOcrReader


def _claude():
    from .claude_reader import ClaudeReader
    return ClaudeReader()


READERS = {
    "browser-ocr": BrowserOcrReader,
    "claude": _claude,
}


def get_reader() -> DocumentReader:
    name = config.DOCUMENT_READER()
    if name not in READERS:
        raise ValueError(f"Unknown DOCUMENT_READER '{name}'. Options: {', '.join(READERS)}")
    return READERS[name]()


__all__ = ["DocumentReader", "ExtractedFields", "Photo", "get_reader"]
