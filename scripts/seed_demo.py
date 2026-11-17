"""Fill the app with made-up checks, for a demonstration.

    python scripts/seed_demo.py            # add about 160 checks over the last 60 days
    python scripts/seed_demo.py --clear    # remove every demo check again

The cars are 40 real VINs, decoded and priced by the app's own lists. Everything else is INVENTED: the declared prices, the sellers,
the buyers, the inspectors and the decisions. Demo checks are saved under
inspector names starting with "demo." so they can be found and removed.

It refuses to run against a PostgreSQL database unless you add --force, so a
real port's data cannot be mixed with demo data by accident.
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import db  # noqa: E402
from app.demo import clear, seed  # noqa: E402


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
