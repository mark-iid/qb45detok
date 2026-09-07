"""Tests for the front half of tokenizing: the lexer and the parser."""

import pytest

from qb45detok.expr import Cursor, ParseError, parse_expression
from qb45detok.lex import Kind, LexError, lex
from qb45detok.parse import parse_line


def words(text):
    return " ".join(str(e) for e in parse_expression(Cursor(lex(text))))


def stream(text):
    return " ".join(str(e) for st in parse_line(text) for e in st.emits)


# -- the lexer ----------------------------------------------------------

def test_a_period_is_part_of_a_name():
    """Press.Any.Key is one identifier, not three."""
    toks = lex("Press.Any.Key")
    assert [t.text for t in toks[:-1]] == ["Press.Any.Key"]


def test_a_type_suffix_binds_to_the_name():
    toks = lex("Count%")
    assert toks[0].text == "Count" and toks[0].suffix == "%"


def test_a_period_after_a_name_is_a_record_field():
    toks = lex("Disk(N).Sectors")
    assert [t.text for t in toks[:-1]] == ["Disk", "(", "N", ")", ".", "Sectors"]


@pytest.mark.parametrize("text,kind", [
    ("1", "integer"), ("70000", "long"), ("1.5", "single"), (".01", "single"),
    ("1.5D+10", "double"), ("1.5E+10", "single"), ("100#", "double"),
    ("100!", "single"), ("100&", "long"), ("100%", "integer"),
    ("&HFF", "integer"), ("&HFFFFF", "long"), ("&O777", "integer"),
])
def test_a_literal_spells_its_own_type(text, kind):
    assert lex(text)[0].numtype == kind


def test_a_comment_takes_the_rest_of_the_line():
    toks = lex("X = 1 ' and this: is not a separator")
    assert toks[-2].kind is Kind.STRING
    assert toks[-2].text == " and this: is not a separator"


def test_rem_takes_the_rest_of_the_line():
    toks = lex("REM $DYNAMIC")
    assert toks[1].text == " $DYNAMIC"


def test_an_unknown_character_is_reported():
    with pytest.raises(LexError):
        lex("X = {")


# -- expressions --------------------------------------------------------

def test_precedence_without_brackets():
    assert words("1 + 2 * 3").endswith("MULTIPLY ADD")


def test_brackets_are_kept_rather_than_folded_away():
    """QB stores what was written, so a redundant bracket has to survive."""
    assert "PAREN" in words("(2)")
    out = words("(1 + 2) * 3")
    assert out.index("PAREN") < out.index("MULTIPLY")


def test_power_binds_tighter_than_unary_minus():
    assert words("-X ^ 2").endswith("POWER NEGATE")


def test_power_groups_to_the_right():
    assert words("2 ^ 3 ^ 2").count("POWER") == 2


def test_a_word_operator_is_not_a_variable():
    assert "MOD" in words("A MOD B")


# -- statements ---------------------------------------------------------

def test_an_assignment_is_not_a_comparison():
    """"=" is a comparison in an expression and an assignment in a statement."""
    assert stream("Attr = 16").endswith("STORE ['Attr', '']")


def test_assignment_into_an_array_and_a_field():
    assert "ARRSET" in stream("A(1) = 2")
    assert "FIELD_SET" in stream("Disk(N).Sectors = 63")


def test_a_call_written_without_the_keyword():
    assert "CALL_IMPLICIT" in stream("Press.Any.Key")


def test_statements_split_on_a_colon():
    assert len(parse_line("A = 1: B = 2")) == 2


def test_a_colon_inside_a_string_does_not_split():
    assert len(parse_line('PRINT "a:b"')) == 1


def test_graphics_coordinates_are_one_argument():
    out = stream("LINE (0, 0)-(10, 10), 1")
    assert "COORD" in out and "COORD_TO" in out


def test_an_omitted_argument_keeps_its_place():
    assert "ARG_OMITTED" in stream("LOCATE , , 1")


def test_a_file_number_is_marked():
    assert "FILE_NUMBER" in stream('IOCTL #1, "MD"')


def test_a_signature_carries_its_parameters():
    out = parse_line("DECLARE SUB Ext CDECL ALIAS \"extfn\" (BYVAL N%)")
    emit = out[0].emits[0]
    assert emit.mnemonic == "DECLARE"
    name, suffix, kind, cdecl, alias, params, static = emit.operands
    assert (name, kind, cdecl, alias) == ("Ext", "SUB", True, "extfn")
    assert params[0][:3] == ("N", "%", True)      # name, suffix, byval


def test_a_block_if_and_a_line_if_differ():
    assert stream("IF X THEN").endswith("IF_THEN_BLOCK")
    assert "IF_THEN_LINE" in stream("IF X THEN Y = 1")


def test_for_with_and_without_step():
    assert "FOR_STEP" in stream("FOR I = 1 TO 10 STEP 2")
    assert stream("FOR I = 1 TO 10").endswith("FOR ['I', '']")
