"""Tests for turning decoded tokens back into source.

The corpus is an exact oracle here: QB wrote the text itself, so a rendering
is right only if it matches byte for byte. These tests pin down the files that
already round-trip and guard the overall line-match rate against regressions.
"""

import pytest

from conftest import requires_corpus,   CORPUS_BIN, CORPUS_TXT, NAMES
from qb45detok import BinFile
from qb45detok.decode import decode_file
from qb45detok.render import Renderer, format_double, format_single

pytestmark = requires_corpus

#: Every file in the corpus reproduces QB's text output byte for byte.
#: Removing one from this list is a regression.
BYTE_IDENTICAL = [
    "CHAINRUN.BAS", "COLON1.BAS", "COLON2.BAS", "COVER2.BAS",
    "COVERAGE.BAS", "DECLARE.BAS", "DEFFN.BAS", "DEFTYPE.BAS",
    "DESCFILE.BAS", "DIRMAST.BAS", "DOTS.BAS", "DOTSUB.BAS",
    "DRAWSCR1.BAS", "EDGE.BAS", "EDGE2.BAS", "EDGE3.BAS", "EDGE4.BAS",
    "EDGE5.BAS", "EDGE6.BAS", "EDGE7.BAS", "EDGE8.BAS", "FILEIO.BAS",
    "FILEOPS.BAS", "GRAPHIC2.BAS", "JOHNNY.BAS", "MAINMENU.BAS",
    "MATTMENU.BAS", "MISC.BAS", "MISC2.BAS", "NODOTS.BAS", "OBJSCAN.BAS",
    "OPENMODE.BAS", "PHYSICS.BAS", "PROJECT2.BAS", "RESERVED.BAS",
    "STARDEF.BAS", "SYSTEM.BAS", "TORUS.BAS", "TRAIL1.BAS", "TRAIL2.BAS",
    "TYPES.BAS", "TYPES2.BAS",
]

#: Lower bound on the share of lines rendered exactly across the corpus.
MIN_LINE_MATCH = 1.0


def render(name):
    bf = BinFile.from_path(CORPUS_BIN / name)
    return Renderer(bf).file(decode_file(bf))


def expected(name):
    text = (CORPUS_TXT / name).read_text(encoding="latin-1", newline="")
    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines.pop()  # the file's own trailing newline
    return lines


@pytest.mark.parametrize("name", BYTE_IDENTICAL)
def test_round_trips_byte_for_byte(name):
    produced = "".join(line + "\r\n" for line in render(name))
    assert produced == (CORPUS_TXT / name).read_text(encoding="latin-1", newline="")


def test_line_match_rate():
    matched = total = 0
    for name in NAMES:
        want = expected(name)
        got = render(name)
        total += len(want)
        matched += sum(1 for a, b in zip(got, want) if a.rstrip() == b.rstrip())
    rate = matched / total
    assert rate >= MIN_LINE_MATCH, f"render quality regressed to {rate:.1%}"


def test_rendering_never_raises():
    """A line the renderer cannot express is marked, not fatal."""
    for name in NAMES:
        for line in render(name):
            assert isinstance(line, str)


def test_descfile_is_exact():
    assert render("DESCFILE.BAS") == expected("DESCFILE.BAS")


def test_parentheses_are_recorded_not_inferred():
    """QB stores the parentheses that were written, redundant ones included."""
    line = next(l for l in render("DRAWSCR1.BAS") if l.startswith("Bytes% ="))
    assert line == "Bytes% = 4 + INT(((320 - 1 + 1) * (2) + 7) / 8) * 1 * ((200 - 1) + 1)"



def test_deftype_ranges_render():
    lines = render("DEFTYPE.BAS")
    assert "DEFINT A-C" in lines
    assert "DEFLNG D-F" in lines
    assert "DEFSTR M-O" in lines
    assert "DEFINT A-Z" in render("TORUS.BAS")


def test_parameter_forms():
    """The mode word decides suffix, array, BYVAL and AS forms."""
    lines = render("MATTMENU.BAS")
    assert "DECLARE SUB MeanAverageD (Array#(), First%, Last%, Average#, ErrCode%)" in lines
    assert "DECLARE FUNCTION FarPeek% (BYVAL DSeg%, BYVAL DOfs%)" in lines
    # A DECLARE keeps its parentheses even with no parameters.
    assert any(l.startswith("DECLARE SUB ") and l.endswith(" ()") for l in lines)


def test_deftype_is_written_only_when_it_changes():
    """The record copied into each procedure is silent unless it differs.

    TORUS declares DEFINT A-Z in the module. Every procedure but TorusCalc
    carries a copy of that, which is not printed. TorusCalc has no record at
    all, meaning the language default, so QB writes DEFSNG A-Z before it and
    DEFINT A-Z again before the next procedure.
    """
    lines = render("TORUS.BAS")
    assert [l for l in lines if l.startswith("DEF") and l.endswith("A-Z")] == [
        "DEFINT A-Z", "DEFSNG A-Z", "DEFINT A-Z",
    ]
