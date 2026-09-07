"""Parse a line of QuickBASIC source into the statements it holds.

This is the front half of tokenizing. It produces the same reverse-Polish
stream the decoder reads back, as ``Emit`` records naming an opcode rather
than as words: choosing the exact opcode for a statement means knowing how
many arguments were written and which of its forms that picks, and that is
easier to do once the whole statement has been read.

What it does not do is resolve names to references or lay out sections. That
belongs with the writer, which owns the name table.
"""

from __future__ import annotations

from typing import List, Optional

from .expr import Cursor, Emit, ParseError, parse_expression
from .lex import Kind, Token, lex

#: Statements that are a keyword on its own.
BARE = {
    "END": "END", "STOP": "STOP", "SYSTEM": "SYSTEM", "BEEP": "BEEP",
    "TRON": "TRON", "TROFF": "TROFF", "RESET": "RESET", "RETURN": "RETURN",
    "WEND": "WEND", "CLS": "CLS", "SLEEP": "SLEEP_BARE", "RANDOMIZE":
    "RANDOMIZE_BARE", "FILES": "FILES_BARE", "SHELL": "SHELL_BARE",
    "VIEW": "VIEW_BARE", "WINDOW": "WINDOW_BARE", "PALETTE": "PALETTE_BARE",
    "RESTORE": "RESTORE", "RESUME": "RESUME",
}

#: Statements that take a list of expressions and nothing else.
LIST_STATEMENTS = {
    "LOCATE": "LOCATE", "COLOR": "COLOR", "SOUND": "SOUND", "PLAY": "PLAY",
    "DRAW": "DRAW", "PAINT": "PAINT", "PSET": "PSET", "PRESET": "PRESET",
    "KILL": "KILL", "NAME": "NAME", "CHDIR": "CHDIR", "MKDIR": "MKDIR",
    "RMDIR": "RMDIR", "ENVIRON": "ENVIRON", "POKE": "POKE", "OUT": "OUT",
    "WAIT": "WAIT", "WIDTH": "WIDTH", "SWAP": "SWAP", "ERASE": "ERASE",
    "ERROR": "ERROR", "RANDOMIZE": "RANDOMIZE", "SLEEP": "SLEEP",
    "FILES": "FILES", "SHELL": "SHELL", "PCOPY": "PCOPY", "SCREEN": "SCREEN",
    "CHAIN": "CHAIN", "BLOAD": "BLOAD", "BSAVE": "BSAVE", "CLEAR": "CLEAR",
    "LOCK": "LOCK", "UNLOCK": "UNLOCK", "SEEK": "SEEK_STMT",
    "LINE": "LINE", "CIRCLE": "CIRCLE", "GET": "GET_FILE", "PUT": "PUT_FILE",
    "IOCTL": "IOCTL", "LSET": "LSET", "RSET": "RSET", "WRITE": "WRITE",
    "READ": "READ", "VIEW": "VIEW", "WINDOW": "WINDOW", "PALETTE": "PALETTE",
    "CLOSE": "CLOSE", "KEY": "KEY", "TIMER": "TIMER", "DEF": "DEF_SEG",
}

#: Keywords that introduce a block and take a condition.
BLOCK_HEADS = {"IF", "SELECT", "DO", "WHILE", "FOR"}

#: Graphics statements, where a leading "(x, y)" is a coordinate pair rather
#: than a parenthesised expression, and "-(x, y)" continues from the last one.
GRAPHICS = {"PSET", "PRESET", "LINE", "CIRCLE", "PAINT", "VIEW", "WINDOW",
            "GET", "PUT"}


class Statement:
    """One statement: the stream it emits, and how it was separated."""

    def __init__(self, emits: List[Emit], separator: str = ":") -> None:
        self.emits = emits
        self.separator = separator

    def __repr__(self) -> str:
        return f"Statement({' '.join(str(e) for e in self.emits)})"


