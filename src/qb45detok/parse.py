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

from . import tokens
from .expr import Cursor, Emit, ParseError, parse_expression
from .lex import Kind, Token, lex

#: Statements that are a keyword on its own.
BARE = {
    "END": "END", "STOP": "STOP", "SYSTEM": "SYSTEM", "BEEP": "BEEP",
    "TRON": "TRON", "TROFF": "TROFF", "RESET": "RESET", "RETURN": "RETURN",
    "WEND": "WEND", "CLS": "CLS", "SLEEP": "SLEEP_BARE", "RANDOMIZE":
    "RANDOMIZE_BARE", "FILES": "FILES_BARE", "SHELL": "SHELL_BARE",
    "VIEW": "VIEW_BARE", "WINDOW": "WINDOW_BARE", "PALETTE": "PALETTE_BARE",
    "RESUME": "RESUME",
}

#: Statements that take a list of expressions and nothing else.
LIST_STATEMENTS = {
    "CLS": "CLS", "LOCATE": "LOCATE", "COLOR": "COLOR", "SOUND": "SOUND", "PLAY": "PLAY",
    "DRAW": "DRAW",
    "KILL": "KILL", "CHDIR": "CHDIR", "MKDIR": "MKDIR",
    "RMDIR": "RMDIR", "ENVIRON": "ENVIRON", "POKE": "POKE", "OUT": "OUT",
    "SWAP": "SWAP",
    "ERROR": "ERROR", "RANDOMIZE": "RANDOMIZE", "SLEEP": "SLEEP",
    "FILES": "FILES", "SHELL": "SHELL", "PCOPY": "PCOPY", "SCREEN": "SCREEN",
    "CHAIN": "CHAIN", "BLOAD": "BLOAD", "BSAVE": "BSAVE", "CLEAR": "CLEAR",
    "LOCK": "LOCK", "UNLOCK": "UNLOCK", "SEEK": "SEEK_STMT",
    "GET": "GET_FILE", "PUT": "PUT_FILE",
    "IOCTL": "IOCTL",
 "WINDOW": "WINDOW", "PALETTE": "PALETTE",
    "CLOSE": "CLOSE",
}

#: Statements whose arguments each carry a marker saying whether they were
#: written. Every one of these can leave arguments out.
MARKED = {"COLOR", "LOCATE", "SCREEN", "CLEAR"}

#: Statements that write a marker when written on their own.
BARE_MARKED = {"CLS"}

#: Statements another statement can follow without a colon between them.
_CONTINUES = {"IF_THEN_LINE", "IF_GOTO", "IF_THEN_GOTO", "ELSE",
              "ELSE_AFTER_LINE", "ELSEIF", "ELSE_LINE", "COLON"}

#: Statements that can be followed by "=" and still be statements.
_ASSIGNABLE = {"LET", "CONST", "DEF", "FOR", "MID$", "LSET", "RSET",
               "DATE$", "TIME$"}

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


def split_records(toks: List[Token], records) -> List[Token]:
    """Break "Rec.Field" apart when Rec is known to be a record.

    A period is an ordinary name character in BASIC, so the lexer cannot tell
    a field access from a name that merely has a period in it. Only the
    declarations can, which is why this needs the set of record variables.
    """
    if not records:
        return toks
    out: List[Token] = []
    for t in toks:
        head = t.text.split(".")[0]
        if (t.kind is Kind.NAME and "." in t.text
                and head.lower() in records):
            out.append(Token(Kind.NAME, head, column=t.column))
            for field in t.text.split(".")[1:]:
                out.append(Token(Kind.OP, ".", column=t.column))
                out.append(Token(Kind.NAME, field, column=t.column))
            out[-1].suffix = t.suffix
        else:
            out.append(t)
    return out


def parse_line(text: str, records=(), column: int = 0) -> List[Statement]:
    """Parse one source line into its statements.

    The leading label, if any, is not consumed here: a caller that cares about
    labels should strip it first, because whether a leading name is a label
    depends on the colon after it rather than on anything in the statement.
    """
    toks = split_records(lex(text), records)
    if column:
        for t in toks:
            t.column += column
    cur = Cursor(toks, text)
    out: List[Statement] = []
    while not cur.at_end():
        if cur.at(":"):
            cur.take()
            if out:
                out[-1].emits.append(Emit("COLON"))
            continue
        before = cur.i
        out.append(Statement(_parse_statement(cur)))
        if cur.i == before:
            raise ParseError(f"made no progress at {cur.tok.text!r}")
        if not (cur.at_end() or cur.at(":", "ELSE") or cur.tok.text == "'"
                or out[-1].emits and out[-1].emits[-1].mnemonic in _CONTINUES):
            # Statements are separated by a colon. The exceptions are the
            # ones that introduce another statement on the same line, as
            # "IF X THEN Y = 1" does. QB keeps any other line as text.
            raise ParseError(f"unexpected {cur.tok.text!r} after a statement")
    return out


