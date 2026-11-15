"""Find the invoice fields inside plain text that came out of OCR.

OCR text is messy, so this uses simple, forgiving rules ("heuristics").
It will make mistakes, which is why the inspector always reviews the result.
"""
import re

from ..vin import compute_check_digit
from .base import ExtractedFields

MAKES = [
    "Mercedes-Benz", "Land Rover", "Alfa Romeo", "Aston Martin", "Rolls-Royce",
    "Toyota", "Honda", "Ford", "Chevrolet", "Nissan", "Hyundai", "Kia", "BMW",
    "Lexus", "Tesla", "Volkswagen", "Audi", "Jeep", "Porsche", "Mazda", "Subaru",
    "Mitsubishi", "Suzuki", "Volvo", "Dodge", "GMC", "Cadillac", "Infiniti",
    "Acura", "Jaguar", "Bentley", "Ferrari", "Lamborghini", "Maserati", "Peugeot",
    "Renault", "Fiat", "Chrysler", "Buick", "Lincoln", "Genesis", "Mini", "Isuzu",
    "BYD", "Geely", "Chery", "Ram", "Skoda", "Seat", "Opel", "Citroen", "Daihatsu",
]
CURRENCY_CODES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "AED", "SAR", "KRW",
                  "CNY", "CHF", "KWD", "QAR", "BHD", "OMR", "EGP", "TRY", "INR"]
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
INCOTERMS = ["FOB", "CIF", "CFR", "EXW", "FCA", "CPT", "CIP", "DAP", "DDP", "FAS"]

# Labels, most trustworthy first. The grand total beats a line-item price.
PRICE_LABELS = [
    r"(?:grand\s+|invoice\s+)?total(?:\s+(?:amount|price|value|due))?",
    r"(?:declared|customs|invoice|purchase|sale|selling|vehicle|unit)\s+(?:value|price|amount)",
    r"amount(?:\s+due)?", r"price",
]
SELLER_LABELS = r"(?:seller|sold\s+by|exporter|shipper|vendor|supplier|from)"
BUYER_LABELS = r"(?:buyer|sold\s+to|consignee|importer|bill\s+to|purchaser|customer)"

_NUMBER = r"\d{1,3}(?:[ ,. ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"


def parse_amount(text: str) -> float | None:
    """'12,500.00' -> 12500.0   '12.500,00' -> 12500.0   '12 500' -> 12500.0"""
    text = re.sub(r"[  ]", "", text.strip())
    if not re.fullmatch(r"[\d.,]+", text) or not re.search(r"\d", text):
        return None
    last_sep = max(text.rfind(","), text.rfind("."))
    if last_sep == -1:
        return float(text)
    decimals = len(text) - last_sep - 1
    if decimals in (1, 2):  # the last separator is a decimal point
        whole = re.sub(r"[.,]", "", text[:last_sep])
        return float(f"{whole}.{text[last_sep + 1:]}")
    return float(re.sub(r"[.,]", "", text))  # all separators are thousands marks


def find_vin(text: str) -> str | None:
    """Look for a 17-character VIN. Prefers one that follows a 'VIN' label and
    one whose check digit is correct. Fixes the classic OCR mix-ups O/0, I/1, Q/0."""
    upper = text.upper()
    candidates: list[tuple[int, str]] = []

    def consider(raw: str, score: int) -> None:
        vin = raw.translate(str.maketrans("OIQ", "010"))
        if len(vin) != 17 or not re.search(r"[A-Z]", vin) or not re.search(r"\d", vin):
            return
        if vin[8] == compute_check_digit(vin):
            score += 2
        candidates.append((score, vin))

    # After a label, OCR may have split the VIN with spaces or dashes.
    for match in re.finditer(r"(?:VIN|V\.I\.N\.?|CHASSIS|FRAME)\s*(?:NO|NUMBER|#)?\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9 \-]{15,24})", upper):
        compact = re.sub(r"[ \-]", "", match.group(1))
        consider(compact[:17], 3)
    for match in re.finditer(r"(?<![A-Z0-9])[A-Z0-9]{17}(?![A-Z0-9])", upper):
        consider(match.group(0), 1)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