def parse_line(text: str) -> List[Statement]:
    """Parse one source line into its statements.

    The leading label, if any, is not consumed here: a caller that cares about
    labels should strip it first, because whether a leading name is a label
    depends on the colon after it rather than on anything in the statement.
    """
    cur = Cursor(lex(text))
    out: List[Statement] = []
    while not cur.at_end():
        if cur.at(":"):
            cur.take()
            continue
        before = cur.i
        out.append(Statement(_parse_statement(cur)))
        if cur.i == before:
            raise ParseError(f"made no progress at {cur.tok.text!r}")
    return out


def _parse_statement(cur: Cursor) -> List[Emit]:
    t = cur.tok
    if t.kind is Kind.OP and t.text == "'":
        cur.take()
        body = cur.take()
        return [Emit("REM", (t.column,), body.text)]
    word = t.text.upper() if t.kind is Kind.NAME else ""

    if word == "REM":
        cur.take()
        body = cur.take()
        return [Emit("REM_META", (), body.text)]
    if word in BARE and _ends_statement(cur, 1):
        cur.take()
        return [Emit(BARE[word])]
    handler = _HANDLERS.get(word)
    if handler is not None:
        cur.take()
        return handler(cur)
    if word in LIST_STATEMENTS:
        cur.take()
        return _list_statement(cur, LIST_STATEMENTS[word], word in GRAPHICS)
    return _assignment_or_call(cur)


def _ends_statement(cur: Cursor, ahead: int = 0) -> bool:
    """Whether the token `ahead` places on is the end of a statement."""
    i = cur.i + ahead
    if i >= len(cur.tokens):
        return True
    t = cur.tokens[i]
    return t.kind is Kind.END or t.text in (":", "'")


def _expression_list(cur: Cursor, coords: bool = False) -> List[Emit]:
    out: List[Emit] = []
    if _ends_statement(cur):
        return out
    while True:
        if cur.at("#"):
            # A file number, as in "IOCTL #1, ...".
            cur.take()
            out += parse_expression(cur)
            out.append(Emit("FILE_NUMBER"))
        elif coords and (cur.at("(") or cur.at("STEP")):
            out += _coordinate(cur)
            # "(x1, y1)-(x2, y2)" is one argument, not two.
            if cur.at("-"):
                cur.take()
                out += _coordinate(cur, to=True)
        elif coords and cur.at("-"):
            cur.take()
            out += _coordinate(cur, to=True)
        elif cur.at(","):
            # An argument left out keeps its place: "LOCATE , , 1".
            out.append(Emit("ARG_OMITTED"))
        else:
            out += parse_expression(cur)
        if not cur.at(","):
            break
        cur.take()
    return out


def _coordinate(cur: Cursor, to: bool = False) -> List[Emit]:
    """A graphics coordinate, "(x, y)" or "STEP (x, y)"."""
    step = False
    if cur.at("STEP"):
        cur.take()
        step = True
    cur.expect("(")
    out = parse_expression(cur)
    cur.expect(",")
    out += parse_expression(cur)
    cur.expect(")")
    which = ("COORD_TO_STEP" if step else "COORD_TO") if to else \
            ("COORD_STEP" if step else "COORD")
    return out + [Emit(which)]


def _list_statement(cur: Cursor, mnemonic: str, coords: bool = False) -> List[Emit]:
    args = _expression_list(cur, coords)
    return args + [Emit(mnemonic)]


def _assignment_or_call(cur: Cursor) -> List[Emit]:
    """An assignment, or a call written without CALL."""
    if cur.at("LET"):
        cur.take()
    start = cur.i
    target = _parse_target(cur)
    if target is not None and cur.at("="):
        cur.take()
        value = parse_expression(cur)
        return value + _as_store(target)
    # Not an assignment, so it is a call with its arguments unbracketed.
    cur.i = start
    name = cur.take()
    args: List[Emit] = []
    count = 0
    if not _ends_statement(cur):
        while True:
            args += parse_expression(cur)
            count += 1
            if not cur.at(","):
                break
            cur.take()
    return args + [Emit("CALL_IMPLICIT", (name.text, count))]


