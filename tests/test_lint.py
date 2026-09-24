"""Tests for the QB64 porting check.

The point of checking the token stream rather than the text is that the text
lies: a variable can be called `fre`, a string can contain `TRON`, a comment
can mention `IOCTL`. Those cases are the interesting tests here.
"""

import pytest

from conftest import requires_corpus, CORPUS_BIN, NAMES
from qb45detok.lint import (IGNORED, PLATFORM, REWORK, RULES, check, report)
from qb45detok.reader import BinFile
from qb45detok.tokenize import tokenize


def findings(source):
    return check(BinFile.parse(tokenize(source)))


def keywords(source):
    return {f.keyword for f in findings(source)}


# -- the rules fire -----------------------------------------------------

@pytest.mark.parametrize("source,keyword", [
    ("TRON", "TRON"),
    ("TROFF", "TROFF"),
    ("PRINT FRE(0)", "FRE"),
    ("PRINT ERDEV", "ERDEV"),
    ("PRINT SETMEM(0)", "SETMEM"),
    ('IOCTL #1, "MD"', "IOCTL"),
    ("CALLS Ext(3)", "CALLS"),
    ('DATE$ = "01-01-1994"', "DATE$ ="),
    ('TIME$ = "12:00:00"', "TIME$ ="),
    ("WIDTH LPRINT 80", "WIDTH LPRINT"),
    ("LPRINT \"x\"", "LPRINT"),
    ("CHAIN \"OTHER.BAS\"", "CHAIN"),
    ("UEVENT ON", "UEVENT"),
])
def test_a_keyword_qb64_drops_is_reported(source, keyword):
    assert keyword in keywords(source)


def test_def_fn_needs_a_rewrite():
    found = findings("DEF FnDouble (X) = X * 2")
    assert found and all(f.severity == REWORK for f in found)


def test_call_absolute_is_caught_by_its_name():
    """It is an ordinary CALL; only the target says what it is."""
    found = findings("CALL ABSOLUTE(0)")
    assert [f.keyword for f in found] == ["CALL ABSOLUTE"]
    assert found[0].severity == REWORK


def test_opening_a_device_is_reported():
    assert "OPEN LPT1:" in keywords('OPEN "LPT1:" FOR OUTPUT AS #1')


# -- and do not fire when they should not -------------------------------

def test_a_variable_named_after_a_keyword_is_not_reported():
    """`fre` is a perfectly good variable name, and QB stores it as one."""
    assert keywords("fre = 1\nPRINT fre") == set()


def test_a_keyword_inside_a_string_is_not_reported():
    assert keywords('PRINT "TRON and IOCTL and FRE"') == set()


def test_a_keyword_in_a_comment_is_not_reported():
    assert keywords("X = 1 ' uses TRON and CALLS and SETMEM") == set()


def test_reading_the_clock_is_fine_but_setting_it_is_not():
    """QB gives the statement and the function different opcodes."""
    assert keywords("PRINT DATE$") == set()
    assert keywords('DATE$ = "01-01-1994"') == {"DATE$ ="}


def test_a_clean_program_says_so():
    assert report(findings("PRINT 1\nEND")) == [
        "Nothing here needs changing for QB64."]


# -- the report ---------------------------------------------------------

def test_the_report_leads_with_what_is_silently_ignored():
    text = "\n".join(report(findings("TRON\nDEF FnX (A) = A\nLPRINT \"x\"")))
    assert text.index("ignores these") < text.index("rewritten")
    assert text.index("rewritten") < text.index("Windows")


def test_every_severity_is_spelled_out():
    assert {s for s, _, _ in RULES.values()} == {IGNORED, REWORK, PLATFORM}


# -- against real programs ----------------------------------------------

@requires_corpus
@pytest.mark.parametrize("name", NAMES)
def test_every_corpus_file_checks_without_error(name):
    for f in check(BinFile.from_path(CORPUS_BIN / name)):
        assert f.severity in (IGNORED, REWORK, PLATFORM)
        assert f.number >= 1 and f.keyword and f.advice


@requires_corpus
def test_the_emulator_is_caught_calling_dos():
    """QB8086 reaches DOS through QB.QLB, which is the thing to find."""
    found = check(BinFile.from_path(CORPUS_BIN / "QB8086.BAS"))
    assert any(f.keyword.upper().startswith("CALL INTERRUPT") for f in found)