def _parse_statement(cur: Cursor) -> List[Emit]:
    t = cur.tok
    if t.kind is Kind.OP and t.text == "'":
        cur.take()
        body = cur.take()
        marker = _metacommand(body.text)
        text = body.text[:len(body.text) - len(body.text.lstrip())] if marker \
            else body.text
        return [Emit("REM", (t.column,), text)] + marker
    word = ""
    if t.kind is Kind.NAME:
        spelled = (t.text + t.suffix).upper()
        word = spelled if spelled in _HANDLERS else (
            t.text.upper() if not t.suffix else "")
    ahead = cur.tokens[cur.i + 1]
    if (word and ahead.kind is Kind.OP and ahead.text == "="
            and word not in _ASSIGNABLE):
        # "Name = x" is an assignment, whatever the keyword table says.
        word = ""

    if word == "REM":
        cur.take()
        body = cur.take()
        marker = _metacommand(body.text)
        text = body.text[:len(body.text) - len(body.text.lstrip())] if marker \
            else body.text
        return [Emit("REM_META", (), text)] + marker
    if word in BARE and _ends_statement(cur, 1):
        cur.take()
        if word in BARE_MARKED:
            # "CLS" on its own still writes the marker for its empty list.
            return [Emit("ARG"), Emit(BARE[word])]
        return [Emit(BARE[word])]
    handler = _HANDLERS.get(word)
    if handler is not None:
        cur.take()
        return handler(cur)
    if word in LIST_STATEMENTS:
        cur.take()
        return _list_statement(cur, LIST_STATEMENTS[word], word in GRAPHICS,
                               marked=word in MARKED)
    if (t.kind is Kind.NAME and cur.i + 1 < len(cur.tokens)
            and cur.tokens[cur.i + 1].text.upper() == "AS"):
        # Inside a TYPE block a line is just "name AS type", which is not a
        # statement anywhere else.
        cur.take()
        return [Emit("TYPE_MEMBER", (t.text,))] + _as_clause(cur)
    return _assignment_or_call(cur)


def _metacommand(text: str) -> List[Emit]:
    """$DYNAMIC and $STATIC are comments that QB also marks as instructions."""
    word = text.strip().upper().split(":")[0]
    if word == "$DYNAMIC":
        return [Emit("META_DYNAMIC")]
    if word == "$STATIC":
        return [Emit("META_STATIC")]
    return []


def _ends_statement(cur: Cursor, ahead: int = 0) -> bool:
    """Whether the token `ahead` places on is the end of a statement."""
    i = cur.i + ahead
    if i >= len(cur.tokens):
        return True
    t = cur.tokens[i]
    return t.kind is Kind.END or t.text in (":", "'")


def _expression_list(cur: Cursor, coords: bool = False, marked: bool = False,
                     shapes: bool = False):
    """The arguments of a statement, and what its trailing word weighs.

    A statement that takes a list marks every argument: one written counts
    two towards the trailing word and one left out counts one. An empty list
    is still written as a single marker.
    """
    out: List[Emit] = []
    weight = 0
    if _ends_statement(cur):
        return (out + [Emit("ARG")], 0) if marked else out
    while True:
        if cur.at(","):
            weight += 1
        else:
            weight += 2
            if marked:
                out.append(Emit("ARG"))
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
        elif shapes and cur.tok.text.upper() in ("B", "BF"):
            # LINE's box flag, which is not an argument at all.
            out.append(Emit("SHAPE", (cur.take().text.upper(),)))
        elif cur.at(","):
            # An argument left out keeps its place: "LOCATE , , 1".
            out.append(Emit("ARG_OMITTED"))
        else:
            out += parse_expression(cur)
        if not cur.at(","):
            break
        cur.take()
    return (out, weight) if marked else out


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


def _list_statement(cur: Cursor, mnemonic: str, coords: bool = False,
                    marked: bool = False) -> List[Emit]:
    args, weight = _expression_list(cur, coords, marked=True)
    if not marked:
        args = [e for e in args if e.mnemonic != "ARG"]
        weight = None
    return args + [Emit(mnemonic, (weight,))]


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
        out.append(Emit("FILE_NUMBER"))
        out.append(Emit("PRINT_FILE"))
        if cur.at(","):
            cur.take()
    while cur.at(";") or cur.at(","):
        last = cur.take().text
        while cur.at(";") or cur.at(","):
            last = cur.take().text
        out.append(Emit("PRINT_FUNC_SEMI" if last == ";" else "PRINT_FUNC_COMMA"))
    if cur.at("USING"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("PRINT_USING"))
        if cur.at(";"):
            cur.take()
    while not _ends_statement(cur):
        out += parse_expression(cur)
        if cur.at(";"):
            after = bool(out) and out[-1].mnemonic in ("TAB", "SPC")
            last = cur.take().text
            # QB writes "TAB(5); , X", so a comma can follow a semicolon.
            while after and (cur.at(",") or cur.at(";")):
                last = cur.take().text
            if after:
                out.append(Emit("PRINT_FUNC_SEMI" if last == ";"
                                else "PRINT_FUNC_COMMA"))
            else:
                out.append(Emit("PRINT_SEMI"))
                while cur.at(",") or cur.at(";"):
                    out.append(Emit("PRINT_COMMA" if cur.take().text == ","
                                    else "PRINT_SEMI"))
        elif cur.at(","):
            cur.take()
            after = bool(out) and out[-1].mnemonic in ("TAB", "SPC")
            out.append(Emit("PRINT_FUNC_COMMA" if after else "PRINT_COMMA"))
        else:
            out.append(Emit("PRINT_ITEM"))
            return out
    out.append(Emit("PRINT_NEWLINE"))
    return out


def _at_line_number(cur: Cursor) -> bool:
    """Whether what follows is a line number and nothing else."""
    return (cur.tok.kind is Kind.NUMBER
            and cur.tok.numtype in ("integer", "long")
            and (_ends_statement(cur, 1) or cur.tokens[cur.i + 1].text.upper()
                 == "ELSE"))


