"""Online research: find a manufacturer's price the app does not have yet.

This is the last step of the lookup chain. The app always looks in its own
stored data first; only when the price is missing does it ask Claude to
search the web. Whatever is found is saved in the price book, together with
the web pages it came from, so the next lookup is answered locally.

It is OFF unless you set PRICE_RESEARCH=claude and ANTHROPIC_API_KEY (a paid
Anthropic account; "pip install anthropic"). Only the vehicle description
(year, make, model, trim, engine) is sent, never the VIN or any names.
"""
import json
import os
import re
import time
from dataclasses import dataclass

from . import config

MODEL = os.environ.get("PRICE_RESEARCH_MODEL", "claude-opus-5-5")
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
_not_found: dict[str, float] = {}   # vehicles searched recently without success

PROMPT = """Find the manufacturer's suggested retail price (MSRP) when new, in US \
dollars, for this vehicle:

{vehicle}

This is for a customs inspector checking a declared import value, so accuracy \
matters more than having an answer.
- Search the web. Prefer the manufacturer's own pricing or press pages, then \
long-established price guides and car magazines.
- Give the base MSRP for this exact trim and model year, without destination \
charges or options. If you can only find the price of the base model, give \
that and set "trim_matched" to false.
- If you cannot find a figure on a page you actually opened in this search, \
answer with "msrp": null. Never estimate or work from memory.

End your reply with one JSON object on its own line, exactly in this form:
{{"msrp": 31300, "trim_matched": true, "source_urls": ["https://..."], "note": "one short sentence"}}"""


@dataclass
class Found:
    msrp: float
    trim_matched: bool
    urls: list[str]
    note: str


def enabled() -> bool:
    return config.PRICE_RESEARCH() == "claude" and bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic  # imported here so the app runs without this package
    return anthropic.Anthropic(timeout=120.0, max_retries=1)


def _ask(prompt: str):
    """One web-search conversation. A long search can pause; then we resume it."""
    client = _client()
    messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(4):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # If the model declines, let the API retry on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            tools=[WEB_SEARCH],
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    return response


def _read_answer(response) -> Found | None:
    """Accept the answer only if it is a sensible price backed by pages that
    really came up in the search (so an invented link is never stored)."""
    if response is None or response.stop_reason in ("refusal", "pause_turn", "max_tokens"):
        return None
    text, searched = "", set()
    for block in response.content:
        if block.type == "text":
            text += block.text
        elif block.type == "web_search_tool_result" and isinstance(block.content, list):
            searched.update(item.url for item in block.content if getattr(item, "url", None))
    objects = re.findall(r"\{[^{}]*\}", text)
    if not objects:
        return None
    try:
        answer = json.loads(objects[-1])
        msrp = float(answer.get("msrp") or 0)
    except (ValueError, TypeError):
        return None
    urls = [u for u in answer.get("source_urls") or [] if isinstance(u, str) and u in searched][:3]
    if not 500 <= msrp <= 5_000_000 or not urls:
        return None
    return Found(msrp=msrp, trim_matched=bool(answer.get("trim_matched")), urls=urls,
                 note=str(answer.get("note") or "")[:200])


def find_msrp(*, year: int, make: str, model: str, trim: str = "", details: str = "") -> tuple[Found | None, str]:
    """Return (result, plain-language note about what happened)."""
    import anthropic

    vehicle = " ".join(str(part) for part in (year, make, model, trim) if part).strip()
    key = vehicle.lower()
    if time.time() - _not_found.get(key, 0) < 24 * 3600:
        return None, "An online search for this vehicle found no price in the last 24 hours."
    prompt = PROMPT.format(vehicle=vehicle + (f"\n({details})" if details else ""))
    try:
        found = _read_answer(_ask(prompt))
    except anthropic.AuthenticationError:
        return None, "Online search is not set up correctly (the API key was rejected)."
    except anthropic.RateLimitError:
        return None, "Online search is busy right now. Try again in a minute."
    except anthropic.APIStatusError as exc:
        return None, f"Online search failed (error {exc.status_code})."
    except anthropic.APIConnectionError:
        return None, "Online search could not be reached."
    if found is None:
        _not_found[key] = time.time()
        return None, "An online search found no reliable manufacturer's price for this vehicle."
    return found, "Found online."