def _parse_target(cur: Cursor) -> Optional[List[Emit]]:
    """The left-hand side of an assignment, and nothing beyond it.

    Parsed separately from an expression because "=" is a comparison in an
    expression, so letting the expression parser at it would swallow the
    whole assignment.
    """
    if cur.tok.kind is not Kind.NAME:
        return None
    name = cur.take()
    out: List[Emit] = []
    if cur.at("("):
        cur.take()
        args = 0
        if not cur.at(")"):
            while True:
                out += parse_expression(cur)
                args += 1
                if not cur.at(","):
                    break
                cur.take()
        if not cur.at(")"):
            return None
        cur.take()
        out.append(Emit("CALL_OR_INDEX", (name.text, name.suffix, args)))
    else:
        out.append(Emit("LOAD", (name.text, name.suffix)))
    while cur.at("."):
        cur.take()
        field_tok = cur.take()
        out.append(Emit("FIELD", (field_tok.text, field_tok.suffix)))
    return out


def _as_store(target: List[Emit]) -> List[Emit]:
    """Turn the parsed left-hand side into the opcode that stores into it."""
    if not target:
        raise ParseError("assignment with no target")
    last = target[-1]
    rest = target[:-1]
    if last.mnemonic == "LOAD":
        return rest + [Emit("STORE", last.operands)]
    if last.mnemonic == "CALL_OR_INDEX":
        return rest + [Emit("ARRSET", last.operands)]
    if last.mnemonic == "FIELD":
        return rest + [Emit("FIELD_SET", last.operands)]
    raise ParseError(f"cannot assign to {last.mnemonic}")


# -- individual statements ----------------------------------------------

def _st_print(cur: Cursor) -> List[Emit]:
    out: List[Emit] = []
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("PRINT_FILE"))
        if cur.at(","):
            cur.take()
    while cur.at(";") or cur.at(","):
        out.append(Emit("PRINT_SEMI" if cur.take().text == ";" else "PRINT_COMMA"))
    if cur.at("USING"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("PRINT_USING"))
        if cur.at(";"):
            cur.take()
    while not _ends_statement(cur):
        out += parse_expression(cur)
        if cur.at(";"):
            cur.take()
            out.append(Emit("PRINT_SEMI"))
            # QB writes "TAB(5); , X", so a comma can follow a semicolon.
            while cur.at(",") or cur.at(";"):
                out.append(Emit("PRINT_COMMA" if cur.take().text == ","
                                else "PRINT_SEMI"))
        elif cur.at(","):
            cur.take()
            out.append(Emit("PRINT_COMMA"))
        else:
            out.append(Emit("PRINT_ITEM"))
            return out
    out.append(Emit("PRINT_NEWLINE"))
    return out


def _st_if(cur: Cursor) -> List[Emit]:
    cond = parse_expression(cur)
    if cur.at("THEN"):
        cur.take()
    elif cur.at("GOTO"):
        cur.take()
        target = cur.take()
        return cond + [Emit("IF_GOTO", (target.text,))]
    if _ends_statement(cur):
        return cond + [Emit("IF_THEN_BLOCK")]
    return cond + [Emit("IF_THEN_LINE")]


def _st_for(cur: Cursor) -> List[Emit]:
    target = cur.take()
    cur.expect("=")
    out = parse_expression(cur)
    cur.expect("TO")
    out += parse_expression(cur)
    if cur.at("STEP"):
        cur.take()
        out += parse_expression(cur)
        return out + [Emit("FOR_STEP", (target.text, target.suffix))]
    return out + [Emit("FOR", (target.text, target.suffix))]


def _st_next(cur: Cursor) -> List[Emit]:
    names = []
    while cur.tok.kind is Kind.NAME:
        names.append(cur.take())
        if not cur.at(","):
            break
        cur.take()
    if not names:
        return [Emit("NEXT_BARE")]
    return [Emit("NEXT", (n.text, n.suffix)) for n in names]


def _st_goto(cur: Cursor) -> List[Emit]:
    return [Emit("GOTO", (cur.take().text,))]


def _st_gosub(cur: Cursor) -> List[Emit]:
    return [Emit("GOSUB", (cur.take().text,))]


