"""Tests with REAL vehicle VINs.

tests/data/real_vins.json holds 40 real VINs (34 makes, model years 1993-2015)
sampled from the open "vin-test-data" dataset (MIT licence,
https://github.com/sen4ik/vin-test-data). Each was decoded once with the live
NHTSA vPIC service and the answer was saved next to it, so these tests run
without internet and always give the same result.

To also call the live NHTSA service, run:  RUN_LIVE_VPIC=1 pytest
"""
import json
import os
from pathlib import Path

import pytest

from app import db, vpic
from app.mismatch import find_mismatches
from app.readers.parser import find_vin
from app.vin import possible_model_years, validate_vin
from app.vpic import parse_vpic_response

REAL = json.loads((Path(__file__).parent / "data" / "real_vins.json").read_text())
IDS = [f"{v['year']}-{v['make']}-{v['model']}".replace(" ", "_") for v in REAL]


def test_sample_is_varied():
    assert len(REAL) == 40
    assert len({v["make"] for v in REAL}) >= 30
    assert min(v["year"] for v in REAL) < 1995 and max(v["year"] for v in REAL) >= 2015


@pytest.mark.parametrize("vehicle", REAL, ids=IDS)
def test_real_vin_passes_validation(vehicle):
    result = validate_vin(vehicle["vin"])
    assert result.ok and result.is_standard and result.check_digit_ok
    assert result.warnings == [] and result.suggestions == []
    assert vehicle["year"] in possible_model_years(vehicle["vin"])


@pytest.mark.parametrize("vehicle", REAL, ids=IDS)
def test_real_nhtsa_answer_is_understood(vehicle):
    decoded = parse_vpic_response({"Results": [vehicle["vpic"]]})
    assert decoded["clean"] and decoded["year"] == vehicle["year"]
    assert decoded["make"].lower() == vehicle["make"].lower()
    assert decoded["model"] == vehicle["model"]
    # Paperwork that matches the car must not raise a mismatch warning...
    paper = {"make": vehicle["make"], "model": vehicle["model"], "year": vehicle["year"]}
    assert find_mismatches(paper, decoded) == []
    # ...and paperwork for a different car must.
    wrong = {"make": "Zastava", "model": "Yugo", "year": vehicle["year"] + 3}
    assert len(find_mismatches(wrong, decoded)) == 3


@pytest.mark.parametrize("vehicle", REAL, ids=IDS)
def test_real_vin_with_one_wrong_character_is_caught(vehicle):
    vin = vehicle["vin"]
    # Change the last character: the check digit must no longer match.
    altered = vin[:-1] + ("1" if vin[-1] != "1" else "2")
    assert validate_vin(altered).check_digit_ok is False


@pytest.mark.parametrize("vehicle", REAL, ids=IDS)
def test_real_vin_is_found_in_invoice_text(vehicle):
    text = f"Vehicle: {vehicle['year']} {vehicle['make']} {vehicle['model']}\nVIN: {vehicle['vin']}\nTotal: USD 9,500.00"
    assert find_vin(text) == vehicle["vin"]


def test_make_names_are_tidied():
    by_vin = {v["vin"]: parse_vpic_response({"Results": [v["vpic"]]})["make"] for v in REAL}
    assert by_vin["WBXPC93458WJ12426"] == "BMW"
    assert by_vin["2GKFLZE39D6253778"] == "GMC"
    assert by_vin["WDDPK5HA1CF016122"] == "Mercedes-Benz"


@pytest.mark.skipif(os.environ.get("RUN_LIVE_VPIC") != "1", reason="set RUN_LIVE_VPIC=1 to call the real NHTSA service")
@pytest.mark.parametrize("vehicle", REAL[:5], ids=IDS[:5])
def test_live_nhtsa_decode(vehicle):
    db.init()
    decoded = vpic.decode_online(vehicle["vin"])
    assert decoded["available"], decoded.get("message")
    assert decoded["year"] == vehicle["year"]
    assert decoded["make"].lower() == vehicle["make"].lower()
    assert decoded["model"] == vehicle["model"]


# ---------- vehicle identity: catching a car presented as a different trim ----------
BY_VIN = {v["vin"]: parse_vpic_response({"Results": [v["vpic"]]}) for v in REAL}


