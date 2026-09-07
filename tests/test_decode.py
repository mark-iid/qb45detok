"""Tests for walking the token stream.

Two independent oracles come out of the corpus, and both are stronger than
they look. The section trailer records how many source lines the section
holds, so a decoder that miscounts operands somewhere shows up as a line-count
mismatch. And each line header carries its own indentation, so comparing that
against the leading spaces in QB's text output catches a decoder that has the
right number of lines but has split them in the wrong places.
"""

import pytest

from conftest import requires_corpus,   CORPUS_BIN, CORPUS_TXT, NAMES, ROOT, text_blocks
from qb45detok import BinFile
from qb45detok.decode import decode_file, decode_section, parse_signature
from qb45detok import tokens

pytestmark = requires_corpus

#: Sections whose line count matches the trailer. Every section does; this is
#: here so a regression shows up as a failure rather than a silent slip.
SECTIONS_IN_SYNC = 87
TOTAL_SECTIONS = 87


@pytest.fixture(scope="module", params=NAMES)
def pair(request):
    name = request.param
    binary = BinFile.from_path(CORPUS_BIN / name)
    blocks = text_blocks((CORPUS_TXT / name).read_text(encoding="latin-1"))
    return name, binary, blocks


def decoded(bf):
    return decode_file(bf)


def test_nothing_truncates(pair):
    """Every section's stream ends where the section does."""
    _, bf, _ = pair
    for ds in decoded(bf):
        assert not ds.truncated, ds.section.name


def test_line_offsets_are_inside_the_section(pair):
    _, bf, _ = pair
    for ds in decoded(bf):
        for line in ds.lines:
            assert 0 <= line.offset < len(ds.section.body)
            for ins in line.instrs:
                assert 0 <= ins.offset < len(ds.section.body)


def test_enough_sections_are_in_sync():
    """A blunt regression guard on decoder coverage across the whole corpus."""
    total = in_sync = 0
    for name in NAMES:
        for ds in decode_file(BinFile.from_path(CORPUS_BIN / name)):
            total += 1
            in_sync += bool(ds.in_sync)
    assert total == TOTAL_SECTIONS
    assert in_sync >= SECTIONS_IN_SYNC, f"decoder coverage regressed: {in_sync} sections"


def test_indentation_matches_the_source(pair):
    """For procedures that line up, every decoded indent must match the text."""
    _, bf, blocks = pair
    for ds in decoded(bf):
        if ds.section.is_module or not ds.in_sync:
            continue
        src = blocks.get(ds.section.name.lower())
        if src is None or len(src) != len(ds.lines):
            continue
        for line, text in zip(ds.lines, src):
            if not text.strip():
                continue
            expected = len(text) - len(text.lstrip(" "))
            assert line.indent == expected, f"{ds.section.name}: {text!r}"


def test_labels_resolve_to_label_entries(pair):
    """A labelled line points at a name-table label, and the text agrees."""
    _, bf, blocks = pair
    for ds in decoded(bf):
        src = blocks.get(ds.section.name.lower()) if ds.section.name else None
        for i, line in enumerate(ds.lines):
            if not line.labelled:
                continue
            entry = bf.symbol(line.label_ref)
            assert entry is not None, f"unknown label ref {line.label_ref:#06x}"
            assert entry.is_label, f"{entry} is not a label"
            if src is not None and len(src) == len(ds.lines):
                assert src[i].strip().startswith(entry.label)


def test_descfile_decodes_exactly():
    """DESCFILE is small enough to pin down statement by statement."""
    bf = BinFile.from_path(CORPUS_BIN / "DESCFILE.BAS")
    (module,) = decode_file(bf)
    assert module.in_sync and len(module.lines) == 12
    assert not module.unknown_codes

    mnemonics = [[i.mnemonic for i in line.instrs] for line in module.lines]
    assert mnemonics[0] == ["REM"]
    assert module.lines[0].instrs[0].text == " Describe File"
    assert mnemonics[2] == ["REM"] and module.lines[2].instrs[0].text == ""
    # Desc$ = SPACE$(80) -- operands come before the operator.
    assert mnemonics[4] == ["PUSH_INT", "SPACE$", "STORE$"]
    assert module.lines[4].instrs[0].operands == [80]
    # File$ = LCASE$(RTRIM$(LTRIM$(COMMAND$)))
    assert mnemonics[5] == ["COMMAND$", "LTRIM$", "RTRIM$", "LCASE$", "STORE$"]
    assert mnemonics[8] == ["LOAD$", "PRINT_ITEM"]
    assert mnemonics[10] == ["PRINT_NEWLINE"]
    assert mnemonics[11] == ["END"]


def test_declare_signature_matches_the_source():
    bf = BinFile.from_path(CORPUS_BIN / "DESCFILE.BAS")
    (module,) = decode_file(bf)
    declare = module.lines[3].instrs[0]
    assert declare.mnemonic == "DECLARE"
    sig = declare.signature
    assert bf.symbol(sig.ref).name == "IdentifyFile"
    got = [(bf.symbol(p.ref).name, p.suffix) for p in sig.params]
    assert got == [("FileName", "$"), ("Description", "$"), ("DescriptionLen", "%")]


