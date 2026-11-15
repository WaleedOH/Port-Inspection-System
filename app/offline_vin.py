"""Decode a VIN with the app's OWN vehicle database: no internet needed.

The database (app/vehicle_data/vehicles.db.gz) is a copy of NHTSA's public
vPIC data, built by scripts/build_vehicle_db.py. It works like this:

  1. Characters 1-3 of a VIN (the "WMI") identify the manufacturer.
  2. Character 10 gives the model year.
  3. For that manufacturer and year, the database holds PATTERNS over
     characters 4-8 and 10-17. Each matching pattern contributes one fact,
     for example "CM826" -> Model: Accord, Trim: EX-V6.
"""
import gzip
import re
import shutil
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from . import config
from .vin import compute_check_digit, possible_model_years

PACKED = Path(__file__).parent / "vehicle_data" / "vehicles.db.gz"
_lock = threading.Lock()
_connection: sqlite3.Connection | None = None


def available() -> bool:
    return PACKED.exists()


def _db() -> sqlite3.Connection:
    """Unpack the database on first use and keep one read-only connection."""
    global _connection
    with _lock:
        if _connection is None:
            target = config.data_dir() / "vehicles.db"
            if not target.exists() or target.stat().st_mtime < PACKED.stat().st_mtime:
                partial = target.with_suffix(".part")
                with gzip.open(PACKED, "rb") as f_in, open(partial, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                partial.replace(target)
            _connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, check_same_thread=False)
            _connection.row_factory = sqlite3.Row
        return _connection


def data_date() -> str | None:
    row = _db().execute("SELECT value FROM info WHERE key = 'data_date'").fetchone()
    return row["value"] if row else None


@lru_cache(maxsize=20000)
def _matcher(keys: str) -> tuple[re.Pattern, int]:
    """Turn a vPIC key such as 'CM82[67]' or '*****|*U' into a matcher.
    '*' = any character, '#' = any digit, '[..]' = one of a set, '|' = skip
    the check digit. Also returns how specific the key is (for tie-breaks)."""
    out, specific, i = [], 0, 0
    while i < len(keys):
        ch = keys[i]
        if ch == "*":
            out.append(".")
        elif ch == "#":
            out.append(r"\d")
            specific += 1
        elif ch == "[":
            end = keys.find("]", i)
            if end == -1:
                out.append(re.escape(keys[i:]))
                break
            out.append(keys[i:end + 1])
            specific += 1
            i = end
        else:
            out.append(re.escape(ch))
            specific += 0 if ch == "|" else 2
        i += 1
    try:
        return re.compile("".join(out)), specific
    except re.error:
        return re.compile(r"(?!)"), 0   # a malformed key never matches


def _candidate_years(vin: str) -> list[int]:
    """Character 10 repeats every 30 years. For cars and light trucks,
    character 7 tells the cycles apart: a digit means 1980-2009, a letter 2010+."""
    years = possible_model_years(vin, latest_year=2039)
    preferred = [y for y in years if (y < 2010) == vin[6].isdigit()]
    return preferred + [y for y in years if y not in preferred]


def _wmi_rows(vin: str):
    db = _db()
    rows = []
    if vin[2] == "9":   # small manufacturers: the WMI continues in characters 12-14
        rows = db.execute("SELECT * FROM wmi WHERE wmi = ?", (vin[:3] + vin[11:14],)).fetchall()
    return rows or db.execute("SELECT * FROM wmi WHERE wmi = ?", (vin[:3],)).fetchall()


def decode(vin: str) -> dict:
    """Return a row shaped like the NHTSA API's answer, so the rest of the
    app treats offline and online decoding the same way."""
    row = {"Make": "", "Model": "", "ModelYear": "", "ErrorCode": "", "ErrorText": ""}
    wmis = _wmi_rows(vin)
    check_ok = vin[8] == compute_check_digit(vin)
    if not wmis:
        row.update(ErrorCode="7", ErrorText="Manufacturer is not in the vehicle database")
        return row
    wmi_ids = [w["id"] for w in wmis]
    marks = ",".join("?" for _ in wmi_ids)
    target = vin[3:8] + "|" + vin[9:17]
    db = _db()

    facts: dict[str, tuple] = {}
    year = None
    for candidate in _candidate_years(vin):
        # A schema that covers fewer years is more specific, so it wins ties.
        schemas = {r["schema_id"]: (r["year_to"] or 2100) - r["year_from"] for r in db.execute(
            f"SELECT schema_id, year_from, year_to FROM wmi_schema WHERE wmi_id IN ({marks}) "
            "AND year_from <= ? AND (year_to IS NULL OR year_to >= ?)", (*wmi_ids, candidate, candidate))}
        found: dict[str, tuple] = {}
        if schemas:
            schema_marks = ",".join("?" for _ in schemas)
            for p in db.execute(f"SELECT schema_id, keys, field, value, make FROM pattern "
                                f"WHERE schema_id IN ({schema_marks})", list(schemas)):
                regex, specific = _matcher(p["keys"])
                rank = (specific, -schemas[p["schema_id"]])
                if regex.match(target) and rank >= found.get(p["field"], ((-1, 0),))[0]:
                    found[p["field"]] = (rank, p["value"], p["make"])
        if year is None or ("Model" in found and "Model" not in facts):
            year, facts = candidate, found
        if "Model" in facts:
            break

    for field, (_, value, _) in facts.items():
        row[field] = value
    # Fill gaps from the engine model, then work out litres from cc or cubic inches.
    if row.get("EngineModel"):
        for extra in db.execute("SELECT field, value FROM engine_model WHERE name = ?", (row["EngineModel"],)):
            row.setdefault(extra["field"], extra["value"])
    try:
        if not row.get("DisplacementL") and row.get("DisplacementCC"):
            row["DisplacementL"] = str(float(row["DisplacementCC"]) / 1000)
        elif not row.get("DisplacementL") and row.get("DisplacementCI"):
            row["DisplacementL"] = str(float(row["DisplacementCI"]) * 0.016387064)
    except ValueError:
        pass
    row["ModelYear"] = str(year) if year else ""
    model_make = facts.get("Model", ((0, 0), "", None))[2]
    row["Make"] = row.get("Make") or model_make or wmis[0]["make"] or ""
    row["Manufacturer"] = wmis[0]["manufacturer"] or ""
    row["VehicleType"] = wmis[0]["vehicle_type"] or ""
    row.setdefault("PlantCountry", "")
    codes, texts = [], []
    if not check_ok:
        codes.append("1")
        texts.append("Check digit does not calculate properly")
    if "Model" not in facts:
        codes.append("8")
        texts.append("No detailed data available for this VIN in the vehicle database")
    row["ErrorCode"] = ",".join(codes) or "0"
    row["ErrorText"] = "; ".join(texts) or "VIN decoded from the app's own vehicle database"
    return row
