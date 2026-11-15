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
    decoded = vpic.decode_vin(vehicle["vin"])
    assert decoded["available"], decoded.get("message")
    assert decoded["year"] == vehicle["year"]
    assert decoded["make"].lower() == vehicle["make"].lower()
    assert decoded["model"] == vehicle["model"]
