"""Optional, more accurate reader: Claude looks at the photo directly.

This one needs a paid Anthropic account. To switch it on:
    pip install anthropic
    set the environment variables DOCUMENT_READER=claude and ANTHROPIC_API_KEY=...
The key is read from the environment by the SDK; it is never in the code.
Note: with this reader the photos are sent to Anthropic's API for reading.
Check that this is allowed for your documents before using it.
"""
import base64
import os

from pydantic import BaseModel

from .base import ExtractedFields, Photo

MODEL = os.environ.get("CLAUDE_READER_MODEL", "claude-opus-5-5")

PROMPT = """These photos are vehicle import paperwork (an invoice or customs \
document, and possibly a VIN plate) that a customs inspector needs to check.

Read them and fill in the fields. Rules:
- Copy values exactly as printed. Use null for anything you cannot read with \
confidence; a blank is far more useful to the inspector than a guess.
- vin: the 17-character vehicle identification number. If a VIN plate photo is \
included, prefer the VIN on the plate.
- declared_price: the total price for the vehicle as a plain number, with \
currency as a 3-letter ISO code (USD, EUR, JPY, ...).
- year: the vehicle's model year. mileage: the odometer reading as a number.
- incoterm: the delivery term if shown (FOB, CIF, ...)."""


class _Fields(BaseModel):
    vin: str | None
    declared_price: float | None
    currency: str | None
    make: str | None
    model: str | None
    year: int | None
    seller: str | None
    buyer: str | None
    mileage: int | None
    incoterm: str | None


def _image_block(photo: Photo) -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": photo.media_type,
        "data": base64.standard_b64encode(photo.data).decode("ascii")}}


class ClaudeReader:
    name = "Claude vision"
    needs_browser_ocr = False

    def read(self, *, document: Photo | None = None, vin_plate: Photo | None = None,
             document_text: str = "", vin_plate_text: str = "") -> ExtractedFields:
        import anthropic  # imported here so the app runs without this package

        content = [_image_block(p) for p in (document, vin_plate) if p]
        if not content:
            return ExtractedFields()
        content.append({"type": "text", "text": PROMPT})

        client = anthropic.Anthropic()
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            # If the model declines a request, let the API retry on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=_Fields,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return ExtractedFields()  # the inspector fills the form in by hand
        return ExtractedFields(**response.parsed_output.model_dump())