def _else_after_line(cur: Cursor) -> List[Emit]:
    """The ELSE of an IF that jumped, which QB spells differently."""
    if not cur.at("ELSE"):
        return []
    cur.take()
    out = [Emit("ELSE_AFTER_LINE")]
    if _at_line_number(cur):
        out.append(Emit("ELSE_LINE", (cur.take().text,)))
    return out


def _st_if(cur: Cursor) -> List[Emit]:
    cond = parse_expression(cur)
    if cur.at("GOTO"):
        cur.take()
        cond += [Emit("IF_GOTO", (cur.take().text,))]
        return cond + _else_after_line(cur)
    if cur.at("THEN"):
        cur.take()
    if _ends_statement(cur):
        return cond + [Emit("IF_THEN_BLOCK")]
    if _at_line_number(cur):
        cond += [Emit("IF_THEN_GOTO", (cur.take().text,))]
        return cond + _else_after_line(cur)
    return cond + [Emit("IF_THEN_LINE")]


def _st_for(cur: Cursor) -> List[Emit]:
    # The control variable is loaded first, then its bounds, then the keyword,
    # which names it a second time.
    target = cur.take()
    cur.expect("=")
    out = [Emit("LOAD", (target.text, target.suffix))]
    out += parse_expression(cur)
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
    out: List[Emit] = []
    for n in names:
        out += [Emit("LOAD", (n.text, n.suffix)), Emit("NEXT", (n.text, n.suffix))]
    return out


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


#: The comparison a "CASE IS" was written with, as QB spells it.
_CASE_IS = {"=": "CASE_IS_EQ", "<": "CASE_IS_LT", ">": "CASE_IS_GT",
            "<=": "CASE_IS_LE", "=<": "CASE_IS_LE", ">=": "CASE_IS_GE",
            "=>": "CASE_IS_GE", "<>": "CASE_IS_NE", "><": "CASE_IS_NE"}


def _st_case(cur: Cursor) -> List[Emit]:
    if cur.at("ELSE"):
        cur.take()
        return [Emit("CASE_ELSE")]
    out: List[Emit] = []
    while True:
        if cur.at("IS"):
            cur.take()
            op = cur.take().text
            if cur.tok.text in ("=", ">"):        # "<=" written as two tokens
                op += cur.take().text
            out += parse_expression(cur)
            out.append(Emit(_CASE_IS[op]))
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


def _st_else(cur: Cursor, elseif: bool = False) -> List[Emit]:
    if elseif:
        cond = parse_expression(cur)
        if cur.at("THEN"):
            cur.take()
        return cond + [Emit("ELSEIF")]
    if _at_line_number(cur):
        return [Emit("ELSE"), Emit("ELSE_LINE", (cur.take().text,))]
    return [Emit("ELSE")]


#: The type codes "AS" can name, the inverse of the renderer's table.
_TYPE_BY_KEYWORD = {v: k for k, v in tokens.TYPE_KEYWORDS.items()}
_TYPE_BY_KEYWORD["ANY"] = 0

def _st_declaration(kind: str):
    """DIM, REDIM, STATIC, COMMON and SHARED all declare a list of names.

    QB writes the keywords in reverse, so "DIM SHARED" is stored as SHARED
    then DIM, and REDIM comes after the names rather than before them.
    """

    def handler(cur: Cursor) -> List[Emit]:
        out: List[Emit] = []
        if cur.at("SHARED") and kind != "SHARED":
            cur.take()
            out.append(Emit("SHARED"))
        block = None
        if kind == "COMMON" and cur.at("/"):
            cur.take()
            block = cur.take().text
            cur.expect("/")
        if kind != "REDIM":
            out.append(Emit("SHARED_TYPED" if kind == "SHARED" else kind,
                            (block,)))
        while not _ends_statement(cur):
            out += _declared_item(cur, array_access="ARRAY" if kind == "REDIM"
                                  else "ARRDECL")
            if kind == "REDIM":
                out.append(Emit("REDIM"))
            if not cur.at(","):
                break
            cur.take()
        return out

    return handler


def _rest_of(cur: Cursor) -> str:
    """What is left of the statement, for a look-ahead that needs the text."""
    return " ".join(t.text for t in cur.tokens[cur.i:] if t.kind is not Kind.END)


def _declared_item(cur: Cursor, array_access: str = "ARRDECL") -> List[Emit]:
    """One name in a declaration, with its bounds and its declared type."""
    name = cur.take()
    out: List[Emit] = []
    dims = 0
    empty = False
    if cur.at("("):
        cur.take()
        empty = cur.at(")")
        if not cur.at(")"):
            while True:
                lower = parse_expression(cur)
                if cur.at("TO"):
                    cur.take()
                    out += lower + parse_expression(cur)
                else:
                    # Only an upper bound was written; the marker says so.
                    out.append(Emit("DIM_ARRAY"))
                    out += lower
                dims += 1
                if not cur.at(","):
                    break
                cur.take()
        cur.expect(")")
    out += _as_clause(cur)
    if dims or empty:
        out.append(Emit(array_access, (name.text, name.suffix, dims * 2)))
    else:
        out.append(Emit("DECL", (name.text, name.suffix)))
    return out


def _as_clause(cur: Cursor) -> List[Emit]:
    """An "AS type" written on a declaration, if there is one."""
    if not cur.at("AS"):
        return []
    column = cur.tok.column
    cur.take()
    word = cur.take()
    named = word.text.upper()
    if named == "STRING" and cur.at("*"):
        cur.take()
        return [Emit("FIXED_STRING", (int(cur.take().text), column))]
    code = _TYPE_BY_KEYWORD.get(named)
    if code is not None:
        return [Emit("AS_TYPE", (code, column))]
    return [Emit("AS_USER_TYPE", (word.text, column))]


