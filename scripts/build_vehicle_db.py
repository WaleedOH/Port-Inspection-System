"""Build the app's own offline vehicle database from NHTSA's vPIC data.

NHTSA publishes its whole VIN database (public US government data). This
script takes a SQLite copy of it and keeps only what the app needs, so the
app can decode VINs by itself with no internet connection.

How to refresh the data (a few times a year is plenty):
  1. Get a recent "vpic.lite.db" (for example from the open-source
     @cardog/corgi package: node_modules/@cardog/corgi/dist/db/vpic.lite.db.gz).
  2. Run:  python scripts/build_vehicle_db.py path/to/vpic.lite.db
  3. Commit the new app/vehicle_data/vehicles.db.gz.
"""
import gzip
import shutil
import sqlite3
import sys
from pathlib import Path

OUT = Path(__file__).parent.parent / "app" / "vehicle_data" / "vehicles.db"

# vPIC element id -> the field name the app uses (same names as the vPIC API).
ELEMENTS = {26: "Make", 28: "Model", 38: "Trim", 34: "Series", 5: "BodyClass",
            13: "DisplacementL", 9: "EngineCylinders", 71: "EngineHP",
            24: "FuelTypePrimary", 15: "DriveType", 14: "Doors",
            37: "TransmissionStyle", 75: "PlantCountry", 136: "BasePrice",
            11: "DisplacementCC", 12: "DisplacementCI", 18: "EngineModel"}


def build(source: str) -> None:
    src = sqlite3.connect(source)
    OUT.unlink(missing_ok=True)
    out = sqlite3.connect(OUT)
    out.executescript("""
        CREATE TABLE wmi (id INTEGER PRIMARY KEY, wmi TEXT NOT NULL, make TEXT, manufacturer TEXT,
                          vehicle_type TEXT, country TEXT);
        CREATE INDEX idx_wmi ON wmi(wmi);
        CREATE TABLE wmi_schema (wmi_id INTEGER NOT NULL, schema_id INTEGER NOT NULL,
                                 year_from INTEGER NOT NULL, year_to INTEGER);
        CREATE INDEX idx_wmi_schema ON wmi_schema(wmi_id);
        CREATE TABLE pattern (schema_id INTEGER NOT NULL, keys TEXT NOT NULL, field TEXT NOT NULL,
                              value TEXT NOT NULL, make TEXT);
        CREATE INDEX idx_pattern ON pattern(schema_id);
        CREATE TABLE engine_model (name TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL);
        CREATE INDEX idx_engine_model ON engine_model(name);
        CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
    """)
    out.executemany("INSERT INTO wmi VALUES (?,?,?,?,?,?)", src.execute("""
        SELECT w.Id, w.Wmi, mk.Name, mf.Name, vt.Name, c.Name FROM Wmi w
        LEFT JOIN Make mk ON mk.Id = COALESCE(w.MakeId, (SELECT MIN(MakeId) FROM Wmi_Make WHERE WmiId = w.Id))
        LEFT JOIN Manufacturer mf ON mf.Id = w.ManufacturerId
        LEFT JOIN VehicleType vt ON vt.Id = w.VehicleTypeId
        LEFT JOIN Country c ON c.Id = w.CountryId"""))
    out.executemany("INSERT INTO wmi_schema VALUES (?,?,?,?)",
                    src.execute("SELECT WmiId, VinSchemaId, YearFrom, YearTo FROM Wmi_VinSchema"))

    # Turn id references into readable names once, here, so the app doesn't have to.
    lookups = {eid: table for eid, table in src.execute(
        "SELECT Id, LookupTable FROM Element WHERE LookupTable IS NOT NULL") if eid in ELEMENTS}
    names = {eid: dict(src.execute(f"SELECT CAST(Id AS TEXT), Name FROM [{table}]"))
             for eid, table in lookups.items()}
    model_make = dict(src.execute(
        "SELECT CAST(mm.ModelId AS TEXT), mk.Name FROM Make_Model mm JOIN Make mk ON mk.Id = mm.MakeId"))
    rows = []
    marks = ",".join(str(e) for e in ELEMENTS)
    for schema, keys, element, attribute in src.execute(
            f"SELECT VinSchemaId, Keys, ElementId, AttributeId FROM Pattern WHERE ElementId IN ({marks})"):
        value = names[element].get(attribute) if element in names else attribute
        if value is None or not str(value).strip():
            continue
        rows.append((schema, keys, ELEMENTS[element], str(value).strip(),
                     model_make.get(attribute) if element == 28 else None))
    out.executemany("INSERT INTO pattern VALUES (?,?,?,?,?)", rows)
    # Facts that follow from the engine model (e.g. 'D16Y8' -> 4 cylinders, gasoline).
    engine_rows = []
    for name, element, attribute in src.execute(
            f"SELECT em.Name, p.ElementId, p.AttributeId FROM EngineModelPattern p "
            f"JOIN EngineModel em ON em.Id = p.EngineModelId WHERE p.ElementId IN ({marks})"):
        value = names[element].get(attribute) if element in names else attribute
        if value is not None and str(value).strip():
            engine_rows.append((name.strip(), ELEMENTS[element], str(value).strip()))
    out.executemany("INSERT INTO engine_model VALUES (?,?,?)", engine_rows)
    newest = src.execute("SELECT MAX(COALESCE(UpdatedOn, CreatedOn)) FROM Pattern").fetchone()[0]
    out.execute("INSERT INTO info VALUES ('source', 'NHTSA vPIC (public data)')")
    out.execute("INSERT INTO info VALUES ('data_date', ?)", ((newest or "")[:10],))
    out.commit()
    out.execute("VACUUM")
    out.close()
    with open(OUT, "rb") as f_in, gzip.open(f"{OUT}.gz", "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    size = OUT.stat().st_size
    OUT.unlink()
    print(f"{len(rows):,} patterns; {size / 1e6:.0f} MB, {Path(f'{OUT}.gz').stat().st_size / 1e6:.1f} MB compressed")


if __name__ == "__main__":
    build(sys.argv[1])
