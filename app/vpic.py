"""VIN decoding.

By default the app decodes VINs with its OWN vehicle database (see
offline_vin.py), so it needs no internet and depends on nobody. It can also
ask the free NHTSA vPIC service; that is chosen with the VIN_DECODER setting.
Online answers are cached so the same VIN is only looked up once.
"""
import json

import httpx

from . import config, db, offline_vin

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"


_ACRONYMS = {"Bmw": "BMW", "Gmc": "GMC", "Byd": "BYD", "Mg": "MG", "Mini": "MINI"}


def _tidy_make(make: str) -> str | None:
    """vPIC sends makes in capitals ('HONDA'); show 'Honda' but keep 'BMW'."""
    make = make.strip().title()
    return _ACRONYMS.get(make, make) or None


def _engine(row: dict) -> str | None:
    """'3.5L 6-cyl, 268 hp' from the separate vPIC engine fields."""
    parts = []
    litres = (row.get("DisplacementL") or "").strip()
    if litres.replace(".", "", 1).isdigit():
        parts.append(f"{float(litres):.1f}L")
    if (row.get("EngineCylinders") or "").strip():
        parts.append(f"{row['EngineCylinders'].strip()}-cyl")
    text = " ".join(parts)
    hp = (row.get("EngineHP") or "").strip()
    if hp.replace(".", "", 1).isdigit():
        text = f"{text}, {float(hp):.0f} hp".strip(", ")
    return text or None


# What the factory built, as recorded in the VIN. Label -> value.
def _specs(row: dict) -> dict:
    get = lambda key: (row.get(key) or "").strip() or None  # noqa: E731
    specs = {
        "Trim": get("Trim"), "Series": get("Series"), "Engine": _engine(row),
        "Fuel": get("FuelTypePrimary"), "Drive": get("DriveType"),
        "Body": get("BodyClass"), "Doors": get("Doors"),
        "Transmission": get("TransmissionStyle"),
    }
    return {label: value for label, value in specs.items() if value}


def parse_vpic_response(payload: dict) -> dict:
    """Pick the few fields we need out of vPIC's ~150 fields."""
    row = (payload.get("Results") or [{}])[0]
    year = (row.get("ModelYear") or "").strip()
    price = (row.get("BasePrice") or "").strip()
    codes = [c.strip() for c in (row.get("ErrorCode") or "").split(",") if c.strip()]
    return {
        "available": True,
        "make": _tidy_make(row.get("Make") or ""),
        "model": (row.get("Model") or "").strip() or None,
        "year": int(year) if year.isdigit() else None,
        "manufacturer": (row.get("Manufacturer") or "").strip() or None,
        "body_class": (row.get("BodyClass") or "").strip() or None,
        "trim": (row.get("Trim") or "").strip() or None,
        "series": (row.get("Series") or "").strip() or None,
        "specs": _specs(row),
        "country": (row.get("PlantCountry") or "").strip().title() or None,
        # Manufacturer's base price when new. NHTSA only has it for a few vehicles.
        "base_price": float(price) if price.replace(".", "", 1).isdigit() and float(price) > 0 else None,
        "error_codes": codes,
        # vPIC reports problems inside the body, not via HTTP status.
        "clean": codes in ([], ["0"]),
        "message": (row.get("ErrorText") or "").strip() or None,
    }


def decode_offline(vin: str) -> dict:
    decoded = parse_vpic_response({"Results": [offline_vin.decode(vin)]})
    decoded["source"] = f"The app's own vehicle database (NHTSA data of {offline_vin.data_date()})"
    decoded["versions"] = list(offline_vin.model_versions(vin))
    return decoded


def decode_online(vin: str, client: httpx.Client | None = None) -> dict:
    cached = db.get_cached_vin(vin)
    if cached:
        return json.loads(cached)
    try:
        own = client is None
        client = client or httpx.Client(timeout=10)
        try:
            response = client.get(VPIC_URL.format(vin=vin))
            response.raise_for_status()
            decoded = parse_vpic_response(response.json())
        finally:
            if own:
                client.close()
    except (httpx.HTTPError, ValueError):
        return {
            "available": False, "make": None, "model": None, "year": None,
            "message": "The VIN decoding service could not be reached. "
                       "You can continue and check the vehicle details by hand.",
        }
    decoded["source"] = "NHTSA online service"
    db.cache_vin(vin, json.dumps(decoded))
    return decoded


def decode_vin(vin: str, client: httpx.Client | None = None) -> dict:
    mode = config.VIN_DECODER()
    if mode == "online" or not offline_vin.available():
        return decode_online(vin, client)
    decoded = decode_offline(vin)
    if mode == "both" and not decoded["model"]:
        online = decode_online(vin, client)
        if online.get("available") and online.get("model"):
            return online
    return decoded


def decoder_name() -> str:
    if config.VIN_DECODER() == "online" or not offline_vin.available():
        return "NHTSA online service"
    extra = ", then NHTSA online" if config.VIN_DECODER() == "both" else ""
    return f"Own vehicle database, no internet needed (NHTSA data of {offline_vin.data_date()}){extra}"
