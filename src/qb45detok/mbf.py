"""Convert between Microsoft Binary Format and IEEE 754.

MBF is what Microsoft BASIC used for floating point before it moved to IEEE,
so a random-access data file written by QuickBASIC 4.5 through ``MKSMBF$`` or
``MKDMBF$``, or by anything older, holds numbers in this format. The functions
here are the modern side of ``CVSMBF`` and ``CVDMBF``.

Both sizes share a layout. The first byte of the stored form is a biased
exponent, where zero means the value is zero whatever the rest holds. The top
bit of the mantissa is the sign, and the bit it displaces is an implicit 1,
the same trick IEEE uses. What differs is the bias and the split:

===========  ========  ========  ========
size         bytes     exponent  mantissa
===========  ========  ========  ========
single       4         8 bits    23 bits
double       8         8 bits    55 bits
===========  ========  ========  ========

MBF has no infinities and no NaNs, and its exponent bias is one larger than
the IEEE equivalent, so the same bit pattern is worth half as much.
"""

from __future__ import annotations

import struct

__all__ = ["single_to_float", "double_to_float",
           "float_to_single", "float_to_double",
           "MBFError"]

#: Exponent bias. An exponent of 0x81 with an all-implicit mantissa is 1.0,
#: which is one more than the IEEE bias for the same width.
_SINGLE_BIAS = 0x81
_DOUBLE_BIAS = 0x81

#: Largest magnitude each size can hold, used to reject values that would
#: silently wrap.
_SINGLE_MAX = 1.7014117331926443e38
_DOUBLE_MAX = 1.7014118346046923e38


class MBFError(ValueError):
    """Raised when a value cannot be represented in Microsoft Binary Format."""


def _to_float(data: bytes, mantissa_bits: int, bias: int) -> float:
    """Shared body: unpack a little-endian MBF number of either size."""
    size = (mantissa_bits + 9) // 8
    if len(data) != size:
        raise MBFError(f"expected {size} bytes, got {len(data)}")
    exponent = data[-1]
    if exponent == 0:
        # A zero exponent means zero, whatever the mantissa holds.
        return 0.0
    body = int.from_bytes(data[:-1], "little")
    sign = -1.0 if body >> (mantissa_bits - 1) else 1.0
    # Clear the sign bit and put back the 1 that it displaced.
    mantissa = (body & ((1 << (mantissa_bits - 1)) - 1)) | (1 << (mantissa_bits - 1))
    return sign * mantissa * 2.0 ** (exponent - bias - mantissa_bits + 1)


def _from_float(value: float, mantissa_bits: int, bias: int,
                limit: float) -> bytes:
    """Shared body: pack a float into a little-endian MBF number."""
    size = (mantissa_bits + 9) // 8
    if value != value or value in (float("inf"), float("-inf")):
        raise MBFError("Microsoft Binary Format has no infinities or NaNs")
    if value == 0.0:
        return bytes(size)
    if abs(value) > limit:
        raise MBFError(f"{value!r} is too large for {size}-byte MBF")
    negative = value < 0
    mantissa, exponent = _frexp_int(abs(value), mantissa_bits)
    exponent += bias
    if exponent <= 0:
        # Smaller than the format can hold; MBF has no subnormals.
        return bytes(size)
    if exponent > 0xFF:
        raise MBFError(f"{value!r} is too large for {size}-byte MBF")
    # The leading 1 is implicit, so its bit carries the sign instead.
    body = mantissa & ((1 << (mantissa_bits - 1)) - 1)
    if negative:
        body |= 1 << (mantissa_bits - 1)
    return body.to_bytes(size - 1, "little") + bytes([exponent])


def _frexp_int(value: float, mantissa_bits: int):
    """Split a positive float into an integer mantissa and an exponent."""
    import math

    fraction, exponent = math.frexp(value)
    mantissa = int(round(fraction * (1 << mantissa_bits)))
    if mantissa >> mantissa_bits:
        # Rounding carried into the next binade.
        mantissa >>= 1
        exponent += 1
    return mantissa, exponent - 1


def single_to_float(data: bytes) -> float:
    """A four-byte MBF single as a Python float."""
    return _to_float(data, 24, _SINGLE_BIAS)


def double_to_float(data: bytes) -> float:
    """An eight-byte MBF double as a Python float."""
    return _to_float(data, 56, _DOUBLE_BIAS)


def float_to_single(value: float) -> bytes:
    """A Python float as a four-byte MBF single."""
    if value != value or value in (float("inf"), float("-inf")):
        raise MBFError("Microsoft Binary Format has no infinities or NaNs")
    if abs(value) > _SINGLE_MAX:
        # Check before narrowing, since packing an out-of-range value as a
        # single raises before it can be reported properly.
        raise MBFError(f"{value!r} is too large for 4-byte MBF")
    # Round to single precision, so the result matches what BASIC would have
    # stored for the same number.
    value = struct.unpack("<f", struct.pack("<f", value))[0]
    return _from_float(value, 24, _SINGLE_BIAS, _SINGLE_MAX)


def float_to_double(value: float) -> bytes:
    """A Python float as an eight-byte MBF double."""
    return _from_float(value, 56, _DOUBLE_BIAS, _DOUBLE_MAX)
