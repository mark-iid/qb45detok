"""Turn an expression into the reverse-Polish stream QB stores.

There is no precedence table to invert. QB records the parentheses the
programmer wrote as an explicit opcode, so parsing has to keep them rather
than fold them away: ``4 + (2)`` stores the ``(2)``, and dropping it would not
round-trip.

The emitted stream is a list of ``(mnemonic, operands)`` pairs. Turning those
into words is the assembler's job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .lex import Kind, Token

#: Binary operators, tightest binding first. QB's own order: exponent, then
#: the unary minus that is handled separately, then the arithmetic, then the
#: comparisons, then the logical operators.
PRECEDENCE = [
    ("IMP",),
    ("EQV",),
    ("XOR",),
    ("OR",),
    ("AND",),
    # NOT is unary and sits here in QB's table.
    ("=", "<>", "><", "<", ">", "<=", "=<", ">=", "=>"),
    ("+", "-"),
    ("MOD",),
    ("\\",),
    ("*", "/"),
    # "^" is not here: it binds tighter than the unary minus, so that
    # -X ^ 2 is -(X ^ 2), and it is right associative.
]

#: How each operator is spelled in the opcode table.
OP_MNEMONIC = {
    "+": "ADD", "-": "SUBTRACT", "*": "MULTIPLY", "/": "DIVIDE",
    "\\": "IDIVIDE", "^": "POWER", "MOD": "MOD",
    "=": "EQ", "<>": "NE", "><": "NE", "<": "LT", ">": "GT",
    "<=": "LE", "=<": "LE", ">=": "GE", "=>": "GE",
    "AND": "AND", "OR": "OR", "XOR": "XOR", "EQV": "EQV", "IMP": "IMP",
}

#: Word operators, which lex as names and have to be recognised as operators.
WORD_OPS = {"MOD", "AND", "OR", "XOR", "EQV", "IMP", "NOT"}


class ParseError(ValueError):
    """Raised when a line cannot be parsed."""


@dataclass
class Emit:
    """One instruction in the stream being built."""

    mnemonic: str
    operands: Sequence = ()
    text: Optional[str] = None

    def __repr__(self) -> str:
        rest = f" {list(self.operands)}" if self.operands else ""
        return f"{self.mnemonic}{rest}"


@dataclass
class Cursor:
    """A position in a token list, with the small helpers a parser wants."""

    tokens: List[Token]
    i: int = 0

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def at(self, *texts: str) -> bool:
        t = self.tok
        return t.kind is not Kind.END and t.text.upper() in {x.upper() for x in texts}

    def at_end(self) -> bool:
        return self.tok.kind is Kind.END

    def take(self) -> Token:
        t = self.tok
        self.i += 1
        return t

    def expect(self, text: str) -> Token:
        if not self.at(text):
            raise ParseError(f"expected {text!r}, found {self.tok.text!r}")
        return self.take()


def parse_expression(cur: Cursor, level: int = 0) -> List[Emit]:
    """Parse an expression, emitting it in reverse Polish."""
    if level >= len(PRECEDENCE):
        return _parse_unary(cur)
    out = parse_expression(cur, level + 1)
    while True:
        t = cur.tok
        if t.kind is Kind.END:
            break
        word = t.text.upper()
        if word not in PRECEDENCE[level]:
            break
        # A word operator only counts as one if it is not a variable name.
        if t.kind is Kind.NAME and word not in WORD_OPS:
            break
        cur.take()
        out += parse_expression(cur, level + 1)
        out.append(Emit(OP_MNEMONIC[word]))
    return out


def _parse_unary(cur: Cursor) -> List[Emit]:
    if cur.at("-"):
        cur.take()
        return _parse_unary(cur) + [Emit("NEGATE")]
    if cur.at("+"):
        cur.take()
        return _parse_unary(cur)
    if cur.at("NOT") and cur.tok.kind is Kind.NAME:
        cur.take()
        return _parse_unary(cur) + [Emit("NOT")]
    return _parse_power(cur)


def _parse_power(cur: Cursor) -> List[Emit]:
    """``^``, which binds tighter than unary minus and groups to the right."""
    out = _parse_primary(cur)
    if cur.at("^"):
        cur.take()
        return out + _parse_unary(cur) + [Emit("POWER")]
    return out


def _parse_primary(cur: Cursor) -> List[Emit]:
    t = cur.tok
    if t.kind is Kind.NUMBER:
        cur.take()
        return [Emit("PUSH_NUMBER", (t.text, t.numtype))]
    if t.kind is Kind.STRING:
        cur.take()
        return [Emit("PUSH_STR", (), t.text)]
    if cur.at("("):
        cur.take()
        inner = parse_expression(cur)
        cur.expect(")")
        # QB stores the parentheses the programmer wrote, redundant or not.
        return inner + [Emit("PAREN")]
    if t.kind is Kind.NAME:
        return _parse_name(cur)
    raise ParseError(f"cannot start an expression with {t.text!r}")


def _parse_name(cur: Cursor) -> List[Emit]:
    """A variable, an array or function call, or a record field access."""
    t = cur.take()
    out: List[Emit] = []
    args: List[List[Emit]] = []
    if cur.at("("):
        cur.take()
        if not cur.at(")"):
            while True:
                args.append(parse_expression(cur))
                if not cur.at(","):
                    break
                cur.take()
        cur.expect(")")
        for a in args:
            out += a
        out.append(Emit("CALL_OR_INDEX", (t.text, t.suffix, len(args))))
    else:
        out.append(Emit("LOAD", (t.text, t.suffix)))
    # Record fields chain: Disk(n).Sectors, and T.xc.
    while cur.at("."):
        cur.take()
        field_tok = cur.take()
        out.append(Emit("FIELD", (field_tok.text, field_tok.suffix)))
    return out
