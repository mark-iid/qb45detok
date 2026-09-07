"""Split a code section's token stream into lines.

This is the layer below detokenization: it does not try to reconstruct source,
only to walk the stream correctly. Walking it correctly needs one fact per
opcode -- how many operand words follow -- which is what ``tokens.OPS``
records. Where an opcode is unknown the walk assumes no operands and flags it,
so a wrong guess shows up as a line-count mismatch against the section trailer
rather than as silently plausible output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import tokens
from .reader import BinFile, Section


@dataclass
class Instr:
    """One opcode and its operand words, at a known place in the section."""

    offset: int  #: byte offset within the section body
    code: int
    operands: List[int] = field(default_factory=list)
    payload: bytes = b""  #: for length-prefixed opcodes
    op: Optional[tokens.Op] = None

    @property
    def known(self) -> bool:
        return self.op is not None

    @property
    def mnemonic(self) -> str:
        return self.op.mnemonic if self.op else f"?{self.code:04x}"

    @property
    def text(self) -> Optional[str]:
        """Source text carried by the opcode, for comments and literals.

        ``None`` when the payload is not source text -- the comment opcode is
        reused for a binary payload on ``DIM ... AS <type>`` lines.
        """
        if self.code in tokens.RAW_TEXT_OPS:
            return tokens.expand_runs(self.payload).decode("latin-1")
        if self.code in tokens.TEXT_PAYLOAD_OPS:
            body = self.payload[2:].rstrip(b"\x00")
            if b"\x00" in body:
                return None
            text = tokens.expand_runs(body).decode("latin-1")
            if self.code == tokens.REM and text.endswith(" "):
                # The payload is padded to an even length with a space, and
                # QB does not write that pad back out.
                text = text[:-1]
            return text
        if self.op is not None and self.op.form == "literal" and self.payload:
            return tokens.expand_runs(self.payload).decode("latin-1")
        return None

    @property
    def comment_column(self) -> Optional[int]:
        """The column the apostrophe sits at, for a comment."""
        if self.code not in tokens.TEXT_PAYLOAD_OPS or len(self.payload) < 2:
            return None
        return int.from_bytes(self.payload[:2], "little")

    @property
    def signature(self) -> Optional["Signature"]:
        """The procedure signature carried by DECLARE, SUB and FUNCTION."""
        if self.code not in tokens.SIGNATURE_OPS:
            return None
        return parse_signature(self.payload)

    def __str__(self) -> str:
        parts = [self.mnemonic]
        parts += [f"{w:#06x}" for w in self.operands]
        if self.payload:
            parts.append(repr(self.payload.decode("latin-1")))
        return " ".join(parts)


@dataclass(frozen=True)
class Param:
    ref: int
    type_code: int
    #: Bit field describing how the parameter was written:
    #: 0x0200 a type suffix, 0x0400 an array, 0x1000 BYVAL, 0x2000 an AS clause.
    mode: int = 0x0200

    @property
    def is_array(self) -> bool:
        return bool(self.mode & 0x0400)

    @property
    def by_value(self) -> bool:
        return bool(self.mode & 0x1000)

    @property
    def has_as_clause(self) -> bool:
        return bool(self.mode & 0x2000)

    @property
    def has_suffix(self) -> bool:
        return bool(self.mode & 0x0200)

    @property
    def suffix(self) -> str:
        return tokens.PARAM_TYPES.get(self.type_code, "")


@dataclass(frozen=True)
class Signature:
    """A procedure name plus its parameter list, as DECLARE/SUB/FUNCTION store it."""

    ref: int
    params: List[Param] = field(default_factory=list)
    kind: int = 0x0100  #: high byte SUB/FUNCTION, low byte the return type
    listed: bool = True  #: False when the source wrote no parentheses at all

    @property
    def return_suffix(self) -> str:
        """The type suffix written on a FUNCTION's own name, if any."""
        low = self.kind & 0xFF
        return tokens.PARAM_TYPES.get(low & 0x7F, "") if low & 0x80 else ""


