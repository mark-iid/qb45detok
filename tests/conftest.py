import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS_BIN = ROOT / "corpus" / "bin"
CORPUS_TXT = ROOT / "corpus" / "txt"
SYNTHETIC = ROOT / "corpus" / "synthetic"

#: Every matched pair in the corpus, if one is present. The corpus is a set of
#: programs saved twice by QB 4.5 -- once tokenized, once as text -- and is not
#: distributed with this repository. Tests that need it skip when it is absent.
CORPUS_AVAILABLE = CORPUS_BIN.is_dir() and any(CORPUS_BIN.glob("*.BAS"))
NAMES = sorted(p.name for p in CORPUS_BIN.glob("*.BAS")) if CORPUS_AVAILABLE else []
requires_corpus = pytest.mark.skipif(
    not CORPUS_AVAILABLE, reason="no corpus in corpus/bin and corpus/txt"
)

import re

_PROC_START = re.compile(r"\s*(?:SUB|FUNCTION)\s+([A-Za-z0-9_.]+)", re.I)
_PROC_END = re.compile(r"\s*END\s+(?:SUB|FUNCTION)\b", re.I)


_COMMENT = re.compile(r"\s*'")


def text_blocks(text):
    """Split QB's text output into ``{lowercased proc name: lines}``.

    A procedure's section in the binary starts at the run of comment lines
    immediately above its ``SUB``/``FUNCTION``, not at the keyword itself, so
    those comments are moved out of the module text and into the procedure.

    The module-level text lands under ``None``. Its line count is not
    trustworthy -- blank lines between procedures fall into it -- so tests
    compare the module against its section trailer instead.
    """
    lines = text.splitlines()
    out, module, i = {}, [], 0
    while i < len(lines):
        m = _PROC_START.match(lines[i])
        if not m:
            module.append(lines[i])
            i += 1
            continue
        lead = []
        while module and _COMMENT.match(module[-1]):
            lead.insert(0, module.pop())
        j = i + 1
        while j < len(lines) and not _PROC_END.match(lines[j]):
            j += 1
        out[m.group(1).lower()] = lead + lines[i : j + 1]
        i = j + 1
    out[None] = module
    return out
