"""Tests for identifying what a pile of old BASIC files are."""

import pytest

from qb45detok.triage import classify, scan, SIGNATURES


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("first,kind", [
    (0xFC, "QuickBASIC 4.5"),
    (0xFF, "GW-BASIC, BASICA or MSX-BASIC"),
    (0xF9, "older Microsoft BASIC"),
])
def test_leading_byte_names_the_format(tmp_path, first, kind):
    path = write(tmp_path, "X.BAS", bytes([first]) + b"\x00" * 40)
    assert classify(path).kind == kind


def test_quickbasic_is_the_one_this_tool_can_read(tmp_path):
    qb = classify(write(tmp_path, "A.BAS", b"\xfc" + b"\x00" * 40))
    gw = classify(write(tmp_path, "B.BAS", b"\xff" + b"\x00" * 40))
    assert qb.readable_here
    assert not gw.readable_here


def test_plain_source_is_recognised(tmp_path):
    path = write(tmp_path, "C.BAS", b"PRINT \"hello\"\r\nEND\r\n")
    found = classify(path)
    assert found.kind == "plain text"
    assert "ASCII" in found.detail


def test_code_page_characters_are_still_text(tmp_path):
    """DOS source is full of box drawing, which is not a sign of binary."""
    body = "' " + "╔═╗" * 60 + "\r\nPRINT 1\r\n"
    path = write(tmp_path, "D.BAS", body.encode("cp437"))
    assert classify(path).kind == "plain text"


def test_nul_bytes_mean_it_is_not_text(tmp_path):
    path = write(tmp_path, "E.BAS", b"PRINT 1\x00\x00\x00\x00binary")
    assert classify(path).kind == "unrecognised"


def test_empty_and_missing_are_reported_not_guessed(tmp_path):
    assert classify(write(tmp_path, "F.BAS", b"")).kind == "empty"
    assert classify(tmp_path / "nope.BAS").kind == "unreadable"


def test_scan_walks_a_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path, "one.BAS", b"\xfc" + b"\x00" * 40)
    write(tmp_path / "sub", "two.BAS", b"\xff" + b"\x00" * 40)
    write(tmp_path, "notes.txt", b"ignore me")
    found = scan([tmp_path])
    assert sorted(f.path.name for f in found) == ["one.BAS", "two.BAS"]


def test_scan_takes_a_file_directly(tmp_path):
    path = write(tmp_path, "one.BAS", b"\xfc" + b"\x00" * 40)
    assert [f.path for f in scan([path])] == [path]


def test_every_signature_names_a_tool():
    for kind, tool in SIGNATURES.values():
        assert kind and tool
