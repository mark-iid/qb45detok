"""Work out what a pile of old BASIC files actually are.

The BASICs of the eighties all had tokenized save formats and none of them
are compatible, so a directory pulled off an old disk is usually a mixture.
Every one of them is identified by its first byte, which is why a wrong guess
costs so little to avoid.

This is the check I wanted when I started, having wasted an afternoon feeding
QuickBASIC files to a GW-BASIC detokenizer and getting nonsense back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

#: What each leading byte means, and what will read it. Only the QuickBASIC
#: 4.5 row is something this package can act on; the rest are here so the
#: answer is "use that other tool" rather than a wrong conversion.
SIGNATURES = {
    0xFC: ("QuickBASIC 4.5", "qb45detok"),
    0xFD: ("QuickBASIC 4.0 or BASIC 7 PDS", "untested here"),
    0xFE: ("QuickBASIC 3 or earlier", "untested here"),
    0xFF: ("GW-BASIC, BASICA or MSX-BASIC", "bascat, gwbasic-decoder"),
    0xF9: ("older Microsoft BASIC", "decode_ms_basic.py"),
    0xF1: ("older Microsoft BASIC", "decode_ms_basic.py"),
    0xF3: ("older Microsoft BASIC", "decode_ms_basic.py"),
}

#: A protected file is tokenized and then encrypted; the tokenizer is the same
#: but the bytes have to be unscrambled first.
PROTECTED = 0xFE


@dataclass
class Finding:
    """What one file turned out to be."""

    path: Path
    kind: str
    tool: str
    first_byte: Optional[int] = None
    detail: str = ""

    @property
    def readable_here(self) -> bool:
        return self.tool == "qb45detok"


def _looks_like_text(head: bytes) -> bool:
    """Whether the first block is plausibly plain source.

    Judged on control characters rather than on printable ASCII. DOS BASIC is
    full of box drawing and accented characters from the code page, so a file
    can be perfectly good source and still have a byte in ten above 0x7f.
    """
    if not head or b"\0" in head:
        return False
    allowed = (9, 10, 13, 26)  # tab, newline, return, end of file
    control = sum(1 for b in head if b < 0x20 and b not in allowed)
    return control / len(head) < 0.02


def classify(path: Path) -> Finding:
    """Identify one file."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError as exc:
        return Finding(path, "unreadable", "none", detail=str(exc))
    if not head:
        return Finding(path, "empty", "none")

    first = head[0]
    if first in SIGNATURES:
        kind, tool = SIGNATURES[first]
        detail = ""
        if first == PROTECTED:
            detail = "may be a protected save"
        return Finding(path, kind, tool, first, detail)
    if _looks_like_text(head):
        return Finding(path, "plain text", "nothing to do", first,
                       "already ASCII source")
    return Finding(path, "unrecognised", "none", first)


def scan(paths: Iterable[Path], pattern: str = "*.BAS") -> List[Finding]:
    """Identify every file under the given paths.

    A path that is a file is taken as it is; a directory is searched
    recursively for ``pattern``, case-insensitively.
    """
    found: List[Finding] = []
    for path in paths:
        if path.is_file():
            found.append(classify(path))
            continue
        seen = set()
        for candidate in sorted(path.rglob("*")):
            if not candidate.is_file():
                continue
            if not candidate.match(pattern) and not candidate.match(pattern.lower()):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            found.append(classify(candidate))
    return found