def _find_price(text: str) -> tuple[float | None, str | None]:
    """Return (amount, currency found next to it)."""
    for label in PRICE_LABELS:
        pattern = rf"(?im)\b{label}\b[^\d\n$€£¥]{{0,25}}([$€£¥]|[A-Z]{{3}})?\s*({_NUMBER})\s*([A-Z]{{3}})?"
        for match in re.finditer(pattern, text):
            amount = parse_amount(match.group(2))
            if amount is None or amount < 100:  # skip quantities, VAT %, etc.
                continue
            near = match.group(1) or match.group(3) or ""
            currency = CURRENCY_SYMBOLS.get(near) or (near if near in CURRENCY_CODES else None)
            return amount, currency
    return None, None


def _find_currency(text: str) -> str | None:
    for code in CURRENCY_CODES:
        if re.search(rf"\b{code}\b", text):
            return code
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None


def _labelled(text: str, labels: str) -> str | None:
    match = re.search(rf"(?im)^\s*{labels}\s*(?:name)?\s*[:\-]\s*(.+)$", text)
    if not match:
        return None
    value = re.split(r"\s{3,}|\t", match.group(1).strip())[0].strip(" .,;:")
    return value[:120] or None


def _find_make_model(text: str) -> tuple[str | None, str | None]:
    make = model = None
    labelled_make = _labelled(text, r"(?:make|brand|manufacturer)")
    labelled_model = _labelled(text, r"model")
    for candidate in MAKES:
        pattern = re.escape(candidate).replace(r"\-", r"[\- ]?")
        match = re.search(rf"(?i)\b{pattern}\b[ \t]*([^\n]*)", text)
        if match:
            make = candidate
            if not labelled_model:
                rest = re.sub(r"(?i)\b(model|year|vin|color|colour)\b.*", "", match.group(1))
                words = [w for w in re.findall(r"[A-Za-z0-9\-]+", rest) if not re.fullmatch(r"(19|20)\d\d", w)]
                model = " ".join(words[:2]) or None
            break
    if labelled_make and not make:
        make = labelled_make.split()[0].title()
    if labelled_model:
        model = " ".join(labelled_model.split()[:3])
    return make, model


def _find_year(text: str) -> int | None:
    match = re.search(r"(?i)\b(?:model\s+)?year\b\s*[:\-]?\s*((?:19[5-9]|20[0-3])\d)\b", text)
    if match:
        return int(match.group(1))
    # Otherwise: a year on the same line as a known make.
    for make in MAKES:
        line = re.search(rf"(?im)^.*\b{re.escape(make)}\b.*$", text)
        if line:
            year = re.search(r"\b((?:19[5-9]|20[0-3])\d)\b", line.group(0))
            if year:
                return int(year.group(1))
    return None


def parse_document_text(text: str, vin_plate_text: str = "") -> ExtractedFields:
    text = text or ""
    fields = ExtractedFields()
    # The VIN plate photo, when given, is the more trustworthy place for the VIN.
    fields.vin = find_vin(vin_plate_text or "") or find_vin(text)
    fields.declared_price, price_currency = _find_price(text)
    fields.currency = price_currency or _find_currency(text)
    fields.make, fields.model = _find_make_model(text)
    fields.year = _find_year(text)
    fields.seller = _labelled(text, SELLER_LABELS)
    fields.buyer = _labelled(text, BUYER_LABELS)
    mileage = re.search(rf"(?i)\b(?:mileage|odometer|odo)\b[^\d\n]{{0,15}}({_NUMBER})", text)
    if mileage:
        amount = parse_amount(mileage.group(1))
        fields.mileage = int(amount) if amount is not None else None
    incoterm = re.search(rf"\b({'|'.join(INCOTERMS)})\b", text)
    fields.incoterm = incoterm.group(1) if incoterm else None
    return fields
