"""Compare what the paperwork says with what the VIN decodes to."""
import re


def _clean(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _same(a: str | None, b: str | None) -> bool:
    """Loose match: 'Mercedes-Benz' = 'MERCEDES BENZ', 'CR-V' = 'CRV EX'."""
    a, b = _clean(a), _clean(b)
    return bool(a and b) and (a == b or a in b or b in a)


def _tokens(text: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 2}


def trim_matches(doc_trim: str | None, decoded_trim: str | None, decoded_series: str | None) -> bool | None:
    """True/False, or None when there is nothing to compare.

    Trim names are written many ways ('SEL, TDI', 'LS (&1LS)'), so this passes
    when any word of the paper trim appears in the VIN's trim or series."""
    # A series alone is often a chassis code (e.g. 'ZZE130L'), not a trim name,
    # so only compare when the VIN gives an actual trim.
    if not (doc_trim or "").strip() or not decoded_trim:
        return None
    known = " ".join(filter(None, [decoded_trim, decoded_series]))
    if _tokens(doc_trim) & _tokens(known):
        return True
    doc = _clean(doc_trim)
    return len(doc) >= 3 and doc in _clean(known)  # 'SLK 350' matches 'SLK350'


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
    if trim_matches(doc.get("trim"), decoded.get("trim"), decoded.get("series")) is False:
        known = " / ".join(filter(None, [decoded.get("trim"), decoded.get("series")]))
        warnings.append(
            f"Trim on paper is '{doc['trim']}' but the VIN was built as '{known}'. "
            "Compare badges, engine and interior with the VIN details."
        )
    return warnings
