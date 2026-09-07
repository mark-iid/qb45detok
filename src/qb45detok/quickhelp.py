"""Read a Microsoft QuickHelp ``.HLP`` database.

This is the format ``HELPMAKE`` produced and ``QB.EXE`` read: the QuickBASIC
4.5 language reference lives in ``QB45ADVR.HLP`` and ``QB45QCK.HLP``, and
nothing modern reads it. The layout is worked out in ``docs/quickhelp.md``.

A file is a fixed header, a topic offset table, the context names and the map
from each name to a topic, a keyword dictionary, a Huffman tree, and then the
topics themselves. Each topic is Huffman-coded; the bytes that come out are
compressed again against the keyword dictionary and carry the display layout
as control codes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

MAGIC = b"LN"

#: Header layout: magic, version, flags, then counts and the section offsets.
_OFFSETS_AT = 0x22
_NAME_AT = 0x10

#: Bytes 0x10-0x17 are a two-byte reference into the keyword dictionary. The
#: low two bits of the first byte are the high bits of the index; 0x14 and up
#: mean the same word followed by a space.
KEYWORD_LO, KEYWORD_HI = 0x10, 0x17
KEYWORD_SPACE = 0x14

#: 0x18 is a run of spaces, 0x1a sets the display attribute. Both take a byte.
RUN_OF_SPACES = 0x18
SET_ATTRIBUTE = 0x1A

#: A hyperlink's target follows this byte as a NUL-terminated context
#: reference such as "QB45ADVR.HLP!.zpu". It is not display text.
LINK_TARGET = 0xFF

#: Record markers. 0x02 starts a display line whose length byte follows,
#: except for subtype 1, which introduces the hotspot table and carries no
#: text. 0x04 continues the line already being built.
LINE_BREAK = b"\x02\x00"
#: A break whose text is not length-prefixed, so it runs to the next record.
LINE_BREAK_RAW = b"\x02\x02"
LINE_CONTINUE = b"\x04\x00"
_SEPARATORS = (LINE_BREAK, LINE_BREAK_RAW, LINE_CONTINUE,
               b"\x0e\x00", b"\x02\x01")


class QuickHelpError(Exception):
    """Raised when a file does not match the QuickHelp layout."""


@dataclass
class HelpFile:
    """A parsed QuickHelp database."""

    data: bytes
    name: str = ""
    topic_index: Sequence[int] = ()
    keywords: Sequence[bytes] = ()
    tree: Sequence[int] = ()
    contexts: Dict[str, int] = field(default_factory=dict)

    @property
    def topic_count(self) -> int:
        return max(len(self.topic_index) - 1, 0)

    @classmethod
    def parse(cls, data: bytes) -> "HelpFile":
        if data[:2] != MAGIC:
            raise QuickHelpError(f"not a QuickHelp file: magic {data[:2]!r}")
        if len(data) < _OFFSETS_AT + 24:
            raise QuickHelpError("truncated: no section table")
        words = struct.unpack("<8H", data[:16])
        n_topics, n_contexts = words[4], words[5]
        (topics_off, ctx_str_off, ctx_map_off,
         keyword_off, huffman_off, text_off) = struct.unpack(
            "<6I", data[_OFFSETS_AT:_OFFSETS_AT + 24])
        if huffman_off + 1024 > len(data):
            raise QuickHelpError("truncated: no Huffman tree")

        self = cls(data=data)
        self.name = data[_NAME_AT:_OFFSETS_AT].split(b"\0")[0].decode("latin-1")
        self.topic_index = struct.unpack(
            f"<{n_topics + 1}I", data[topics_off:topics_off + 4 * (n_topics + 1)])
        self.tree = struct.unpack("<512H", data[huffman_off:huffman_off + 1024])

        words_ = data[keyword_off:huffman_off]
        keywords, i = [], 0
        while i < len(words_) and words_[i]:
            length = words_[i]
            keywords.append(words_[i + 1:i + 1 + length])
            i += 1 + length
        self.keywords = keywords

        names = [b for b in data[ctx_str_off:ctx_map_off].split(b"\0") if b]
        topic_of = struct.unpack(
            f"<{n_contexts}H", data[ctx_map_off:ctx_map_off + 2 * n_contexts])
        self.contexts = {n.decode("latin-1"): t
                         for n, t in zip(names, topic_of)}
        return self

    @classmethod
    def from_path(cls, path) -> "HelpFile":
        with open(path, "rb") as fh:
            return cls.parse(fh.read())

    # -- decompression ---------------------------------------------------

    def _huffman(self, blob: bytes) -> bytes:
        """Undo the Huffman coding of one topic.

        The tree is 512 words. A word with 0x8000 set is a leaf holding a
        byte; anything else is the byte offset of the node to move to. A 0 bit
        follows that offset and a 1 bit steps to the next word.
        """
        if len(blob) < 2:
            return b""
        want = struct.unpack("<H", blob[:2])[0]
        tree, out, p = self.tree, bytearray(), 0
        for byte in blob[2:]:
            for shift in range(7, -1, -1):
                p = (p + 1) if (byte >> shift) & 1 else (tree[p] // 2)
                if p >= len(tree):
                    return bytes(out)
                node = tree[p]
                if node & 0x8000:
                    out.append(node & 0xFF)
                    p = 0
                    if len(out) >= want:
                        return bytes(out)
        return bytes(out)

    def _expand(self, raw: bytes, i: int, want: Optional[int]) -> tuple:
        """Expand one display segment, returning its text and where it ended.

        ``want`` is the character count the file records for the segment, or
        None for a continuation, which runs to the next separator.
        """
        out = bytearray()
        while i < len(raw):
            if want is not None and len(out) >= want:
                break
            # Stop at the next record whatever the declared length says. A
            # hotspot table can sit in the middle of a line, and its bytes are
            # not text; running past it produces nonsense rather than the few
            # characters of link text that are lost by stopping.
            if any(raw.startswith(sep, i) for sep in _SEPARATORS):
                break
            c = raw[i]
            if KEYWORD_LO <= c <= KEYWORD_HI and i + 1 < len(raw):
                index = ((c - KEYWORD_LO) & 3) << 8 | raw[i + 1]
                if index < len(self.keywords):
                    out += self.keywords[index]
                    if c >= KEYWORD_SPACE:
                        out += b" "
                i += 2
            elif c == RUN_OF_SPACES and i + 1 < len(raw):
                out += b" " * raw[i + 1]
                i += 2
            elif c == SET_ATTRIBUTE and i + 1 < len(raw):
                i += 2
            elif c == LINK_TARGET:
                end = raw.find(b"\0", i + 1)
                i = len(raw) if end < 0 else end + 1
            elif c < 0x20:
                i += 1
            else:
                out.append(c)
                i += 1
        return bytes(out), i

    @staticmethod
    def _length(raw: bytes, i: int) -> Optional[int]:
        """The character count a record declares, if it declares one.

        Most records are length-prefixed, but one that opens with a control
        code has no length byte, so a value that could be a control is not
        read as one.
        """
        if i < len(raw) and raw[i] >= 0x20:
            return raw[i] - 1
        return None

    def topic(self, number: int) -> List[str]:
        """The display lines of one topic."""
        if not 0 <= number < self.topic_count:
            raise IndexError(f"topic {number} out of range")
        start, end = self.topic_index[number], self.topic_index[number + 1]
        raw = self._huffman(self.data[start:end])
        lines: List[str] = []
        i = 0
        while i < len(raw):
            at_break = raw.startswith(LINE_BREAK, i)
            at_raw = raw.startswith(LINE_BREAK_RAW, i)
            at_cont = raw.startswith(LINE_CONTINUE, i)
            if not (at_break or at_raw or at_cont):
                i += 1
                continue
            i += 2
            if at_break:
                if i >= len(raw):
                    break
                # Most lines are length-prefixed, but a line that opens with
                # a control code has no length byte, so only a value that
                # cannot be a control is read as one.
                want = self._length(raw, i)
                if want is not None:
                    i += 1
                text, i = self._expand(raw, i, want)
                lines.append(text.decode("latin-1"))
            elif at_raw:
                text, i = self._expand(raw, i, self._length(raw, i))
                lines.append(text.decode("latin-1"))
            else:
                want = self._length(raw, i)
                if want is not None:
                    i += 1
                text, i = self._expand(raw, i, want)
                if lines:
                    lines[-1] += text.decode("latin-1")
                else:
                    lines.append(text.decode("latin-1"))
        return lines

    def topic_for(self, context: str) -> Optional[int]:
        """The topic a context name resolves to, matched case-insensitively."""
        if context in self.contexts:
            return self.contexts[context]
        folded = context.lower()
        for name, number in self.contexts.items():
            if name.lower() == folded:
                return number
        return None
