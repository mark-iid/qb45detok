"""BASIC 7 PDS writes a variant of the same format, and this reads it.

The differences are small and all of them are here: one extra byte in the
header, which moves everything after it up by one; CURRENCY taking type code
5, which pushes STRING to 6; two more bytes per signature parameter; and a
block of opcodes of its own.
"""

import pytest

from conftest import CORPUS_PDS_BIN, CORPUS_PDS_TXT, PDS_NAMES, requires_pds
from qb45detok import tokens
from qb45detok.decode import PARAM_LEN, PDS_PARAM_LEN, decode_file
from qb45detok.reader import PDS71, QB45, BinFile, ParseError
from qb45detok.render import Renderer
from qb45detok.triage import classify


# -- the layout ---------------------------------------------------------

def test_the_version_byte_picks_the_layout():
    assert QB45.version == 0x00 and PDS71.version == 0x02
    assert PDS71.ref_base == QB45.ref_base + 1
    assert PDS71.names_off == QB45.names_off + 1


def test_pds_numbers_currency_before_string():
    """The one change that quietly alters what a file means."""
    assert QB45.type_keywords[5] == "STRING"
    assert PDS71.type_keywords[5] == "CURRENCY"
    assert PDS71.type_keywords[6] == "STRING"
    assert PDS71.deftype_keywords[5] == "DEFCUR"


def test_an_unknown_variant_is_refused():
    with pytest.raises(ParseError, match="unknown variant"):
        BinFile.parse(bytes([0xFC, 0x77]) + bytes(200))


def test_a_signature_parameter_is_longer_in_pds():
    assert PDS_PARAM_LEN == PARAM_LEN + 2


# -- the opcodes it adds ------------------------------------------------

def test_the_pds_only_opcodes_are_named():
    assert {op.mnemonic for op in tokens.PDS_OPS.values()} >= {
        "CURDIR$", "DIR$", "CVC", "MKC$", "CCUR", "CHDRIVE"}


def test_ccur_sits_in_the_conversion_family():
    """Its low byte is 08 and its high byte is the index 4.5 leaves unused."""
    assert 0x1508 in tokens.PDS_OPS
    assert tokens.PDS_OPS[0x1508].mnemonic == "CCUR"
    assert 0x1508 & 0xFF == tokens.CONVERT_LOW_BYTE


def test_chdrive_prints_in_pds_and_not_in_4_5():
    assert tokens.lookup(0x017F).text is None
    assert tokens.PDS_OPS[0x017F].text == "CHDRIVE"


# -- against real files -------------------------------------------------

@requires_pds
@pytest.mark.parametrize("name", PDS_NAMES)
def test_a_pds_file_reads_back_as_its_own_text(name):
    bf = BinFile.from_path(CORPUS_PDS_BIN / name)
    assert bf.layout is PDS71
    want = (CORPUS_PDS_TXT / name).read_text(encoding="latin-1").splitlines()
    assert Renderer(bf).file(decode_file(bf)) == want


@requires_pds
@pytest.mark.parametrize("name", PDS_NAMES)
def test_every_opcode_in_a_pds_file_is_known(name):
    bf = BinFile.from_path(CORPUS_PDS_BIN / name)
    for ds in decode_file(bf):
        for line in ds.lines:
            for instr in line.instrs:
                assert instr.op is not None, f"{name}: {instr.code:#06x}"


@requires_pds
def test_triage_tells_the_two_apart():
    assert classify(CORPUS_PDS_BIN / PDS_NAMES[0]).kind == "BASIC 7 PDS"


def test_the_isam_families_are_one_opcode_each():
    """MOVE and SEEK put the member in an operand, numbered in fours."""
    assert tokens.ISAM_MOVES == {0: "MOVEFIRST", 4: "MOVELAST",
                                 8: "MOVENEXT", 12: "MOVEPREVIOUS"}
    assert tokens.ISAM_SEEKS == {0: "SEEKEQ", 4: "SEEKGE", 8: "SEEKGT"}
    assert tokens.PDS_OPS[0x0198].operands == ("u16",)
    assert tokens.PDS_OPS[0x019F].operands == ("u16", "u16")


def test_pds_moves_the_type_high_bytes_too():
    """A function returning a string is 24 in PDS and 20 in 4.5."""
    assert tokens.TYPE_BY_HIGH_BYTE[20] == "$"
    assert tokens.PDS_TYPE_BY_HIGH_BYTE[20] == "@"
    assert tokens.PDS_TYPE_BY_HIGH_BYTE[24] == "$"
    assert tokens.lookup(0x180E) is None
    assert tokens.lookup(0x180E, tokens.PDS_TYPE_BY_HIGH_BYTE).mnemonic == "ARRAY$"


def test_savepoint_and_getindex_are_functions():
    """Both were refused as statements; the help file said why."""
    assert tokens.PDS_OPS[0x018A].mnemonic == "SAVEPOINT"
    assert tokens.PDS_OPS[0x018A].arity == 0
    assert tokens.PDS_OPS[0x0188].mnemonic == "GETINDEX$"
    assert tokens.PDS_OPS[0x0188].arity == 1


def test_rollback_has_three_forms():
    assert tokens.PDS_OPS[0x019C].text == "ROLLBACK"
    assert tokens.PDS_OPS[0x019D].arity == 1        # ROLLBACK <savepoint>
    assert tokens.PDS_OPS[0x019E].text == "ROLLBACK ALL"
