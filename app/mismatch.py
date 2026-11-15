"""Compare what the paperwork says with what the VIN decodes to."""
import re


def _clean(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _same(a: str | None, b: str | None) -> bool:
    """Loose match: 'Mercedes-Benz' = 'MERCEDES BENZ', 'CR-V' = 'CRV EX'."""
    a, b = _clean(a), _clean(b)
    return bool(a and b) and (a == b or a in b or b in a)


def find_mismatches(doc: dict, decoded: dict) -> list[str]:
    """Return plain-language warnings. Blank fields are skipped, because the
    VIN database often has no model for vehicles not sold in the US."""
    warnings = []
    for name in ("make", "model"):
        d, v = doc.get(name), decoded.get(name)
        if d and v and not _same(d, v):
            warnings.append(f"{name.capitalize()} on paper is '{d}' but the VIN decodes to '{v}'.")
    d_year, v_year = doc.get("year"), decoded.get("year")
    if d_year and v_year and int(d_year) != int(v_year):
        warnings.append(
            f"Year on paper is {d_year} but the VIN decodes to model year {v_year}. "
            "(Model year can differ from build/registration year by one.)"
        )
    return warnings
