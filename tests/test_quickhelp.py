"""Tests for the QuickHelp reader.

The two .HLP files are Microsoft's and are not distributed, so the tests that
need them skip when they are absent. What is left still covers the parsing and
the decompression against a file built here.
"""

import struct
from pathlib import Path

import pytest

from qb45detok.quickhelp import (HelpFile, QuickHelpError, KEYWORD_LO,
                                 KEYWORD_SPACE, MAGIC, RUN_OF_SPACES,
                                 SET_ATTRIBUTE)

#: Where QuickBASIC installs its help, if it is installed at all.
HLP_DIR = Path.home() / "dos" / "QB45" / "HLP"
QCK, ADVR = HLP_DIR / "QB45QCK.HLP", HLP_DIR / "QB45ADVR.HLP"

requires_help = pytest.mark.skipif(
    not QCK.exists(), reason="QB45QCK.HLP not present")


def build(topics, keywords=(b"ALPHA", b"BETA")):
    """Assemble a minimal QuickHelp file with an identity Huffman tree.

    A tree whose every branch is a leaf cannot exist, so this uses a flat
    eight-bit code: 256 leaves reached by walking eight 1 bits is not what a
    real file looks like, but it exercises the same walk.
    """
    # Build a tree where the byte value is read MSB first: node i has children
    # at 2i+1 style positions is too deep for 512 words, so instead the test
    # file stores one topic that is entirely one repeated byte.
    tree = [0] * 512
    # word 0: a 1 bit steps to word 1, which is a leaf for 'A'.
    tree[1] = 0x8000 | ord("A")
    # a 0 bit jumps to tree[0]/2 = 0, so only 1 bits are used below.
    body = bytearray()
    for text in topics:
        body += text
    header = bytearray(0x3A)
    header[0:2] = MAGIC
    struct.pack_into("<H", header, 2, 2)
    struct.pack_into("<H", header, 8, len(topics))
    struct.pack_into("<H", header, 10, 0)
    header[0x10:0x16] = b"t.hlp\0"
    index_off = 0x3A
    index = b"".join(struct.pack("<I", 0) for _ in range(len(topics) + 1))
    ctx_off = index_off + len(index)
    kw_off = ctx_off
    kw = b"".join(bytes([len(k)]) + k for k in keywords)
    huff_off = kw_off + len(kw)
    text_off = huff_off + 1024
    struct.pack_into("<6I", header, 0x22, index_off, ctx_off, ctx_off,
                     kw_off, huff_off, text_off)
    out = bytes(header) + index + kw + struct.pack("<512H", *tree) + bytes(body)
    return out


def test_rejects_a_file_without_the_magic():
    with pytest.raises(QuickHelpError):
        HelpFile.parse(b"XX" + bytes(200))


def test_rejects_a_truncated_file():
    with pytest.raises(QuickHelpError):
        HelpFile.parse(MAGIC + bytes(8))


def test_huffman_walk_emits_the_leaf_it_reaches():
    hf = HelpFile.parse(build([b""]))
    # Three bytes: the decompressed length, then bits. Every 1 bit steps from
    # word 0 to word 1, which is the leaf 'A'.
    assert hf._huffman(struct.pack("<H", 4) + b"\xff") == b"AAAA"


def test_keyword_reference_expands_and_the_high_bank_adds_a_space():
    hf = HelpFile.parse(build([b""]))
    plain = bytes([KEYWORD_LO, 0])
    spaced = bytes([KEYWORD_SPACE, 1])
    assert hf._expand(plain, 0, None)[0] == b"ALPHA"
    assert hf._expand(spaced, 0, None)[0] == b"BETA "


def test_run_of_spaces_and_attribute():
    hf = HelpFile.parse(build([b""]))
    assert hf._expand(bytes([RUN_OF_SPACES, 4]) + b"x", 0, None)[0] == b"    x"
    # The attribute takes an operand and produces nothing.
    assert hf._expand(bytes([SET_ATTRIBUTE, 0x11]) + b"y", 0, None)[0] == b"y"


def test_link_target_is_not_display_text():
    hf = HelpFile.parse(build([b""]))
    raw = b"see \xffFILE.HLP!.ctx\x00 here"
    assert hf._expand(raw, 0, None)[0] == b"see  here"


def test_length_is_only_read_when_it_cannot_be_a_control():
    assert HelpFile._length(b"\x4b", 0) == 0x4a
    assert HelpFile._length(b"\x1a", 0) is None
    assert HelpFile._length(b"", 0) is None


@requires_help
def test_quick_reference_parses():
    hf = HelpFile.from_path(QCK)
    assert hf.name == "qb45qck.hlp"
    assert hf.topic_count == 200
    assert len(hf.keywords) == 1024
    assert len(hf.tree) == 512


@requires_help
def test_a_known_topic_reads_back_as_documentation():
    hf = HelpFile.from_path(QCK)
    lines = hf.topic(hf.topic_for("PRINT"))
    body = "\n".join(lines)
    assert "PRINT - a device I/O statement" in body
    assert "PRINT [expressionlist][{,|;}]" in body


@requires_help
def test_context_lookup_is_case_insensitive():
    hf = HelpFile.from_path(QCK)
    assert hf.topic_for("print") == hf.topic_for("PRINT")
    assert hf.topic_for("no such context") is None


@requires_help
def test_every_topic_decodes_without_control_characters():
    hf = HelpFile.from_path(QCK)
    bad = 0
    for number in range(hf.topic_count):
        for line in hf.topic(number)[1:]:
            if any(ord(c) < 0x20 for c in line):
                bad += 1
    assert bad == 0


@requires_help
@pytest.mark.skipif(not ADVR.exists(), reason="QB45ADVR.HLP not present")
def test_the_full_reference_reproduces_a_code_example():
    hf = HelpFile.from_path(ADVR)
    body = "\n".join(hf.topic(57))
    assert "  IF X = 1 THEN" in body
    assert '    PRINT "one"' in body
