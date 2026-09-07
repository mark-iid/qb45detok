"""Unit tests that stand on their own.

Everything here exercises the format rules directly rather than against the
sample programs, so it runs whether or not a corpus is present.
"""

import struct

import pytest

from qb45detok import BinFile, ParseError, tokens
from qb45detok.decode import parse_signature
from qb45detok.render import format_double, format_single


def test_rejects_a_non_qb_file():
    with pytest.raises(ParseError, match="not a QuickBASIC 4.5 binary file"):
        BinFile.parse(b"' just some ASCII BASIC\r\nPRINT 1\r\n")


def test_rejects_an_empty_file():
    with pytest.raises(ParseError, match="empty file"):
        BinFile.parse(b"")


def test_rejects_a_truncated_file():
    with pytest.raises(ParseError, match="truncated"):
        BinFile.parse(bytes([tokens.MAGIC if hasattr(tokens, "MAGIC") else 0xFC]) + b"\x00" * 8)


def test_immediate_constants():
    """An xx64 opcode carries its value in the high byte."""
    assert tokens.immediate_value(0x0164) == 0
    assert tokens.immediate_value(0x0564) == 1
    assert tokens.immediate_value(0x2964) == 10
    assert tokens.immediate_value(0x0165) is None  # literal form, not immediate
    assert tokens.lookup(0x0964).mnemonic == "PUSH_2"


def test_typed_variable_opcodes():
    """The high byte is the type, the low byte the operation."""
    assert tokens.lookup(0x140B).mnemonic == "LOAD$"
    assert tokens.lookup(0x040C).mnemonic == "STORE%"
    assert tokens.lookup(0x000B).mnemonic == "LOAD"
    assert tokens.lookup(0x140E).operands == ("u16", "ref")


def test_type_conversion_opcodes():
    assert tokens.lookup(0x0508).mnemonic == "CINT"
    assert tokens.lookup(0x0908).mnemonic == "CLNG"
    assert tokens.lookup(0x0D08).mnemonic == "CSNG"
    assert tokens.lookup(0x1108).mnemonic == "CDBL"


def test_line_headers_are_distinguishable_from_opcodes():
    assert tokens.is_line_header(0x0000)
    assert tokens.is_line_header(0x2000)  # eight spaces of indent
    assert tokens.indent_of(0x2000) == 8
    assert tokens.is_line_header(0x0004)  # labelled line
    assert tokens.is_line_header(0x0001)  # indentation in the next word
    assert not tokens.is_line_header(0x0097)


def test_escaped_indent_reads_both_forms():
    assert tokens.escaped_indent(0x0021) == 33  # raw count, too big to shift
    assert tokens.escaped_indent(0x0C00) == 3  # header-shaped word
    assert tokens.escaped_indent(0x0000) == 0


def test_run_length_expansion():
    assert tokens.expand_runs(b"ab") == b"ab"
    assert tokens.expand_runs(b"\x0d\x04=") == b"===="
    assert tokens.expand_runs(b" \x0d\x03- x") == b" --- x"
    # A trailing 0x0d with no room for a count and character is literal.
    assert tokens.expand_runs(b"x\x0d") == b"x\x0d"


def test_parse_signature_rejects_short_payloads():
    assert parse_signature(b"") is None
    assert parse_signature(b"\x01\x00\x00\x01\x05\x00") is None  # claims 5 params


def test_signature_without_a_parameter_list():
    """A count of 0xffff means no parentheses were written at all."""
    sig = parse_signature(b"\xaf\x1c\x82\x02\xff\xff")
    assert sig is not None and not sig.listed
    assert sig.return_suffix == "&"  # DECLARE FUNCTION Top10&


def test_float_formatting_uses_the_shortest_round_trip():
    # 1.00794 stored as a single reads back as 1.0079400539... in double
    # arithmetic; the shortest form that still round-trips is what was written.
    assert format_single(struct.unpack("<HH", struct.pack("<f", 1.00794))) == "1.00794"
    assert format_single(struct.unpack("<HH", struct.pack("<f", 2.5))) == "2.5"
    assert format_single(struct.unpack("<HH", struct.pack("<f", 0.2))) == ".2"
    # A whole-number double literal keeps its "#", as in FnArea#(2#).
    assert format_double(struct.unpack("<HHHH", struct.pack("<d", 2.0))) == "2#"
