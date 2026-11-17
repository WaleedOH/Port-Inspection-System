"""Figures for the supervisor's dashboard, worked out from the saved checks.

Everything here is a count or a sum of what inspectors recorded. The one
estimate is "duty at stake": for declarations that need a price review, the
gap between the expected value and the declared one, times the duty rate set
in Settings. It shows how much money rides on reviewing them. It is not a
finding: a low declared price can be legitimate.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days"}
FLAGS = ("GREEN", "YELLOW", "RED", "NO_DATA")
FLAG_NAMES = {"green": "within expected range", "yellow": "needs review", "red": "priority review",
              "no_data": "no market value"}
CHART_H = 50.0       # the chart is drawn in a 100 x 50 box and stretched to fit


def since(days: int, today: datetime | None = None) -> str:
    today = today or datetime.now(timezone.utc)
    return (today - timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00:00")


def _needs_review(row) -> bool:
    return row["flag"] in ("YELLOW", "RED") or bool(row["identity_review"])


def _top(counter: Counter, flagged: Counter, limit: int = 5) -> list[dict]:
    rows = [{"name": name, "total": total, "flagged": flagged.get(name, 0)}
            for name, total in counter.items() if flagged.get(name, 0)]
    rows.sort(key=lambda r: (-r["flagged"], -r["total"], r["name"]))
    return rows[:limit]


def build(rows, days: int, duty_rate: float, today: datetime | None = None) -> dict:
    """rows = db.checks_since(since(days)), oldest first."""
    today = today or datetime.now(timezone.utc)
    total = len(rows)
    flags = Counter(row["flag"] for row in rows)
    review = [row for row in rows if _needs_review(row)]
    decided = [row for row in review if row["decision"] != "pending"]
    referred = [row for row in review if row["decision"] == "referred"]

    # Money: the gap between expected and declared value on price-flagged checks.
    gap = sum(max(0.0, (row["market_value"] or 0) - (row["declared_base"] or 0))
              for row in rows if row["flag"] in ("YELLOW", "RED"))
    referred_gap = sum(max(0.0, (row["market_value"] or 0) - (row["declared_base"] or 0))
                       for row in referred if row["flag"] in ("YELLOW", "RED"))

    # One bar per day; for 90 days, one bar per week so the chart stays readable.
    step = 7 if days > 31 else 1
    start = (today - timedelta(days=days - 1)).date()
    buckets: dict[int, Counter] = defaultdict(Counter)
    for row in rows:
        day = datetime.strptime(row["created_at"][:10], "%Y-%m-%d").date()
        buckets[max(0, (day - start).days) // step][row["flag"]] += 1
    count = (days + step - 1) // step
    tallest = max([sum(buckets[i].values()) for i in range(count)] + [1])
    bars, width = [], 100 / count
    for i in range(count):
        first = start + timedelta(days=i * step)
        parts = [(flag.lower(), buckets[i][flag]) for flag in ("RED", "YELLOW", "GREEN", "NO_DATA")]
        # Stacked rectangles, bottom up: where each one sits in the chart box.
        rects, top = [], CHART_H - 0.5
        for flag, n in parts:
            if n:
                height = n / tallest * (CHART_H - 1.5)
                top -= height
                rects.append({"flag": flag, "count": n, "x": round(i * width + width * 0.14, 3),
                              "y": round(top, 3), "w": round(width * 0.72, 3), "h": round(height, 3)})
        bars.append({"label": first.strftime("%d %b"), "total": sum(buckets[i].values()),
                     "parts": parts, "rects": rects})

    makes, makes_flagged = Counter(), Counter()
    sellers, sellers_flagged = Counter(), Counter()
    people, people_flagged = Counter(), Counter()
    for row in rows:
        make = (row["decoded_make"] or row["doc_make"] or "").strip().title()
        seller = (row["seller"] or "").strip()
        for counter, flagged, name in ((makes, makes_flagged, make), (sellers, sellers_flagged, seller),
                                       (people, people_flagged, row["created_by"])):
            if name:
                counter[name] += 1
                if _needs_review(row):
                    flagged[name] += 1

    return {
        "days": days, "total": total, "tallest": tallest, "bars": bars, "weekly": step == 7,
        "flags": [(flag.lower(), flags[flag]) for flag in FLAGS],
        "review": len(review), "review_pct": round(100 * len(review) / total) if total else 0,
        "awaiting": len(review) - len(decided),
        "decided": len(decided), "referred": len(referred), "cleared": len(decided) - len(referred),
        "gap": gap, "duty_at_stake": gap * duty_rate / 100,
        "referred_duty": referred_gap * duty_rate / 100, "duty_rate": duty_rate,
        "identity": sum(1 for row in rows if row["identity_review"]),
        "makes": _top(makes, makes_flagged), "sellers": _top(sellers, sellers_flagged),
        "people": sorted(({"name": n, "total": t, "flagged": people_flagged.get(n, 0)} for n, t in people.items()),
                         key=lambda r: -r["total"])[:8],
    }