def test_procedure_sections_open_with_their_own_signature(pair):
    _, bf, _ = pair
    for ds in decoded(bf):
        if ds.section.is_module:
            continue
        # A section may open with a hidden DEFTYPE record and with the
        # comment lines that sit above its SUB in the source.
        heads = [i for line in ds.lines for i in line.instrs
                 if i.mnemonic in ("SUB", "FUNCTION")]
        assert heads, f"{ds.section.name} has no signature"
        entry = bf.symbol(heads[0].signature.ref)
        assert entry is not None and entry.name.lower() == ds.section.name.lower()
        preceding = ds.lines[: next(n for n, l in enumerate(ds.lines) if heads[0] in l.instrs)]
        assert all(i.mnemonic in ("REM", "DEFTYPE")
                   for l in preceding for i in l.instrs), ds.section.name






def test_wide_indentation_is_decoded(pair):
    """Indents of 32 and more do not fit a header, and take an escape word."""
    _, bf, blocks = pair
    for ds in decode_file(bf):
        if ds.section.is_module or not ds.in_sync:
            continue
        src = blocks.get(ds.section.name.lower())
        if src is None or len(src) != len(ds.lines):
            continue
        for line, text in zip(ds.lines, src):
            if line.wide_indent is None or not text.rstrip():
                continue
            assert line.indent == len(text) - len(text.lstrip(" "))



def test_comments_reconstruct_exactly(pair):
    """A decoded comment must be byte-for-byte what QB wrote out as text.

    Repeated characters are stored run-length encoded as ``0x0d n c``, which
    is why TORUS's banner comments need expanding. QB's text writer trims
    trailing whitespace, so the stored text can be longer at the right.
    """
    name, bf, _ = pair
    raw = (CORPUS_TXT / name).read_text(encoding="latin-1")
    checked = 0
    for ds in decode_file(bf):
        if not ds.in_sync:
            continue
        for line in ds.lines:
            for ins in line.instrs:
                if ins.mnemonic != "REM" or ins.text is None:
                    continue
                checked += 1
                assert "'" + ins.text.rstrip() in raw, repr(ins.text)
    assert checked or name in ("JOHNNY.BAS", "STARDEF.BAS", "DEFTYPE.BAS",
                               "TRAIL1.BAS", "TRAIL2.BAS", "FILEIO.BAS")



def test_unparsed_lines_are_kept_as_source_text():
    """QB stores a line it cannot parse verbatim, under opcode 000a.

    TYPES.BAS provokes this on purpose: its TYPE member is called ``Name``,
    which is the QB ``NAME`` statement, so the member declaration is rejected
    and every line referring to it is stored as text instead of tokens.
    """
    bf = BinFile.from_path(CORPUS_BIN / "TYPES.BAS")
    (module,) = decode_file(bf)
    assert module.in_sync
    raw = {ins.text for line in module.lines for ins in line.instrs
           if ins.mnemonic == "TEXT_LINE"}
    assert "        Name AS STRING * 20" in raw
    assert "REDIM PRESERVE Dynamic(1 TO 4)" in raw
    source = (CORPUS_TXT / "TYPES.BAS").read_text(encoding="latin-1")
    for text in raw:
        assert text in source


def test_deftype_masks():
    """DEFTYPE holds a type code and a letter bitmask, A at the top bit."""
    bf = BinFile.from_path(CORPUS_BIN / "DEFTYPE.BAS")
    (module,) = decode_file(bf)
    got = [i.operands for line in module.lines for i in line.instrs
           if i.mnemonic == "DEFTYPE"]
    assert [ops[1] & 0x3F for ops in got] == [1, 2, 3, 4, 5]  # INT LNG SNG DBL STR
    # DEFINT A-C sets the top three bits of the A-P mask, and so on down.
    assert [ops[2] for ops in got] == [0xE000, 0x1C00, 0x0380, 0x0070, 0x000E]
    # TORUS says DEFINT A-Z: every letter, in both masks.
    torus = decode_file(BinFile.from_path(CORPUS_BIN / "TORUS.BAS"))[0]
    ops = next(i.operands for line in torus.lines for i in line.instrs
               if i.mnemonic == "DEFTYPE")
    assert ops[2] == 0xFFFF and ops[1] >> 6 == 0x3FF and ops[1] & 0x3F == 1



def test_fixed_length_string_member():
    """TYPE ... AS STRING * n keeps the length in the FIXED_STRING record."""
    bf = BinFile.from_path(CORPUS_BIN / "TYPES2.BAS")
    (module,) = decode_file(bf)
    fixed = [i for line in module.lines for i in line.instrs
             if i.mnemonic == "FIXED_STRING"]
    assert len(fixed) == 1
    assert fixed[0].operands[1] == 20  # AS STRING * 20


def test_jump_targets_are_only_filled_in_once_the_program_has_run():
    """A file loaded from text and saved has unresolved control flow.

    EDIT1QB.BAS is TRAIL1 after pressing F5; the two differ in exactly one
    word, the IF's jump target, which is zero until QB compiles the program.
    """
    from qb45detok.reader import BinFile as _BF
    ran = decode_file(_BF.from_path(ROOT / "corpus" / "synthetic" / "EDIT1QB.BAS"))
    not_ran = decode_file(BinFile.from_path(CORPUS_BIN / "TRAIL1.BAS"))
    def jumps(secs):
        return [i.operands[0] for ds in secs for l in ds.lines for i in l.instrs
                if i.mnemonic == "IF_THEN_BLOCK"]
    assert jumps(not_ran) == [0]
    assert jumps(ran) == [116]
