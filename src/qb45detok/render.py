"""Turn a decoded line back into source.

The token stream is reverse Polish, so rendering is a stack machine: values
push their text, operators pop theirs and push the combination, statements pop
what they need and emit a line.

Parenthesisation needs no precedence rules. QB records the parentheses the
programmer wrote as an explicit opcode (``016e``), redundant ones included --
which is why ``4 + INT(((320 - 1 + 1) * (2) + 7) / 8)`` round-trips with its
``(2)`` intact.
"""

from __future__ import annotations

import struct
from typing import List, Optional

from . import tokens
from .decode import DecodedSection, Instr, Line
from .reader import BinFile


def format_single(words) -> str:
    value = struct.unpack("<f", struct.pack("<HH", *words))[0]
    return _trim(_shortest(value, single=True))


def format_double(words) -> str:
    value = struct.unpack("<d", struct.pack("<HHHH", *words))[0]
    text = _trim(_shortest(value, single=False))
    # QB marks a whole-number double literal with "#", as in FnArea#(2#).
    return text + "#" if text.lstrip("-").isdigit() else text


def _shortest(value: float, single: bool) -> str:
    """The fewest digits that still read back as the same stored number.

    A single holds about seven digits, so ``1.00794`` comes back as
    1.0079400539... in double arithmetic. Printing the shortest round-tripping
    form recovers what was written.
    """
    limit = 9 if single else 17
    for digits in range(1, limit + 1):
        text = f"{value:.{digits}g}"
        back = float(text)
        if single:
            back = struct.unpack("<f", struct.pack("<f", back))[0]
        if back == value:
            return text
    return repr(value)


def _trim(text: str) -> str:
    """Write a number the way QB does: no trailing zeros, no leading zero."""
    if "e" in text or "E" in text:
        return text.upper().replace("E+0", "E+").replace("E-0", "E-")
    if text.endswith(".0"):
        text = text[:-2]
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


class RenderError(Exception):
    """Raised when a line cannot be rendered from its tokens."""


