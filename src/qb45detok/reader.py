"""Structural reader for QuickBASIC 4.5 binary ``.BAS`` files.

Everything here was established by observation against ``corpus/``; see
``docs/format.md`` for the derivation and for what is still unknown.

File layout::

    0x00  28 bytes   fixed header (magic 0xFC, version/flag bytes)
    0x1c  82 bytes   symbol table: 41 hash buckets, then two trailer words
    0x72  ...        name table: chained entries, one per symbol
    ....             code sections: module text, then one per procedure

Symbol *references* — in the header, in bucket slots, in chain links and in
the token stream — are byte offsets measured from ``REF_BASE`` (0x1c), the
start of the bucket array, not from the start of the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MAGIC = 0xFC

#: Symbol references are offsets from here, not from the start of the file.
REF_BASE = 0x1C

#: Bytes 0x00..0x11 are byte-identical in every corpus file. Offset 0x12 is
#: 0x10 or 0x11, and offset 0x13 is the save-format flag described in README.
HEADER_SIGNATURE = bytes.fromhex("fc0001000c00810182010600010203040508")
_SIG_LEN = len(HEADER_SIGNATURE)

BUCKETS_OFF = 0x1C
BUCKET_COUNT = 41
BUCKETS_END = BUCKETS_OFF + BUCKET_COUNT * 2  # 0x6e
FREE_REF_OFF = 0x6E  # ref one past the last name-table entry
NAMES_OFF = 0x72  # == REF_BASE + 0x56
CODE_REF_OFF = 0x1A

#: Every code section is followed by a 16-byte trailer.
_TRAILER_LEN = 16
#: A procedure's token stream is introduced by ``01 00 <kind> <u16 length>``.
_PROC_PREAMBLE_LEN = 5

# Name-entry ``flags`` values seen in the corpus.
FLAG_NAME = 0x00  # variable, constant, external
FLAG_USER_TYPE = 0x08  # variable whose type is a user-defined TYPE
FLAG_NUM_LABEL_A = 0x02  # numeric line label, u16 payload
FLAG_LABEL = 0x04  # alphanumeric line label (GOTO/GOSUB target)
FLAG_NUM_LABEL = 0x06  # numeric line label, u16 payload
FLAG_SUB = 0x40  # a name used in statement position: a SUB, or a CALL target
_TEXT_FLAGS = (FLAG_NAME, FLAG_USER_TYPE, FLAG_LABEL, FLAG_SUB)
_NUM_FLAGS = (FLAG_NUM_LABEL_A, FLAG_NUM_LABEL)


class ParseError(Exception):
    """Raised when a file does not match the QB 4.5 binary layout."""


@dataclass(frozen=True)
class NameEntry:
    """One record in the name table."""

    ref: int  #: reference used by the token stream (offset from REF_BASE)
    offset: int  #: byte offset in the file
    link: int  #: ref of the next entry in this hash bucket, 0 to end
    flags: int
    raw: bytes  #: the record payload, ``flags``-dependent

    @property
    def is_text(self) -> bool:
        return self.flags in _TEXT_FLAGS

    @property
    def is_sub(self) -> bool:
        """Set for SUB names and CALL targets, never for a FUNCTION."""
        return bool(self.flags & FLAG_SUB)

    @property
    def is_label(self) -> bool:
        """A line label: either an identifier or a line number."""
        return self.flags in (FLAG_LABEL,) + _NUM_FLAGS

    @property
    def line_number(self) -> Optional[int]:
        """The numeric line label, for entries that hold one."""
        if self.flags not in _NUM_FLAGS:
            return None
        return int.from_bytes(self.raw, "little")

    @property
    def label(self) -> Optional[str]:
        """How this label is written in source, or ``None`` if not a label."""
        if self.flags == FLAG_LABEL:
            return self.raw.decode("latin-1")
        n = self.line_number
        return None if n is None else str(n)

    @property
    def name(self) -> Optional[str]:
        """The identifier, or ``None`` for non-name records (flags 2/4/6)."""
        return self.raw.decode("latin-1") if self.is_text else None

    def __str__(self) -> str:
        shown = self.name if self.is_text else (self.label or self.raw.hex(" "))
        return f"{self.ref:#06x} flags={self.flags:02x} {shown}"


@dataclass(frozen=True)
class Trailer:
    """The 16 bytes that close every code section.

    Four unidentified words, then the section's source line count, another
    unidentified word, and a kind word. The leading words are often all 0xff,
    which is why they once looked like a fixed signature; TORUS shows they are
    not, so sections are found by chaining declared lengths instead.
    """

    offset: int
    head: Tuple[int, int, int, int]  #: four u16s, purpose unknown
    line_count: int  #: u32, source lines in the section (SUB..END SUB inclusive)
    unknown_c: int  #: u16
    kind: int  #: u16, 0x0102 for the module text, 0x0c02 for a procedure


@dataclass
class Section:
    """A run of statement tokens: the module text, or one procedure body."""

    offset: int  #: file offset of the section (its length word or name header)
    body: bytes  #: the token stream, word-aligned from its first byte
    body_offset: int
    trailer: Optional[Trailer] = None
    name: Optional[str] = None  #: procedure name; ``None`` for module text
    declared_length: Optional[int] = None  #: length word preceding the stream
    kind_byte: Optional[int] = None  #: procedure preamble byte, 0x30 or 0x38

    @property
    def is_module(self) -> bool:
        return self.name is None

    def __str__(self) -> str:
        who = "<module>" if self.is_module else self.name
        return f"{who} @{self.offset:#07x} {len(self.body)} bytes"


def _u16(d: bytes, o: int) -> int:
    return int.from_bytes(d[o : o + 2], "little")


def _u32(d: bytes, o: int) -> int:
    return int.from_bytes(d[o : o + 4], "little")


@dataclass
class BinFile:
    """A parsed QuickBASIC 4.5 binary ``.BAS`` file."""

    data: bytes
    buckets: List[int] = field(default_factory=list)
    names: Dict[int, NameEntry] = field(default_factory=dict)
    sections: List[Section] = field(default_factory=list)
    tail: bytes = b""  #: bytes after the last trailer; purpose unknown
    tail_offset: int = 0

    # -- construction ----------------------------------------------------

    @classmethod
    def parse(cls, data: bytes) -> "BinFile":
        if not data:
            raise ParseError("empty file")
        if data[0] != MAGIC:
            raise ParseError(
                f"not a QuickBASIC 4.5 binary file: first byte is {data[0]:#04x}, expected {MAGIC:#04x}"
            )
        if len(data) < NAMES_OFF:
            raise ParseError(f"truncated: {len(data)} bytes, header alone needs {NAMES_OFF}")

        self = cls(data=data)
        self.buckets = [_u16(data, BUCKETS_OFF + 2 * i) for i in range(BUCKET_COUNT)]
        self._parse_names()
        self._parse_sections()
        return self

    @classmethod
    def from_path(cls, path) -> "BinFile":
        with open(path, "rb") as fh:
            return cls.parse(fh.read())

    def _parse_names(self) -> None:
        d = self.data
        end = self.free_ref + REF_BASE
        if not NAMES_OFF <= end <= len(d):
            raise ParseError(f"name table end {end:#x} outside file of {len(d):#x} bytes")
        off = NAMES_OFF
        while off < end:
            if off + 4 > end:
                raise ParseError(f"name entry header at {off:#x} runs past table end {end:#x}")
            link, flags, length = _u16(d, off), d[off + 2], d[off + 3]
            if off + 4 + length > end:
                raise ParseError(f"name entry at {off:#x} claims {length} bytes, past {end:#x}")
            self.names[off - REF_BASE] = NameEntry(
                ref=off - REF_BASE,
                offset=off,
                link=link,
                flags=flags,
                raw=d[off + 4 : off + 4 + length],
            )
            off += 4 + length

    def _parse_sections(self) -> None:
        """Walk the sections by their declared lengths.

        Each section states how long its token stream is, so the sections can
        be chained: stream, 16-byte trailer, next section, to the end of the
        file. An earlier version located them by scanning for the run of 0xff
        bytes that usually opens a trailer, but those bytes are not a reliable
        signature -- in TORUS most trailers do not have the run at all.
        """
        d = self.data
        cursor = self.code_ref + REF_BASE
        if not NAMES_OFF <= cursor < len(d):
            raise ParseError(f"code offset {cursor:#x} outside file of {len(d):#x} bytes")

        while cursor + 2 <= len(d):
            sec = self._read_module(cursor) if not self.sections else self._read_proc(cursor)
            end = sec.body_offset + len(sec.body)
            if end + _TRAILER_LEN > len(d):
                raise ParseError(
                    f"section at {cursor:#x} claims {sec.declared_length} bytes, "
                    f"which runs past the end of the file"
                )
            sec.trailer = self._read_trailer(end)
            self.sections.append(sec)
            cursor = end + _TRAILER_LEN

        self.tail_offset = cursor
        self.tail = d[cursor:]

    def _read_module(self, off: int) -> Section:
        """The module text: a length word, then the token stream."""
        declared = _u16(self.data, off)
        return Section(
            offset=off,
            body=self.data[off + 2 : off + 2 + declared],
            body_offset=off + 2,
            declared_length=declared,
        )

    def _read_proc(self, off: int) -> Section:
        """A procedure section.

        ``00 <namelen> 00 <name>`` names it, then a five-byte preamble
        ``01 00 <kind> <u16 length>`` introduces the token stream. Skipping
        the preamble matters for more than tidiness: the stream is a sequence
        of 16-bit words and the name is variable-length, so reading words from
        the wrong byte shifts every opcode.
        """
        d = self.data
        if off + 3 > len(d):
            raise ParseError(f"procedure header at {off:#x} runs past the end of the file")
        length = d[off + 1]
        name_end = off + 3 + length
        if name_end + _PROC_PREAMBLE_LEN > len(d):
            raise ParseError(f"procedure name at {off:#x} claims {length} bytes, past EOF")
        declared = _u16(d, name_end + 3)
        body_off = name_end + _PROC_PREAMBLE_LEN
        return Section(
            offset=off,
            body=d[body_off : body_off + declared],
            body_offset=body_off,
            name=d[off + 3 : name_end].decode("latin-1"),
            declared_length=declared,
            kind_byte=d[name_end + 2],
        )

    def _read_trailer(self, off: int) -> Trailer:
        d = self.data
        return Trailer(
            offset=off,
            head=(_u16(d, off), _u16(d, off + 2), _u16(d, off + 4), _u16(d, off + 6)),
            line_count=_u32(d, off + 8),
            unknown_c=_u16(d, off + 12),
            kind=_u16(d, off + 14),
        )

    # -- header accessors ------------------------------------------------

    @property
    def code_ref(self) -> int:
        return _u16(self.data, CODE_REF_OFF)

    @property
    def free_ref(self) -> int:
        """Ref one past the last name-table entry."""
        return _u16(self.data, FREE_REF_OFF)

    @property
    def has_standard_header(self) -> bool:
        return self.data[:_SIG_LEN] == HEADER_SIGNATURE

    # -- symbol lookup ---------------------------------------------------

    def symbol(self, ref: int) -> Optional[NameEntry]:
        """Resolve a token-stream symbol reference."""
        return self.names.get(ref)

    def chain(self, bucket: int) -> List[NameEntry]:
        """The name entries hashing to ``bucket``, in table order."""
        out: List[NameEntry] = []
        seen = set()
        ref = self.buckets[bucket]
        while ref:
            if ref in seen:
                raise ParseError(f"cycle in hash bucket {bucket} at ref {ref:#x}")
            seen.add(ref)
            entry = self.names.get(ref)
            if entry is None:
                raise ParseError(f"hash bucket {bucket} points at unknown ref {ref:#x}")
            out.append(entry)
            ref = entry.link
        return out

    def subs(self) -> Sequence[NameEntry]:
        return [e for e in self.names.values() if e.is_sub]
