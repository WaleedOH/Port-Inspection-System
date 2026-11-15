"""VIN decoding with the free NHTSA vPIC API (no account or key needed).

Results are cached in the database so the same VIN is only looked up once.
If the service is down or blocked, the app keeps working: the inspector
just doesn't get the automatic comparison.
"""
import json

import httpx

from . import db

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"


def parse_vpic_response(payload: dict) -> dict:
    """Pick the few fields we need out of vPIC's ~150 fields."""
    row = (payload.get("Results") or [{}])[0]
    year = (row.get("ModelYear") or "").strip()
    codes = [c.strip() for c in (row.get("ErrorCode") or "").split(",") if c.strip()]
    return {
        "available": True,
        "make": (row.get("Make") or "").strip().title() or None,
        "model": (row.get("Model") or "").strip() or None,
        "year": int(year) if year.isdigit() else None,
        "manufacturer": (row.get("Manufacturer") or "").strip() or None,
        "body_class": (row.get("BodyClass") or "").strip() or None,
        "country": (row.get("PlantCountry") or "").strip().title() or None,
        "error_codes": codes,
        # vPIC reports problems inside the body, not via HTTP status.
        "clean": codes in ([], ["0"]),
        "message": (row.get("ErrorText") or "").strip() or None,
    }


def decode_vin(vin: str, client: httpx.Client | None = None) -> dict:
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
    db.cache_vin(vin, json.dumps(decoded))
    return decoded