class Renderer:
    """Renders decoded lines back to source for one file."""

    #: Section kind byte that marks a procedure declared STATIC.
    STATIC_KIND = 0xB8

    #: Trailing word on LINE, giving the box style.
    LINE_SHAPES = {1: "B", 2: "BF"}

    #: Trailing word on PUT, giving the raster operation. All five confirmed.
    PUT_ACTIONS = {0: "OR", 1: "AND", 2: "PRESET", 3: "PSET", 4: "XOR"}

    #: How OPEN's trailing word names the access mode.
    OPEN_MODES = {1: "INPUT", 2: "OUTPUT", 4: "RANDOM", 8: "APPEND", 32: "BINARY"}

    #: The event selector opcodes render as KEY(n), STRIG(n), TIMER(n).
    EVENT_TEXT = {"STRIG_EVENT": "STRIG", "KEY_EVENT": "KEY", "TIMER_SELECT": "TIMER"}

    #: Statements whose arguments are simply everything left on the stack.
    LIST_STATEMENTS = frozenset({
        "READ", "LINE", "LINE_NOCOLOR", "LINE_TO", "PSET", "PSET_NOCOLOR",
        "PRESET", "PUT_GRAPHICS", "GET_GRAPHICS", "CIRCLE", "CIRCLE_COLOR",
        "PAINT", "WINDOW", "VIEW_PRINT", "SOUND", "PLAY", "BLOAD", "BSAVE",
        "SHELL", "KILL", "ERASE", "RANDOMIZE", "WIDTH", "DEF_SEG_TO",
        "KEY", "PALETTE", "SWAP",
        "RESTORE", "VIEW_PRINT", "ERASE", "DEF_SEG_TO", "FILES", "GET_FILE",
        "PUT_FILE", "GET_FILE_VAR", "PUT_FILE_VAR", "LOCK", "UNLOCK",
        "SEEK_STMT", "BSAVE", "PALETTE_USING",
    })

    #: Statements that introduce a list of declarations written after them.
    DECLARATION_HEADS = {
        "DIM": "DIM", "REDIM": "REDIM", "SHARED": "SHARED",
        "SHARED_TYPED": "SHARED", "STATIC": "STATIC", "COMMON": "COMMON",
        "TYPE_MEMBER": "",
    }

    def __init__(self, bf: BinFile):
        self.bf = bf
        self.section_kind: Optional[int] = None
        self.section_is_function = False

    # -- names -----------------------------------------------------------

    def name(self, ref: int) -> str:
        entry = self.bf.symbol(ref)
        if entry is None or entry.name is None:
            return f"<{ref:#06x}>"
        return entry.name

    def label(self, ref: int) -> str:
        entry = self.bf.symbol(ref)
        if entry is None or entry.label is None:
            return f"<{ref:#06x}>"
        return entry.label

    # -- one line --------------------------------------------------------

    def line(self, line: Line) -> str:
        stack: List[str] = []
        parts: List[str] = []  # (separator, text) for each statement
        seps: List[str] = []
        printing: List[str] = []
        using: Optional[str] = None
        declared_type: Optional[str] = None
        declared_col: Optional[int] = None
        pad_col: Optional[int] = None
        decl_heads: List[str] = []
        # How many entries on the stack are finished declarations rather than
        # values, so an array's bounds cannot swallow the name before it.
        declared_so_far = 0
        single_bound = False
        marks: List[bool] = []  # one per ARG: True when a value follows
        channel: Optional[str] = None  # the "#n" a file statement applies to
        field_channel: Optional[str] = None
        print_keyword = "PRINT"
        input_prompt: Optional[str] = None
        default_sep = ": "
        trailing_colon = False
        # A DIM/REDIM keyword can come after its declarations, so look ahead:
        # array subscripts inside a declaration are bounds, written "lo TO hi".
        declaring_line = any(i.mnemonic in self.DECLARATION_HEADS
                             for i in line.instrs)
        pending: Optional[str] = None  # a keyword prefixing the next statement
        comment_at: Optional[int] = None

        def pop(n: int = 1) -> List[str]:
            if len(stack) < n:
                raise RenderError(f"stack underflow rendering {line.offset:#x}")
            out = stack[-n:]
            del stack[-n:]
            return out

        def emit(text: str, sep: Optional[str] = None) -> None:
            nonlocal pending
            sep = default_sep if sep is None else sep
            if pending:
                text = f"{pending} {text}"
                pending = None
            parts.append(text)
            seps.append(sep)

        def take_args() -> List[str]:
            """Collect the arguments an ARG-marked statement was given."""
            nonlocal marks
            wanted = min(sum(marks), len(stack))
            values = pop(wanted) if wanted else []
            out, i = [], 0
            for supplied in marks:
                if supplied and i < len(values):
                    out.append(values[i])
                    i += 1
                else:
                    out.append("")
            marks = []
            return out

        for ins in line.instrs:
            op, mn = ins.op, ins.mnemonic
            if op is None:
                raise RenderError(f"unknown opcode {ins.code:#06x}")

            # -- values ---------------------------------------------------
            if mn == "PUSH_INT":
                stack.append(str(ins.operands[0]))
            elif mn == "PUSH_STR":
                stack.append('"' + (ins.text or "") + '"')
            elif mn == "PUSH_HEX":
                stack.append("&H%X" % ins.operands[0])
            elif mn == "PUSH_LONG":
                stack.append(str(ins.operands[0] | (ins.operands[1] << 16)))
            elif mn == "PUSH_SINGLE":
                stack.append(format_single(ins.operands))
            elif mn == "PUSH_DOUBLE":
                stack.append(format_double(ins.operands))
            elif op.form == "literal":
                stack.append(op.text)
            elif op.form == "var" and mn.startswith(("DECL", "ARRDECL")):
                text = self._declaration(ins, stack, declared_so_far, single_bound)
                single_bound = False
                if declared_type is not None:
                    # Each name carries its own AS clause: "DIM A AS X, B AS Y".
                    text += f" AS {declared_type}"
                    if pad_col is None:
                        pad_col = declared_col
                    declared_type = declared_col = None
                stack.append(text)
                declared_so_far = len(stack)
            elif (op.form == "var" and mn.startswith("ARRAY")
                  and (declaring_line or declared_type is not None)):
                # An array declared with empty parentheses has no bounds, and
                # anything already on the stack belongs to an earlier name.
                free = len(stack) - declared_so_far
                if ins.operands[0] == 0:
                    wanted = 0
                elif single_bound:
                    wanted = min(1, free)
                else:
                    wanted = min(free, 2)
                single_bound = False
                stack.append(self._value(ins, pop, bounds=wanted, declaring=True))
                declared_so_far = len(stack)
            elif op.form == "var" and mn.startswith(("LOAD", "ARRAY")):
                stack.append(self._value(ins, pop))
            elif op.form == "var" and mn.startswith(("STORE", "ARRSET")):
                target = self._target(ins, pop)
                (value,) = pop()
                emit(f"{target} = {value}")
            elif mn == "FIELD":
                (base,) = pop()
                stack.append(f"{base}.{self.name(ins.operands[0])}")
            elif mn == "FIELD_SET":
                (base,) = pop()
                (value,) = pop()
                emit(f"{base}.{self.name(ins.operands[0])} = {value}")
            elif mn == "PAREN":
                (inner,) = pop()
                stack.append(f"({inner})")
            elif mn == "TIMER_EVENT":
                stack.append("TIMER")
            elif mn in ("CIRCLE_START", "CIRCLE_END", "CIRCLE_ASPECT"):
                pass  # markers around CIRCLE's optional arguments
            elif mn in ("LSET", "RSET"):
                value, target = pop(2)
                emit(f"{op.text} {target} = {value}")
            elif mn == "FIELD_STMT":
                (field_channel,) = pop()
            elif mn == "FIELD_ITEM":
                width, target = pop(2)
                stack.append(f"{width} AS {target}")
            elif mn in ("STRIG_EVENT", "KEY_EVENT", "TIMER_SELECT"):
                (which,) = pop()
                stack.append(f"{self.EVENT_TEXT[mn]}({which})")
            elif mn in ("EVENT_ON", "EVENT_OFF", "EVENT_STOP"):
                (event,) = pop()
                emit(f"{event} {op.text}")
            elif mn == "ON_EVENT_GOSUB":
                (event,) = pop()
                emit(f"ON {event} GOSUB {self.label(ins.operands[0])}")
            elif mn in ("COORD", "COORD_TO", "COORD_STEP", "COORD_TO_STEP"):
                x, y = pop(2)
                lead = {"COORD": "", "COORD_TO": "-", "COORD_STEP": "STEP",
                        "COORD_TO_STEP": "-STEP"}[mn]
                stack.append(f"{lead}({x}, {y})")

            # -- operators ------------------------------------------------
            elif op.form == "infix":
                left, right = pop(2)
                stack.append(f"{left} {op.text} {right}")
            elif op.form == "prefix":
                (value,) = pop()
                stack.append(f"{op.text}{'' if op.text == '-' else ' '}{value}")
            elif op.form == "func":
                if op.arity:
                    stack.append(f"{op.text}({', '.join(pop(op.arity))})")
                else:
                    stack.append(op.text)

            # -- markers --------------------------------------------------
            elif mn == "ARG":
                marks.append(True)
            elif mn == "ARG_OMITTED":
                marks.append(False)
            elif mn == "COLON":
                # A colon with nothing after it is written out. 0017 can sit
                # past it and produces nothing, so look through it.
                rest = line.instrs[line.instrs.index(ins) + 1:]
                if all(x.mnemonic == "UNKNOWN_17" for x in rest):
                    trailing_colon = True
            elif mn == "DIM_ARRAY":
                single_bound = True
            elif mn == "CONST":
                pending = "CONST"
                default_sep = ", "   # "CONST TRUE = -1, FALSE = 0"
            elif mn in self.DECLARATION_HEADS and mn != "TYPE_MEMBER":
                # "DIM SHARED" arrives as SHARED then DIM, so the keywords
                # read back in reverse.
                decl_heads.insert(0, self.DECLARATION_HEADS[mn])
            elif mn == "REM_META":
                # The only form in the corpus; the text is not stored.
                emit("REM $DYNAMIC" if ins.operands[:1] == [1] else "REM")
            elif mn == "DEF_FN":
                emit("DEF " + self._def_fn(ins))
            elif mn == "DEF_FN_END_LINE":
                (value,) = pop()
                parts[-1] += f" = {value}"
            elif mn == "TYPE":
                emit(f"TYPE {self.name(ins.operands[1])}")
            elif mn == "DEFTYPE":
                emit(self._deftype(ins))
            elif mn == "AS_TYPE":
                declared_type = tokens.TYPE_KEYWORDS.get(ins.operands[0], "ANY")
                declared_col = ins.operands[1]
            elif mn == "AS_USER_TYPE":
                declared_type = self.name(ins.operands[0])
                declared_col = ins.operands[1]
            elif mn == "FIXED_STRING":
                declared_type = f"STRING * {ins.operands[1]}"
                declared_col = ins.operands[2]
            elif mn == "TYPE_MEMBER":
                decl_heads = [""]
                stack.append(self.name(ins.operands[0]))
            elif mn == "UNKNOWN_17":
                pass
            elif mn == "LET":
                pending = "LET"

            # -- text -----------------------------------------------------
            elif mn == "DATA":
                emit("DATA" + (ins.text or ""))
            elif mn == "REM":
                comment_at = ins.comment_column
                emit("'" + (ins.text or ""))
            elif mn == "TEXT_LINE":
                emit(ins.text or "")

            # -- files ----------------------------------------------------
            elif mn == "FILE_NUMBER":
                (number,) = pop()
                stack.append(f"#{number}")
            elif mn == "WRITE_FILE":
                print_keyword = "WRITE"
            elif mn == "LPRINT":
                print_keyword = "LPRINT"
            elif mn == "PRINT_FILE":
                (channel,) = pop()
            elif mn == "OPEN_RANDOM":
                argv = pop(len(stack))
                mode = self.OPEN_MODES.get(ins.operands[0] if ins.operands else 4, "RANDOM")
                target, chan = argv[0], argv[1]
                length = argv[2] if len(argv) > 2 else None
                text = f"OPEN {target} FOR {mode} AS {chan}"
                emit(text + (f" LEN = {length}" if length else ""))
            elif mn == "OPEN":
                argv = pop(len(stack))
                mode = self.OPEN_MODES.get(ins.operands[0] if ins.operands else 0)
                chan = argv[-1] if argv else ""
                target = ", ".join(argv[:-1])
                emit(f"OPEN {target} FOR {mode} AS {chan}" if mode
                     else f"OPEN {target} AS {chan}")
            elif mn == "CLOSE":
                argv = pop(len(stack))
                emit(f"CLOSE {', '.join(argv)}".rstrip())
            elif mn == "LINE_INPUT_FILE":
                (channel,) = pop()
            elif mn == "INPUT_VARS_END":
                argv = pop(len(stack))
                emit(f"LINE INPUT {channel}, {', '.join(argv)}")
                channel = None
            elif mn == "INPUT_PROMPT":
                (input_prompt,) = pop()
            elif mn == "INPUT_VARS":
                pass
            elif mn == "INPUT":
                argv = pop(len(stack))
                head = f"INPUT {input_prompt}, " if input_prompt else "INPUT "
                emit(head + ", ".join(argv))
                input_prompt = None

            # -- PRINT ----------------------------------------------------
            elif mn in ("PRINT_SEMI", "PRINT_COMMA"):
                (value,) = pop()
                printing.append(value + (";" if mn == "PRINT_SEMI" else ","))
            elif mn == "PRINT_ITEM":
                (value,) = pop()
                printing.append(value)
                emit(self._print(printing, using, print_keyword, channel))
                printing, using, channel = [], None, None
                print_keyword = "PRINT"
            elif mn == "PRINT_NEWLINE":
                emit(self._print(printing, using, print_keyword, channel))
                printing, using, channel = [], None, None
                print_keyword = "PRINT"

            # -- control flow ---------------------------------------------
            elif mn in ("IF_THEN_BLOCK", "ELSEIF"):
                (cond,) = pop()
                emit(f"{op.text} {cond} THEN")
            elif mn == "IF_THEN_LINE":
                (cond,) = pop()
                emit(f"IF {cond} THEN", sep=" ")
            elif mn == "ELSE" and parts:
                seps[-1] = " "
                emit("ELSE", sep=" ")
            elif mn in ("DO_UNTIL", "DO_WHILE", "LOOP_UNTIL", "LOOP_WHILE", "WHILE"):
                (cond,) = pop()
                emit(f"{op.text} {cond}")
            elif mn in ("FOR", "FOR_STEP"):
                count = 4 if mn == "FOR_STEP" else 3
                values = pop(count)
                text = f"FOR {values[0]} = {values[1]} TO {values[2]}"
                if count == 4:
                    text += f" STEP {values[3]}"
                emit(text)
            elif mn == "NEXT":
                emit(f"NEXT {stack.pop()}" if stack else "NEXT")
            elif mn in ("SELECT_CASE", "CASE", "CASE_IS_EQ", "CASE_IS_LT",
                        "CASE_IS_GT", "CASE_IS_GE"):
                (value,) = pop()
                if mn == "CASE" and parts and parts[-1].startswith("CASE"):
                    parts[-1] += f", {value}"   # "CASE 1, 2, 7, 8"
                else:
                    emit(f"{op.text} {value}")
            elif mn == "CASE_RANGE":
                low, high = pop(2)
                emit(f"CASE {low} TO {high}")
            elif mn == "ON_ERROR":
                target = ins.operands[0]
                emit("ON ERROR GOTO " + ("0" if target == 0xFFFF else self.label(target)))
            elif mn in ("GOTO", "GOSUB", "RESUME_LABEL"):
                emit(f"{op.text} {self.label(ins.operands[0])}")
            elif mn == "CLS":
                # CLS takes an optional mode argument. The space where that
                # argument would go is written even when it is absent, which
                # is why "CLS : END" has a space and "DO: LOOP" does not; a
                # trailing one at the end of a line is trimmed below.
                if stack:
                    emit(f"CLS {pop()[0]}")
                else:
                    emit("CLS " if marks else "CLS")
                marks = []
            elif mn in ("CALL", "CALL_IMPLICIT"):
                count = ins.operands[0]
                argv = pop(count) if count else []
                name = self.name(ins.operands[1])
                if mn == "CALL":
                    emit(f"CALL {name}({', '.join(argv)})" if argv else f"CALL {name}")
                else:
                    emit(f"{name} {', '.join(argv)}".rstrip())
            elif mn == "END_SUB":
                emit("END FUNCTION" if self.section_is_function else "END SUB")
            elif mn == "EXIT_SUB":
                emit("EXIT FUNCTION" if self.section_is_function else "EXIT SUB")
            elif mn in ("SUB", "FUNCTION", "DECLARE"):
                text = self._signature(ins)
                if mn != "DECLARE" and self.section_kind == self.STATIC_KIND:
                    text += " STATIC"
                emit(text)
            elif mn == "PRINT_USING":
                (using,) = pop()

            # -- everything else ------------------------------------------
            elif mn == "NAME" and len(stack) == 2:
                a, b = pop(2)
                emit(f"NAME {a} AS {b}")
            elif mn == "VIEW" and len(stack) >= 4:
                argv = pop(len(stack))
                head = f"({argv[0]}, {argv[1]})-({argv[2]}, {argv[3]})"
                rest = argv[4:]
                emit(("VIEW " + ", ".join([head] + rest)).rstrip())
            elif mn in ("WINDOW", "WINDOW_SCREEN") and len(stack) == 4:
                a, b, c, d = pop(4)
                emit(f"{op.text} ({a}, {b})-({c}, {d})")
            elif mn in self.LIST_STATEMENTS:
                argv = pop(len(stack)) if stack else []
                argv = self._merge_coords(argv)
                joiner = " TO " if mn == "VIEW_PRINT" else ", "
                if mn == "DEF_SEG_TO":
                    joiner = " = "
                text = f"{op.text} {joiner.join(argv)}".rstrip() if op.text else joiner.join(argv)
                if mn == "DEF_SEG_TO":
                    text = "DEF SEG = " + ", ".join(argv)
                if mn.startswith("LINE") and ins.operands:
                    shape = self.LINE_SHAPES.get(ins.operands[0])
                    if shape:
                        # LINE_NOCOLOR means the colour argument was omitted,
                        # and QB keeps the empty slot: "..., , B".
                        text += (", , " if mn == "LINE_NOCOLOR" else ", ") + shape
                elif mn == "PUT_GRAPHICS" and ins.operands:
                    action = self.PUT_ACTIONS.get(ins.operands[0])
                    if action:
                        text += f", {action}"
                emit(text)
            elif marks:
                argv = take_args()
                while argv and argv[-1] == "":
                    argv.pop()
                emit(f"{op.text} {', '.join(argv)}".rstrip())
            elif op.arity:
                emit(f"{op.text} {', '.join(pop(op.arity))}")
            elif op.text:
                emit(op.text)
            else:
                raise RenderError(f"no rendering for {mn}")

        if field_channel is not None and stack:
            emit(f"FIELD {field_channel}, " + ", ".join(pop(len(stack))))
        if printing or using:
            emit(self._print(printing, using, print_keyword, channel))
        if decl_heads and stack:
            names = pop(len(stack))
            if declared_type is not None:
                names[-1] += f" AS {declared_type}"
                if pad_col is None:
                    pad_col = declared_col
            body = f"{' '.join(decl_heads)} {', '.join(names)}".strip()
            if len(names) == 1 and pad_col and " AS " in body:
                # A lone declaration puts its AS clause at a recorded column,
                # which is how QB lines up the members of a TYPE block.
                head, _, tail = body.partition(" AS ")
                width = pad_col - line.indent
                body = head.ljust(max(width, len(head) + 1)) + "AS " + tail
            emit(body)
        if stack:
            raise RenderError(f"{len(stack)} values left on the stack")

        text = ""
        for i, part in enumerate(parts):
            if i == 0:
                text = part
            elif comment_at is not None and i == len(parts) - 1 and part.startswith("'"):
                text = text.ljust(max(comment_at - line.indent, len(text) + 1)) + part
            else:
                text += seps[i - 1] + part

        if trailing_colon:
            text += ":"
        prefix = " " * line.indent
        if line.labelled:
            entry = self.bf.symbol(line.label_ref)
            marker = entry.label if entry else f"{line.label_ref:#06x}"
            if entry is not None and entry.line_number is None:
                marker += ":"
            return f"{prefix}{marker}{(' ' + text) if text else ''}"
        # Trailing whitespace is kept: a comment can legitimately end in
        # spaces, and a whitespace-only line carries its indentation. The one
        # exception is the space CLS leaves for an argument it did not get,
        # which QB does not write at the end of a line.
        if text.endswith("CLS "):
            text = text[:-1]
        return prefix + text

    # -- helpers ---------------------------------------------------------

    @staticmethod
    @staticmethod
    def _merge_coords(argv: List[str]) -> List[str]:
        """``-(x, y)`` belongs to the coordinate before it, not after a comma."""
        out: List[str] = []
        for item in argv:
            if item.startswith(("-(", "-STEP(")) and out:
                out[-1] += item
            else:
                out.append(item)
        return out

    @staticmethod
    def _print(items: List[str], using: Optional[str] = None,
               keyword: str = "PRINT", channel: Optional[str] = None) -> str:
        head = keyword if using is None else f"{keyword} USING {using};"
        if channel:
            head = f"{keyword} {channel}," if using is None else \
                f"{keyword} {channel}, USING {using};"
        return (head + " " + " ".join(items)).rstrip()

    _SUFFIX_AT = {"LOAD": 4, "STORE": 5, "DECL": 4,
                  "ARRAY": 5, "ARRSET": 6, "ARRDECL": 7}

    def _split(self, mn: str):
        for prefix, cut in self._SUFFIX_AT.items():
            if mn.startswith(prefix):
                return prefix, mn[cut:]
        raise RenderError(f"not a variable opcode: {mn}")

    def _value(self, ins: Instr, pop, bounds: Optional[int] = None,
               declaring: bool = False) -> str:
        """A variable or array reference used as a value."""
        prefix, suffix = self._split(ins.mnemonic)
        if prefix in ("LOAD", "DECL"):
            return self.name(ins.operands[0]) + suffix
        return self._subscripted(ins, suffix, pop, bounds, declaring)

    def _target(self, ins: Instr, pop) -> str:
        """The left-hand side of an assignment."""
        prefix, suffix = self._split(ins.mnemonic)
        if prefix == "STORE":
            return self.name(ins.operands[0]) + suffix
        return self._subscripted(ins, suffix, pop)

    def _declaration(self, ins: Instr, stack: List[str], declared_so_far: int,
                     single_bound: bool = False) -> str:
        """A name being declared, with its bounds if it is an array.

        Inside a ``DIM``-style list the names already declared sit on the stack
        too, so only the values pushed since the last one can be this name's
        bounds. QB writes them as a single upper bound or as ``lo TO hi``.
        """
        prefix, suffix = self._split(ins.mnemonic)
        if prefix == "DECL":
            return self.name(ins.operands[0]) + suffix
        base = self.name(ins.operands[1]) + suffix
        free = len(stack) - declared_so_far
        limit = 0 if ins.operands[0] == 0 else (1 if single_bound else 2)
        bounds: List[str] = []
        while stack and free > 0 and len(bounds) < limit:
            bounds.insert(0, stack.pop())
            free -= 1
        return base + "(" + " TO ".join(bounds) + ")" if bounds else base + "()"

    def _subscripted(self, ins: Instr, suffix: str, pop, bounds: Optional[int] = None,
                     declaring: bool = False) -> str:
        count, ref = ins.operands[0], ins.operands[1]
        if declaring:
            # A declaration's subscripts are bounds: one upper bound, or
            # "lo TO hi".
            taken = pop(bounds or 0)
            return self.name(ref) + suffix + "(" + " TO ".join(taken) + ")"
        base = self.name(ref) + suffix
        if count == 0x8000:
            return base
        if count == 0:
            return base + "()"
        return base + "(" + ", ".join(pop(count)) + ")"

    def _signature(self, ins: Instr) -> str:
        sig = ins.signature
        if sig is None:
            return ins.op.text or ins.mnemonic
        name = self.name(sig.ref) + sig.return_suffix
        params = ", ".join(self._param(p) for p in sig.params)
        if ins.mnemonic == "DECLARE":
            keyword = "DECLARE FUNCTION" if sig.kind >> 8 == 2 else "DECLARE SUB"
        else:
            keyword = ins.mnemonic
        if not sig.listed:
            # "DECLARE FUNCTION Top10&" -- no parameter list was written.
            return f"{keyword} {name}"
        if ins.mnemonic == "DECLARE":
            # A DECLARE that has a list writes it even when it is empty.
            return f"{keyword} {name} ({params})"
        return f"{keyword} {name}" + (f" ({params})" if params else "")

    def _def_fn(self, ins: Instr) -> str:
        """``FnDouble (X)`` -- a DEF FN header, whose payload has a lead word."""
        from .decode import parse_signature

        sig = parse_signature(ins.payload[2:])
        if sig is None:
            return ins.mnemonic
        name = self.name(sig.ref) + sig.return_suffix
        params = ", ".join(self._param(p) for p in sig.params)
        return f"{name} ({params})" if params else name

    def _param(self, p) -> str:
        """One entry of a parameter list.

        The mode word records how it was written rather than just its type:
        a suffix (``Array#``), an array (``()``), ``BYVAL``, or an ``AS``
        clause. They combine, and ``Array#()`` puts the suffix before the
        parentheses.
        """
        text = self.name(p.ref)
        if p.has_suffix:
            text += p.suffix
        if p.is_array:
            text += "()"
        if p.has_as_clause:
            text += " AS " + self._type_name(p.type_code)
        return "BYVAL " + text if p.by_value else text

    #: The three DEFTYPE words are a link, then Q-Z plus the type code, then
    #: A-P with A at the top bit.
    DEFTYPE_KEYWORDS = {1: "DEFINT", 2: "DEFLNG", 3: "DEFSNG", 4: "DEFDBL", 5: "DEFSTR"}

    def _deftype(self, ins: Instr) -> str:
        """``DEFINT A-C`` and friends, read out of the two letter masks."""
        _, tail, head = ins.operands
        keyword = self.DEFTYPE_KEYWORDS.get(tail & 0x3F, "DEFINT")
        letters = [i for i in range(16) if head >> (15 - i) & 1]
        letters += [16 + i for i in range(10) if tail >> (15 - i) & 1]
        return f"{keyword} {self._ranges(letters)}"

    @staticmethod
    def _ranges(letters: List[int]) -> str:
        out, start = [], None
        for i, value in enumerate(letters):
            if start is None:
                start = value
            if i + 1 == len(letters) or letters[i + 1] != value + 1:
                a, b = chr(65 + start), chr(65 + value)
                out.append(a if a == b else f"{a}-{b}")
                start = None
        return ", ".join(out)

    def _type_name(self, code: int) -> str:
        if code == 0:
            return "ANY"
        return tokens.TYPE_KEYWORDS.get(code) or self.name(code)

    # -- whole sections --------------------------------------------------

    def safe_line(self, line: Line) -> str:
        """Render a line, or a marker naming the failure.

        A file with one statement the renderer cannot express is still worth
        emitting; the marker says which line to look at rather than losing the
        whole file.
        """
        try:
            return self.line(line)
        except (RenderError, ValueError, IndexError, KeyError) as exc:
            return f"{' ' * line.indent}' <qb45detok: {type(exc).__name__}: {exc}>"

    def file(self, sections: List[DecodedSection]) -> List[str]:
        """Every section in the order QB writes them out.

        The module text comes first, then the procedures sorted by name --
        which is the order QB's own Save As Text produces, not the order the
        sections sit in the file.
        """
        modules = [d for d in sections if d.section.is_module]
        procs = sorted((d for d in sections if not d.section.is_module),
                       key=lambda d: d.section.name.lower())
        out: List[str] = []
        # QB writes a DEF<type> line whenever the default type changes from
        # one section to the next. A procedure with no DEFtype record of its
        # own is back to the language default, which is single precision.
        current_type = self._deftype_of(modules[0]) if modules else None
        for ds in modules + procs:
            if not ds.section.is_module:
                wanted = self._deftype_of(ds) or "DEFSNG A-Z"
                if current_type is not None and wanted != current_type:
                    out.append(wanted)
                    current_type = wanted
            body = self.section(ds)
            if ds.section.is_module and procs and self._uses_dynamic(ds):
                # A module that turned on $DYNAMIC gets a matching $STATIC
                # written after it, one blank line past its last real line,
                # and that line stands in for the usual section separator.
                while body and not body[-1].strip():
                    body.pop()
                body += ["", "REM $STATIC"]
                out.extend(body)
                continue
            out.extend(body)
            if out and out[-1].strip():
                # QB separates sections with a blank line, but does not add
                # one when the section already ended with blank lines.
                out.append("")
        return out

    def _deftype_of(self, ds: DecodedSection) -> Optional[str]:
        """The DEF<type> a section declares, as source, or None."""
        for line in ds.lines:
            for ins in line.instrs:
                if ins.mnemonic == "DEFTYPE":
                    return self._deftype(ins)
        return None

    @staticmethod
    def _uses_dynamic(ds: DecodedSection) -> bool:
        return any(i.mnemonic == "REM_META" for line in ds.lines for i in line.instrs)

    def section(self, ds: DecodedSection) -> List[str]:
        self.section_kind = ds.section.kind_byte
        self.section_is_function = any(
            i.mnemonic == "FUNCTION" for line in ds.lines for i in line.instrs
        )
        try:
            lines = ds.lines
            # A procedure starts with a copy of the module's DEFtype state.
            # It occupies a counted line in the stream but QB never prints it.
            if (not ds.section.is_module and lines and lines[0].instrs
                    and lines[0].instrs[0].mnemonic == "DEFTYPE"):
                lines = lines[1:]
            return [self.safe_line(line) for line in lines]
        finally:
            self.section_kind = None
