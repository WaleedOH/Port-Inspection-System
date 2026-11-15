import pytest

from app.vin import compute_check_digit, normalize_vin, possible_model_years, validate_vin

# Real-format VINs with correct check digits.
VALID_VINS = [
    "1HGCM82633A004352",  # Honda Accord 2003
    "1M8GDM9AXKP042788",  # check digit is the letter X
    "5YJSA1E27HF000337",  # Tesla Model S 2017
    "11111111111111111",  # classic test VIN
]


@pytest.mark.parametrize("vin", VALID_VINS)
def test_valid_vins_pass(vin):
    result = validate_vin(vin)
    assert result.ok and result.is_standard and result.check_digit_ok
    assert result.warnings == []


def test_check_digit_calculation():
    assert compute_check_digit("1HGCM82633A004352") == "3"
    assert compute_check_digit("1M8GDM9AXKP042788") == "X"


def test_normalize_removes_spaces_dashes_and_lowercase():
    assert normalize_vin(" 1hgcm-82633 a004352 ") == "1HGCM82633A004352"
    assert validate_vin("1hgcm82633a004352").check_digit_ok


def test_wrong_check_digit_is_a_warning_not_an_error():
    # One character changed. Non-North-American VINs fail this legitimately,
    # so the inspector is warned but can continue.
    result = validate_vin("1HGCM82633A004353")
    assert result.ok and result.is_standard
    assert result.check_digit_ok is False
    assert "Check digit mismatch" in result.warnings[0]


@pytest.mark.parametrize("vin", ["1HGCM82633A00435O", "IHGCM82633A004352", "1HGCM82633A0043Q2"])
def test_letters_i_o_q_are_errors(vin):
    result = validate_vin(vin)
    assert not result.ok
    assert "I, O or Q" in result.errors[0]


def test_empty_vin_is_an_error():
    assert not validate_vin("").ok
    assert not validate_vin("  - ").ok


@pytest.mark.parametrize("vin", ["JZX100-0012345", "1HGCM82633A00435", "1HGCM82633A0043522"])
def test_non_17_character_is_non_standard_with_warning(vin):
    # Chassis numbers and pre-1981 VINs exist; warn, don't block.
    result = validate_vin(vin)
    assert result.ok and not result.is_standard
    assert result.check_digit_ok is None
    assert "not 17" in result.warnings[0]


def test_model_year_from_tenth_character():
    assert possible_model_years("1HGCM82633A004352") == [2003]
    assert possible_model_years("1M8GDM9AXKP042788") == [1989, 2019]
    assert possible_model_years("5YJSA1E27HF000337") == [1987, 2017]
