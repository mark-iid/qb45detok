"""Tests for the formatter.

It has no house style of its own: QuickBASIC reformatted a program as it read
it, and this hands the source to the tokenizer and reads it back. So the two
properties worth testing are that it settles (running it twice changes
nothing) and that QuickBASIC's own output is already a fixed point.
"""

import pytest

from conftest import requires_corpus, CORPUS_TXT, NAMES
from qb45detok.fmt import diff, format_source, would_change

#: The one file whose comment ends in spaces, which the payload padding rule
#: means cannot survive a round trip. `docs/format.md` describes it.
NOT_IDEMPOTENT = {"DIRMAST.BAS"}


def fmt(source):
    return format_source(source)


# -- what it does -------------------------------------------------------

@pytest.mark.parametrize("before,after", [
    ("print 1", 'PRINT 1'),
    ("dim shared total as integer", "DIM SHARED total AS INTEGER"),
    ("for i=1 to 10", "FOR i = 1 TO 10"),
    ("if a mod 2=0 then print a", "IF a MOD 2 = 0 THEN PRINT a"),
    ('print "hi";name$', 'PRINT "hi"; name$'),
    ("x=y+1", "x = y + 1"),
    ("sub greet(name$)", "SUB greet (name$)"),
])
def test_one_line(before, after):
    assert fmt(before)[0] == after


def test_indentation_is_left_alone():
    """QuickBASIC never re-indented anyone's code, so neither does this."""
    assert fmt("IF X THEN\n        PRINT 1\nEND IF")[:3] == [
        "IF X THEN", "        PRINT 1", "END IF"]


def test_a_comment_keeps_its_column():
    assert fmt("X = 1                ' note")[0] == "X = 1                ' note"


def test_a_line_it_cannot_read_is_left_as_it_was():
    assert fmt("this is not BASIC at all")[0] == "this is not BASIC at all"


def test_a_name_takes_one_spelling_throughout():
    """QB stores a name once, so every mention comes back the same."""
    assert fmt("Total = 1\ntotal = 2\nTOTAL = 3") == [
        "Total = 1", "Total = 2", "Total = 3", ""]


# -- procedure order ----------------------------------------------------

def source_with_two_procedures():
    return ("PRINT 1\n\nSUB Zebra\nPRINT 2\nEND SUB\n\nSUB Alpha\n"
            "PRINT 3\nEND SUB")


def test_the_file_keeps_its_own_order_by_default():
    """A formatter that moves code around is a surprise."""
    out = fmt(source_with_two_procedures())
    assert out.index("SUB Zebra") < out.index("SUB Alpha")


def test_qb_order_sorts_them_the_way_save_as_text_does():
    out = format_source(source_with_two_procedures(), qb_order=True)
    assert out.index("SUB Alpha") < out.index("SUB Zebra")


# -- the properties -----------------------------------------------------

def test_formatting_settles():
    messy = "dim shared total as integer\nfor i=1 to 10\nnext i"
    once = fmt(messy)
    assert fmt("\n".join(once)) == once


def test_would_change_reports_honestly():
    assert would_change("print 1", fmt("print 1"))
    # QB ends a section with a blank line, so formatted source has one too.
    assert not would_change("PRINT 1\n\n", fmt("PRINT 1"))


def test_the_diff_names_both_sides():
    out = diff(["print 1"], fmt("print 1"), "x.BAS")
    assert out[0].startswith("--- x.BAS") and out[1].endswith("formatted")


@requires_corpus
@pytest.mark.parametrize("name", NAMES)
def test_quickbasics_own_output_is_a_fixed_point(name):
    """QB wrote these, so a formatter matching QB should not touch them."""
    from tests.test_tokenize import KNOWN_DIFFERENT
    if name in KNOWN_DIFFERENT:
        pytest.skip("a documented round-trip difference")
    text = (CORPUS_TXT / name).read_text(encoding="latin-1")
    assert format_source(text, CORPUS_TXT) == text.splitlines()
