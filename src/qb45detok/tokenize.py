"""Turn QuickBASIC source back into the binary QuickBASIC saves.

This is the other direction from the rest of the package: `render` reads a
tokenized file and writes text, and this reads that text and writes the file.
Running one after the other and getting the original bytes back is the
strongest check on the format there is, since it exercises every field, not
just the ones a reader happens to look at.

The work splits three ways. `lex` and `parse` turn a line into the sequence
of instructions QB would have stored, `assemble` picks the opcode for each
and interns the names, and this module puts the lines into sections, gives
each one its header, and hands the result to `writer`.
"""

from __future__ import annotations

import re
import struct
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import tokens
from .assemble import FLAG_SUB, Assembler
from .expr import Emit
from .expr import ParseError
from .lex import LexError
from .parse import parse_line
from .writer import OutSection

#: A leading label: a name followed by a colon, or a bare line number.
_LABEL = re.compile(r"^([A-Za-z.][\w.]*):|^(\d+)\b")

#: The header of a procedure, which starts a section of its own.
_PROC = re.compile(r"^\s*(SUB|FUNCTION)\s+([A-Za-z][\w.]*[%&!#$]?)", re.I)
_END_PROC = re.compile(r"^\s*END\s+(SUB|FUNCTION)\b", re.I)

#: The name of a user-defined type, so that "Rec.Field" can be told from a
#: variable whose name merely has a period in it.
_TYPE = re.compile(r"^\s*TYPE\s+([A-Za-z][\w.]*)", re.I)
_AS = re.compile(r"([A-Za-z][\w.]*)\s*(?:\([^)]*\))?\s+AS\s+([A-Za-z][\w.]*)", re.I)


#: A line that is only a comment, which is what can be pulled into the
#: section below it.
_COMMENT = re.compile(r"^\s*('|REM\b)", re.I)

#: A DEFINT/DEFSNG line, which a text export writes between two sections
#: whenever the default types change from one to the next.
_DEFTYPE = re.compile(r"^\s*DEF(INT|LNG|SNG|DBL|STR)\b", re.I)

#: A metacommand comment, which is not an ordinary comment: it belongs to
#: the section it was written in.
_META = re.compile(r"^\s*(?:'|REM)\s*\$(?:STATIC|DYNAMIC)\b", re.I)

#: The metacommand a text export writes after a module that used $DYNAMIC.
_STATIC_META = re.compile(r"^\s*(?:'|REM)\s*\$STATIC\s*$", re.I)


class Source:
    """One section's worth of source, and the procedure it declares."""

    def __init__(self, lines: Optional[List[str]] = None) -> None:
        self.lines: List[str] = lines if lines is not None else []
        #: A DEFtype line written above the section, which records the
        #: defaults it inherited rather than a statement of its own.
        self.inherited: Optional[str] = None

    @property
    def header(self) -> Optional[str]:
        """The SUB or FUNCTION line, if this section declares one."""
        for line in self.lines:
            if _PROC.match(line):
                return line
        return None


def split_sections(text: str) -> List[Source]:
    """Split a program into its module text and one part per procedure.

    QB keeps each SUB and FUNCTION in a section of its own and the rest in
    the module section, in the order they were written. A comment block
    written directly above a procedure goes with the procedure: that is
    where QB puts it, and there is nothing in a saved file that could say
    otherwise, since section membership is all a comment has.
    """
    module = Source()
    out = [module]
    current = module
    for line in text.splitlines():
        if _PROC.match(line) and (current is module or _closed(current)):
            lead: List[str] = []
            while (current.lines and _COMMENT.match(current.lines[-1])
                   and not _META.match(current.lines[-1])):
                lead.insert(0, current.lines.pop())
            inherited = None
            trailing = 0
            while (trailing < len(current.lines)
                   and not current.lines[-1 - trailing].strip()):
                trailing += 1
            if (trailing < len(current.lines)
                    and _DEFTYPE.match(current.lines[-1 - trailing])):
                inherited = current.lines.pop(len(current.lines) - 1 - trailing)
            current = Source(lead)
            current.inherited = inherited
            out.append(current)
        current.lines.append(line)
    for part in out:
        blanks = 0
        while blanks < len(part.lines) and not part.lines[-1 - blanks].strip():
            blanks += 1
        if blanks == 1:
            # Saving as text puts one blank line between sections, unless the
            # section already ends with one. A single trailing blank is
            # therefore that separator rather than part of the section.
            part.lines.pop()
        if part is module and len(out) > 1:
            # The same goes for the "REM $STATIC" that closes a module which
            # turned on $DYNAMIC.
            at = len(part.lines) - 1
            while at >= 0 and not part.lines[at].strip():
                at -= 1
            if at >= 0 and _STATIC_META.match(part.lines[at]):
                del part.lines[at:]
    return out


def _closed(part: Source) -> bool:
    """Whether a section has had its END SUB, so a new one may start."""
    return any(_END_PROC.match(line) for line in part.lines)


def record_variables(text: str) -> Set[str]:
    """Variables declared as a user-defined type, which have fields.

    A period is an ordinary name character in BASIC, so nothing local to a
    line says whether ``Disk.Sectors`` is one name or a field of ``Disk``.
    Only the declarations say, which is why this reads the whole program.
    """
    types = {m.group(1).lower() for m in
             (_TYPE.match(line) for line in text.splitlines()) if m}
    if not types:
        return set()
    names: Set[str] = set()
    for line in text.splitlines():
        for m in _AS.finditer(line):
            if m.group(2).lower() in types:
                names.add(m.group(1).lower())
    return names


def indent_of(line: str) -> int:
    """The column the first non-blank character sits in, tabs expanded."""
    column = 0
    for ch in line:
        if ch == " ":
            column += 1
        elif ch == "\t":
            column += 8 - column % 8
        else:
            break
    return column