#: What LINE's trailing word says was written after the colour.
_LINE_SHAPE = {"": 0, "B": 1, "BF": 2}


def _st_view(cur: Cursor) -> List[Emit]:
    """VIEW, VIEW SCREEN and VIEW PRINT are three different statements."""
    if cur.at("PRINT"):
        cur.take()
        if _ends_statement(cur):
            return [Emit("VIEW_PRINT_BARE")]
        out = parse_expression(cur)
        cur.expect("TO")
        return out + parse_expression(cur) + [Emit("VIEW_PRINT")]
    screen = cur.at("SCREEN")
    if screen:
        cur.take()
    if _ends_statement(cur):
        return [Emit("VIEW_BARE")]
    out = _corners(cur)
    given = 0
    while cur.at(","):
        cur.take()
        if cur.at(",") or _ends_statement(cur):
            out.append(Emit("ARG"))
        else:
            out += parse_expression(cur)
        given += 1
    out += [Emit("ARG")] * max(0, 2 - given)
    return out + [Emit("VIEW_SCREEN" if screen else "VIEW")]


def _st_bload(cur: Cursor) -> List[Emit]:
    out = parse_expression(cur)
    if not cur.at(","):
        return out + [Emit("BLOAD_ONE")]
    cur.take()
    return out + parse_expression(cur) + [Emit("BLOAD")]


def _corners(cur: Cursor) -> List[Emit]:
    """"(x1, y1)-(x2, y2)" written as four plain values."""
    out: List[Emit] = []
    for _ in range(2):
        cur.expect("(")
        out += parse_expression(cur)
        cur.expect(",")
        out += parse_expression(cur)
        cur.expect(")")
        if cur.at("-"):
            cur.take()
    return out


def _st_view_screen(cur: Cursor) -> List[Emit]:
    """VIEW SCREEN, whose corners are four plain numbers rather than a pair."""
    out: List[Emit] = []
    for _ in range(2):
        cur.expect("(")
        out += parse_expression(cur)
        cur.expect(",")
        out += parse_expression(cur)
        cur.expect(")")
        if cur.at("-"):
            cur.take()
    while cur.at(","):
        cur.take()
        if not (cur.at(",") or _ends_statement(cur)):
            out.append(Emit("ARG"))
            out += parse_expression(cur)
    return out + [Emit("VIEW_SCREEN")]


def _st_plot(word: str):
    """PSET and PRESET, with or without a colour."""

    def handler(cur: Cursor) -> List[Emit]:
        out = _coordinate(cur)
        if not cur.at(","):
            return out + [Emit(f"{word}_NOCOLOR")]
        cur.take()
        return out + parse_expression(cur) + [Emit(word)]

    return handler


def _st_set(word: str):
    """LSET and RSET, which take a target and a value and no comparison."""

    def handler(cur: Cursor) -> List[Emit]:
        target = _parse_target(cur) or []
        cur.expect("=")
        reads = {"STORE": "LOAD", "ARRSET": "ARRAY", "FIELD_SET": "FIELD"}
        target = [Emit(reads.get(e.mnemonic, e.mnemonic), e.operands, e.text)
                  for e in target]
        return parse_expression(cur) + target + [Emit(word)]

    return handler


def _st_wait(cur: Cursor) -> List[Emit]:
    """WAIT, which is a different instruction when given a mask to xor."""
    out = parse_expression(cur)
    count = 1
    while cur.at(","):
        cur.take()
        out += parse_expression(cur)
        count += 1
    return out + [Emit("WAIT_XOR" if count > 2 else "WAIT")]


def _st_paint(cur: Cursor) -> List[Emit]:
    """PAINT, which marks the arguments left off the end of its list."""
    out = _coordinate(cur)
    given = 0
    while cur.at(","):
        cur.take()
        out += parse_expression(cur)
        given += 1
    return out + [Emit("ARG")] * max(0, 2 - given) + [Emit("PAINT")]


def _st_window(cur: Cursor) -> List[Emit]:
    if cur.at("SCREEN"):
        cur.take()
        keyword = "WINDOW_SCREEN"
    elif _ends_statement(cur):
        return [Emit("WINDOW_BARE")]
    else:
        keyword = "WINDOW"
    return _corners(cur) + [Emit(keyword)]


def _st_deftype(code: int):
    """DEFINT A-Z and its relatives, which set the default type of a range."""

    def handler(cur: Cursor) -> List[Emit]:
        letters: List[int] = []
        while cur.tok.kind is Kind.NAME:
            first = cur.take().text.upper()
            last = first
            if cur.at("-"):
                cur.take()
                last = cur.take().text.upper()
            letters += list(range(ord(first[0]) - 65, ord(last[0]) - 65 + 1))
            if not cur.at(","):
                break
            cur.take()
        return [Emit("DEFTYPE", (code, tuple(letters)))]

    return handler


def _st_name(cur: Cursor) -> List[Emit]:
    out = parse_expression(cur)
    cur.expect("AS")
    return out + parse_expression(cur) + [Emit("NAME")]


def _st_option(cur: Cursor) -> List[Emit]:
    """OPTION BASE, the one OPTION QuickBASIC has."""
    cur.expect("BASE")
    base = cur.take().text
    return [Emit("OPTION_BASE" if base == "1" else "OPTION_BASE_0")]


