"""The QuickBASIC 4.5 opcode table.

Opcodes are 16-bit words. Every entry here was established by aligning
``corpus/bin`` against ``corpus/txt``; anything not yet identified simply is
not in the table, and the decoder reports it.

``operands`` says how the decoder advances past an opcode:

``()``        no operands
``("u16",)``  one literal word
``("ref",)``  one symbol reference into the name table
``("str",)``  a word count followed by that many bytes, padded to a word
``("u32",)``  a 32-bit integer held in the next two words
``("f32",)``  a 32-bit float held in the next two words
``("f64",)``  a 64-bit float held in the next four
``("argc", "ref")`` a call: argument count then the procedure

The ``arity`` field is how many values the opcode pops off the expression
stack, and ``text`` is how it is written in source. Both are ``None`` while an
opcode's meaning is still unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Op:
    code: int
    mnemonic: str
    operands: Tuple[str, ...] = ()
    arity: Optional[int] = None
    text: Optional[str] = None
    #: How to render: "infix", "prefix", "func", "stmt", "literal", "var".
    form: str = "stmt"


def _op(code, mnemonic, operands=(), arity=None, text=None, form="stmt") -> Op:
    return Op(code, mnemonic, tuple(operands), arity, text, form)


#: Runs of a repeated character in stored source text are compressed to
#: ``0x0d <count> <char>``. QB writes the expansion back out verbatim, except
#: that its text writer trims trailing whitespace.
RUN_MARKER = 0x0D


def expand_runs(payload: bytes) -> bytes:
    """Undo the run-length encoding used inside stored source text."""
    out = bytearray()
    i = 0
    while i < len(payload):
        if payload[i] == RUN_MARKER and i + 2 < len(payload):
            out.extend(bytes([payload[i + 2]]) * payload[i + 1])
            i += 3
        else:
            out.append(payload[i])
            i += 1
    return bytes(out)


#: Opcodes whose ``str`` payload is a leading word followed by source text.
#: For a comment that word is the column the apostrophe sits at, which is how
#: an inline ``X = 1    ' note`` keeps its spacing. The same opcode also
#: carries a non-text payload on ``DIM ... AS <type>`` lines, which is why
#: callers check for NUL bytes before treating a payload as source.
TEXT_PAYLOAD_OPS = frozenset({0x000A, 0x0097, 0x00A6, 0x00E3})
#: The comment opcode.
REM = 0x0097

#: Opcodes whose ``str`` payload is source text with no leading word.
RAW_TEXT_OPS = frozenset()

#: Opcodes whose ``str`` payload is a procedure signature.
SIGNATURE_OPS = frozenset({0x0044, 0x0058, 0x0076})

#: Parameter type codes used inside a procedure signature.
PARAM_TYPES = {1: "%", 2: "&", 3: "!", 4: "#", 5: "$"}
#: The same codes written out, as a DIM ... AS clause spells them.
TYPE_KEYWORDS = {1: "INTEGER", 2: "LONG", 3: "SINGLE", 4: "DOUBLE", 5: "STRING"}

#: Marks the end of a code section's token stream.
END_OF_SECTION = (0x0009, 0x0008)

#: A line header word carries the source indentation in its top six bits and
#: flags in the low ten, which is what distinguishes it from an opcode.
LINE_HEADER_MASK = 0x03FF
INDENT_SHIFT = 10
#: Set in a line header when the indentation is carried in the following word
#: instead of the header's own field. That word holds a raw space count when
#: the indent is too large for the header form (32 or more), and is otherwise
#: a header-shaped word. Why the small cases take the escape at all is not
#: understood; both forms are decoded by ``escaped_indent``.
LINE_HAS_INDENT = 0x0001


def escaped_indent(word: int) -> int:
    """Read the indentation out of a line header's escape word."""
    return word >> INDENT_SHIFT if word & LINE_HEADER_MASK == 0 else word
#: Set in a line header when a label follows: two more words, an offset and
#: the label's name-table reference.
LINE_HAS_LABEL = 0x0004
_HEADER_FLAGS = LINE_HAS_INDENT | LINE_HAS_LABEL


def is_line_header(word: int) -> bool:
    return word & LINE_HEADER_MASK & ~_HEADER_FLAGS == 0


def indent_of(word: int) -> int:
    return word >> INDENT_SHIFT


#: Opcodes whose low byte is this convert to the type in the high byte, using
#: the same ``(high - 1) / 4`` encoding as an immediate constant: 0508 is
#: CINT, 0908 CLNG, 0d08 CSNG, 1108 CDBL.
CONVERT_LOW_BYTE = 0x08
CONVERT_NAMES = {1: "CINT", 2: "CLNG", 3: "CSNG", 4: "CDBL"}

#: Opcodes whose low byte is this push a small integer held in the high byte.
IMMEDIATE_LOW_BYTE = 0x64
#: ...and this one is the same but with the value in a following word.
LITERAL_LOW_BYTE = 0x65


