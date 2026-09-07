import pytest

from conftest import requires_corpus,   CORPUS_BIN
from qb45detok.cli import main

pytestmark = requires_corpus

SAMPLE = str(CORPUS_BIN / "DRAWSCR1.BAS")


def test_dump(capsys):
    assert main(["dump", SAMPLE]) == 0
    out = capsys.readouterr().out
    assert "SUB/FUNCTION Save.Screen" in out
    assert "names            : 301" in out


def test_names_procs_only(capsys):
    assert main(["names", SAMPLE, "--procs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 10
    assert all("sub" in line for line in lines)


def test_sections_hexdump(capsys):
    assert main(["sections", SAMPLE, "--index", "1", "--bytes", "16"]) == 0
    out = capsys.readouterr().out
    assert "SUB/FUNCTION up" in out
    assert "more bytes" in out


def test_detok_writes_source(capsys):
    assert main(["detok", str(CORPUS_BIN / "DESCFILE.BAS")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("' Describe File\n")
    assert "DECLARE SUB IdentifyFile (FileName$, Description$, DescriptionLen%)" in out


def test_detok_to_a_file_reproduces_qb_output(tmp_path):
    target = tmp_path / "out.BAS"
    assert main(["detok", str(CORPUS_BIN / "DESCFILE.BAS"), "-o", str(target)]) == 0
    assert target.read_bytes() == (CORPUS_BIN.parent / "txt" / "DESCFILE.BAS").read_bytes()


def test_bad_file_reports_cleanly(capsys, tmp_path):
    junk = tmp_path / "junk.BAS"
    junk.write_bytes(b"PRINT 1\r\n")
    assert main(["dump", str(junk)]) == 1
    assert "not a QuickBASIC 4.5 binary file" in capsys.readouterr().err


def test_missing_file_reports_cleanly(capsys):
    assert main(["dump", "/nonexistent/NOPE.BAS"]) == 1
    assert "qb45detok:" in capsys.readouterr().err
