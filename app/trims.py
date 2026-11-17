"""Match the trim a VIN decodes to against the trim names in a price list.

The same trim is written differently everywhere. A VIN says 'SXP, X-LINE SXP'
(two trims share that VIN pattern) while a price list says 'SX-Prestige AWD'
and 'X-Line SX-Prestige AWD'. Rules:

  - A VIN trim may list alternatives, separated by commas or slashes. Each is
    matched on its own and the results are combined.
  - Every word of the VIN trim must appear in the listed name. A word also
    matches two neighbouring words joined ('EXL' = 'EX-L') or the first joined
    with the next one's initial ('SXP' = 'SX-Prestige').
  - Among the names that match, the ones with the fewest other trim words
    win: 'EX' picks 'EX', not 'EX-L'.
  - Words that say nothing about the trim (drive type, body, cab, gearbox,
    plain numbers) are not counted.
"""
import re

_NOT_TRIM = {
    "awd", "fwd", "rwd", "4wd", "2wd", "4x4", "4x2", "4matic", "xdrive", "quattro",
    "door", "dr", "sedan", "coupe", "wagon", "hatchback", "convertible", "suv", "van", "pickup",
    "cab", "reg", "regular", "crew", "double", "quad", "extended", "ext", "supercab", "supercrew",
    "box", "bed", "wb", "short", "long", "standard",
    "automatic", "auto", "manual", "cvt", "at", "mt", "spd", "speed",
}


def _tokens(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _counts(word: str) -> bool:
    return word not in _NOT_TRIM and not word.isdigit()


def alternatives(*vin_trims: str | None) -> list[list[str]]:
    """The trims a VIN may be, each as its list of words."""
    found = []
    for text in vin_trims:
        for part in re.split(r"[,/;]", text or ""):
            words = [w for w in _tokens(part) if _counts(w)]
            if words and words not in found:
                found.append(words)
    return found


def _extra_words(wanted: list[str], name_words: list[str]) -> int | None:
    """How many other trim words the name has, or None if it lacks a wanted word."""
    used = set()
    for word in wanted:
        for i, here in enumerate(name_words):
            following = name_words[i + 1] if i + 1 < len(name_words) else ""
            if here == word:
                used.add(i)
                break
            if following and word in (here + following, here + following[0]):
                used.update((i, i + 1))
                break
        else:
            return None
    return sum(1 for i, w in enumerate(name_words) if i not in used and _counts(w))


def match(names: list[str], *vin_trims: str | None, ignore: str = "") -> list[int]:
    """Positions in `names` of the trims this VIN can be. Empty when the VIN
    has no trim or nothing matches. `ignore` is the model name, which price
    lists often repeat in front of the trim."""
    skip = set(_tokens(ignore))
    listed = [[w for w in _tokens(name) if w not in skip] for name in names]
    chosen: set[int] = set()
    for wanted in alternatives(*vin_trims):
        wanted = [w for w in wanted if w not in skip]
        scores = {i: _extra_words(wanted, words) for i, words in enumerate(listed)} if wanted else {}
        scores = {i: s for i, s in scores.items() if s is not None}
        if scores:
            best = min(scores.values())
            chosen.update(i for i, s in scores.items() if s == best)
    return sorted(chosen)