def immediate_value(code: int) -> Optional[int]:
    """The constant pushed by an ``xx64`` opcode, or ``None``."""
    if code & 0xFF != IMMEDIATE_LOW_BYTE:
        return None
    high = code >> 8
    return (high - 1) >> 2 if high % 4 == 1 else None

# Variable access opcodes pack the data type in the high byte and the
# operation in the low byte.
#: The high byte of a variable opcode is how the name is *written*, not just
#: its type: an unsuffixed name and an explicit "!" are both single precision
#: but encode differently, which matters for reproducing the source.
TYPE_BY_HIGH_BYTE = {
    0x00: "",   # default type, written with no suffix
    0x04: "%",  # INTEGER
    0x08: "&",  # LONG
    0x0C: "!",  # SINGLE
    0x10: "#",  # DOUBLE
    0x14: "$",  # STRING
}
DECLARE_LOW_BYTE = 0x0D
LOAD_LOW_BYTE = 0x0B
STORE_LOW_BYTE = 0x0C
ARRAY_LOAD_LOW_BYTE = 0x0E
ARRAY_STORE_LOW_BYTE = 0x0F
ARRAY_DECL_LOW_BYTE = 0x10

_OPS = [
    # -- structure ----------------------------------------------------
    _op(0x0097, "REM", ("str",), 0, "'", "stmt"),
    _op(0x0044, "DECLARE", ("str",), 0, "DECLARE", "stmt"),
    _op(0x0058, "FUNCTION", ("str",), 0, "FUNCTION", "stmt"),
    _op(0x0076, "SUB", ("str",), 0, "SUB", "stmt"),
    # -- literals -----------------------------------------------------
    _op(0x0166, "PUSH_LONG", ("u32",), 0, None, "literal"),
    _op(0x0167, "PUSH_HEX", ("u16",), 0, None, "literal"),
    _op(0x0168, "PUSH_HEX_LONG", ("u32",), 0, None, "literal"),
    _op(0x0169, "PUSH_OCT", ("u16",), 0, None, "literal"),
    _op(0x016A, "PUSH_OCT_LONG", ("u32",), 0, None, "literal"),
    _op(0x016B, "PUSH_SINGLE", ("f32",), 0, None, "literal"),
    _op(0x016C, "PUSH_DOUBLE", ("f64",), 0, None, "literal"),
    _op(0x016D, "PUSH_STR", ("str",), 0, None, "literal"),
    # -- operators ----------------------------------------------------
    _op(0x0100, "ADD", (), 2, "+", "infix"),
    _op(0x0101, "AND", (), 2, "AND", "infix"),
    _op(0x0104, "EQV", (), 2, "EQV", "infix"),
    _op(0x0102, "DIVIDE", (), 2, "/", "infix"),
    _op(0x0103, "EQ", (), 2, "=", "infix"),
    _op(0x015E, "GE", (), 2, ">=", "infix"),
    _op(0x015F, "GT", (), 2, ">", "infix"),
    _op(0x0160, "IDIVIDE", (), 2, "\\", "infix"),
    _op(0x0161, "IMP", (), 2, "IMP", "infix"),
    _op(0x0162, "LE", (), 2, "<=", "infix"),
    _op(0x0163, "LT", (), 2, "<", "infix"),
    _op(0x016E, "PAREN", (), 1, None, "paren"),
    _op(0x016F, "MOD", (), 2, "MOD", "infix"),
    _op(0x0170, "MULTIPLY", (), 2, "*", "infix"),
    _op(0x0171, "NE", (), 2, "<>", "infix"),
    _op(0x0174, "NOT", (), 1, "NOT", "prefix"),
    _op(0x0175, "OR", (), 2, "OR", "infix"),
    _op(0x0177, "SUBTRACT", (), 2, "-", "infix"),
    _op(0x0178, "NEGATE", (), 1, "-", "prefix"),
    _op(0x0179, "XOR", (), 2, "XOR", "infix"),
    _op(0x017A, "UEVENT", (), 0, "UEVENT", "stmt"),
    # -- control flow -------------------------------------------------
    _op(0x0046, "DO", (), 0, "DO", "stmt"),
    _op(0x0027, "COM_EVENT", (), 1, "COM", "stmt"),
    _op(0x0028, "ON_EVENT_GOSUB", ("ref",), 1, "ON", "stmt"),
    _op(0x002D, "PEN_EVENT", (), 0, "PEN", "stmt"),
    _op(0x002E, "PLAY_EVENT", (), 0, "PLAY", "stmt"),
    _op(0x002F, "PLAY_EVENT_N", (), 1, "PLAY", "stmt"),
    _op(0x0029, "KEY_EVENT", (), 1, "KEY", "stmt"),
    _op(0x002B, "EVENT_ON", (), 0, "ON", "stmt"),
    _op(0x0023, "CONST", (), None, "CONST", "stmt"),
    _op(0x0032, "TIMER_EVENT", (), 0, "TIMER", "stmt"),
    _op(0x0033, "TIMER_SELECT", (), 1, "TIMER", "stmt"),
    _op(0x003A, "CASE_ELSE", (), 0, "CASE ELSE", "stmt"),
    _op(0x003B, "CASE", (), 1, "CASE", "stmt"),
    _op(0x003C, "CASE_RANGE", (), 2, "CASE", "stmt"),
    _op(0x003D, "CASE_IS_EQ", (), 1, "CASE IS =", "stmt"),
    _op(0x003E, "CASE_IS_LT", (), 1, "CASE IS <", "stmt"),
    _op(0x003F, "CASE_IS_GT", (), 1, "CASE IS >", "stmt"),
    _op(0x0040, "CASE_IS_LE", (), 1, "CASE IS <=", "stmt"),
    _op(0x0041, "CASE_IS_GE", (), 1, "CASE IS >=", "stmt"),
    _op(0x0042, "CASE_IS_NE", (), 1, "CASE IS <>", "stmt"),
    _op(0x0043, "CHAIN", (), 1, "CHAIN", "stmt"),
    _op(0x0045, "DEF_FN", ("str",), 0, "DEF", "stmt"),
    _op(0x0047, "DO_UNTIL", ("u16",), 1, "DO UNTIL", "stmt"),
    _op(0x0048, "DO_WHILE", ("u16",), 1, "DO WHILE", "stmt"),
    _op(0x0049, "ELSE", ("u16",), 0, "ELSE", "stmt"),
    # After a line-number THEN the ELSE keyword is its own opcode, and an
    # ELSE that names a line writes the number with no GOTO.
    _op(0x004A, "ELSE_LINE", ("ref",), 0, None, "stmt"),
    _op(0x004C, "ELSE_AFTER_LINE", (), 0, "ELSE", "stmt"),
    _op(0x004D, "ELSEIF", ("u16",), 1, "ELSEIF", "stmt"),
    _op(0x0051, "END_SUB", (), 0, "END SUB", "stmt"),
    _op(0x005E, "IF_THEN_GOTO", ("ref",), 1, "IF", "stmt"),
    _op(0x0060, "IF_GOTO", ("ref",), 1, "IF", "stmt"),
    _op(0x005D, "IF_THEN_LINE", ("u16",), 1, "IF", "stmt"),
    _op(0x0061, "IF_THEN_BLOCK", ("u16",), 1, "IF", "stmt"),
    _op(0x0050, "END_IF", (), 0, "END IF", "stmt"),
    _op(0x004F, "END_DEF", ("u16", "u16"), 0, "END DEF", "stmt"),
    _op(0x0052, "CASE_END", (), 0, "END SELECT", "stmt"),
    _op(0x0055, "EXIT_SUB", ("u16",), 0, "EXIT SUB", "stmt"),
    _op(0x0053, "EXIT_DO", ("u16",), 0, "EXIT DO", "stmt"),
    _op(0x0054, "EXIT_FOR", ("u16",), 0, "EXIT FOR", "stmt"),
    _op(0x0056, "FOR", ("u16", "u16"), None, "FOR", "stmt"),
    _op(0x0059, "GOSUB", ("ref",), 0, "GOSUB", "stmt"),
    _op(0x005B, "GOTO", ("ref",), 0, "GOTO", "stmt"),
    _op(0x0063, "LOOP_UNTIL", ("u16",), 1, "LOOP UNTIL", "stmt"),
    _op(0x0064, "LOOP_WHILE", ("u16",), 1, "LOOP WHILE", "stmt"),
    _op(0x0057, "FOR_STEP", ("u16", "u16"), None, "FOR", "stmt"),
    _op(0x0062, "LOOP", ("u16",), 0, "LOOP", "stmt"),
    _op(0x0065, "NEXT_BARE", ("u16", "u16"), 0, "NEXT", "stmt"),
    _op(0x0066, "NEXT", ("u16", "u16"), None, "NEXT", "stmt"),
    _op(0x0067, "ON_ERROR", ("ref",), 0, "ON ERROR GOTO", "stmt"),
    _op(0x006C, "RESUME", (), 0, "RESUME", "stmt"),
    _op(0x006D, "RESUME_LABEL", ("ref",), 0, "RESUME", "stmt"),
    _op(0x006E, "RESUME_NEXT", (), 0, "RESUME NEXT", "stmt"),
    _op(0x006F, "RETURN", (), 0, "RETURN", "stmt"),
    _op(0x0071, "RUN_FILE", (), 1, "RUN", "stmt"),
    _op(0x0072, "RUN_LINE", ("ref",), 0, "RUN", "stmt"),
    _op(0x0073, "RUN_BARE", (), 0, "RUN", "stmt"),
    _op(0x0074, "SELECT_CASE", ("u16",), 1, "SELECT CASE", "stmt"),
    _op(0x0077, "WAIT", (), 2, "WAIT", "stmt"),
    _op(0x0078, "WAIT_XOR", (), 3, "WAIT", "stmt"),
    _op(0x0079, "WEND", ("u16",), 0, "WEND", "stmt"),
    _op(0x00F9, "WIDTH_LPRINT", (), 1, "WIDTH LPRINT", "stmt"),
    _op(0x00FB, "WINDOW", (), None, "WINDOW", "stmt"),
    _op(0x00FC, "WINDOW_BARE", (), 0, "WINDOW", "stmt"),
    _op(0x00FD, "WINDOW_SCREEN", (), None, "WINDOW SCREEN", "stmt"),
    _op(0x00FE, "WRITE_FILE", (), None, "WRITE #", "stmt"),
    _op(0x00FF, "PRINT_USING", (), 1, "PRINT USING", "stmt"),
    _op(0x007A, "WHILE", ("u16",), 1, "WHILE", "stmt"),
    # -- statement punctuation ----------------------------------------
    _op(0x0006, "COLON", (), 0, ":", "stmt"),
    # Marks a line that names an identifier containing a period. One per
    # line, always last, and it produces no text.
    _op(0x0017, "DOTTED_NAME", (), 0, None, "stmt"),
    _op(0x0018, "DIM_ARRAY", (), 0, None, "stmt"),
    _op(0x00E2, "READ", (), None, "READ", "stmt"),
    _op(0x001A, "SHARED", (), 0, "SHARED", "stmt"),
    _op(0x001B, "DEFTYPE", ("u16", "u16", "u16"), 0, "DEFINT", "stmt"),
    _op(0x000A, "TEXT_LINE", ("str",), 0, None, "stmt"),
    _op(0x0011, "FIELD", ("ref",), 1, ".", "infix"),
    _op(0x0012, "FIELD_SET", ("ref",), 2, ".", "infix"),
    _op(0x0015, "AS_USER_TYPE", ("ref", "u16"), 0, "AS", "stmt"),
    _op(0x0016, "AS_TYPE", ("u16", "u16"), 0, "AS", "stmt"),
    _op(0x0019, "TYPE_MEMBER", ("ref",), 0, None, "stmt"),
    _op(0x001D, "END_TYPE", ("u16",), 0, "END TYPE", "stmt"),
    _op(0x001E, "SHARED_TYPED", ("u16",), 0, "SHARED", "stmt"),
    _op(0x001F, "STATIC", ("u16",), 0, "STATIC", "stmt"),
    _op(0x0020, "TYPE", ("u16", "ref"), 0, "TYPE", "stmt"),
    _op(0x0026, "DEF_FN_END_LINE", ("u16", "u16"), 0, None, "stmt"),
    _op(0x001C, "REDIM", (), 0, "REDIM", "stmt"),
    _op(0x017B, "SLEEP", (), 1, "SLEEP", "stmt"),
    _op(0x017C, "FIXED_STRING", ("u16", "u16", "u16"), 0, "AS STRING *", "stmt"),
    _op(0x017D, "DIM", ("u16",), 0, "DIM", "stmt"),
    _op(0x0172, "ARG_OMITTED", (), 0, None, "stmt"),
    _op(0x0173, "ARG", (), 0, None, "stmt"),
    _op(0x0081, "COORD", (), 2, None, "stmt"),
    _op(0x0083, "COORD_TO", (), 2, None, "stmt"),
    # -- screen and graphics statements -------------------------------
    _op(0x007E, "CIRCLE_ASPECT", (), 1, None, "stmt"),
    _op(0x007F, "CIRCLE_END", (), 1, None, "stmt"),
    _op(0x0080, "CIRCLE_START", (), 1, None, "stmt"),
    _op(0x0082, "COORD_STEP", (), 2, "STEP", "stmt"),
    _op(0x0084, "COORD_TO_STEP", (), 2, "-STEP", "stmt"),
    _op(0x0085, "FIELD_STMT", (), None, "FIELD", "stmt"),
    _op(0x0086, "FIELD_ITEM", (), 2, None, "stmt"),
    _op(0x007D, "PRINT_FILE", (), None, "PRINT #", "stmt"),
    _op(0x0087, "LINE_INPUT_FILE", (), None, "LINE INPUT #", "stmt"),
    _op(0x0088, "INPUT", (), None, "INPUT", "stmt"),
    _op(0x008A, "FILE_NUMBER", (), 1, "#", "stmt"),
    # Counted payload: byte 0 is the punctuation flags, then one byte per
    # variable. 0x04 a prompt was written, 0x02 a leading ";", 0x01 the
    # prompt is followed by "," rather than ";".
    _op(0x0089, "INPUT_PROMPT", ("str",), 1, None, "stmt"),
    _op(0x002A, "EVENT_OFF", (), 0, "OFF", "stmt"),
    _op(0x002C, "EVENT_STOP", (), 0, "STOP", "stmt"),
    _op(0x0031, "STRIG_EVENT", (), 1, "STRIG", "stmt"),
    _op(0x008F, "SPC", (), 1, "SPC", "func"),
    _op(0x0090, "TAB", (), 1, "TAB", "func"),
    _op(0x0091, "PRINT_FUNC_COMMA", (), 0, None, "stmt"),
    _op(0x0092, "PRINT_FUNC_SEMI", (), 0, None, "stmt"),
    _op(0x009A, "BEEP", (), 0, "BEEP", "stmt"),
    _op(0x009B, "BLOAD_ONE", (), 1, "BLOAD", "stmt"),
    _op(0x009C, "BLOAD", (), None, "BLOAD", "stmt"),
    _op(0x009E, "CHDIR", (), 1, "CHDIR", "stmt"),
    _op(0x009D, "BSAVE", (), None, "BSAVE", "stmt"),
    _op(0x009F, "CIRCLE", (), None, "CIRCLE", "stmt"),
    _op(0x00A0, "CIRCLE_COLOR", (), None, "CIRCLE", "stmt"),
    _op(0x006A, "RESTORE", (), None, "RESTORE", "stmt"),
    _op(0x00A6, "DATA", ("str",), 0, "DATA", "stmt"),
    _op(0x00A5, "COMMON", ("u16", "u16"), 0, "COMMON", "stmt"),
    _op(0x00AA, "DRAW", (), 1, "DRAW", "stmt"),
    _op(0x00AB, "ENVIRON", (), 1, "ENVIRON", "stmt"),
    _op(0x00AD, "ERROR", (), 1, "ERROR", "stmt"),
    _op(0x00AE, "FILES_BARE", (), 0, "FILES", "stmt"),
    _op(0x00AF, "FILES", (), None, "FILES", "stmt"),
    _op(0x00A8, "DEF_SEG", (), 0, "DEF SEG", "stmt"),
    _op(0x00AC, "ERASE", ("u16",), None, "ERASE", "stmt"),
    _op(0x00A9, "DEF_SEG_TO", (), 1, "DEF SEG", "stmt"),
    _op(0x00A1, "CLEAR", ("u16",), None, "CLEAR", "stmt"),
    _op(0x00A2, "CLOSE", ("u16",), None, "CLOSE", "stmt"),
    _op(0x00A3, "CLS", (), 0, "CLS", "stmt"),
    _op(0x00A4, "COLOR", ("u16",), None, "COLOR", "stmt"),
    _op(0x00B1, "GET_FILE", (), None, "GET", "stmt"),
    _op(0x00B2, "GET_FILE_NOREC", ("u16",), None, "GET", "stmt"),
    _op(0x00B3, "GET_FILE_VAR", ("u16",), None, "GET", "stmt"),
    _op(0x00B4, "GET_GRAPHICS", (), None, "GET", "stmt"),
    _op(0x00B5, "PUT_GRAPHICS", ("u16",), None, "PUT", "stmt"),
    _op(0x00B6, "INPUT_VARS", (), None, None, "stmt"),
    _op(0x00BA, "KILL", (), 1, "KILL", "stmt"),
    _op(0x00BB, "LINE_NOCOLOR", ("u16",), None, "LINE", "stmt"),
    _op(0x00B7, "IOCTL", (), None, "IOCTL", "stmt"),
    _op(0x00B8, "KEY_MODE", ("u16",), 0, "KEY", "stmt"),
    _op(0x00B9, "KEY", (), None, "KEY", "stmt"),
    _op(0x00BF, "LET", (), 0, "LET", "stmt"),
    _op(0x00BC, "LINE", ("u16",), None, "LINE", "stmt"),
    _op(0x00BD, "LINE_STYLE_NOCOLOR", ("u16",), None, "LINE", "stmt"),
    _op(0x00BE, "LINE_STYLE", ("u16",), None, "LINE", "stmt"),
    _op(0x00C3, "LPRINT", (), None, "LPRINT", "stmt"),
    _op(0x00E0, "RANDOMIZE_BARE", (), 0, "RANDOMIZE", "stmt"),
    _op(0x00E1, "RANDOMIZE", (), 1, "RANDOMIZE", "stmt"),
    _op(0x00E3, "REM_META", ("u16", "u16", "u16", "u16"), 0, "REM", "stmt"),
    _op(0x00E4, "RESET", (), 0, "RESET", "stmt"),
    _op(0x00E5, "RMDIR", (), 1, "RMDIR", "stmt"),
    _op(0x00E6, "RSET", (), 2, "RSET", "stmt"),
    _op(0x00E8, "SEEK_STMT", (), None, "SEEK", "stmt"),
    _op(0x00E9, "SHELL_BARE", (), 0, "SHELL", "stmt"),
    _op(0x00EA, "SHELL", (), 1, "SHELL", "stmt"),
    _op(0x00ED, "SWAP", ("u16",), 2, "SWAP", "stmt"),
    _op(0x00EB, "SLEEP_BARE", (), 0, "SLEEP", "stmt"),
    _op(0x00EC, "SOUND", (), 2, "SOUND", "stmt"),
    _op(0x00F0, "TROFF", (), 0, "TROFF", "stmt"),
    _op(0x00F1, "TRON", (), 0, "TRON", "stmt"),
    _op(0x00F2, "UNLOCK", ("u16",), None, "UNLOCK", "stmt"),
    _op(0x00F3, "VIEW", (), None, "VIEW", "stmt"),
    _op(0x00F4, "VIEW_BARE", (), 0, "VIEW", "stmt"),
    _op(0x00F7, "VIEW_SCREEN", (), None, "VIEW SCREEN", "stmt"),
    _op(0x00F5, "VIEW_PRINT_BARE", (), 0, "VIEW PRINT", "stmt"),
    _op(0x00EE, "SYSTEM", (), 0, "SYSTEM", "stmt"),
    _op(0x00F8, "WIDTH", (), None, "WIDTH", "stmt"),
    _op(0x00F6, "VIEW_PRINT", (), None, "VIEW PRINT", "stmt"),
    _op(0x00C0, "INPUT_VARS_END", ("u16",), None, None, "stmt"),
    _op(0x00C1, "LOCATE", ("u16",), None, "LOCATE", "stmt"),
    _op(0x00C2, "LOCK", ("u16",), None, "LOCK", "stmt"),
    _op(0x00C4, "LSET", (), 2, "LSET", "stmt"),
    _op(0x00C7, "MKDIR", (), 1, "MKDIR", "stmt"),
    _op(0x00C8, "NAME", (), 2, "NAME", "stmt"),
    _op(0x00C5, "MID_ASSIGN2", (), 3, "MID$", "stmt"),
    _op(0x00C6, "MID_ASSIGN3", (), 4, "MID$", "stmt"),
    _op(0x00FA, "WIDTH_FILE", (), 2, "WIDTH", "stmt"),
    _op(0x00CC, "OPEN_MODE_STRING", (), None, "OPEN", "stmt"),
    _op(0x00C9, "OPEN", ("u16",), None, "OPEN", "stmt"),
    # The base is encoded in the opcode rather than an operand.
    _op(0x00CA, "OPEN_RANDOM", ("u16",), None, "OPEN", "stmt"),
    _op(0x00CD, "OPTION_BASE_0", (), 0, "OPTION BASE 0", "stmt"),
    _op(0x00CE, "OPTION_BASE", (), 0, "OPTION BASE 1", "stmt"),
    _op(0x00D0, "PAINT", (), None, "PAINT", "stmt"),
    _op(0x00D2, "PALETTE_BARE", (), 0, "PALETTE", "stmt"),
    _op(0x00D3, "PALETTE", (), None, "PALETTE", "stmt"),
    _op(0x00D6, "PLAY", (), 1, "PLAY", "stmt"),
    _op(0x00D8, "PRESET_NOCOLOR", (), None, "PRESET", "stmt"),
    _op(0x00D9, "PRESET", (), None, "PRESET", "stmt"),
    _op(0x00CF, "OUT", (), 2, "OUT", "stmt"),
    _op(0x00D4, "PALETTE_USING", (), 1, "PALETTE USING", "stmt"),
    _op(0x00D5, "PCOPY", (), 2, "PCOPY", "stmt"),
    _op(0x00D7, "POKE", (), 2, "POKE", "stmt"),
    _op(0x00DA, "PSET_NOCOLOR", (), None, "PSET", "stmt"),
    _op(0x00DD, "PUT_FILE", (), None, "PUT", "stmt"),
    _op(0x00DE, "PUT_FILE_NOREC", ("u16",), None, "PUT", "stmt"),
    _op(0x00DF, "PUT_FILE_VAR", ("u16",), None, "PUT", "stmt"),
    _op(0x00DB, "PSET", (), None, "PSET", "stmt"),
    _op(0x00E7, "SCREEN", ("u16",), None, "SCREEN", "stmt"),
    # -- calls --------------------------------------------------------
    _op(0x0037, "CALL", ("argc", "ref"), None, "CALL", "stmt"),
    # Counted payload holding one u16 label reference per branch target.
    _op(0x0068, "ON_GOSUB", ("str",), 1, "ON", "stmt"),
    _op(0x0069, "ON_GOTO", ("str",), 1, "ON", "stmt"),
    _op(0x006B, "RESTORE_LABEL", ("ref",), 0, "RESTORE", "stmt"),
    _op(0x0070, "RETURN_LABEL", ("ref",), 0, "RETURN", "stmt"),
    _op(0x0075, "STOP", ("u16",), 0, "STOP", "stmt"),
    _op(0x0039, "CALLS", ("argc", "ref"), None, "CALLS", "stmt"),
    _op(0x0038, "CALL_IMPLICIT", ("argc", "ref"), None, None, "stmt"),
    # -- statements ---------------------------------------------------
    _op(0x004E, "END", (), 0, "END", "stmt"),
    _op(0x0093, "PRINT_NEWLINE", (), 0, "PRINT", "stmt"),
    _op(0x0094, "PRINT_COMMA", (), 0, ",", "stmt"),
    _op(0x0095, "PRINT_SEMI", (), 0, ";", "stmt"),
    _op(0x0096, "PRINT_ITEM", (), 1, "PRINT", "stmt"),
    # -- string functions ---------------------------------------------
    _op(0x010A, "COMMAND$", (), 0, "COMMAND$", "func"),
    _op(0x012A, "LCASE$", (), 1, "LCASE$", "func"),
    _op(0x012B, "LTRIM$", (), 1, "LTRIM$", "func"),
    _op(0x0142, "RIGHT$", (), 2, "RIGHT$", "func"),
    _op(0x0145, "RTRIM$", (), 1, "RTRIM$", "func"),
    _op(0x0149, "SEEK", (), 1, "SEEK", "func"),
    _op(0x014B, "SGN", (), 1, "SGN", "func"),
    _op(0x014D, "SIN", (), 1, "SIN", "func"),
    _op(0x014F, "SQR", (), 1, "SQR", "func"),
    _op(0x014E, "SPACE$", (), 1, "SPACE$", "func"),
    # -- numeric and misc functions -----------------------------------
    _op(0x0106, "ASC", (), 1, "ASC", "func"),
    _op(0x011F, "HEX$", (), 1, "HEX$", "func"),
    _op(0x0115, "EOF", (), 1, "EOF", "func"),
    _op(0x0120, "INKEY$", (), 0, "INKEY$", "func"),
    _op(0x0121, "INP", (), 1, "INP", "func"),
    _op(0x0123, "INPUT$", (), 2, "INPUT$", "func"),
    _op(0x0124, "INSTR", (), 2, "INSTR", "func"),
    _op(0x0125, "INSTR_FROM", (), 3, "INSTR", "func"),
    _op(0x0127, "IOCTL$", (), 1, "IOCTL$", "func"),
    _op(0x0126, "INT", (), 1, "INT", "func"),
    _op(0x012D, "LEN", ("u16",), 1, "LEN", "func"),
    _op(0x012E, "LOC", (), 1, "LOC", "func"),
    _op(0x013F, "POINT_ONE", (), 1, "POINT", "func"),
    _op(0x0140, "POINT", (), 2, "POINT", "func"),
    _op(0x0105, "ABS", (), 1, "ABS", "func"),
    _op(0x0109, "CHR$", (), 1, "CHR$", "func"),
    _op(0x012C, "LEFT$", (), 2, "LEFT$", "func"),
    _op(0x010B, "COS", (), 1, "COS", "func"),
    _op(0x0107, "ATN", (), 1, "ATN", "func"),
    _op(0x010C, "CSRLIN", (), 0, "CSRLIN", "func"),
    _op(0x010D, "CVD", (), 1, "CVD", "func"),
    _op(0x010E, "CVDMBF", (), 1, "CVDMBF", "func"),
    _op(0x010F, "CVI", (), 1, "CVI", "func"),
    _op(0x0110, "CVL", (), 1, "CVL", "func"),
    _op(0x0111, "CVS", (), 1, "CVS", "func"),
    _op(0x0112, "CVSMBF", (), 1, "CVSMBF", "func"),
    _op(0x0116, "ERDEV", (), 0, "ERDEV", "func"),
    _op(0x0117, "ERDEV$", (), 0, "ERDEV$", "func"),
    _op(0x0113, "DATE$", (), 0, "DATE$", "func"),
    _op(0x0114, "ENVIRON$", (), 1, "ENVIRON$", "func"),
    _op(0x0118, "ERL", (), 0, "ERL", "func"),
    _op(0x0119, "ERR", (), 0, "ERR", "func"),
    _op(0x011A, "EXP", (), 1, "EXP", "func"),
    _op(0x011B, "FILEATTR", (), 2, "FILEATTR", "func"),
    _op(0x011D, "FRE", (), 1, "FRE", "func"),
    _op(0x011E, "FREEFILE", (), 0, "FREEFILE", "func"),
    _op(0x011C, "FIX", (), 1, "FIX", "func"),
    _op(0x012F, "LOF", (), 1, "LOF", "func"),
    _op(0x0128, "LBOUND", (), 1, "LBOUND", "func"),
    _op(0x0129, "LBOUND_DIM", (), 2, "LBOUND", "func"),
    _op(0x0130, "LOG", (), 1, "LOG", "func"),
    _op(0x0131, "LPOS", (), 1, "LPOS", "func"),
    _op(0x0132, "MID$_REST", (), 2, "MID$", "func"),
    _op(0x0133, "MID$", (), 3, "MID$", "func"),
    _op(0x0134, "MKD$", (), 1, "MKD$", "func"),
    _op(0x0135, "MKDMBF$", (), 1, "MKDMBF$", "func"),
    _op(0x0136, "MKI$", (), 1, "MKI$", "func"),
    _op(0x0137, "MKL$", (), 1, "MKL$", "func"),
    _op(0x0138, "MKS$", (), 1, "MKS$", "func"),
    _op(0x0139, "MKSMBF$", (), 1, "MKSMBF$", "func"),
    _op(0x013C, "PEN_FUNC", (), 1, "PEN", "func"),
    _op(0x013D, "PLAY_FUNC", (), 1, "PLAY", "func"),
    _op(0x013A, "OCT$", (), 1, "OCT$", "func"),
    _op(0x013B, "PEEK", (), 1, "PEEK", "func"),
    _op(0x013E, "PMAP", (), 2, "PMAP", "func"),
    _op(0x0141, "POS", (), 1, "POS", "func"),
    _op(0x0143, "RND", (), 0, "RND", "func"),
    _op(0x0144, "RND_SEED", (), 1, "RND", "func"),
    _op(0x0146, "SADD", (), 1, "SADD", "func"),
    _op(0x0147, "SCREEN_FUNC", (), 2, "SCREEN", "func"),
    _op(0x0148, "SCREEN_FUNC3", (), 3, "SCREEN", "func"),
    _op(0x014A, "SETMEM", (), 1, "SETMEM", "func"),
    _op(0x0154, "TAN", (), 1, "TAN", "func"),
    _op(0x0155, "TIME$", (), 0, "TIME$", "func"),
    _op(0x0156, "TIMER", (), 0, "TIMER", "func"),
    _op(0x0157, "UBOUND", (), 1, "UBOUND", "func"),
    _op(0x0158, "UBOUND_DIM", (), 2, "UBOUND", "func"),
    _op(0x0159, "UCASE$", (), 1, "UCASE$", "func"),
    _op(0x015A, "VAL", (), 1, "VAL", "func"),
    _op(0x0150, "STICK", (), 1, "STICK", "func"),
    _op(0x0153, "STRING$", (), 2, "STRING$", "func"),
    _op(0x015B, "VARPTR", (), 1, "VARPTR", "func"),
    _op(0x0151, "STR$", (), 1, "STR$", "func"),
    _op(0x015C, "VARPTR$", ("u16",), 1, "VARPTR$", "func"),
    _op(0x015D, "VARSEG", (), 1, "VARSEG", "func"),
]

