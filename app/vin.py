"""VIN validation.

A modern VIN (Vehicle Identification Number) has exactly 17 characters and
never contains the letters I, O or Q (they look too much like 1 and 0).

Position 9 is a "check digit": a number calculated from the other 16
characters. If someone mistypes or alters the VIN, the check digit usually
no longer matches. IMPORTANT: the check digit is only mandatory for vehicles
built for North America. Many European and Japanese VINs legitimately fail
it, so a mismatch is reported as a WARNING, never as an error.
"""
from dataclasses import dataclass, field

# Each letter is turned into a number using this fixed table (49 CFR 565.15).
TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
# Each of the 17 positions has a weight. Position 9 (the check digit) is 0.
WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

# Position 10 encodes the model year. The codes repeat every 30 years.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


@dataclass
class VinResult:
    vin: str                      # cleaned-up VIN (upper case, no spaces)
    is_standard: bool             # True if it is a well-formed 17-character VIN
    check_digit_ok: bool | None   # None when it cannot be calculated
    errors: list[str] = field(default_factory=list)    # must be fixed
    warnings: list[str] = field(default_factory=list)  # inspector should look
    possible_years: list[int] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)  # likely intended VINs

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "vin": self.vin, "is_standard": self.is_standard,
            "check_digit_ok": self.check_digit_ok, "ok": self.ok,
            "errors": self.errors, "warnings": self.warnings,
            "possible_years": self.possible_years,
            "suggestions": self.suggestions,
        }


def normalize_vin(raw: str) -> str:
    """Upper-case the VIN and remove spaces, dashes and dots."""
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def compute_check_digit(vin: str) -> str:
    """Return the check digit ('0'-'9' or 'X') a 17-character VIN should have."""
    total = 0
    for char, weight in zip(vin, WEIGHTS):
        value = int(char) if char.isdigit() else TRANSLITERATION[char]
        total += value * weight
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def possible_model_years(vin: str, latest_year: int = 2027) -> list[int]:
    """Model years the 10th character can stand for (codes repeat every 30 years)."""
    code = vin[9]
    if code not in _YEAR_CODES:
        return []
    first = 1980 + _YEAR_CODES.index(code)
    return [y for y in (first, first + 30, first + 60) if y <= latest_year]


# Characters that cameras and people often mix up.
_LOOKALIKES = {"S": "5", "5": "S", "B": "8", "8": "B", "Z": "2", "2": "Z",
               "G": "6", "6": "G", "D": "0", "0": "D"}


def suggest_corrections(vin: str, limit: int = 3) -> list[str]:
    """Suggest VINs one look-alike character away whose check digit IS correct.

    These are only offered to the inspector as "did you mean?"; the VIN is
    never changed automatically, because a failing check digit can be genuine.
    """
    found = []
    for i, char in enumerate(vin):
        swap = _LOOKALIKES.get(char)
        if not swap:
            continue
        candidate = vin[:i] + swap + vin[i + 1:]
        if candidate[8] == compute_check_digit(candidate):
            found.append(candidate)
    return found[:limit]


def validate_vin(raw: str) -> VinResult:
    vin = normalize_vin(raw)
    result = VinResult(vin=vin, is_standard=False, check_digit_ok=None)

    if not vin:
        result.errors.append("VIN is empty.")
        return result

    bad_letters = sorted({ch for ch in vin if ch in "IOQ"})
    if len(vin) != 17:
        # Pre-1981 vehicles and Japanese-market chassis numbers are shorter.
        result.warnings.append(
            f"This has {len(vin)} characters, not 17. It may be a pre-1981 VIN "
            "or a chassis number (e.g. Japanese domestic market). It cannot be "
            "checked or decoded automatically, so verify it by hand."
        )
        return result
    if bad_letters:
        result.errors.append(
            f"A VIN never contains the letters I, O or Q (found: {', '.join(bad_letters)}). "
            "They are often misread 1 and 0."
        )
        fixed = vin.translate(str.maketrans("IOQ", "100"))
        if fixed[8] == compute_check_digit(fixed):
            result.suggestions.append(fixed)
        return result

    result.is_standard = True
    expected = compute_check_digit(vin)
    result.check_digit_ok = vin[8] == expected
    if not result.check_digit_ok:
        result.warnings.append(
            f"Check digit mismatch: position 9 is '{vin[8]}' but the calculation "
            f"gives '{expected}'. This is normal for many vehicles not built for "
            "North America, but it can also mean a typo. Compare with the VIN plate."
        )
        result.suggestions = suggest_corrections(vin)
    result.possible_years = possible_model_years(vin)
    return result
