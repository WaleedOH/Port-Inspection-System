"""Made-up checks for a demonstration.

The cars are real VINs, decoded and priced by the app's own lists. Everything
else is INVENTED: the declared prices, the sellers, the buyers, the inspectors
and the decisions. Demo checks are saved under inspector names starting with
"demo." so they can be found and removed.

Two ways in:
  - python scripts/seed_demo.py           by hand, on your own machine
  - DEMO_DATA=1 in the environment        the app fills itself when it starts
    with no checks. Meant for a public demo site whose storage is erased
    regularly. It is ignored when a real database (DATABASE_URL) is connected.
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

from . import config, db, reference_prices, vpic
from .compare import compare_prices
from .pricing.mock import estimate_current_value

# Real VINs of ordinary US-market cars, the same ones the tests use.
VINS = [
    "1FAHP2FW5CG103485", "1G1ZG57B48F175084", "5N1BV28U45N126526", "2T1BR32EX7C819459",
    "1D7HU18N68S582396", "2HGEJ8649XH566483", "5NPEB4AC6CH361336", "WBXPC93458WJ12426",
    "2C4RC1CG9ER351466", "KNALN4D73F5170942", "WDDPK5HA1CF016122", "1C4NJRBB4ED771602",
    "JN1BY1AP3DM514252", "3VWLL7AJ5CM395442", "JTHCK262785022036", "2GKFLZE39D6253778",
    "4F2YZ02Z16KM21453", "1GYFK63817R141846", "JH4CL95846C019471", "3LNHM261X6R633550",
    "WAULC68E94A298150", "4S4BRBCC3E3279476", "1G4HD57247U198902", "4A32B3FF3CE024331",
    "1G2AL15F377415575", "YV1622FS9C2117877", "WMWRC33556TJ74027", "5GZCZ53B23S898198",
    "1C6RR7MT1ES422095", "SALMF134X9A304649", "3MEHM0HA9AR635061", "WP1AB29P36LA65473",
    "SAJWA1CB1CLV31447", "KL5JD56ZX6K326127", "1NXAE09E4PZ016415", "1FALP52U4RA229476",
    "1N4BU31D4RC136505", "JTHBA30G265161304", "1GC1KXC86DF240018", "JTHBC96S975001334",
]

INSPECTORS = ["demo.amira", "demo.tomas", "demo.keanu", "demo.ngozi"]
# (seller, share of its declarations that are well below value)
SELLERS = [("Harbor Line Auto Export", 0.10), ("Castellan Motors LLC", 0.15), ("Blue Quay Trading", 0.60),
           ("Northgate Vehicle Sales", 0.08), ("Mirador Auto Group", 0.45), ("Sable & Finch Exports", 0.12)]
BUYERS = ["Rahim Okafor", "Lucía Brandt", "Deniz Aksoy", "Mei-Ling Sato", "Tobias Achterberg",
          "Farah Idowu", "Anselm Varga", "Priya Venkataraman"]


def clear() -> int:
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM checks WHERE created_by LIKE 'demo.%'").fetchone()["n"]
        conn.execute("DELETE FROM checks WHERE created_by LIKE 'demo.%'")
        conn.execute("DELETE FROM audit_log WHERE username LIKE 'demo.%'")
    return n


def seed(count: int, days: int, rng: random.Random) -> int:
    settings = db.get_settings()
    green, yellow = settings["green_threshold"], settings["yellow_threshold"]
    now = datetime.now(timezone.utc)
    made = 0
    for _ in range(count):
        vin = rng.choice(VINS)
        decoded = vpic.decode_offline(vin)
        if not decoded.get("make") or not decoded.get("year"):
            continue
        listed = reference_prices.find(decoded["make"], decoded.get("model") or "", int(decoded["year"]),
                                       decoded.get("specs"))
        when = now - timedelta(days=rng.triangular(0, days, 0), hours=rng.uniform(0, 9))
        market = low = high = None
        if listed:
            market = estimate_current_value(listed.typical, int(decoded["year"]), None, when.date(),
                                            settings["depreciation"], False)
            low, high = round(market * 0.85, -2), round(market * 1.15, -2)
        seller, risky = rng.choice(SELLERS)
        # Most declarations sit near the expected value; some sellers come in well below.
        share = rng.uniform(0.45, 0.78) if rng.random() < risky else rng.gauss(0.99, 0.09)
        declared = round(max(500.0, (market or rng.uniform(4000, 22000)) * share), -1)
        result = compare_prices(declared, market, 1.0, green, yellow, "USD", settings["base_currency"])
        identity = rng.random() < 0.06
        needs_review = result.flag in ("YELLOW", "RED") or identity
        age_days = (now - when).days
        decision = "pending"
        if needs_review and age_days > 2 and rng.random() < 0.8:
            decision = "referred" if (result.flag == "RED" and rng.random() < 0.75) or rng.random() < 0.3 else "cleared"
        inspector = rng.choice(INSPECTORS)
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        check_id = db.insert_check({
            "created_at": stamp, "created_by": inspector, "vin": vin, "vin_standard": 1,
            "vin_check_digit_ok": 1, "vin_notes": "", "doc_make": decoded["make"],
            "doc_model": decoded.get("model") or "", "doc_year": decoded["year"],
            "decoded_make": decoded["make"], "decoded_model": decoded.get("model"), "decoded_year": decoded["year"],
            "mismatch_notes": "", "doc_trim": decoded.get("trim") or "", "decoded_trim": decoded.get("trim"),
            "decoded_specs": json.dumps(decoded.get("specs") or {}),
            "physical_check": "differs" if identity else "matches",
            "physical_details": json.dumps({"Trim": "differs"} if identity else {"Engine": "matches"}),
            "identity_review": int(identity), "seller": seller, "buyer": rng.choice(BUYERS),
            "mileage": None, "incoterm": rng.choice(["FOB", "CIF", ""]),
            "declared_price": declared, "currency": "USD", "exchange_rate": 1.0,
            "base_currency": settings["base_currency"], "declared_base": result.declared_base,
            "market_value": result.market_value, "market_low": low, "market_high": high,
            "market_source": ("Estimate from the manufacturer's price when new (" + listed.describe(int(decoded["year"]))
                              + ")")[:400] if listed and result.market_value else None,
            "diff_pct": result.diff_pct, "flag": result.flag, "flag_label": result.label,
            "explanation": "\n".join(result.explanation), "green_threshold": green, "yellow_threshold": yellow,
            "photo_file": None, "vin_photo_file": None, "notes": "Demo data, not a real declaration.",
            "decision": decision,
            "decision_by": "demo.supervisor" if decision != "pending" else None,
            "decision_at": (when + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") if decision != "pending" else None,
        })
        with db.connect() as conn:      # back-date the audit entry to match
            conn.execute("INSERT INTO audit_log (at, username, action, detail) VALUES (?,?,?,?)",
                         (stamp, inspector, "Saved a check", f"#{check_id} · VIN {vin} · {result.label}"))
        made += 1
    return made


def seed_if_asked() -> int:
    """Fill an empty demo site. Never touches a real database, never adds twice."""
    if os.environ.get("DEMO_DATA") != "1" or config.database_url():
        return 0
    with db.connect() as conn:
        if conn.execute("SELECT COUNT(*) AS n FROM checks").fetchone()["n"]:
            return 0
    try:
        return seed(160, 60, random.Random(7))
    except Exception as error:      # a demo must never stop the app from starting
        print(f"Demo data could not be added: {error}")
        return 0