class Tokenizer:
    """Assembles one program: its name table and one section per procedure."""

    #: Set in header byte 0x13 when the file indents with tabs.
    TABS = 0x80

    def __init__(self, text: str) -> None:
        self.text = text
        self.records = record_variables(text)
        self.asm = Assembler()
        self._types = [self.DEFAULT_TYPE] * 26
        if any(line.startswith("\t") for line in text.splitlines()):
            self.asm.writer.header[0x13] |= self.TABS

    def build(self) -> bytes:
        for part in split_sections(self.text):
            self.asm.writer.sections.append(self._section(part))
        return self.asm.writer.build()

    #: The default type of each letter, which every procedure records at
    #: its head. Single precision is the language default.
    DEFAULT_TYPE = 3

    def _record(self) -> Optional[Emit]:
        """The DEFtype record for the state now in force.

        QB stores one type code and the letters that have it, so a state
        mixing two non-default types cannot be written exactly; the one
        covering the most letters wins.
        """
        counts: Dict[int, List[int]] = {}
        for i, code in enumerate(self._types):
            if code != self.DEFAULT_TYPE:
                counts.setdefault(code, []).append(i)
        if not counts:
            return None
        code = max(counts, key=lambda c: len(counts[c]))
        return Emit("DEFTYPE", (code, tuple(counts[code])))

    def _apply(self, emit: Emit) -> None:
        code, letters = emit.operands
        for i in letters:
            self._types[i] = code

    # -- one section -----------------------------------------------------

    def _section(self, part: Source) -> OutSection:
        words: List[int] = []
        count = 0
        head = part.header
        if part.inherited is not None:
            # A DEFtype line above a procedure is a change of defaults, not
            # a statement: it applies to what the procedure inherits.
            for statement in parse_line(part.inherited.strip()):
                for emit in statement.emits:
                    if emit.mnemonic == "DEFTYPE":
                        self._apply(emit)
        record = self._record()
        if head is not None and record is not None:
            # A procedure records the defaults it inherited. The line carries
            # nothing else, and is not written back out as text.
            words += [0] + self.asm.words_for([record])
            count += 1
        for line in part.lines:
            words += self._line(line)
            count += 1
        words += list(tokens.END_OF_SECTION)
        body = struct.pack(f"<{len(words)}H", *words)
        if head is None:
            return OutSection(body=body, line_count=count)
        keyword, name = _PROC.match(head).group(1), _PROC.match(head).group(2)
        kind_byte = 0xB8 if re.search(r"\bSTATIC\s*$", head, re.I) else 0x38
        suffix = name[-1] if name[-1] in "%&!#$" else ""
        self.asm.name_ref(name[:len(name) - len(suffix)] if suffix else name,
                          FLAG_SUB if keyword.upper() == "SUB" else 0)
        return OutSection(
            body=body, line_count=count,
            name=name[:len(name) - len(suffix)] if suffix else name,
            kind_byte=kind_byte,
            proc_kind=1 if keyword.upper() == "SUB" else 2,
            return_type=_RETURN_TYPE.get(suffix, 0))

    def _line(self, line: str) -> List[int]:
        """One source line: its header word, any label, then its opcodes."""
        body = line.strip("\r\n").lstrip(" \t")
        indent = indent_of(line)
        column = indent
        label = None
        match = _LABEL.match(body)
        if match:
            label = match.group(1) or match.group(2)
            rest = body[match.end():]
            # A labelled line starts at column 0, and what the header records
            # is the gap between the label and the statement after it.
            indent = len(rest) - len(rest.lstrip(": "))
            column = match.end() + indent
            body = rest.lstrip(": ")
        header = (indent << tokens.INDENT_SHIFT) if indent < 32 else 0
        out: List[int] = []
        if label is not None:
            header |= tokens.LINE_HAS_LABEL
            out += [0, self.asm.label_ref(label).ref]
        if indent >= 32:
            header |= tokens.LINE_HAS_INDENT
            out.append(indent)
        words = [header] + out
        if body:
            try:
                dotted = False
                for statement in parse_line(body, self.records, column):
                    for emit in statement.emits:
                        if emit.mnemonic == "DEFTYPE":
                            self._apply(emit)
                        dotted = dotted or _has_period(emit)
                    words += self.asm.words_for(statement.emits)
                if dotted:
                    # QB marks a line that names an identifier with a period
                    # in it. The marker carries no text and comes last.
                    words += self.asm.words_for([Emit("DOTTED_NAME")])
            except (ParseError, LexError, ValueError, IndexError, KeyError):
                # QB keeps a line it cannot read as text, so that editing a
                # file with a typo in it does not lose the typo.
                words = words[:len(words) - len(out)] + out + _text_line(body)
        return words


def _has_period(emit: Emit) -> bool:
    """Whether an instruction names an identifier containing a period."""
    return any(isinstance(v, str) and "." in v and not v.startswith(".")
               for v in emit.operands)


def _text_line(body: str) -> List[int]:
    """A line QB could not read, kept as text: a lead word, then the text.

    The length word counts the text, not the byte the payload is padded with
    to reach an even length, which is why that is added afterwards.
    """
    raw = struct.pack("<H", 0) + body.encode("latin-1", "replace")
    length = len(raw)
    if len(raw) % 2:
        raw += b"\x00"
    return ([0x000A, length]
            + list(struct.unpack(f"<{len(raw) // 2}H", raw)))


#: A FUNCTION's return type, as its section header records it.
_RETURN_TYPE = {"%": 1, "&": 2, "!": 3, "#": 4, "$": 5}


def tokenize(text: str) -> bytes:
    """The binary QuickBASIC would have saved for this source."""
    return Tokenizer(text).build()