def _st_do(cur: Cursor) -> List[Emit]:
    if cur.at("WHILE"):
        cur.take()
        return parse_expression(cur) + [Emit("DO_WHILE")]
    if cur.at("UNTIL"):
        cur.take()
        return parse_expression(cur) + [Emit("DO_UNTIL")]
    return [Emit("DO")]


def _st_loop(cur: Cursor) -> List[Emit]:
    if cur.at("WHILE"):
        cur.take()
        return parse_expression(cur) + [Emit("LOOP_WHILE")]
    if cur.at("UNTIL"):
        cur.take()
        return parse_expression(cur) + [Emit("LOOP_UNTIL")]
    return [Emit("LOOP")]


def _st_while(cur: Cursor) -> List[Emit]:
    return parse_expression(cur) + [Emit("WHILE")]


def _st_select(cur: Cursor) -> List[Emit]:
    if cur.at("CASE"):
        cur.take()
    return parse_expression(cur) + [Emit("SELECT_CASE")]


def _st_case(cur: Cursor) -> List[Emit]:
    if cur.at("ELSE"):
        cur.take()
        return [Emit("CASE_ELSE")]
    out: List[Emit] = []
    while True:
        if cur.at("IS"):
            cur.take()
            op = cur.take().text
            out += parse_expression(cur)
            out.append(Emit(f"CASE_IS_{op}"))
        else:
            out += parse_expression(cur)
            if cur.at("TO"):
                cur.take()
                out += parse_expression(cur)
                out.append(Emit("CASE_RANGE"))
            else:
                out.append(Emit("CASE"))
        if not cur.at(","):
            break
        cur.take()
    return out


def _st_end(cur: Cursor) -> List[Emit]:
    for word, mnemonic in (("IF", "END_IF"), ("SELECT", "CASE_END"),
                           ("SUB", "END_SUB"), ("FUNCTION", "END_SUB"),
                           ("TYPE", "END_TYPE"), ("DEF", "END_DEF")):
        if cur.at(word):
            cur.take()
            return [Emit(mnemonic)]
    return [Emit("END")]


def _st_exit(cur: Cursor) -> List[Emit]:
    word = cur.take().text.upper()
    return [Emit({"DO": "EXIT_DO", "FOR": "EXIT_FOR"}.get(word, "EXIT_SUB"))]


def _st_else(cur: Cursor) -> List[Emit]:
    if cur.at("IF"):
        cur.take()
        cond = parse_expression(cur)
        if cur.at("THEN"):
            cur.take()
        return cond + [Emit("ELSEIF")]
    return [Emit("ELSE")]


def _st_declaration(kind: str):
    def handler(cur: Cursor) -> List[Emit]:
        out: List[Emit] = []
        shared = False
        if cur.at("SHARED"):
            cur.take()
            shared = True
        while not _ends_statement(cur):
            name = cur.take()
            bounds: List[Emit] = []
            if cur.at("("):
                cur.take()
                if not cur.at(")"):
                    while True:
                        bounds += parse_expression(cur)
                        if cur.at("TO"):
                            cur.take()
                            bounds += parse_expression(cur)
                        if not cur.at(","):
                            break
                        cur.take()
                cur.expect(")")
            as_type = None
            if cur.at("AS"):
                cur.take()
                as_type = cur.take().text
            out += bounds
            out.append(Emit(f"{kind}_ITEM", (name.text, name.suffix, as_type)))
            if not cur.at(","):
                break
            cur.take()
        out.append(Emit(f"{kind}_SHARED" if shared else kind))
        return out
    return handler


def _st_input(cur: Cursor) -> List[Emit]:
    out: List[Emit] = []
    if cur.at(";"):
        cur.take()
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("INPUT_CHANNEL"))
        if cur.at(","):
            cur.take()
    elif cur.tok.kind is Kind.STRING:
        out.append(Emit("PUSH_STR", (), cur.take().text))
        out.append(Emit("INPUT_PROMPT"))
        if cur.at(",") or cur.at(";"):
            cur.take()
    out += _expression_list(cur)
    out.append(Emit("INPUT"))
    return out


