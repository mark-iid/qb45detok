"""Split a line of QuickBASIC source into tokens.

The lexer is the easy half of tokenizing and it is kept separate because the
awkward parts are all local to it: a type suffix binds to the name before it,
a period is part of an identifier rather than an operator, and a literal's
form decides its type before any value is looked at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class Kind(Enum):
    NAME = "name"
    NUMBER = "number"
    STRING = "string"
    OP = "op"
    END = "end"


@dataclass
class Token:
    kind: Kind
    text: str
    #: For a name, the type suffix written on it, if any.
    suffix: str = ""
    #: For a number, which of QB's numeric types the literal spells.
    numtype: str = ""
    column: int = 0

    def __repr__(self) -> str:
        return f"{self.kind.name}({self.text!r})"


class LexError(ValueError):
    """Raised when a line cannot be split into tokens."""


#: An identifier: letter, then letters, digits, periods or underscores, then
#: an optional type suffix. Periods are ordinary name characters in BASIC,
#: which is why Press.Any.Key is one name and not three.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.]*[%&!#$]?")

#: Numbers, longest form first so that 1E5 does not lex as 1 then E5.
_HEX = re.compile(r"&[Hh][0-9A-Fa-f]+&?")
_OCT = re.compile(r"&[Oo]?[0-7]+&?")
_NUM = re.compile(r"(\d+\.?\d*|\.\d+)([EeDd][-+]?\d+)?[!#&%]?")

#: Two-character operators have to be tried before the single-character ones.
_LONG_OPS = ("<=", ">=", "<>", "><", "=<", "=>")
_OPS = set("+-*/\\^=<>(),;:#?")


def _numtype(text: str) -> str:
    """Which type a numeric literal spells, from its form alone.

    A suffix decides it outright. Otherwise a decimal point or an exponent
    makes it single, a D exponent makes it double, and anything else is an
    integer if it fits and a long if it does not.
    """
    lowered = text.lower()
    if lowered.endswith("#"):
        return "double"
    if lowered.endswith("!"):
        return "single"
    if lowered.endswith("&"):
        return "long"
    if lowered.endswith("%"):
        return "integer"
    if not lowered.startswith("&"):
        if "d" in lowered:
            return "double"
        if "." in text or "e" in lowered:
            # An E in a hex literal is a digit, which is why this is asked
            # only of a decimal one.
            return "single"
    if lowered.startswith("&"):
        # Hex and octal are integers unless they overflow one.
        digits = text.lstrip("&hHoO").rstrip("&")
        base = 16 if lowered.startswith("&h") else 8
        return "integer" if int(digits or "0", base) <= 0xFFFF else "long"
    return "integer" if -32768 <= int(text) <= 32767 else "long"


def lex(line: str) -> List[Token]:
    """Split one source line into tokens.

    A comment or a DATA statement swallows the rest of the line, so both are
    left to the parser: this stops at the marker and hands back what is left
    as a single token.
    """
    out: List[Token] = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t":
            i += 1
            continue
        # REM swallows the rest of the line, metacommands included.
        if (not out or out[-1].text == ":") and line[i:i + 3].upper() == "REM" \
                and (i + 3 >= n or not line[i + 3].isalnum()):
            out.append(Token(Kind.NAME, "REM", column=i))
            out.append(Token(Kind.STRING, line[i + 3:], column=i + 3))
            out.append(Token(Kind.END, "", column=n))
            return out
        # DATA is copied out verbatim, so its items are never read as
        # tokens: "$25.00" is a perfectly good DATA item.
        if (not out or out[-1].text == ":") and line[i:i + 4].upper() == "DATA" \
                and (i + 4 >= n or not line[i + 4].isalnum()):
            # A colon outside quotes ends DATA; what follows is another
            # statement and is lexed as usual.
            end, quoted = n, False
            for j in range(i + 4, n):
                if line[j] == '"':
                    quoted = not quoted
                elif line[j] == ":" and not quoted:
                    end = j
                    break
            out.append(Token(Kind.NAME, "DATA", column=i))
            out.append(Token(Kind.STRING, line[i + 4:end], column=i + 4))
            if end == n:
                out.append(Token(Kind.END, "", column=n))
                return out
            i = end
            continue
        # A period straight after a name or a closing bracket is a record
        # field rather than the start of something new.
        if c == "." and out and (out[-1].kind is Kind.NAME or out[-1].text == ")"):
            m = _NAME.match(line, i + 1)
            if m:
                text = m.group(0)
                suffix = text[-1] if text[-1] in "%&!#$" else ""
                body = text[:len(text) - len(suffix)] if suffix else text
                out.append(Token(Kind.OP, ".", column=i))
                out.append(Token(Kind.NAME, body, suffix=suffix, column=i + 1))
                i = m.end()
                continue
        if c == "'":
            out.append(Token(Kind.OP, "'", column=i))
            out.append(Token(Kind.STRING, line[i + 1:], column=i + 1))
            out.append(Token(Kind.END, "", column=n))
            return out
        if c == '"':
            end = line.find('"', i + 1)
            if end < 0:
                # QB tolerates a string that runs to the end of the line.
                out.append(Token(Kind.STRING, line[i + 1:], column=i))
                out.append(Token(Kind.END, "", column=n))
                return out
            out.append(Token(Kind.STRING, line[i + 1:end], column=i))
            i = end + 1
            continue
        if c == "&" or c.isdigit() or (c == "." and i + 1 < n and line[i + 1].isdigit()):
            for pattern in (_HEX, _OCT, _NUM):
                m = pattern.match(line, i)
                if m and m.group(0) not in ("&", ""):
                    text = m.group(0)
                    out.append(Token(Kind.NUMBER, text, numtype=_numtype(text),
                                     column=i))
                    i = m.end()
                    break
            else:
                raise LexError(f"cannot read a number at column {i}: {line[i:]!r}")
            continue
        m = _NAME.match(line, i)
        if m:
            text = m.group(0)
            suffix = text[-1] if text[-1] in "%&!#$" else ""
            out.append(Token(Kind.NAME, text[:len(text) - len(suffix)] if suffix else text,
                             suffix=suffix, column=i))
            i = m.end()
            continue
        pair = line[i:i + 2]
        if pair in _LONG_OPS:
            out.append(Token(Kind.OP, pair, column=i))
            i += 2
            continue
        if c in _OPS:
            out.append(Token(Kind.OP, c, column=i))
            i += 1
            continue
        raise LexError(f"unexpected character {c!r} at column {i}")
    out.append(Token(Kind.END, "", column=n))
    return out
