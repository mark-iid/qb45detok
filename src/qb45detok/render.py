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
    text = _trim(_shortest(value, single=True))
    # A literal with no decimal point and no exponent would read back as an
    # integer, so QB marks it single: "180!".
    if "." not in text and "E" not in text:
        text += "!"
    return text


def format_double(words) -> str:
    value = struct.unpack("<d", struct.pack("<HHHH", *words))[0]
    text = _trim(_shortest(value, single=False))
    if "E" in text:
        # An exponent makes it double by writing D rather than E.
        return text.replace("E", "D")
    # Anything else would read back as an integer or a single, so it carries
    # the "#": "3.141592653#", "2#".
    return text + "#"


def _shortest(value: float, single: bool) -> str:
    """The fewest digits that still read back as the same stored number.

    A single holds about seven digits, so ``1.00794`` comes back as
    1.0079400539... in double arithmetic. Printing the shortest round-tripping
    form recovers what was written.
    """
    limit = 9 if single else 17

    def same(text: str) -> bool:
        back = float(text)
        if single:
            back = struct.unpack("<f", struct.pack("<f", back))[0]
        return back == value

    for digits in range(1, limit + 1):
        text = f"{value:.{digits}g}"
        if not same(text):
            continue
        if "e" in text:
            # QB writes an exponent only when the fixed form will not do, so
            # 180 comes back as 180 rather than 1.8E+2.
            exponent = int(text.split("e")[1])
            if -5 < exponent < 16:
                fixed = f"{value:.{max(0, digits - 1 - exponent)}f}"
                if same(fixed):
                    return fixed
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

    #: Bit of the procedure preamble kind byte that marks it STATIC.
    STATIC_KIND = 0x80

    #: Trailing word on LINE, giving the box style.
    LINE_SHAPES = {1: "B", 2: "BF"}

    #: Trailing word on PUT, giving the raster operation. All five confirmed.
    PUT_ACTIONS = {0: "OR", 1: "AND", 2: "PRESET", 3: "PSET", 4: "XOR"}

    #: The low byte of OPEN's trailing word names the FOR mode.
    OPEN_MODES = {1: "INPUT", 2: "OUTPUT", 4: "RANDOM", 8: "APPEND", 32: "BINARY"}

    #: Bits 0-1 of the high byte are the ACCESS clause.
    OPEN_ACCESS = {1: "READ", 2: "WRITE", 3: "READ WRITE"}

    #: Bits 4-6 of the high byte are the sharing clause.
    OPEN_LOCKS = {0x10: "LOCK READ WRITE", 0x20: "LOCK WRITE",
                  0x30: "LOCK READ", 0x40: "SHARED"}

    def _open_clauses(self, word: int) -> str:
        """The ACCESS and lock clauses that sit between FOR and AS."""
        out = ""
        access = self.OPEN_ACCESS.get(word >> 8 & 0x03)
        if access:
            out += f" ACCESS {access}"
        lock = self.OPEN_LOCKS.get(word >> 8 & 0x70)
        if lock:
            out += f" {lock}"
        return out

    #: KEY's mode word: OFF, ON or LIST.
    KEY_MODES = {0: "OFF", 1: "ON", 2: "LIST"}

    #: The event selector opcodes render as KEY(n), STRIG(n), TIMER(n).
    EVENT_TEXT = {"STRIG_EVENT": "STRIG", "KEY_EVENT": "KEY",
                  "TIMER_SELECT": "TIMER", "COM_EVENT": "COM",
                  "PLAY_EVENT_N": "PLAY", "SIGNAL_EVENT": "SIGNAL"}

    #: Statements whose arguments are simply everything left on the stack.
    LIST_STATEMENTS = frozenset({
        "LINE", "LINE_NOCOLOR", "LINE_TO", "PSET", "PSET_NOCOLOR",
        "PRESET", "PRESET_NOCOLOR", "PUT_GRAPHICS", "GET_GRAPHICS",
        "CIRCLE", "CIRCLE_COLOR",
        "PAINT", "PAINT_ALT", "PUT_ALT", "WINDOW", "VIEW_PRINT", "SOUND",
        "PLAY", "BLOAD", "BSAVE",
        "SHELL", "KILL", "ERASE", "RANDOMIZE", "WIDTH", "DEF_SEG_TO",
        "BLOAD_ONE",
        "KEY", "PALETTE", "SWAP",
        "RESTORE", "VIEW_PRINT", "ERASE", "DEF_SEG_TO", "FILES", "GET_FILE",
        "PUT_FILE", "GET_FILE_VAR", "PUT_FILE_VAR",
        "SEEK_STMT", "BSAVE", "PALETTE_USING", "IOCTL", "WAIT_XOR",
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
        self.in_module = True   # module text: EXIT there means EXIT DEF

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
        common_block: Optional[str] = None  # the /name/ on a COMMON block
        # How many entries on the stack are finished declarations rather than
        # values, so an array's bounds cannot swallow the name before it.
        declared_so_far = 0
        dim_marks: set = set()  # stack depths whose value is a lone upper bound
        marks: List[bool] = []  # one per ARG: True when a value follows
        channel: Optional[str] = None  # the "#n" a file statement applies to
        field_channel: Optional[str] = None
        print_keyword = "PRINT"
        input_prompt: Optional[str] = None
        input_flags = 0  # INPUT punctuation: 0x04 prompt, 0x02 ";", 0x01 ","
        default_sep = ": "
        trailing_colon = False
        # A DIM/REDIM keyword can come after its declarations, so look ahead:
        # array subscripts inside a declaration are bounds, written "lo TO hi".
        declaring_line = any(i.mnemonic in self.DECLARATION_HEADS
                             for i in line.instrs)
        pending: Optional[str] = None  # a keyword prefixing the next statement
        comment_at: Optional[int] = None
        comment_colon = False  # a colon sits between the code and the comment
        read_at: Optional[int] = None  # index of the READ being extended

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
            elif mn == "PUSH_HEX_LONG":
                stack.append(self._long_literal("&H%X", ins.operands))
            elif mn == "PUSH_OCT":
                stack.append("&O%o" % ins.operands[0])
            elif mn == "PUSH_OCT_LONG":
                stack.append(self._long_literal("&O%o", ins.operands))
            elif mn == "PUSH_LONG":
                stack.append(self._long_literal("%d", ins.operands))
            elif mn == "PUSH_SINGLE":
                stack.append(format_single(ins.operands))
            elif mn == "PUSH_DOUBLE":
                stack.append(format_double(ins.operands))
            elif op.form == "literal":
                stack.append(op.text)
            elif mn in ("SPC", "TAB") and not any(
                    x.mnemonic in ("PRINT_FUNC_SEMI", "PRINT_FUNC_COMMA")
                    for x in line.instrs[line.instrs.index(ins) + 1:
                                         line.instrs.index(ins) + 2]):
                # SPC and TAB are print items that always carry their own
                # semicolon; only the PRINT_FUNC form spells it out.
                printing.append(f"{op.text}({pop()[0]});")

            elif op.form == "var" and mn.startswith(("DECL", "ARRDECL")):
                text = self._declaration(ins, stack, declared_so_far, dim_marks)
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
                dims = self._bounds(stack, ins.operands[0], dim_marks,
                                    declared_so_far)
                text = self._value(ins, pop, bounds=dims, declaring=True)
                if declared_type is not None:
                    # REDIM writes an AS clause per name, same as DIM.
                    text += f" AS {declared_type}"
                    if pad_col is None:
                        pad_col = declared_col
                    declared_type = declared_col = None
                stack.append(text)
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
            elif mn == "KEY_MODE":
                emit("KEY " + self.KEY_MODES.get(ins.operands[0], str(ins.operands[0])))
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
            elif mn in ("PEN_EVENT", "UEVENT", "PLAY_EVENT"):
                stack.append(op.text)
            elif mn in ("STRIG_EVENT", "KEY_EVENT", "TIMER_SELECT", "COM_EVENT",
                        "PLAY_EVENT_N", "SIGNAL_EVENT"):
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
                # A colon with nothing after it is written out. The dotted
                # name marker can sit past it, so look through it.
                rest = line.instrs[line.instrs.index(ins) + 1:]
                read_at = None
                kept = [x for x in rest if x.mnemonic != "DOTTED_NAME"]
                if not kept:
                    trailing_colon = True
                elif all(x.mnemonic == "REM" for x in kept):
                    # "CASE 0: 'comment" keeps its colon, and the comment
                    # still goes at its recorded column.
                    comment_colon = True
            elif mn == "DIM_ARRAY":
                # Marks the dimension about to be pushed as having only an
                # upper bound. The rest were written "lo TO hi".
                dim_marks.add(len(stack))
            elif mn == "CONST":
                pending = "CONST"
                default_sep = ", "   # "CONST TRUE = -1, FALSE = 0"
            elif mn in self.DECLARATION_HEADS and mn != "TYPE_MEMBER":
                # "DIM SHARED" arrives as SHARED then DIM, so the keywords
                # read back in reverse.
                head = self.DECLARATION_HEADS[mn]
                # REDIM repeats once per name declared; the keyword is written
                # only once.
                if head not in decl_heads:
                    decl_heads.insert(0, head)
                # COMMON's second operand names its block, 0xffff when it has
                # none. The name follows every keyword: "COMMON SHARED /B/ C".
                if mn == "COMMON" and len(ins.operands) > 1 and ins.operands[1] != 0xFFFF:
                    common_block = self.name(ins.operands[1])
            elif mn == "REM_META":
                # A comment written with the REM keyword rather than "'".
                emit("REM" + (ins.text or ""))
            elif mn in ("META_DYNAMIC", "META_STATIC"):
                # Supplies the metacommand text for the comment before it.
                # "' $DYNAMIC" keeps the space the comment stored, which the
                # usual pad-stripping would have dropped.
                if parts:
                    before = line.instrs[line.instrs.index(ins) - 1]
                    gap = ""
                    if (before.payload
                            and before.payload.endswith(b" ")
                            and not parts[-1].endswith(" ")):
                        gap = " "
                    parts[-1] += gap + op.text
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
            elif mn in ("DOTTED_NAME", "ELSE_MARK", "STOP_MARK",
                        "MARK_7B", "MARK_7C", "MARK_24",
                        "MARK_8B", "MARK_8C", "MARK_8D", "MARK_8E",
                        "MARK_7E", "MARK_7F"):
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
                word = ins.operands[0] if ins.operands else 4
                mode = self.OPEN_MODES.get(word & 0xFF, "RANDOM")
                target, chan = argv[0], argv[1]
                length = argv[2] if len(argv) > 2 else None
                text = f"OPEN {target} FOR {mode}{self._open_clauses(word)} AS {chan}"
                emit(text + (f" LEN = {length}" if length else ""))
            elif mn == "OPEN":
                argv = pop(len(stack))
                word = ins.operands[0] if ins.operands else 0
                mode = self.OPEN_MODES.get(word & 0xFF)
                chan = argv[-1] if argv else ""
                target = ", ".join(argv[:-1])
                emit(f"OPEN {target} FOR {mode}{self._open_clauses(word)} AS {chan}"
                     if mode else f"OPEN {target} AS {chan}")
            elif mn in ("MID_ASSIGN2", "MID_ASSIGN3"):
                # Stack is start, [length,] value, target.
                if mn == "MID_ASSIGN3":
                    start, length, value, target = pop(4)
                    emit(f"MID$({target}, {start}, {length}) = {value}")
                else:
                    start, value, target = pop(3)
                    emit(f"MID$({target}, {start}) = {value}")
            elif mn in ("ON_GOTO", "ON_GOSUB"):
                (index,) = pop()
                pay = ins.payload or b""
                refs = [int.from_bytes(pay[k:k + 2], "little")
                        for k in range(0, len(pay) - len(pay) % 2, 2)]
                word = "GOTO" if mn == "ON_GOTO" else "GOSUB"
                emit(f"ON {index} {word} " + ", ".join(self.label(r) for r in refs))
            elif mn == "RESTORE_LABEL":
                emit(f"RESTORE {self.label(ins.operands[0])}")
            elif mn == "RETURN_LABEL":
                emit(f"RETURN {self.label(ins.operands[0])}")
            elif mn == "STOP":
                emit("STOP")
            elif mn == "WIDTH_FILE":
                argv = pop(len(stack))
                emit("WIDTH " + ", ".join(argv))
            elif mn == "GET_FILE_BARE":
                emit(f"GET {pop()[0]}")
            elif mn in ("GET_FILE_NOREC", "PUT_FILE_NOREC"):
                # "GET #1, , Rec" -- the record number was left out.
                argv = pop(len(stack))
                head = "GET" if mn == "GET_FILE_NOREC" else "PUT"
                chan = argv[0] if argv else ""
                emit(f"{head} {chan}, , " + ", ".join(argv[1:]))
            elif mn in ("LOCK", "UNLOCK"):
                # "LOCK #n", "LOCK #n, record", "LOCK #n, first TO last",
                # and "LOCK #n, TO last" when the first is left out.
                argv = pop(len(stack))
                chan = argv[0] if argv else ""
                rest = argv[1:]
                if len(rest) == 2:
                    omitted = any(x.mnemonic == "ARG_OMITTED" for x in line.instrs)
                    span = f"TO {rest[1]}" if omitted else f"{rest[0]} TO {rest[1]}"
                    emit(f"{op.text} {chan}, {span}")
                elif rest:
                    emit(f"{op.text} {chan}, {rest[0]}")
                else:
                    emit(f"{op.text} {chan}")
            elif mn in ("OPEN_MODE_STRING", "OPEN_MODE_PLAIN"):
                # The pre-4.0 form, "OPEN mode$, #n, file$[, reclen]", whose
                # arguments are already in source order.
                emit(f"OPEN {', '.join(pop(len(stack)))}")
            elif mn == "CLOSE":
                argv = pop(len(stack))
                emit(f"CLOSE {', '.join(argv)}".rstrip())
            elif mn == "INPUT_CHANNEL":
                # Sets the file channel for both INPUT # and LINE INPUT #.
                (channel,) = pop()
            elif mn == "INPUT_VARS_END":
                argv = pop(len(stack))
                flags = ins.operands[0] if ins.operands else 0
                if channel:
                    emit(f"LINE INPUT {channel}, " + ", ".join(argv))
                else:
                    head = "LINE INPUT " + ("; " if flags & 0x02 else "")
                    if flags & 0x04 and argv:
                        sep = ", " if flags & 0x01 else "; "
                        head += argv.pop(0) + sep
                    emit(head + ", ".join(argv))
                channel = None
            elif mn == "INPUT_PROMPT":
                input_flags = ins.payload[0] if ins.payload else 0
                # With no prompt written there is nothing on the stack to take.
                input_prompt = pop()[0] if input_flags & 0x04 else None
            elif mn == "INPUT_VARS":
                pass
            elif mn == "INPUT":
                argv = pop(len(stack))
                if channel:
                    head = f"INPUT {channel}, "
                else:
                    lead = "; " if input_flags & 0x02 else ""
                    if input_flags & 0x04:
                        sep = ", " if input_flags & 0x01 else "; "
                        head = f"INPUT {lead}{input_prompt}{sep}"
                    else:
                        head = f"INPUT {lead}"
                emit(head + ", ".join(argv))
                input_prompt = None
                input_flags = 0
                channel = None

            # -- PRINT ----------------------------------------------------
            elif mn in ("PRINT_FUNC_SEMI", "PRINT_FUNC_COMMA"):
                # A print item that carries its own separator. TAB and SPC
                # always take a ";", so a following comma is written after
                # it: "PRINT TAB(5); , X".
                if stack:
                    printing.append(pop()[0] + ";")
                if mn == "PRINT_FUNC_COMMA":
                    printing.append(",")
                elif not printing:
                    printing.append(";")
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
                out_text = self._print(printing, using, print_keyword, channel)
                # A bare PRINT keeps a space before a following colon, the
                # way QB writes "PRINT : INPUT ...".
                if not printing and any(
                        x.mnemonic == "COLON"
                        for x in line.instrs[line.instrs.index(ins) + 1:]):
                    out_text += " "
                emit(out_text)
                printing, using, channel = [], None, None
                print_keyword = "PRINT"

            # -- control flow ---------------------------------------------
            elif mn in ("IF_THEN_BLOCK", "ELSEIF"):
                (cond,) = pop()
                # An ELSEIF with its body on the same line takes a space
                # rather than the usual colon separator.
                rest = line.instrs[line.instrs.index(ins) + 1:]
                inline = mn == "ELSEIF" and any(
                    x.mnemonic not in ("DOTTED_NAME", "REM") for x in rest)
                emit(f"{op.text} {cond} THEN", sep=" " if inline else None)
            elif mn == "IF_THEN_LINE":
                (cond,) = pop()
                emit(f"IF {cond} THEN", sep=" ")
            elif mn == "IF_THEN_ALT":
                (cond,) = pop()
                emit(f"IF {cond} THEN", sep=" ")
            elif mn in ("IF_THEN_GOTO", "IF_GOTO"):
                # "IF x THEN 100" and "IF x GOTO 100" name their target
                # directly rather than opening a THEN clause.
                (cond,) = pop()
                word = "THEN" if mn == "IF_THEN_GOTO" else "GOTO"
                emit(f"IF {cond} {word} {self.label(ins.operands[0])}", sep=" ")
            elif mn == "ELSE_LINE":
                # The else branch is a bare line number, no GOTO written.
                if parts:
                    seps[-1] = " "
                emit(self.label(ins.operands[0]), sep=" ")
            elif mn == "ELSE_AFTER_LINE":
                if parts:
                    seps[-1] = " "
                emit("ELSE", sep=" ")
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
            elif mn in ("GOTO", "GOSUB", "RESUME_LABEL", "RUN_LINE"):
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
            elif mn in ("GOSUB_ALT", "GOTO_ALT"):
                emit(f"{op.text} {self.label(ins.operands[0])}")
            elif mn in ("CALL", "CALLS", "CALL_IMPLICIT"):
                count = ins.operands[0]
                argv = pop(count) if count else []
                name = self.name(ins.operands[1])
                if mn in ("CALL", "CALLS"):
                    emit(f"{mn} {name}({', '.join(argv)})" if argv
                         else f"{mn} {name}")
                else:
                    emit(f"{name} {', '.join(argv)}".rstrip())
            elif mn == "END_SUB":
                emit("END FUNCTION" if self.section_is_function else "END SUB")
            elif mn == "EXIT_SUB":
                # In the module text the only thing there is to exit is a
                # DEF FN, and QB writes it that way whatever the source said.
                if self.in_module:
                    emit("EXIT DEF")
                else:
                    emit("EXIT FUNCTION" if self.section_is_function else "EXIT SUB")
            elif mn in ("SUB", "FUNCTION", "DECLARE"):
                text = self._signature(ins)
                static = (self.section_kind is not None
                          and self.section_kind & self.STATIC_KIND)
                if mn != "DECLARE" and static:
                    text += " STATIC"
                emit(text)
            elif mn == "PRINT_USING":
                (using,) = pop()

            # -- everything else ------------------------------------------
            elif mn == "NAME" and len(stack) == 2:
                a, b = pop(2)
                emit(f"NAME {a} AS {b}")
            elif mn in ("VIEW", "VIEW_SCREEN") and len(stack) >= 4:
                argv = pop(len(stack))
                head = f"({argv[0]}, {argv[1]})-({argv[2]}, {argv[3]})"
                rest = argv[4:]
                # VIEW takes an optional colour and border. An ARG marker
                # stands for a slot that was left out, so one marker with one
                # value means the colour went missing: "VIEW ..., , 1".
                omitted = sum(1 for x in line.instrs if x.mnemonic == "ARG")
                if omitted == 1 and len(rest) == 1:
                    rest = [""] + rest
                emit((f"{op.text} " + ", ".join([head] + rest)).rstrip())
            elif mn in ("VIEW_BARE", "WINDOW_BARE", "SHELL_BARE",
                        "FILES_BARE", "RANDOMIZE_BARE", "SLEEP_BARE",
                        "RUN_BARE", "PALETTE_BARE"):
                emit(op.text)
            elif mn in ("WINDOW", "WINDOW_SCREEN") and len(stack) == 4:
                a, b, c, d = pop(4)
                emit(f"{op.text} ({a}, {b})-({c}, {d})")
            elif mn in ("LINE_STYLE", "LINE_STYLE_NOCOLOR"):
                # "LINE (a,b)-(c,d), colour, shape, style", where the colour
                # and the shape can each be left as an empty slot.
                argv = self._merge_coords(pop(len(stack)))
                style = argv.pop()
                coords = argv.pop(0) if argv else ""
                colour = argv.pop(0) if mn == "LINE_STYLE" and argv else ""
                shape = self.LINE_SHAPES.get(ins.operands[0], "") if ins.operands else ""
                emit(f"LINE {coords}, {colour}, {shape}, {style}")
            elif mn in ("DATE$_SET", "TIME$_SET"):
                emit(f"{op.text} = {pop()[0]}")
            elif mn == "READ":
                # One READ opcode per variable, but QB writes them as a
                # single statement: "READ A$, B$". A real "READ A: READ B"
                # has a COLON between them, which clears the run.
                argv = pop(len(stack)) if stack else []
                if read_at is not None and read_at < len(parts):
                    parts[read_at] += ", " + ", ".join(argv)
                else:
                    read_at = len(parts)
                    emit("READ " + ", ".join(argv))
            elif mn in ("CIRCLE", "CIRCLE_COLOR"):
                # CIRCLE (x, y), radius[, colour[, start[, end[, aspect]]]].
                # Any of the optional arguments can be left out, and QB keeps
                # the empty slots: "CIRCLE (x, y), r, 1, , , .3".
                argv = self._merge_coords(pop(len(stack)))
                base = 2 + (1 if mn == "CIRCLE_COLOR" else 0)
                head, rest = argv[:base], argv[base:]
                if len(head) < base:
                    head = head + [""] * (base - len(head))
                if any(x.mnemonic == "CIRCLE_ASPECT" for x in line.instrs) and rest:
                    aspect = rest.pop()
                    rest = rest + [""] * (2 - len(rest)) + [aspect]
                elif mn == "CIRCLE" and rest:
                    # No colour was written but arc angles were.
                    head = head + [""]
                emit("CIRCLE " + ", ".join(head + rest))
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
                # A keyword with no arguments keeps a space before a following
                # colon, the way QB writes "COLOR : PRINT".
                if not argv and any(x.mnemonic == "COLON"
                                    for x in line.instrs[line.instrs.index(ins) + 1:]):
                    text += " "
                emit(text)
            elif marks:
                argv = take_args()
                while argv and argv[-1] == "":
                    argv.pop()
                text = f"{op.text} {', '.join(argv)}".rstrip()
                # A keyword left with no arguments keeps a space before a
                # following colon: "COLOR : PRINT".
                if not argv and any(x.mnemonic == "COLON"
                                    for x in line.instrs[line.instrs.index(ins) + 1:]):
                    text += " "
                emit(text)
            elif op.arity:
                emit(f"{op.text} {', '.join(pop(op.arity))}")
            elif op.text:
                text = op.text
                # A bare COLOR keeps a space before a following colon.
                if mn == "COLOR" and any(
                        x.mnemonic == "COLON"
                        for x in line.instrs[line.instrs.index(ins) + 1:]):
                    text += " "
                emit(text)
            else:
                raise RenderError(f"no rendering for {mn}")

        if field_channel is not None and stack:
            emit(f"FIELD {field_channel}, " + ", ".join(pop(len(stack))))
        if printing or using:
            emit(self._print(printing, using, print_keyword, channel))
        if decl_heads and stack:
            # A declaration is emitted once the whole list has been read, so
            # a comment on the same line has already been queued. QB writes
            # the declaration first.
            tail = None
            if parts and parts[-1].startswith("'") and comment_at is not None:
                tail, tail_sep = parts.pop(), seps.pop()
            names = pop(len(stack))
            if declared_type is not None:
                names[-1] += f" AS {declared_type}"
                if pad_col is None:
                    pad_col = declared_col
            heads = list(decl_heads)
            if common_block:
                heads.append(f"/{common_block}/")
            body = f"{' '.join(heads)} {', '.join(names)}".strip()
            if len(names) == 1 and pad_col and " AS " in body:
                # A lone declaration puts its AS clause at a recorded column,
                # which is how QB lines up the members of a TYPE block.
                head, _, rest = body.partition(" AS ")
                width = pad_col - line.indent
                body = head.ljust(max(width, len(head) + 1)) + "AS " + rest
            emit(body)
            if tail is not None:
                parts.append(tail)
                seps.append(tail_sep)
        if stack:
            raise RenderError(f"{len(stack)} values left on the stack")

        prefix = self._indent(line.indent)
        lead = ""
        if line.labelled:
            prefix = ""
            entry = self.bf.symbol(line.label_ref)
            marker = entry.label if entry else f"{line.label_ref:#06x}"
            # A line number takes no colon. Neither does a numeric label too
            # large to store as one, which QB keeps as a name instead.
            if (entry is not None and entry.line_number is None
                    and not marker.isdigit()):
                marker += ":"
            # The label sits at column 0, and the line's indent is the gap
            # between it and the statement: "30     PRINT I".
            lead = marker + " " * max(line.indent, 1)

        text = ""
        for i, part in enumerate(parts):
            if comment_at is not None and i == len(parts) - 1 and part.startswith("'"):
                gap = 0
                if comment_colon:
                    # "DATA &H55        : 'note" keeps a space after the colon.
                    text += ":"
                    gap = 1
                # QB puts the comment at its recorded column even when that
                # leaves no gap: "... OR 128'S?". A label counts towards the
                # column too.
                # A tab counts as eight columns, so measure from the
                # recorded indent rather than the prefix string.
                base = len(lead) if line.labelled else line.indent
                width = comment_at - base
                text = text.ljust(max(width, len(text) + gap)) + part
            elif i == 0:
                text = part
            else:
                text += seps[i - 1] + part

        if trailing_colon:
            text += ":"
        if line.labelled:
            if not text:
                return prefix + lead.rstrip()
            return prefix + lead + text
        # Trailing whitespace is kept: a comment can legitimately end in
        # spaces, and a whitespace-only line carries its indentation. The one
        # exception is the space CLS leaves for an argument it did not get,
        # which QB does not write at the end of a line.
        if text.endswith("CLS "):
            text = text[:-1]
        return prefix + text

    # -- helpers ---------------------------------------------------------

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

    def _indent(self, width: int) -> str:
        """A line's leading whitespace.

        A program QB has flagged as tab-indented gets one tab per eight
        columns and spaces for the remainder; everything else gets spaces.
        """
        if self.bf.uses_tabs:
            return "\t" * (width // 8) + " " * (width % 8)
        return " " * width

    def _long_literal(self, fmt: str, words) -> str:
        """A long hex or octal literal.

        QB writes the trailing "&" only when it is needed. A value that does
        not fit in 16 bits is already unambiguously long, so "&HFFFFF" needs
        no suffix while "&HFFFF&" does.
        """
        value = words[0] | (words[1] << 16)
        text = fmt % value
        # QB writes the suffix only where the literal would otherwise read as
        # an integer. Decimal integers are signed, so 65535 is already long,
        # while &HFFFF is a valid 16-bit pattern and does need the "&".
        fits = -32768 <= value <= 32767 if fmt == "%d" else value <= 0xFFFF
        return text + "&" if fits else text

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

    def _value(self, ins: Instr, pop, bounds=None,
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

    def _bounds(self, stack: List[str], count: int, dim_marks: set,
                floor: int) -> List[str]:
        """Split the pushed bound values into one string per dimension.

        The count operand is twice the number of dimensions however they were
        written. A dimension marked by ``DIM_ARRAY`` has only an upper bound
        and takes one value; the rest were written ``lo TO hi`` and take two.
        """
        dims: List[str] = []
        for _ in range(count // 2):
            if len(stack) <= floor:
                break
            if (len(stack) - 1) in dim_marks or len(stack) - 1 <= floor:
                dims.insert(0, stack.pop())
            else:
                hi = stack.pop()
                dims.insert(0, f"{stack.pop()} TO {hi}")
        return dims

    def _declaration(self, ins: Instr, stack: List[str], declared_so_far: int,
                     dim_marks: set) -> str:
        """A name being declared, with its bounds if it is an array.

        Inside a ``DIM``-style list the names already declared sit on the stack
        too, so only the values pushed since the last one can be this name's
        bounds. QB writes them as a single upper bound or as ``lo TO hi``.
        """
        prefix, suffix = self._split(ins.mnemonic)
        if prefix == "DECL":
            return self.name(ins.operands[0]) + suffix
        base = self.name(ins.operands[1]) + suffix
        dims = self._bounds(stack, ins.operands[0], dim_marks, declared_so_far)
        return base + "(" + ", ".join(dims) + ")" if dims else base + "()"

    def _subscripted(self, ins: Instr, suffix: str, pop, bounds=None,
                     declaring: bool = False) -> str:
        count, ref = ins.operands[0], ins.operands[1]
        if declaring:
            # A declaration's subscripts are bounds, already split per
            # dimension by _bounds.
            return self.name(ref) + suffix + "(" + ", ".join(bounds or ()) + ")"
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
            keyword = "DECLARE FUNCTION" if sig.is_function else "DECLARE SUB"
            # CDECL and ALIAS sit between the name and the parameter list.
            if sig.cdecl:
                name += " CDECL"
            if sig.alias:
                name += f' ALIAS "{sig.alias}"'
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
        if p.by_value:
            return "BYVAL " + text
        return "SEG " + text if p.by_segment else text

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
            return (self._indent(line.indent)
                    + f"' <qb45detok: {type(exc).__name__}: {exc}>")

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
        default = [self.DEFAULT_TYPE] * 26
        current = (self._deftype_state(modules[0]) if modules else None)
        for ds in modules + procs:
            if not ds.section.is_module and current is not None:
                wanted = self._deftype_state(ds) or list(default)
                out.extend(self._deftype_delta(current, wanted))
                current = wanted
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

    #: The type a letter has when no DEF<type> covers it.
    DEFAULT_TYPE = 3  # single precision

    def _deftype_state(self, ds: DecodedSection) -> Optional[List[int]]:
        """The default type of each letter A-Z, from a section's record.

        The record names one type and the letters that have it; every other
        letter is back to the language default.
        """
        for line in ds.lines:
            for ins in line.instrs:
                if ins.mnemonic == "DEFTYPE":
                    _, tail, head = ins.operands
                    code = tail & 0x3F
                    named = {i for i in range(16) if head >> (15 - i) & 1}
                    named |= {16 + i for i in range(10) if tail >> (15 - i) & 1}
                    return [code if i in named else self.DEFAULT_TYPE
                            for i in range(26)]
        return None

    def _deftype_delta(self, before: List[int], after: List[int]) -> List[str]:
        """The DEF<type> lines QB writes to move from one state to the other.

        It writes the change, not the new state: going from ``DEFINT A-Z`` to
        a section where only C and R are integers gives
        ``DEFSNG A-B, D-Q, S-Z`` rather than ``DEFINT C, R``.
        """
        out = []
        changed = [i for i in range(26) if before[i] != after[i]]
        for code in sorted({after[i] for i in changed}):
            letters = [i for i in changed if after[i] == code]
            keyword = self.DEFTYPE_KEYWORDS.get(code, "DEFSNG")
            out.append(f"{keyword} {self._ranges(letters)}")
        return out

    @staticmethod
    def _uses_dynamic(ds: DecodedSection) -> bool:
        return any(i.mnemonic == "REM_META" for line in ds.lines for i in line.instrs)

    def section(self, ds: DecodedSection) -> List[str]:
        self.section_kind = ds.section.kind_byte
        self.in_module = ds.section.is_module
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