OPS: Dict[int, Op] = {o.code: o for o in _OPS}


def variable_op(code: int) -> Optional[Op]:
    """Synthesise the entry for a typed variable load/store/array opcode."""
    suffix = TYPE_BY_HIGH_BYTE.get(code >> 8)
    if suffix is None:
        return None
    low = code & 0xFF
    if low == LOAD_LOW_BYTE:
        return _op(code, f"LOAD{suffix}", ("ref",), 0, None, "var")
    if low == STORE_LOW_BYTE:
        return _op(code, f"STORE{suffix}", ("ref",), 1, None, "var")
    if low == DECLARE_LOW_BYTE:
        return _op(code, f"DECL{suffix}", ("ref",), 0, None, "var")
    if low == ARRAY_LOAD_LOW_BYTE:
        return _op(code, f"ARRAY{suffix}", ("u16", "ref"), 0, None, "var")
    if low == ARRAY_STORE_LOW_BYTE:
        return _op(code, f"ARRSET{suffix}", ("u16", "ref"), 1, None, "var")
    if low == ARRAY_DECL_LOW_BYTE:
        return _op(code, f"ARRDECL{suffix}", ("u16", "ref"), None, None, "var")
    return None


def convert_op(code: int) -> Optional[Op]:
    """Synthesise the entry for a numeric type-conversion opcode."""
    if code & 0xFF != CONVERT_LOW_BYTE:
        return None
    high = code >> 8
    if high % 4 != 1:
        return None
    name = CONVERT_NAMES.get((high - 1) >> 2)
    return None if name is None else _op(code, name, (), 1, name, "func")


def constant_op(code: int) -> Optional[Op]:
    """Synthesise the entry for an immediate or literal constant push.

    Both forms carry a type in the high byte under the same rule, so the high
    byte has to be checked as well as the low one -- otherwise ``0065``, which
    is a bare ``NEXT``, gets mistaken for a literal push.
    """
    low = code & 0xFF
    if low == LITERAL_LOW_BYTE and (code >> 8) % 4 == 1:
        return _op(code, "PUSH_INT", ("u16",), 0, None, "literal")
    value = immediate_value(code)
    if value is None:
        return None
    return _op(code, f"PUSH_{value}", (), 0, str(value), "literal")


def lookup(code: int) -> Optional[Op]:
    """The opcode's table entry, or ``None`` if it is not yet identified."""
    op = OPS.get(code)
    if op is not None:
        return op
    return variable_op(code) or constant_op(code) or convert_op(code)
