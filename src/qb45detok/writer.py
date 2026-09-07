"""Assemble a QuickBASIC 4.5 binary ``.BAS`` file.

The layout is the one ``docs/format.md`` describes, written back out: a fixed
header, the hash bucket array, the name table, then one code section per
procedure with a trailer each.

Bucket placement does not have to match what QuickBASIC itself would compute.
QB rebuilds its own lookup when it loads a program, and it reads a file whose
names are all chained into a single bucket exactly as it reads a normal one.
That is worth knowing because the hash QB uses is not known.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .reader import (BUCKETS_OFF, BUCKET_COUNT, CODE_REF_OFF, FREE_REF_OFF,
                     NAMES_OFF, REF_BASE)

#: Bytes 0x00-0x1b. Everything here is constant across the corpus except the
#: code reference at 0x1a, which the writer fills in, and 0x12, 0x14, 0x15,
#: 0x18 and 0x19, which vary but which QB does not appear to need on load.
#: The words at 0x06 and 0x08 are constants, not derived from anything.
HEADER_TEMPLATE = bytes.fromhex(
    "fc00 0100 0c00 8101 8201 0600 0102 0304 0508 1010 ffff 2400 ffff 0000"
    .replace(" ", "")
)

#: The word at 0x70 is the reference of 0x6e itself and never varies.
SELF_REF = 0x52

#: QB leaves a fixed run of free space between the end of the name table and
#: the first code section, presumably so the table can grow without moving the
#: code. It is 259 bytes in all 49 corpus files and zero-filled in 48 of them.
NAME_SLACK = 259

#: Trailer kinds. The module is always 0x0102; procedures are usually 0x0c02
#: but 0x0802 and 0x0402 both occur, so the caller can override it.
KIND_MODULE = 0x0102
KIND_PROC = 0x0C02

#: A procedure's token stream is introduced by ``01 00 <kind> <u16 length>``.
DEFAULT_PROC_KIND = 0x38


@dataclass
class OutName:
    """One name-table entry to be written."""

    flags: int
    text: Optional[str] = None       #: for the text flags
    number: Optional[int] = None     #: for the numeric-label flags
    ref: int = 0                     #: filled in by the writer
    bucket: int = 0                  #: which hash chain to put it in

    @property
    def payload(self) -> bytes:
        if self.number is not None:
            return struct.pack("<H", self.number)
        return (self.text or "").encode("latin-1")

    def encoded(self, link: int) -> bytes:
        body = self.payload
        return struct.pack("<HBB", link, self.flags, len(body)) + body


@dataclass
class OutSection:
    """One code section: the token stream plus what its trailer records."""

    body: bytes
    line_count: int
    name: Optional[str] = None       #: None for the module text
    kind_byte: int = DEFAULT_PROC_KIND
    proc_kind: int = 1               #: 1 for a SUB, 2 for a FUNCTION
    return_type: int = 0             #: a FUNCTION's return type, else 0
    trailer_kind: Optional[int] = None
    head: Sequence[int] = (0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
    unknown_c: int = 0

    @property
    def is_module(self) -> bool:
        return self.name is None

    def encoded(self) -> bytes:
        if self.is_module:
            out = struct.pack("<H", len(self.body)) + self.body
        else:
            raw = self.name.encode("latin-1")
            out = (bytes([0x00, len(raw), 0x00]) + raw
                   + bytes([self.proc_kind, self.return_type, self.kind_byte])
                   + struct.pack("<H", len(self.body)) + self.body)
        kind = KIND_MODULE if self.is_module else KIND_PROC
        if self.trailer_kind is not None:
            kind = self.trailer_kind
        return out + struct.pack("<HHHHIHH", *self.head, self.line_count,
                                 self.unknown_c, kind)


class ImageWriter:
    """Builds the byte image from a name table and a list of sections."""

    def __init__(self) -> None:
        self.names: List[OutName] = []
        self.sections: List[OutSection] = []
        self._by_key: Dict[tuple, OutName] = {}
        #: Bytes 0x00-0x1b. Callers may patch the offsets whose meaning is not
        #: known (0x12, 0x14, 0x15, 0x18, 0x19) and the editor-provenance byte
        #: at 0x13; the writer fills in the code reference at 0x1a.
        self.header = bytearray(HEADER_TEMPLATE)
        #: The free space between the name table and the first section. Zero
        #: fill matches 48 of the 49 corpus files.
        self.slack = bytes(NAME_SLACK)

    # -- names -----------------------------------------------------------

    def add_name(self, flags: int, text: Optional[str] = None,
                 number: Optional[int] = None, bucket: Optional[int] = None) -> OutName:
        """Intern a name-table entry, returning the existing one if present.

        ``bucket`` picks the hash chain. Leaving it out puts everything in
        chain 0, which QB accepts; pass one only when reproducing a file whose
        original placement is known.
        """
        key = (flags, text, number)
        found = self._by_key.get(key)
        if found is None:
            found = OutName(flags=flags, text=text, number=number,
                            bucket=0 if bucket is None else bucket)
            self._by_key[key] = found
            self.names.append(found)
        return found

    def _layout_names(self) -> bytes:
        """Assign a reference to every entry and chain each into its bucket.

        Entries are written in the order they were added; the chains thread
        through that layout rather than reordering it.
        """
        ref = NAMES_OFF - REF_BASE
        for entry in self.names:
            entry.ref = ref
            ref += 4 + len(entry.payload)
        self.free_ref = ref
        self.heads = [0] * BUCKET_COUNT
        nxt_of: Dict[int, int] = {}
        prev: Dict[int, OutName] = {}
        for entry in self.names:
            b = entry.bucket
            if b in prev:
                nxt_of[id(prev[b])] = entry.ref
            else:
                self.heads[b] = entry.ref
            prev[b] = entry
        out = bytearray()
        for entry in self.names:
            out += entry.encoded(nxt_of.get(id(entry), 0))
        return bytes(out)

    # -- the whole file --------------------------------------------------

    def build(self) -> bytes:
        names = self._layout_names()
        buckets = bytearray(BUCKET_COUNT * 2)
        for i, head in enumerate(self.heads):
            buckets[2 * i:2 * i + 2] = struct.pack("<H", head)
        body = bytearray()
        for section in self.sections:
            body += section.encoded()
        code_ref = NAMES_OFF + len(names) + len(self.slack) - REF_BASE

        out = bytearray(self.header)
        out[CODE_REF_OFF:CODE_REF_OFF + 2] = struct.pack("<H", code_ref)
        out += buckets
        out[FREE_REF_OFF:FREE_REF_OFF + 2] = struct.pack("<H", self.free_ref)
        out[FREE_REF_OFF + 2:FREE_REF_OFF + 4] = struct.pack("<H", SELF_REF)
        out += names
        out += self.slack
        out += body
        return bytes(out)
