"""Format BASIC source the way QuickBASIC itself formats it.

Every other formatter has to decide what the house style is. This one does
not: QuickBASIC 4.5 reformatted a program as it read it, and the rules it used
are the ones the renderer already reproduces. So the way to format a file is
to hand it to the tokenizer and read it back, which is what this does.

What comes out is what QuickBASIC would have shown you:

* keywords in upper case and identifiers in whatever case the name was first
  entered, applied to every mention of that name
* one space around operators and after commas, and none before them
* the indentation the line was written with, kept as it was
* a comment left at the column it sat in

That last pair matter for a formatter, because they are the things it does
*not* touch. QuickBASIC never re-indented anyone's code and neither does this.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Optional, Sequence

from .decode import decode_file
from .reader import BinFile
from .render import Renderer
from .tokenize import tokenize


def format_source(text: str, base_dir=None, qb_order: bool = False) -> List[str]:
    """The source as QuickBASIC would have written it back out.

    ``qb_order`` sorts the procedures by name, which is what QB's own Save As
    Text does. It is off by default: a formatter that moves code around is a
    surprise, and the order a file is written in is the author's.
    """
    data = tokenize(text, base_dir)
    bf = BinFile.parse(data)
    return Renderer(bf).file(decode_file(bf), sort=qb_order)


def format_path(path, qb_order: bool = False) -> List[str]:
    """The same, for a file on disk, resolving any ``$INCLUDE`` beside it."""
    path = Path(path)
    return format_source(path.read_text(encoding="latin-1"), path.parent,
                         qb_order)


def diff(before: Sequence[str], after: Sequence[str],
         name: str = "source") -> List[str]:
    """A unified diff of what formatting would change."""
    return list(difflib.unified_diff(list(before), list(after),
                                     fromfile=name, tofile=f"{name} formatted",
                                     lineterm=""))


def would_change(text: str, formatted: Sequence[str]) -> bool:
    """Whether formatting the text would alter it."""
    return text.splitlines() != list(formatted)
