"""Fill the app with made-up checks, for a demonstration.

    python scripts/seed_demo.py            # add about 160 checks over the last 60 days
    python scripts/seed_demo.py --clear    # remove every demo check again

The cars are the 40 real VINs from the test data, decoded and priced by the
app's own lists. Everything else is INVENTED: the declared prices, the sellers,
the buyers, the inspectors and the decisions. Demo checks are saved under
inspector names starting with "demo." so they can be found and removed.

It refuses to run against a PostgreSQL database unless you add --force, so a
real port's data cannot be mixed with demo data by accident.
"""
import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import db, reference_prices, vpic  # noqa: E402
from app.compare import compare_prices  # noqa: E402
from app.pricing.mock import estimate_current_value  # noqa: E402

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
    cars = json.loads((Path(__file__).parent.parent / "tests" / "data" / "real_vins.json").read_text())
    now = datetime.now(timezone.utc)
    made = 0
    for _ in range(count):
        car = rng.choice(cars)
        decoded = vpic.decode_offline(car["vin"])
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
            "created_at": stamp, "created_by": inspector, "vin": car["vin"], "vin_standard": 1,
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
                         (stamp, inspector, "Saved a check", f"#{check_id} · VIN {car['vin']} · {result.label}"))
        made += 1
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=160)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--clear", action="store_true", help="remove the demo checks instead of adding any")
    parser.add_argument("--force", action="store_true", help="allow running against a PostgreSQL database")
    parser.add_argument("--seed", type=int, default=7, help="the same number gives the same demo data")
    args = parser.parse_args()
    if db.is_postgres() and not args.force:
        sys.exit("DATABASE_URL is set. Demo data does not belong in a real database; add --force if you are sure.")
    db.init()
    if args.clear:
        print(f"Removed {clear()} demo checks.")
        return
    print(f"Added {seed(args.count, args.days, random.Random(args.seed))} demo checks "
          f"(remove them with: python scripts/seed_demo.py --clear).")


if __name__ == "__main__":
    main()