def _st_clock(mnemonic: str):
    """DATE$ = and TIME$ = set the system clock."""

    def handler(cur: Cursor) -> List[Emit]:
        cur.expect("=")
        return parse_expression(cur) + [Emit(mnemonic)]

    return handler


def _st_field(cur: Cursor) -> List[Emit]:
    """FIELD #n, width AS name$, ... which names parts of a file buffer."""
    out: List[Emit] = []
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("FILE_NUMBER"))
    else:
        out += parse_expression(cur)
    out.append(Emit("FIELD_STMT"))
    while cur.at(","):
        cur.take()
        out += parse_expression(cur)
        cur.expect("AS")
        out += parse_expression(cur)
        out.append(Emit("FIELD_ITEM"))
    return out


def _st_palette(cur: Cursor) -> List[Emit]:
    """PALETTE, or PALETTE USING, which reads a whole array of colours."""
    if cur.at("USING"):
        cur.take()
        name = cur.take()
        if cur.at("("):
            cur.take()
            out = parse_expression(cur)
            cur.expect(")")
        else:
            out = []
        return out + [Emit("ARRAY", (name.text, name.suffix,
                                     1 if out else 0x8000)),
                      Emit("PALETTE_USING")]
    return _list_statement(cur, "PALETTE")


def _st_restore(cur: Cursor) -> List[Emit]:
    if _ends_statement(cur):
        return [Emit("RESTORE")]
    return [Emit("RESTORE_LABEL", (cur.take().text,))]


def _st_return(cur: Cursor) -> List[Emit]:
    if _ends_statement(cur):
        return [Emit("RETURN")]
    return [Emit("RETURN_LABEL", (cur.take().text,))]


def _st_lprint(cur: Cursor) -> List[Emit]:
    return [Emit("LPRINT")] + _st_print(cur)


def _st_circle(cur: Cursor) -> List[Emit]:
    """CIRCLE, which says in its opcode whether a colour was written."""
    out = _coordinate(cur)
    cur.expect(",")
    out += parse_expression(cur)
    if not cur.at(","):
        return out + [Emit("CIRCLE")]
    cur.take()
    coloured = not (cur.at(",") or _ends_statement(cur))
    if coloured:
        out += parse_expression(cur)
    for mnemonic in ("CIRCLE_START", "CIRCLE_END", "CIRCLE_ASPECT"):
        if not cur.at(","):
            break
        cur.take()
        out += parse_expression(cur)
        out.append(Emit(mnemonic))
    return out + [Emit("CIRCLE_COLOR" if coloured else "CIRCLE")]


def _st_line(cur: Cursor) -> List[Emit]:
    """LINE draws, unless it is LINE INPUT, which reads."""
    if cur.at("INPUT"):
        cur.take()
        return _st_line_input(cur)
    out: List[Emit] = []
    if cur.at("(") or cur.at("STEP"):
        out += _coordinate(cur)
    if cur.at("-"):
        cur.take()
        out += _coordinate(cur, to=True)
    slots: List[Optional[List[Emit]]] = []
    while cur.at(","):
        cur.take()
        if cur.at(",") or _ends_statement(cur):
            slots.append(None)
        elif cur.tok.text.upper() in ("B", "BF"):
            slots.append([Emit("SHAPE", (cur.take().text.upper(),))])
        else:
            slots.append(parse_expression(cur))
    shape = ""
    written: List[List[Emit]] = []
    for slot in slots:
        if slot and slot[0].mnemonic == "SHAPE":
            shape = slot[0].operands[0]
            written.append(None)
        else:
            written.append(slot)
    coloured = bool(written and written[0])
    styled = len(written) > 2 and bool(written[2])
    for slot in written:
        if slot:
            out += slot
    keyword = ("LINE_STYLE" if styled else "LINE") if coloured else \
        ("LINE_STYLE_NOCOLOR" if styled else "LINE_NOCOLOR")
    return out + [Emit(keyword, (_LINE_SHAPE[shape],))]


def _st_line_input(cur: Cursor) -> List[Emit]:
    """LINE INPUT, from the keyboard or from a file."""
    out: List[Emit] = []
    if cur.at(";"):
        cur.take()
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("FILE_NUMBER"))
        out.append(Emit("INPUT_CHANNEL"))
        if cur.at(","):
            cur.take()
        out += parse_expression(cur)
        return out + [Emit("LINE_INPUT_FILE")]
    if cur.tok.kind is Kind.STRING:
        out.append(Emit("PUSH_STR", (), cur.take().text))
        out.append(Emit("INPUT_PROMPT"))
        if cur.at(",") or cur.at(";"):
            cur.take()
    out += parse_expression(cur)
    return out + [Emit("LINE_INPUT")]


#: The raster action PUT was given, which it stores as a number.
_PUT_ACTION = {"OR": 0, "AND": 1, "PRESET": 2, "PSET": 3, "XOR": 4}


def _st_get_put(word: str):
    """GET and PUT are two statements each: one on a file, one on the screen."""

    def handler(cur: Cursor) -> List[Emit]:
        if not (cur.at("(") or cur.at("STEP")):
            return _file_get_put(cur, word)
        out = _coordinate(cur)
        if cur.at("-"):
            cur.take()
            out += _coordinate(cur, to=True)
        cur.expect(",")
        target = cur.take()
        if cur.at("("):
            # "Buf(1)" names where in the array to start.
            cur.take()
            inner = parse_expression(cur)
            cur.expect(")")
            out += inner
            out.append(Emit("ARRAY", (target.text, target.suffix, 1)))
        else:
            out.append(Emit("ARRAY", (target.text, target.suffix, 0x8000)))
        if word == "GET":
            return out + [Emit("GET_GRAPHICS")]
        action = ""
        if cur.at(","):
            cur.take()
            action = cur.take().text.upper()
        return out + [Emit("PUT_GRAPHICS", (_PUT_ACTION.get(action, 0xFFFF),))]

    return handler


