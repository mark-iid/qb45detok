"""Turn parsed statements into the words QuickBASIC stores.

This is the back half of tokenizing. The parser has already worked out what
each statement is and in what order its operands are pushed; what is left is
choosing the opcode that spells it, interning every name so it has a
reference, and packing the result into words.

Three of QB's encodings are rules rather than table entries, and all three
have to be run backwards here:

* a variable access puts the type in the high byte and what is being done to
  it in the low one, so ``LOAD`` of a ``%`` variable is ``040b``;
* a small constant is folded into the opcode itself as ``(value * 4 + 1)``
  in the high byte over a low byte of ``64``;
* a conversion puts the target type in the high byte over a low byte of
  ``08``.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Sequence, Tuple

from . import tokens
from .expr import Emit
from .lex import lex
from .parse import Statement, parse_line
from .writer import ImageWriter, OutName, OutSection

#: Name-table flags.
FLAG_NAME = 0x00
FLAG_LABEL = 0x04
FLAG_NUM_LABEL = 0x06
FLAG_SUB = 0x40

#: Low bytes of the variable-access family, by what is being done.
_ACCESS = {
    "LOAD": tokens.LOAD_LOW_BYTE,
    "STORE": tokens.STORE_LOW_BYTE,
    "DECL": tokens.DECLARE_LOW_BYTE,
    "ARRAY": tokens.ARRAY_LOAD_LOW_BYTE,
    "ARRSET": tokens.ARRAY_STORE_LOW_BYTE,
    "ARRDECL": tokens.ARRAY_DECL_LOW_BYTE,
}

#: High byte for each type suffix, the inverse of TYPE_BY_HIGH_BYTE.
_HIGH_BY_SUFFIX = {v: k for k, v in tokens.TYPE_BY_HIGH_BYTE.items()}

#: Type code for each suffix, as signatures and DIM ... AS record it.
_TYPE_CODE = {v: k for k, v in tokens.PARAM_TYPES.items()}


class AssembleError(ValueError):
    """Raised when a statement cannot be turned into words."""


def opcode_for(mnemonic: str) -> Optional[int]:
    """The opcode that spells a mnemonic, or None if the table has no such."""
    for code, op in tokens.OPS.items():
        if op.mnemonic == mnemonic:
            return code
    return None


#: Built once, since the lookup above is over the whole table.
_BY_MNEMONIC: Dict[str, int] = {}
for _code, _op in tokens.OPS.items():
    _BY_MNEMONIC.setdefault(_op.mnemonic, _code)
# PUSH_INT is made by a rule rather than being in the table: a low byte of
# 65 with a high byte of 1, meaning an integer literal in the word after.
_BY_MNEMONIC["PUSH_INT"] = (1 << 8) | tokens.LITERAL_LOW_BYTE
# The conversions are a rule too: the target type sits in the high byte.
for _index, _name in tokens.CONVERT_NAMES.items():
    _BY_MNEMONIC[_name] = (((_index << 2) + 1) << 8) | tokens.CONVERT_LOW_BYTE


def variable_opcode(what: str, suffix: str) -> int:
    """The opcode for a variable access of the given kind and type."""
    low = _ACCESS.get(what)
    if low is None:
        raise AssembleError(f"no variable access called {what!r}")
    high = _HIGH_BY_SUFFIX.get(suffix)
    if high is None:
        raise AssembleError(f"no type suffix {suffix!r}")
    return (high << 8) | low


def immediate_opcode(value: int) -> Optional[int]:
    """The opcode that carries a small constant in itself, if one can."""
    if 0 <= value <= 10:
        return ((value * 4 + 1) << 8) | tokens.IMMEDIATE_LOW_BYTE
    return None


class Assembler:
    """Builds one file: its name table and the words of each section."""

    def __init__(self) -> None:
        self.writer = ImageWriter()
        self._refs: Dict[Tuple[int, str], OutName] = {}

    # -- names -----------------------------------------------------------

    def name_ref(self, text: str, flags: int = FLAG_NAME) -> OutName:
        """Intern a name, so that every mention shares one table entry."""
        key = (flags, text.lower())
        entry = self._refs.get(key)
        if entry is None:
            entry = self.writer.add_name(flags, text=text)
            self._refs[key] = entry
        return entry

    def label_ref(self, text: str) -> OutName:
        """A line label, which is a numeric one when it is all digits."""
        if text.isdigit() and int(text) <= 0xFFFF:
            key = (FLAG_NUM_LABEL, text)
            entry = self._refs.get(key)
            if entry is None:
                entry = self.writer.add_name(FLAG_NUM_LABEL, number=int(text))
                self._refs[key] = entry
            return entry
        return self.name_ref(text, FLAG_LABEL)

    # -- one statement ---------------------------------------------------

    def words_for(self, emits: Sequence[Emit]) -> List[int]:
        """The words that spell a parsed statement."""
        out: List[int] = []
        for emit in emits:
            out += self._one(emit)
        return out

    #: What the trailing word holds when a statement has never been run.
    #: The jump slots QB backpatches start either empty or all ones.
    _FRESH = {"IF_THEN_LINE": 0, "IF_THEN_BLOCK": 0, "ELSE": 0}

    def _one(self, emit: Emit) -> List[int]:
        handler = getattr(self, f"_e_{emit.mnemonic.lower()}", None)
        if handler is not None:
            return handler(emit)
        code = _BY_MNEMONIC.get(emit.mnemonic)
        if code is None:
            raise AssembleError(f"no opcode for {emit.mnemonic}")
        op = tokens.lookup(code)
        out = [code]
        for i, kind in enumerate(op.operands or ()):
            given = emit.operands[i] if i < len(emit.operands) else None
            if kind == "u16":
                # Either a value the parser worked out, or a jump slot, which
                # stays empty until the program is run.
                out.append(given & 0xFFFF if isinstance(given, int)
                           else self._FRESH.get(emit.mnemonic, 0xFFFF))
            elif kind == "ref":
                out.append(self.label_ref(given).ref if given is not None
                           else 0xFFFF)
            else:
                raise AssembleError(f"{emit.mnemonic} needs a {kind} operand")
        return out

    # -- values ----------------------------------------------------------

    def _e_push_number(self, emit: Emit) -> List[int]:
        text, kind = emit.operands
        return self._number(text, kind)

    def _number(self, text: str, kind: str) -> List[int]:
        lowered = text.lower().rstrip("!#&%")
        if kind == "integer":
            value = self._integer_value(text)
            if not lowered.startswith("&") and value is not None:
                folded = immediate_opcode(value)
                if folded is not None:
                    return [folded]
            if lowered.startswith("&h"):
                return [_BY_MNEMONIC["PUSH_HEX"], value & 0xFFFF]
            if lowered.startswith("&"):
                return [_BY_MNEMONIC["PUSH_OCT"], value & 0xFFFF]
            return [_BY_MNEMONIC["PUSH_INT"], value & 0xFFFF]
        if kind == "long":
            value = self._integer_value(text) or 0
            low, high = value & 0xFFFF, (value >> 16) & 0xFFFF
            if lowered.startswith("&h"):
                return [_BY_MNEMONIC["PUSH_HEX_LONG"], low, high]
            if lowered.startswith("&"):
                return [_BY_MNEMONIC["PUSH_OCT_LONG"], low, high]
            return [_BY_MNEMONIC["PUSH_LONG"], low, high]
        if kind == "single":
            packed = struct.pack("<f", float(_as_python(text)))
            return [_BY_MNEMONIC["PUSH_SINGLE"]] + list(struct.unpack("<HH", packed))
        if kind == "double":
            packed = struct.pack("<d", float(_as_python(text)))
            return [_BY_MNEMONIC["PUSH_DOUBLE"]] + list(struct.unpack("<HHHH", packed))
        raise AssembleError(f"cannot store a {kind} literal")

    @staticmethod
    def _integer_value(text: str) -> Optional[int]:
        body = text.rstrip("!#&%")
        lowered = body.lower()
        try:
            if lowered.startswith("&h"):
                return int(body[2:] or "0", 16)
            if lowered.startswith("&o"):
                return int(body[2:] or "0", 8)
            if lowered.startswith("&"):
                return int(body[1:] or "0", 8)
            return int(body)
        except ValueError:
            return None

    def _e_push_str(self, emit: Emit) -> List[int]:
        body = (emit.text or "").encode("latin-1")
        return [_BY_MNEMONIC["PUSH_STR"], len(body)] + _as_words(body)

    def _e_rem(self, emit: Emit) -> List[int]:
        column = emit.operands[0] if emit.operands else 0
        body = struct.pack("<H", column) + (emit.text or "").encode("latin-1")
        return [tokens.REM, len(body)] + _as_words(body)

    def _e_rem_meta(self, emit: Emit) -> List[int]:
        body = (emit.text or "").encode("latin-1")
        return [_BY_MNEMONIC["REM_META"], len(body)] + _as_words(body)

    def _e_data(self, emit: Emit) -> List[int]:
        # A lead word QB uses to chain the DATA pool, the text, then a
        # terminator: DATA items are read at run time, not compiled.
        body = (struct.pack("<H", 0) + (emit.text or "").encode("latin-1")
                + b"\x00")
        return [_BY_MNEMONIC["DATA"], len(body)] + _as_words(body)

    # -- variables -------------------------------------------------------

    def _e_load(self, emit: Emit) -> List[int]:
        name, suffix = emit.operands
        return [variable_opcode("LOAD", suffix), self.name_ref(name).ref]

    def _e_store(self, emit: Emit) -> List[int]:
        name, suffix = emit.operands
        return [variable_opcode("STORE", suffix), self.name_ref(name).ref]

    def _e_call_or_index(self, emit: Emit) -> List[int]:
        name, suffix, count = emit.operands
        return [variable_opcode("ARRAY", suffix), count, self.name_ref(name).ref]

    def _e_arrset(self, emit: Emit) -> List[int]:
        name, suffix, count = emit.operands
        return [variable_opcode("ARRSET", suffix), count, self.name_ref(name).ref]

    def _e_field(self, emit: Emit) -> List[int]:
        name, _suffix = emit.operands
        return [_BY_MNEMONIC["FIELD"], self.name_ref(name).ref]

    def _e_field_set(self, emit: Emit) -> List[int]:
        name, _suffix = emit.operands
        return [_BY_MNEMONIC["FIELD_SET"], self.name_ref(name).ref]

    # -- control flow ----------------------------------------------------

    def _jump(self, mnemonic: str, emit: Emit) -> List[int]:
        target = emit.operands[0]
        return [_BY_MNEMONIC[mnemonic], self.label_ref(target).ref]

    def _e_goto(self, emit: Emit) -> List[int]:
        return self._jump("GOTO", emit)

    def _e_gosub(self, emit: Emit) -> List[int]:
        return self._jump("GOSUB", emit)

    def _e_if_goto(self, emit: Emit) -> List[int]:
        return self._jump("IF_GOTO", emit)

    def _e_call(self, emit: Emit) -> List[int]:
        name, count = emit.operands
        return [_BY_MNEMONIC["CALL"], count, self.name_ref(name, FLAG_SUB).ref]

    def _e_on_event_gosub(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["ON_EVENT_GOSUB"], self.label_ref(emit.operands[0]).ref]

    def _e_restore_label(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["RESTORE_LABEL"], self.label_ref(emit.operands[0]).ref]

    def _e_return_label(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["RETURN_LABEL"], self.label_ref(emit.operands[0]).ref]

    def _e_resume_label(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["RESUME_LABEL"], self.label_ref(emit.operands[0]).ref]

    def _e_calls(self, emit: Emit) -> List[int]:
        name, count = emit.operands
        return [_BY_MNEMONIC["CALLS"], count, self.name_ref(name, FLAG_SUB).ref]

    def _e_run_line(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["RUN_LINE"], self.label_ref(emit.operands[0]).ref]

    def _e_on_error(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["ON_ERROR"], self.label_ref(emit.operands[0]).ref]

    def _e_on_goto(self, emit: Emit) -> List[int]:
        return self._jump_table("ON_GOTO", emit)

    def _e_on_gosub(self, emit: Emit) -> List[int]:
        return self._jump_table("ON_GOSUB", emit)

    def _jump_table(self, mnemonic: str, emit: Emit) -> List[int]:
        """ON n GOTO a, b, c: the targets are a payload of references."""
        refs = [self.label_ref(t).ref for t in emit.operands]
        body = struct.pack(f"<{len(refs)}H", *refs)
        return [_BY_MNEMONIC[mnemonic], len(body)] + _as_words(body)

    def _e_call_implicit(self, emit: Emit) -> List[int]:
        name, count = emit.operands
        return [_BY_MNEMONIC["CALL_IMPLICIT"], count,
                self.name_ref(name, FLAG_SUB).ref]

    def _loop_head(self, mnemonic: str, emit: Emit) -> List[int]:
        """FOR, FOR STEP and NEXT: a jump slot, then the control variable."""
        name, _suffix = emit.operands
        return [_BY_MNEMONIC[mnemonic], 0xFFFF, self.name_ref(name).ref]

    def _e_for(self, emit: Emit) -> List[int]:
        return self._loop_head("FOR", emit)

    def _e_for_step(self, emit: Emit) -> List[int]:
        return self._loop_head("FOR_STEP", emit)

    def _e_next(self, emit: Emit) -> List[int]:
        return self._loop_head("NEXT", emit)

    def _e_input_prompt(self, emit: Emit) -> List[int]:
        # A two-byte payload: how the prompt was punctuated, then a byte the
        # editor uses for its own layout.
        body = struct.pack("<BB", emit.operands[0], 0)
        return [_BY_MNEMONIC["INPUT_PROMPT"], len(body)] + _as_words(body)

    # -- declarations ----------------------------------------------------

    def _e_shared_typed(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["SHARED_TYPED"], 0xFFFF]

    def _e_put_graphics(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["PUT_GRAPHICS"], emit.operands[0]]

    def _e_open(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["OPEN"], emit.operands[0]]

    def _e_open_random(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["OPEN_RANDOM"], emit.operands[0]]

    def _e_deftype(self, emit: Emit) -> List[int]:
        """DEFINT A-Z: two masks of letters, with the type in the low bits."""
        code, letters = emit.operands
        head = tail = 0
        for i in letters:
            if i < 16:
                head |= 1 << (15 - i)
            else:
                tail |= 1 << (15 - (i - 16))
        return [_BY_MNEMONIC["DEFTYPE"], 0xFFFF, tail | code, head]

    def _e_dim(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["DIM"], 0xFFFF]

    def _e_static(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["STATIC"], 0xFFFF]

    def _e_common(self, emit: Emit) -> List[int]:
        block = emit.operands[0] if emit.operands else None
        ref = self.name_ref(block).ref if block else 0xFFFF
        return [_BY_MNEMONIC["COMMON"], 0xFFFF, ref]

    def _e_decl(self, emit: Emit) -> List[int]:
        name, suffix = emit.operands
        return [variable_opcode("DECL", suffix), self.name_ref(name).ref]

    def _e_arrdecl(self, emit: Emit) -> List[int]:
        name, suffix, count = emit.operands
        return [variable_opcode("ARRDECL", suffix), count, self.name_ref(name).ref]

    def _e_array(self, emit: Emit) -> List[int]:
        name, suffix, count = emit.operands
        return [variable_opcode("ARRAY", suffix), count, self.name_ref(name).ref]

    def _e_type(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["TYPE"], 0xFFFF, self.name_ref(emit.operands[0]).ref]

    def _e_type_member(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["TYPE_MEMBER"], self.name_ref(emit.operands[0]).ref]

    def _e_as_type(self, emit: Emit) -> List[int]:
        code, column = emit.operands
        return [_BY_MNEMONIC["AS_TYPE"], code, column]

    def _e_as_user_type(self, emit: Emit) -> List[int]:
        name, column = emit.operands
        return [_BY_MNEMONIC["AS_USER_TYPE"], self.name_ref(name).ref, column]

    def _e_fixed_string(self, emit: Emit) -> List[int]:
        length, column = emit.operands
        return [_BY_MNEMONIC["FIXED_STRING"], 6, length, column]

    # -- procedure headers -----------------------------------------------

    def _e_declare(self, emit: Emit) -> List[int]:
        return self._header("DECLARE", emit)

    def _e_sub(self, emit: Emit) -> List[int]:
        return self._header("SUB", emit)

    def _e_function(self, emit: Emit) -> List[int]:
        return self._header("FUNCTION", emit)

    def _header(self, mnemonic: str, emit: Emit) -> List[int]:
        name, suffix, kind, cdecl, alias, params, _static = emit.operands
        high = {"SUB": 1, "FUNCTION": 2}.get(kind or mnemonic, 1)
        if cdecl:
            high |= 0x80
        if alias:
            high |= (len(alias) & 0x1F) << 2
        low = (0x80 | _TYPE_CODE[suffix]) if suffix else 0
        flags = FLAG_SUB if high & 3 == 1 else FLAG_NAME
        listed = params is not None
        words = [self.name_ref(name, flags).ref, (high << 8) | low,
                 len(params) if listed else 0xFFFF]
        params = params or ()
        for pname, psuffix, byval, seg, as_type, array in params:
            mode = 0
            code = 1
            if psuffix:
                mode |= 0x0200
                code = _TYPE_CODE[psuffix]
            if array:
                mode |= 0x0400
            if seg:
                mode |= 0x0800
            if byval:
                mode |= 0x1000
            if as_type is not None:
                mode |= 0x2000
                code = self._type_code(as_type)
            words += [self.name_ref(pname).ref, mode, code]
        body = struct.pack(f"<{len(words)}H", *words)
        if alias:
            body += alias.encode("latin-1")
        code_word = _BY_MNEMONIC[mnemonic]
        return [code_word, len(body)] + _as_words(body)

    def _e_def_fn(self, emit: Emit) -> List[int]:
        """DEF FN's header, which is a signature behind one extra word.

        The lead word is a link QB fills in when it runs; nothing written
        here depends on it.
        """
        name, suffix, params = emit.operands
        low = (0x80 | _TYPE_CODE[suffix]) if suffix else 0
        words = [0xFFFF, self.name_ref(name).ref, (3 << 8) | low, len(params)]
        for pname, psuffix, _byval, _seg, as_type, _array in params:
            code = _TYPE_CODE[psuffix] if psuffix else 1
            mode = 0x0200 if psuffix else 0
            if as_type is not None:
                mode |= 0x2000
                code = self._type_code(as_type)
            words += [self.name_ref(pname).ref, mode, code]
        body = struct.pack(f"<{len(words)}H", *words)
        return [_BY_MNEMONIC["DEF_FN"], len(body)] + _as_words(body)

    def _e_def_fn_end_line(self, emit: Emit) -> List[int]:
        return [_BY_MNEMONIC["DEF_FN_END_LINE"], 2, emit.operands[0]]

    def _type_code(self, named: str) -> int:
        """The type an AS clause names: a built-in code, or a name reference."""
        for code, keyword in tokens.TYPE_KEYWORDS.items():
            if keyword == named.upper():
                return code
        if named.upper() == "ANY":
            return 0
        return self.name_ref(named).ref


def _as_words(body: bytes) -> List[int]:
    """Pad a payload to an even length and unpack it as words."""
    if len(body) % 2:
        body += b" "
    return list(struct.unpack(f"<{len(body) // 2}H", body))


def _as_python(text: str) -> str:
    """A BASIC numeric literal as something Python's float() will read."""
    return text.rstrip("!#&%").replace("d", "e").replace("D", "E")
