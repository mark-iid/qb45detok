"""Structural tests, driven by the ground-truth corpus.

The ASCII side of each pair was written by QB 4.5 itself, so anything these
tests derive from ``corpus/txt`` is authoritative.
"""

import re

import pytest

from conftest import requires_corpus,   CORPUS_BIN, CORPUS_TXT, NAMES, text_blocks
from qb45detok import BinFile, ParseError
from qb45detok import tokens
from qb45detok.decode import decode_file, parse_signature
from qb45detok.reader import MAGIC, NAMES_OFF, REF_BASE

pytestmark = requires_corpus

PROC_START = re.compile(r"\s*(?:SUB|FUNCTION)\s+([A-Za-z0-9_.]+)", re.I)
PROC_END = re.compile(r"\s*END\s+(?:SUB|FUNCTION)\b", re.I)


@pytest.fixture(scope="module", params=NAMES)
def pair(request):
    """A parsed binary alongside the text QB 4.5 produced for it."""
    name = request.param
    binary = BinFile.from_path(CORPUS_BIN / name)
    text = (CORPUS_TXT / name).read_text(encoding="latin-1")
    return name, binary, text


def procedures_in_text(text):
    """``{lowercased name: line count}`` for each SUB/FUNCTION block."""
    lines = text.splitlines()
    out, i = {}, 0
    while i < len(lines):
        m = PROC_START.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not PROC_END.match(lines[j]):
            j += 1
        out[m.group(1).lower()] = j - i + 1  # SUB..END SUB inclusive
        i = j + 1
    return out


def test_corpus_is_present():
    assert len(NAMES) == 31, "expected thirty-one matched pairs in corpus/"


def test_parses(pair):
    _, bf, _ = pair
    assert bf.data[0] == MAGIC
    assert bf.has_standard_header


def test_name_table_walk_lands_exactly_on_the_end(pair):
    _, bf, _ = pair
    end = bf.free_ref + REF_BASE
    consumed = sum(4 + len(e.raw) for e in bf.names.values())
    assert NAMES_OFF + consumed == end


def test_hash_buckets_reach_every_name_exactly_once(pair):
    _, bf, _ = pair
    seen = [e.ref for i in range(len(bf.buckets)) for e in bf.chain(i)]
    assert sorted(seen) == sorted(bf.names)


def test_symbol_refs_resolve_back_to_the_right_entry(pair):
    _, bf, _ = pair
    for ref, entry in bf.names.items():
        assert bf.symbol(ref) is entry
        assert entry.offset == ref + REF_BASE


def test_chain_links_point_at_real_entries(pair):
    _, bf, _ = pair
    for entry in bf.names.values():
        assert entry.link == 0 or entry.link in bf.names


def test_module_section_matches_its_declared_length(pair):
    _, bf, _ = pair
    module = bf.sections[0]
    assert module.is_module
    assert module.declared_length == len(module.body)


def test_sections_cover_the_file_without_gaps(pair):
    _, bf, _ = pair
    cursor = bf.code_ref + REF_BASE
    for sec in bf.sections:
        assert sec.offset == cursor
        assert sec.body_offset + len(sec.body) == sec.trailer.offset
        cursor = sec.trailer.offset + 16
    assert cursor == len(bf.data), "trailing bytes after the last section"
    assert bf.tail == b""


def test_procedure_sections_match_the_subs_in_the_text(pair):
    _, bf, text = pair
    got = sorted(s.name.lower() for s in bf.sections[1:])
    assert got == sorted(procedures_in_text(text))


def test_procedure_line_counts_match_the_text(pair):
    """``line_count`` counts a hidden DEFTYPE record that QB does not print.

    When the module carries a ``DEFINT``-style statement, QB copies the type
    defaults to the head of every procedure. That record occupies a line in
    the stream and is counted, but never appears in the text output, so the
    procedure reads one line longer in the binary than on paper.
    """
    _, bf, text = pair
    expected = {k: len(v) for k, v in text_blocks(text).items() if k is not None}
    for sec, ds in zip(bf.sections[1:], decode_file(bf)[1:]):
        hidden = 1 if ds.lines and ds.lines[0].instrs and \
            ds.lines[0].instrs[0].mnemonic == "DEFTYPE" else 0
        assert sec.trailer.line_count - hidden == expected[sec.name.lower()], sec.name


def test_section_kind_word_separates_module_from_procedures(pair):
    """The module text is 0x0102; a procedure is something else.

    Procedures are almost always 0x0c02. SYSTEM.BAS has one at 0x0402 and
    nothing else in the corpus does, so the rest of that word is not pinned
    down yet.
    """
    _, bf, _ = pair
    assert bf.sections[0].trailer.kind == 0x0102
    assert all(s.trailer.kind != 0x0102 for s in bf.sections[1:])


def test_procedure_names_are_also_in_the_name_table(pair):
    _, bf, _ = pair
    table = {e.name.lower() for e in bf.names.values() if e.is_text}
    for sec in bf.sections[1:]:
        assert sec.name.lower() in table


def _signature_for(bf, name):
    """The DECLARE/SUB/FUNCTION signature naming ``name``, if the file has one."""
    for ds in decode_file(bf):
        for line in ds.lines:
            for ins in line.instrs:
                if ins.code not in tokens.SIGNATURE_OPS or not ins.payload:
                    continue
                sig = parse_signature(ins.payload)
                entry = bf.symbol(sig.ref) if sig else None
                if entry and entry.name and entry.name.lower() == name.lower():
                    return sig
    return None


def test_flag_0x40_marks_subs_not_functions(pair):
    """Flag 0x40 is set on SUB names and never on a FUNCTION name.

    Across the corpus this holds for every declaration, which is what tells
    the two apart in the name table without reading a signature.
    """
    _, bf, _ = pair
    flagged = {e.name.lower() for e in bf.subs()}
    for sec in bf.sections[1:]:
        sig = _signature_for(bf, sec.name)
        if sig is None:
            continue
        assert (sec.name.lower() in flagged) is not sig.is_function