def _st_play(cur: Cursor) -> List[Emit]:
    """PLAY sounds a tune, unless it is turning its own trap on."""
    if cur.at("(") or (cur.tok.kind is Kind.NAME
                       and cur.tok.text.upper() in ("ON", "OFF", "STOP")):
        return _st_event("PLAY_EVENT")(cur)
    return _list_statement(cur, "PLAY")


def _file_get_put(cur: Cursor, word: str) -> List[Emit]:
    """GET #n, record, variable -- each part written or left out."""
    out: List[Emit] = []
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out.append(Emit("FILE_NUMBER"))
    else:
        out += parse_expression(cur)
    if not cur.at(","):
        return out + [Emit("GET_FILE_BARE" if word == "GET" else "PUT_FILE")]
    cur.take()
    record = not cur.at(",") and not _ends_statement(cur)
    if record:
        out += parse_expression(cur)
    if not cur.at(","):
        return out + [Emit(f"{word}_FILE")]
    cur.take()
    out += parse_expression(cur)
    suffix = "_VAR" if record else "_NOREC"
    return out + [Emit(f"{word}_FILE{suffix}", (0,))]


def _st_event(mnemonic: str):
    """COM(1) ON and its relatives: a channel, the device, then the state."""

    def handler(cur: Cursor) -> List[Emit]:
        out: List[Emit] = []
        if cur.at("("):
            cur.take()
            out += parse_expression(cur)
            cur.expect(")")
        out.append(Emit(mnemonic))
        state = cur.take().text.upper() if not _ends_statement(cur) else "ON"
        return out + [Emit({"OFF": "EVENT_OFF", "STOP": "EVENT_STOP"}
                           .get(state, "EVENT_ON"))]

    return handler


def _st_call(cur: Cursor, mnemonic: str = "CALL") -> List[Emit]:
    """CALL written out, which names the procedure in the opcode itself."""
    name = cur.take()
    args: List[Emit] = []
    count = 0
    if cur.at("("):
        cur.take()
        if not cur.at(")"):
            while True:
                args += parse_expression(cur)
                count += 1
                if not cur.at(","):
                    break
                cur.take()
        cur.expect(")")
    return args + [Emit(mnemonic, (name.text, count))]


def _st_calls(cur: Cursor) -> List[Emit]:
    """CALLS, which passes everything by segmented address."""
    return _st_call(cur, "CALLS")


def _st_run(cur: Cursor) -> List[Emit]:
    """RUN starts over, at a line, or with another program."""
    if _ends_statement(cur):
        return [Emit("RUN_BARE")]
    if cur.tok.kind is Kind.STRING:
        return [Emit("PUSH_STR", (), cur.take().text), Emit("RUN_FILE")]
    return [Emit("RUN_LINE", (cur.take().text,))]


def _st_let(cur: Cursor) -> List[Emit]:
    return [Emit("LET")] + _assignment_or_call(cur)


def _st_const(cur: Cursor) -> List[Emit]:
    out = [Emit("CONST")]
    while True:
        name = cur.take()
        cur.expect("=")
        out += parse_expression(cur)
        out.append(Emit("STORE", (name.text, name.suffix)))
        if not cur.at(","):
            break
        cur.take()
    return out


#: The devices an event trap can name.
_EVENTS = {"COM": "COM_EVENT", "KEY": "KEY_EVENT", "PEN": "PEN_EVENT",
           "PLAY": "PLAY_EVENT", "STRIG": "STRIG_EVENT",
           "TIMER": "TIMER_EVENT", "UEVENT": "UEVENT", "SIGNAL":
           "SIGNAL_EVENT"}


def _st_on(cur: Cursor) -> List[Emit]:
    """ON ERROR GOTO, an event trap, or an ON ... GOTO/GOSUB jump table."""
    if cur.tok.kind is Kind.NAME and cur.tok.text.upper() in _EVENTS:
        device = _EVENTS[cur.take().text.upper()]
        out: List[Emit] = []
        if cur.at("("):
            cur.take()
            out += parse_expression(cur)
            cur.expect(")")
        if out:
            device = {"PLAY_EVENT": "PLAY_EVENT_N",
                      "TIMER_EVENT": "TIMER_SELECT"}.get(device, device)
        out.append(Emit(device))
        cur.expect("GOSUB")
        return out + [Emit("ON_EVENT_GOSUB", (cur.take().text,))]
    if cur.at("ERROR"):
        cur.take()
        cur.expect("GOTO")
        return [Emit("ON_ERROR", (cur.take().text,))]
    out = parse_expression(cur)
    word = cur.take().text.upper()
    targets = [cur.take().text]
    while cur.at(","):
        cur.take()
        targets.append(cur.take().text)
    return out + [Emit("ON_GOSUB" if word == "GOSUB" else "ON_GOTO",
                       tuple(targets))]


def _st_type(cur: Cursor) -> List[Emit]:
    return [Emit("TYPE", (cur.take().text,))]


def _prompt_flags(semicolon: bool, prompt: bool, comma: bool) -> int:
    """How INPUT records the punctuation around its prompt.

    A prompt is worth four, writing a comma after it rather than a semicolon
    adds one, and a semicolon before it adds two.
    """
    return (4 if prompt else 0) + (1 if comma else 0) + (2 if semicolon else 0)