def test_factory_build_details_come_from_the_vin():
    denali = BY_VIN["2GKFLZE39D6253778"]            # real 2013 GMC Terrain Denali
    assert denali["trim"] == "Denali"
    assert denali["specs"]["Engine"] == "3.6L 6-cyl"
    assert denali["specs"]["Drive"] == "AWD/All-Wheel Drive"
    taurus = BY_VIN["1FAHP2FW5CG103485"]["specs"]   # real 2012 Ford Taurus Limited
    assert taurus["Trim"] == "Limited" and taurus["Engine"] == "3.5L 6-cyl, 263 hp"
    # Every real VIN gives at least the body or engine to compare with the car.
    assert all(d["specs"] for d in BY_VIN.values())


def test_trim_declared_lower_or_higher_than_built_is_flagged():
    from app.mismatch import trim_matches
    denali = BY_VIN["2GKFLZE39D6253778"]
    paper = {"make": "GMC", "model": "Terrain", "year": 2013}
    # Declared as the cheap trim, built as the expensive one.
    low = find_mismatches({**paper, "trim": "SLE"}, denali)
    assert len(low) == 1 and "Trim on paper is 'SLE'" in low[0] and "Denali" in low[0]
    # Honest paperwork, written in different styles, is not flagged.
    assert find_mismatches({**paper, "trim": "denali"}, denali) == []
    assert find_mismatches({**paper, "trim": ""}, denali) == []
    assert trim_matches("LS", "LS (&1LS)", None) is True          # real Chevrolet Malibu
    assert trim_matches("LT", "LS (&1LS)", None) is False
    assert trim_matches("SLK 350", "SLK350", None) is True        # real Mercedes SLK
    assert trim_matches("TDI", "SEL, TDI", None) is True          # real VW Jetta
    assert trim_matches("SE", "SEL, TDI", None) is False
    # A chassis code alone is not a trim, so nothing is claimed.
    assert trim_matches("LE", None, "ZZE130L") is None            # real Toyota Corolla
    # A series that is a real trim name is compared (own database stores Denali this way).
    assert trim_matches("SLE", None, "Denali AWD") is False
    assert trim_matches("Denali", None, "Denali AWD") is True


# ---------- the app's own vehicle database (no internet) ----------
@pytest.mark.parametrize("vehicle", REAL, ids=IDS)
def test_own_database_decodes_real_vin(vehicle):
    decoded = vpic.decode_offline(vehicle["vin"])
    online = parse_vpic_response({"Results": [vehicle["vpic"]]})
    assert decoded["clean"] and decoded["available"]
    assert decoded["year"] == vehicle["year"]
    assert decoded["make"].lower() == vehicle["make"].lower()
    assert decoded["model"] == vehicle["model"]
    assert "own vehicle database" in decoded["source"]
    # Whenever the live service knows a trim or series, our own copy has one too.
    if online["trim"] or online["series"]:
        assert decoded["trim"] or decoded["series"]
    assert decoded["specs"]


def test_own_database_is_the_default_and_needs_no_internet(monkeypatch):
    def no_internet(*args, **kwargs):
        raise AssertionError("the app tried to use the internet")
    monkeypatch.setattr(vpic.httpx, "Client", no_internet)
    monkeypatch.delenv("VIN_DECODER", raising=False)
    denali = vpic.decode_vin("2GKFLZE39D6253778")
    assert (denali["year"], denali["make"], denali["model"]) == (2013, "GMC", "Terrain")
    assert "Denali" in (denali["trim"] or denali["series"])
    assert find_mismatches({"make": "GMC", "model": "Terrain", "year": 2013, "trim": "SLE"}, denali)
    assert find_mismatches({"make": "GMC", "model": "Terrain", "year": 2013, "trim": "Denali"}, denali) == []


def test_own_database_handles_unknown_and_altered_vins():
    unknown = vpic.decode_offline("ZZZ11111111111111")          # no such manufacturer
    assert unknown["model"] is None and not unknown["clean"]
    altered = vpic.decode_offline("1HGCM82633A004353")          # wrong check digit
    assert altered["make"] == "Honda" and "1" in altered["error_codes"]