def parse_signature(payload: bytes) -> Optional[Signature]:
    """Unpack ``[ref][0x0100][count]`` then ``[ref][0x0200][type]`` per parameter."""
    if len(payload) < 6:
        return None
    word = lambda o: int.from_bytes(payload[o : o + 2], "little")
    count = word(4)
    if count == 0xFFFF:
        # No parameter list at all, as in "DECLARE FUNCTION Top10&", which is
        # different from an empty one written as "()".
        return Signature(ref=word(0), params=[], kind=word(2), listed=False)
    if len(payload) < 6 + 6 * count:
        return None
    params = [
        Param(ref=word(6 + 6 * i), mode=word(8 + 6 * i), type_code=word(10 + 6 * i))
        for i in range(count)
    ]
    return Signature(ref=word(0), params=params, kind=word(2), listed=True)


@dataclass
class Line:
    """One source line: a label reference plus the instructions on it."""

    header: int  #: the raw header word
    instrs: List[Instr] = field(default_factory=list)
    offset: int = 0
    label_ref: Optional[int] = None  #: name-table ref of this line's label
    label_offset: Optional[int] = None
    wide_indent: Optional[int] = None  #: indentation too large for the header

    @property
    def indent(self) -> int:
        """Leading spaces on the source line."""
        if self.wide_indent is not None:
            return tokens.escaped_indent(self.wide_indent)
        return tokens.indent_of(self.header)

    @property
    def labelled(self) -> bool:
        return self.label_ref is not None

    @property
    def unknown(self) -> List[Instr]:
        return [i for i in self.instrs if not i.known]


@dataclass
class DecodedSection:
    section: Section
    lines: List[Line] = field(default_factory=list)
    truncated: bool = False  #: stream ran out before the end marker

    @property
    def expected_lines(self) -> int:
        return self.section.trailer.line_count

    @property
    def in_sync(self) -> bool:
        return not self.truncated and len(self.lines) == self.expected_lines

    @property
    def unknown_codes(self) -> List[int]:
        return [i.code for line in self.lines for i in line.unknown]


def _words(body: bytes) -> Sequence[int]:
    return [body[i] | (body[i + 1] << 8) for i in range(0, len(body) - 1, 2)]


def decode_section(bf: BinFile, section: Section) -> DecodedSection:
    body = section.body
    w = _words(body)
    out = DecodedSection(section=section)
    i = 0
    n = len(w)

    while i < n:
        if w[i] == tokens.END_OF_SECTION[0] and i + 1 < n and w[i + 1] == tokens.END_OF_SECTION[1]:
            break
        line = Line(header=w[i], offset=i * 2)
        i += 1
        if line.header & tokens.LINE_HAS_INDENT:
            if i >= n:
                out.truncated = True
                out.lines.append(line)
                break
            line.wide_indent = w[i]
            i += 1
        if line.header & tokens.LINE_HAS_LABEL:
            if i + 1 >= n:
                out.truncated = True
                out.lines.append(line)
                break
            line.label_offset, line.label_ref = w[i], w[i + 1]
            i += 2
        while i < n:
            code = w[i]
            if tokens.is_line_header(code):
                break
            if code == tokens.END_OF_SECTION[0] and i + 1 < n and w[i + 1] == tokens.END_OF_SECTION[1]:
                break
            op = tokens.lookup(code)
            instr = Instr(offset=i * 2, code=code, op=op)
            i += 1
            for kind in op.operands if op else ():
                if kind in ("u32", "f32", "f64"):
                    span = 4 if kind == "f64" else 2
                    if i + span > n:
                        out.truncated = True
                        break
                    instr.operands.extend(w[i : i + span])
                    i += span
                elif kind == "str":
                    if i >= n:
                        out.truncated = True
                        break
                    length = w[i]
                    i += 1
                    start = i * 2
                    instr.payload = body[start : start + length]
                    i += (length + 1) // 2  # payloads are padded to a word
                else:
                    if i >= n:
                        out.truncated = True
                        break
                    instr.operands.append(w[i])
                    i += 1
            line.instrs.append(instr)
            if out.truncated:
                break
        out.lines.append(line)
        if out.truncated:
            break

    return out


def decode_file(bf: BinFile) -> List[DecodedSection]:
    return [decode_section(bf, s) for s in bf.sections]
