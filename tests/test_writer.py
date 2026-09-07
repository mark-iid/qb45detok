"""The writer has to reproduce a file it was given, field for field.

Reading a format lets you skip what you do not understand; writing it does
not. Rebuilding every corpus file from its own decoded structure and getting
the same bytes back is the strongest check on the layout there is.
"""

import pytest

from conftest import requires_corpus, CORPUS_BIN, NAMES
from qb45detok import BinFile
from qb45detok.reader import BUCKETS_OFF, BUCKETS_END, NAMES_OFF, REF_BASE
from qb45detok.writer import (HEADER_TEMPLATE, ImageWriter, NAME_SLACK,
                              OutSection, SELF_REF)

#: Header offsets whose meaning is not known, so the rebuild copies them.
OPAQUE_HEADER = (0x12, 0x13, 0x14, 0x15, 0x18, 0x19)


def rebuild(bf: BinFile) -> bytes:
    """Re-emit a parsed file from the pieces the reader exposes."""
    w = ImageWriter()
    for off in OPAQUE_HEADER:
        w.header[off] = bf.data[off]
    home = {e.ref: b for b in range(41) for e in bf.chain(b)}
    entries = sorted((e for b in range(41) for e in bf.chain(b)),
                     key=lambda e: e.ref)
    for e in entries:
        w.add_name(e.flags,
                   text=(e.name or e.label) if e.line_number is None else None,
                   number=e.line_number, bucket=home[e.ref])
    end = NAMES_OFF + bf.free_ref - (NAMES_OFF - REF_BASE)
    w.slack = bf.data[end:bf.sections[0].offset]
    for s in bf.sections:
        w.sections.append(OutSection(
            body=s.body, line_count=s.trailer.line_count, name=s.name,
            kind_byte=s.kind_byte if s.kind_byte is not None else 0x38,
            proc_kind=s.proc_kind if s.proc_kind is not None else 1,
            return_type=s.return_type if s.return_type is not None else 0,
            trailer_kind=s.trailer.kind,
            head=s.trailer.head, unknown_c=s.trailer.unknown_c))
    return w.build()


@requires_corpus
@pytest.mark.parametrize("name", NAMES)
def test_rebuild_is_byte_identical(name):
    original = (CORPUS_BIN / name).read_bytes()
    assert rebuild(BinFile.parse(original)) == original


def test_header_template_is_the_documented_length():
    assert len(HEADER_TEMPLATE) == BUCKETS_OFF
    assert HEADER_TEMPLATE[0] == 0xFC


def test_empty_image_has_the_expected_shape():
    w = ImageWriter()
    w.sections.append(OutSection(body=b"", line_count=0))
    out = w.build()
    assert out[:2] == b"\xfc\x00"
    # No names, so the table is empty and the slack sits straight after it.
    bf = BinFile.parse(out)
    assert bf.free_ref == NAMES_OFF - REF_BASE
    assert bf.sections[0].offset == NAMES_OFF + NAME_SLACK
    assert int.from_bytes(out[BUCKETS_END + 2:BUCKETS_END + 4], "little") == SELF_REF


def test_names_are_chained_into_their_buckets():
    w = ImageWriter()
    w.add_name(0x00, text="Alpha", bucket=3)
    w.add_name(0x00, text="Beta", bucket=3)
    w.add_name(0x00, text="Gamma", bucket=7)
    w.sections.append(OutSection(body=b"", line_count=0))
    bf = BinFile.parse(w.build())
    assert [e.name for e in bf.chain(3)] == ["Alpha", "Beta"]
    assert [e.name for e in bf.chain(7)] == ["Gamma"]
    assert bf.chain(0) == []


def test_add_name_interns_repeats():
    w = ImageWriter()
    first = w.add_name(0x00, text="Same")
    assert w.add_name(0x00, text="Same") is first
    assert len(w.names) == 1
