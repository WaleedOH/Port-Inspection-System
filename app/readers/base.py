"""The document-reader "plug socket".

A reader takes the photo(s) and returns the fields found on them. Every
reader returns the same ExtractedFields, so they can be swapped freely.
"""
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class ExtractedFields:
    vin: str | None = None
    declared_price: float | None = None
    currency: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    seller: str | None = None
    buyer: str | None = None
    mileage: int | None = None
    incoterm: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Photo:
    data: bytes
    media_type: str  # e.g. "image/jpeg"


class DocumentReader(Protocol):
    name: str
    # True = the phone's browser reads the text itself (free, no account) and
    # sends it along. False = the reader looks at the photo on the server.
    needs_browser_ocr: bool

    def read(self, *, document: Photo | None, vin_plate: Photo | None,
             document_text: str = "", vin_plate_text: str = "") -> ExtractedFields: ...