def _input_head(cur: Cursor):
    """The part INPUT and LINE INPUT share: a channel, or a prompt."""
    out: List[Emit] = []
    semicolon = False
    if cur.at(";"):
        cur.take()
        semicolon = True
    if cur.at("#"):
        cur.take()
        out += parse_expression(cur)
        out += [Emit("FILE_NUMBER"), Emit("INPUT_CHANNEL")]
        if cur.at(","):
            cur.take()
        return out, None
    prompt = comma = False
    if cur.tok.kind is Kind.STRING:
        out.append(Emit("PUSH_STR", (), cur.take().text))
        prompt = True
        if cur.at(",") or cur.at(";"):
            comma = cur.take().text == ","
    return out, _prompt_flags(semicolon, prompt, comma)


def _st_input(cur: Cursor) -> List[Emit]:
    """INPUT, which marks every variable it will read into."""
    out, flags = _input_head(cur)
    if flags is not None:
        out.append(Emit("INPUT_PROMPT", (flags,)))
    while not _ends_statement(cur):
        out += parse_expression(cur)
        out.append(Emit("INPUT_VARS"))
        if not cur.at(","):
            break
        cur.take()
    return out + [Emit("INPUT")]


def _st_line_input(cur: Cursor) -> List[Emit]:
    """LINE INPUT, which reads one whole line and so takes one variable."""
    out, flags = _input_head(cur)
    out += parse_expression(cur)
    return out + [Emit("INPUT_VARS_END", (flags or 0,))]


#: What KEY's trailing word says was written after the keyword.
_KEY_MODE = {"OFF": 0, "ON": 1, "LIST": 2}


def _st_mid(cur: Cursor) -> List[Emit]:
    """MID$(A$, n) = B$, which writes into a string rather than reading it."""
    cur.expect("(")
    target = parse_expression(cur)
    cur.expect(",")
    start = parse_expression(cur)
    length: List[Emit] = []
    if cur.at(","):
        cur.take()
        length = parse_expression(cur)
    cur.expect(")")
    cur.expect("=")
    value = parse_expression(cur)
    return start + length + value + target + \
        [Emit("MID_ASSIGN3" if length else "MID_ASSIGN2")]


def _st_key(cur: Cursor) -> List[Emit]:
    """KEY turns the soft-key display on and off, sets one, or traps one."""
    if cur.at("("):
        return _st_event("KEY_EVENT")(cur)
    word = cur.tok.text.upper() if cur.tok.kind is Kind.NAME else ""
    if word in _KEY_MODE and _ends_statement(cur, 1):
        cur.take()
        return [Emit("KEY_MODE", (_KEY_MODE[word],))]
    return _list_statement(cur, "KEY")


def _st_erase(cur: Cursor) -> List[Emit]:
    """ERASE names whole arrays rather than reading them."""
    out: List[Emit] = []
    count = 0
    while not _ends_statement(cur):
        name = cur.take()
        out.append(Emit("ARRAY", (name.text, name.suffix, 0x8000)))
        count += 1
        if not cur.at(","):
            break
        cur.take()
    return out + [Emit("ERASE", (count,))]


def _st_width(cur: Cursor) -> List[Emit]:
    """WIDTH on the screen, on a file, or on the printer."""
    if cur.at("#"):
        cur.take()
        out = parse_expression(cur) + [Emit("FILE_NUMBER")]
        cur.expect(",")
        return out + parse_expression(cur) + [Emit("WIDTH_FILE")]
    if cur.at("LPRINT"):
        cur.take()
        return parse_expression(cur) + [Emit("WIDTH_LPRINT")]
    out = parse_expression(cur)
    if cur.at(","):
        cur.take()
        return out + parse_expression(cur) + [Emit("WIDTH")]
    # Only the width was given, and the marker stands for the rows.
    return out + [Emit("ARG"), Emit("WIDTH")]


def _st_resume(cur: Cursor) -> List[Emit]:
    if cur.at("NEXT"):
        cur.take()
        return [Emit("RESUME_NEXT")]
    if _ends_statement(cur):
        return [Emit("RESUME")]
    return [Emit("RESUME_LABEL", (cur.take().text,))]


def _st_read(cur: Cursor) -> List[Emit]:
    out: List[Emit] = []
    while not _ends_statement(cur):
        out += parse_expression(cur)
        out.append(Emit("READ"))
        if not cur.at(","):
            break
        cur.take()
    return out


def _st_def(cur: Cursor) -> List[Emit]:
    """DEF SEG, with or without an address, and DEF FN."""
    if cur.at("SEG"):
        cur.take()
        if cur.at("="):
            cur.take()
            return parse_expression(cur) + [Emit("DEF_SEG_TO")]
        return [Emit("DEF_SEG")]
    name = cur.take()
    params = _parameter_list(cur)
    head = [Emit("DEF_FN", (name.text, name.suffix, tuple(params or ())))]
    if not cur.at("="):
        return head
    column = cur.tok.column
    cur.take()
    return head + parse_expression(cur) + [Emit("DEF_FN_END_LINE", (column,))]


def _st_write(cur: Cursor) -> List[Emit]:
    """WRITE, which lays its list out the way PRINT does."""
    out: List[Emit] = [Emit("WRITE_FILE")]
    if _ends_statement(cur):
        return out + [Emit("PRINT_NEWLINE")]
    items = _st_print(cur)
    if not any(e.mnemonic.startswith("PRINT_")
               and e.mnemonic not in ("PRINT_FILE", "PRINT_NEWLINE")
               for e in items):
        raise ParseError("WRITE needs something to write")
    return out + items


