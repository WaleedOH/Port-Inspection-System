"""Default reader: free, no account needed.

The inspector's phone turns the photo into text using Tesseract.js (an
open-source OCR engine that runs inside the browser). This reader then
picks the fields out of that text. The photo itself is not sent anywhere
for reading.
"""
from .base import ExtractedFields, Photo
from .parser import parse_document_text


class BrowserOcrReader:
    name = "Free on-device OCR (Tesseract.js)"
    needs_browser_ocr = True

    def read(self, *, document: Photo | None = None, vin_plate: Photo | None = None,
             document_text: str = "", vin_plate_text: str = "") -> ExtractedFields:
        return parse_document_text(document_text, vin_plate_text)
