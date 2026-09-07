"""Tests for the tokenizer: source in, the binary QuickBASIC saves out.

The interesting check is the round trip. Reading a format lets you skip the
parts you have not worked out; writing it and reading it back does not, so a
program that comes out of the tokenizer and back through the reader with its
text intact has exercised every field the two directions share.
"""

import pytest

from conftest import requires_corpus, CORPUS_TXT
from qb45detok.decode import decode_file
from qb45detok.reader import BinFile
from qb45detok.render import Renderer
from qb45detok.tokenize import (Tokenizer, indent_of, record_variables,
                                split_sections, tokenize)

#: Files whose text the round trip does not reproduce, and why. None of these
#: is a disagreement about tokenizing; ``docs/format.md`` describes each.
KNOWN_DIFFERENT = {
    # A comment ending in spaces loses one to the padding rule on the way in.
    "DIRMAST.BAS",
    # Pages of manual text that QB refused to tokenize and this accepts.
    "EXAMPLE1.BAS", "EXAMPLE2.BAS", "EXAMPLE3.BAS", "EXAMPLE4.BAS",
    "EXAMPLE5.BAS",
    # One line the renderer writes as "EXIT DEF" from an EXIT SUB opcode.
    "EXEC86.BAS",
    # Fifty-two procedures the file keeps in its module section rather than
    # in sections of their own, which is not what QB's editor produces.
    "QB8086.BAS",
}


def read_back(data):
    """The text a tokenized program renders as."""
    bf = BinFile.parse(data)
    return Renderer(bf).file(decode_file(bf))


def round_trip(text):
    return read_back(tokenize(text))


# -- the whole trip -----------------------------------------------------

@requires_corpus
@pytest.mark.parametrize("name", sorted(
    p.name for p in CORPUS_TXT.glob("*.BAS")) if CORPUS_TXT.is_dir() else [])
def test_source_survives_the_round_trip(name):
    text = (CORPUS_TXT / name).read_text(encoding="latin-1")
    if name in KNOWN_DIFFERENT:
        pytest.skip("a documented difference, not a tokenizing one")
    assert round_trip(text) == text.splitlines()


def test_the_output_is_a_quickbasic_file():
    data = tokenize("PRINT 1\nEND")
    assert data[0] == 0xFC
    assert BinFile.parse(data).has_standard_header


# -- statements ---------------------------------------------------------

@pytest.mark.parametrize("source", [
    'PRINT "hello"',
    "X% = 1 + 2 * 3",
    "IF A THEN B = 1 ELSE B = 2",
    "FOR I = 1 TO 10 STEP 2",
    "NEXT I",
    "DIM SHARED Names$(1 TO 100)",
    "DIM Rec AS INTEGER",
    'OPEN "F.DAT" FOR RANDOM ACCESS READ WRITE SHARED AS #3',
    "LOCATE , , 1",
    "LINE (0, 0)-(10, 10), 3, BF",
    "CIRCLE (100, 50), 20, 1",
    'INPUT "Name: ", N$',
    "LINE INPUT #1, L$",
    "SELECT CASE X",
    "CASE IS >= 10",
    "ON ERROR GOTO Handler",
    "DEF SEG = &HB800",
    "MID$(A$, 3) = \"L\"",
    "WRITE #1, A$, 2",
    "PRINT TAB(5); \"x\"",
    "CONST PI = 3.14159",
    "DEFINT A-C, X-Z",
])
def test_one_statement_reads_back_as_itself(source):
    assert round_trip(source) == [source, ""]


@pytest.mark.parametrize("source", [
    "Skip:",
    "100 PRINT 1",
    "Loop.Top:  GOTO Loop.Top",
])
def test_a_label_survives(source):
    assert round_trip(source) == [source, ""]


def test_indentation_is_kept():
    assert round_trip("IF X THEN\n    PRINT 1\nEND IF")[:3] == [
        "IF X THEN", "    PRINT 1", "END IF"]


def test_a_comment_keeps_its_column():
    source = "X = 1                ' note"
    assert round_trip(source)[0] == source


def test_a_line_that_does_not_parse_is_kept_as_text():
    """QB stores an unreadable line rather than losing it, and so does this."""
    assert round_trip("this is not BASIC at all")[0] == "this is not BASIC at all"


# -- sections -----------------------------------------------------------

def test_a_procedure_becomes_its_own_section():
    data = tokenize("PRINT 1\n\nSUB Greet\nPRINT 2\nEND SUB")
    assert [s.name for s in BinFile.parse(data).sections] == [None, "Greet"]


def test_a_function_records_its_return_type():
    data = tokenize("SUB S\nEND SUB\n\nFUNCTION Half%\nEND FUNCTION")
    half = [s for s in BinFile.parse(data).sections if s.name == "Half"][0]
    assert (half.proc_kind, half.return_type) == (2, 1)


def test_comments_above_a_procedure_go_with_it():
    """QB puts them in the procedure's section, and nothing else could."""
    parts = split_sections("PRINT 1\n' about Greet\nSUB Greet\nEND SUB")
    assert parts[0].lines == ["PRINT 1"]
    assert parts[1].lines[0] == "' about Greet"


def test_the_blank_line_between_sections_is_not_stored():
    parts = split_sections("PRINT 1\n\nSUB Greet\nEND SUB\n")
    assert parts[0].lines == ["PRINT 1"]


def test_two_blank_lines_are_stored():
    """One is the separator a text export adds; the rest were written."""
    parts = split_sections("PRINT 1\n\n\nSUB Greet\nEND SUB\n")
    assert parts[0].lines == ["PRINT 1", "", ""]


# -- what the whole program says ----------------------------------------

def test_a_record_variable_is_found_from_its_declaration():
    text = "TYPE Point\n  X AS INTEGER\nEND TYPE\nDIM P AS Point\nP.X = 1"
    assert record_variables(text) == {"p"}


def test_a_record_field_is_not_a_name_with_a_period_in_it():
    text = "TYPE Point\n  X AS INTEGER\nEND TYPE\nDIM P AS Point\nP.X = 1"
    assert round_trip(text)[-2] == "P.X = 1"


def test_a_period_in_a_plain_name_stays_there():
    assert round_trip("Press.Any.Key = 1")[0] == "Press.Any.Key = 1"


@pytest.mark.parametrize("line,column", [
    ("X = 1", 0), ("    X = 1", 4), ("\tX = 1", 8), ("  \tX = 1", 8),
])
def test_indent_counts_columns_not_characters(line, column):
    assert indent_of(line) == column


def test_tab_indentation_is_recorded_in_the_header():
    assert Tokenizer("\tPRINT 1").asm.writer.header[0x13] & 0x80
    assert not Tokenizer("    PRINT 1").asm.writer.header[0x13] & 0x80