def _st_open(cur: Cursor) -> List[Emit]:
    out = parse_expression(cur)
    mode = None
    if cur.at("FOR"):
        cur.take()
        mode = cur.take().text.upper()
    if cur.at("ACCESS"):
        cur.take()
        while cur.tok.kind is Kind.NAME and cur.tok.text.upper() in ("READ", "WRITE"):
            cur.take()
    cur.expect("AS")
    if cur.at("#"):
        cur.take()
    out += parse_expression(cur)
    if cur.at("LEN"):
        cur.take()
        cur.expect("=")
        out += parse_expression(cur)
        return out + [Emit("OPEN_RANDOM", (mode,))]
    return out + [Emit("OPEN", (mode,))]


def _st_data(cur: Cursor) -> List[Emit]:
    # DATA takes the rest of the line verbatim.
    rest = cur.tokens[cur.i:]
    cur.i = len(cur.tokens) - 1
    return [Emit("DATA", (), " ".join(t.text for t in rest if t.kind is not Kind.END))]


def _parameter_list(cur: Cursor) -> List[tuple]:
    """The parameters of a DECLARE, SUB or FUNCTION.

    Each comes back as (name, suffix, byval, seg, as_type, array).
    """
    params: List[tuple] = []
    if not cur.at("("):
        return params
    cur.take()
    if cur.at(")"):
        cur.take()
        return params
    while True:
        byval = seg = False
        while cur.at("BYVAL") or cur.at("SEG"):
            if cur.at("BYVAL"):
                byval = True
            else:
                seg = True
            cur.take()
        name = cur.take()
        array = False
        if cur.at("("):
            cur.take()
            cur.expect(")")
            array = True
        as_type = None
        if cur.at("AS"):
            cur.take()
            as_type = cur.take().text
        params.append((name.text, name.suffix, byval, seg, as_type, array))
        if not cur.at(","):
            break
        cur.take()
    cur.expect(")")
    return params


def _signature(cur: Cursor, mnemonic: str) -> List[Emit]:
    """DECLARE SUB, DECLARE FUNCTION, SUB and FUNCTION all share a shape."""
    kind = None
    if cur.at("SUB") or cur.at("FUNCTION"):
        kind = cur.take().text.upper()
    name = cur.take()
    cdecl = False
    alias = None
    if cur.at("CDECL"):
        cur.take()
        cdecl = True
    if cur.at("ALIAS"):
        cur.take()
        alias = cur.take().text
    params = _parameter_list(cur)
    static = False
    if cur.at("STATIC"):
        cur.take()
        static = True
    return [Emit(mnemonic, (name.text, name.suffix, kind, cdecl, alias,
                            tuple(params), static))]


def _st_declare(cur: Cursor) -> List[Emit]:
    return _signature(cur, "DECLARE")


def _st_sub(cur: Cursor) -> List[Emit]:
    cur.i -= 1                      # put SUB back; the signature reads it
    return _signature(cur, "SUB")


def _st_function(cur: Cursor) -> List[Emit]:
    cur.i -= 1
    return _signature(cur, "FUNCTION")


_HANDLERS = {
    "DECLARE": _st_declare, "SUB": _st_sub, "FUNCTION": _st_function,
    "PRINT": _st_print, "LPRINT": _st_print,
    "IF": _st_if, "ELSE": _st_else, "ELSEIF": _st_else,
    "FOR": _st_for, "NEXT": _st_next,
    "GOTO": _st_goto, "GOSUB": _st_gosub,
    "DO": _st_do, "LOOP": _st_loop, "WHILE": _st_while,
    "SELECT": _st_select, "CASE": _st_case,
    "END": _st_end, "EXIT": _st_exit,
    "DIM": _st_declaration("DIM"), "REDIM": _st_declaration("REDIM"),
    "STATIC": _st_declaration("STATIC"), "COMMON": _st_declaration("COMMON"),
    "SHARED": _st_declaration("SHARED"),
    "INPUT": _st_input, "OPEN": _st_open, "DATA": _st_data,
}