#: The word OPEN stores for each mode keyword.
_OPEN_MODES = {"INPUT": 1, "OUTPUT": 2, "RANDOM": 4, "APPEND": 8, "BINARY": 32}


def _st_open(cur: Cursor) -> List[Emit]:
    out = parse_expression(cur)
    if cur.at(","):
        # The old form, where the mode is a string: OPEN "R", #1, "F", 128.
        cur.take()
        hashed = cur.at("#")
        if hashed:
            cur.take()
        out += parse_expression(cur)
        if hashed:
            out.append(Emit("FILE_NUMBER"))
        while cur.at(","):
            cur.take()
            out += parse_expression(cur)
        return out + [Emit("OPEN_MODE_STRING")]
    mode = 0
    if cur.at("FOR"):
        cur.take()
        mode = _OPEN_MODES.get(cur.take().text.upper(), 0)
    if cur.at("ACCESS"):
        cur.take()
        access = 0
        while cur.tok.kind is Kind.NAME and cur.tok.text.upper() in ("READ", "WRITE"):
            access |= 1 if cur.take().text.upper() == "READ" else 2
        mode |= access << 8
    if cur.at("SHARED"):
        cur.take()
        mode |= 0x4000
    elif cur.at("LOCK"):
        cur.take()
        lock = 0
        while cur.tok.kind is Kind.NAME and cur.tok.text.upper() in ("READ", "WRITE"):
            lock |= 1 if cur.take().text.upper() == "READ" else 2
        mode |= {1: 0x30, 2: 0x20, 3: 0x10}.get(lock, 0) << 8
    cur.expect("AS")
    hashed = cur.at("#")
    if hashed:
        cur.take()
    out += parse_expression(cur)
    if hashed:
        out.append(Emit("FILE_NUMBER"))
    if cur.at("LEN"):
        cur.take()
        cur.expect("=")
        out += parse_expression(cur)
        return out + [Emit("OPEN_RANDOM", (mode,))]
    return out + [Emit("OPEN", (mode,))]


def _st_data(cur: Cursor) -> List[Emit]:
    """DATA, whose items the lexer has already handed over as raw text."""
    # The text is kept exactly as written: the spaces before a trailing
    # colon are what puts a comment after it back in its column.
    return [Emit("DATA", (), cur.take().text)]


def _parameter_list(cur: Cursor) -> Optional[List[tuple]]:
    """The parameters of a DECLARE, SUB or FUNCTION.

    Each comes back as (name, suffix, byval, seg, as_type, array). None means
    no list was written at all, which QB stores differently from an empty one.
    """
    params: List[tuple] = []
    if not cur.at("("):
        return None
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
                            tuple(params) if params is not None else None,
                            static))]


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
    "PRINT": _st_print,
    "IF": _st_if, "ELSE": _st_else,
    "ELSEIF": lambda cur: _st_else(cur, elseif=True),
    "FOR": _st_for, "NEXT": _st_next,
    "GOTO": _st_goto, "GOSUB": _st_gosub,
    "DO": _st_do, "LOOP": _st_loop, "WHILE": _st_while,
    "SELECT": _st_select, "CASE": _st_case,
    "END": _st_end, "EXIT": _st_exit,
    "DIM": _st_declaration("DIM"), "REDIM": _st_declaration("REDIM"),
    "STATIC": _st_declaration("STATIC"), "COMMON": _st_declaration("COMMON"),
    "SHARED": _st_declaration("SHARED"),
    "INPUT": _st_input, "OPEN": _st_open, "DATA": _st_data,
    "CALL": _st_call, "CALLS": _st_calls, "RUN": _st_run, "LET": _st_let, "CONST": _st_const, "ON": _st_on,
    "LINE": _st_line, "GET": _st_get_put("GET"), "PUT": _st_get_put("PUT"),
    "CIRCLE": _st_circle, "PAINT": _st_paint, "WINDOW": _st_window,
    "PSET": _st_plot("PSET"), "PRESET": _st_plot("PRESET"),
    "VIEW": _st_view, "BLOAD": _st_bload,
    "LSET": _st_set("LSET"), "RSET": _st_set("RSET"),
    "WAIT": _st_wait,
    "RESTORE": _st_restore, "RETURN": _st_return, "LPRINT": _st_lprint,
    "PALETTE": _st_palette, "FIELD": _st_field,
    "DATE$": _st_clock("DATE$_SET"), "TIME$": _st_clock("TIME$_SET"),
    "OPTION": _st_option, "NAME": _st_name,
    "DEFINT": _st_deftype(1), "DEFLNG": _st_deftype(2),
    "DEFSNG": _st_deftype(3), "DEFDBL": _st_deftype(4),
    "DEFSTR": _st_deftype(5),
    "WRITE": _st_write, "READ": _st_read, "DEF": _st_def,
    "KEY": _st_key, "ERASE": _st_erase, "WIDTH": _st_width, "MID$": _st_mid,
    "RESUME": _st_resume,
    "COM": _st_event("COM_EVENT"), "PEN": _st_event("PEN_EVENT"),
    "TIMER": _st_event("TIMER_EVENT"), "PLAY": _st_play,
    "STRIG": _st_event("STRIG_EVENT"), "UEVENT": _st_event("UEVENT"),
    "TYPE": _st_type,
}
