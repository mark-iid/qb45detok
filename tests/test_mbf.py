"""Tests for Microsoft Binary Format conversion.

The known encodings come from what BASIC stores for those numbers: an
exponent of 0x81 with an empty mantissa is 1.0, the sign lives in the top
mantissa bit, and a zero exponent is zero whatever else the bytes hold.
"""

import struct

import pytest

from qb45detok import mbf

#: (stored bytes, value) pairs that BASIC itself would produce.
KNOWN_SINGLES = [
    (b"\x00\x00\x00\x00", 0.0),
    (b"\x00\x00\x00\x81", 1.0),
    (b"\x00\x00\x80\x81", -1.0),
    (b"\x00\x00\x00\x82", 2.0),
    (b"\x00\x00\x80\x82", -2.0),
    (b"\x00\x00\x00\x80", 0.5),
    (b"\x00\x00\x20\x83", 5.0),
    (b"\x00\x00\x40\x83", 6.0),
]


@pytest.mark.parametrize("raw,value", KNOWN_SINGLES)
def test_known_single_encodings_decode(raw, value):
    assert mbf.single_to_float(raw) == value


@pytest.mark.parametrize("raw,value", KNOWN_SINGLES)
def test_known_single_encodings_re_encode(raw, value):
    assert mbf.float_to_single(value) == raw


def test_a_zero_exponent_is_zero_whatever_the_mantissa_holds():
    assert mbf.single_to_float(b"\xff\xff\xff\x00") == 0.0
    assert mbf.double_to_float(b"\xff" * 7 + b"\x00") == 0.0


@pytest.mark.parametrize("value", [
    1.0, -1.0, 0.5, 2.0, 255.0, 3.14159, 1.0 / 3.0, 1e10, -1e-10, 1e30, -1e30,
])
def test_singles_round_trip(value):
    stored = mbf.float_to_single(value)
    assert len(stored) == 4
    # BASIC would have rounded to single precision on the way in, so compare
    # against the same rounding rather than the original double.
    want = struct.unpack("<f", struct.pack("<f", value))[0]
    assert mbf.single_to_float(stored) == pytest.approx(want, rel=1e-6)


@pytest.mark.parametrize("value", [
    1.0, -1.0, 0.5, 3.141592653589793, 1.0 / 3.0, 1e30, -1e-30, 1e-38,
])
def test_doubles_round_trip(value):
    stored = mbf.float_to_double(value)
    assert len(stored) == 8
    assert mbf.double_to_float(stored) == pytest.approx(value, rel=1e-15)


def test_wrong_length_is_rejected():
    with pytest.raises(mbf.MBFError):
        mbf.single_to_float(b"\x00\x00\x00")
    with pytest.raises(mbf.MBFError):
        mbf.double_to_float(b"\x00" * 4)


def test_infinities_and_nans_are_rejected():
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(mbf.MBFError):
            mbf.float_to_single(bad)
        with pytest.raises(mbf.MBFError):
            mbf.float_to_double(bad)


def test_too_large_is_rejected_rather_than_wrapped():
    with pytest.raises(mbf.MBFError):
        mbf.float_to_single(1e39)
    with pytest.raises(mbf.MBFError):
        mbf.float_to_double(1e39)


def test_underflow_becomes_zero():
    # MBF has no subnormals, so anything below the smallest exponent is zero.
    assert mbf.float_to_single(1e-40) == b"\x00\x00\x00\x00"


def test_sign_lives_in_the_top_mantissa_bit():
    positive = mbf.float_to_single(3.0)
    negative = mbf.float_to_single(-3.0)
    assert positive[-1] == negative[-1]          # same exponent
    assert negative[-2] & 0x80 and not positive[-2] & 0x80
