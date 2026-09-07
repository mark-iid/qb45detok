"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .decode import decode_file, decode_section
from .render import Renderer
from .reader import BinFile, ParseError, REF_BASE, Section


def _hexdump(data: bytes, base: int = 0, limit: Optional[int] = None) -> None:
    end = len(data) if limit is None else min(len(data), limit)
    for off in range(0, end, 16):
        chunk = data[off : off + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {base + off:06x}  {chunk.hex(' '):<47}  |{text}|")
    if end < len(data):
        print(f"  ... {len(data) - end} more bytes")


def _describe(sec: Section) -> str:
    who = "<module text>" if sec.is_module else f"SUB/FUNCTION {sec.name}"
    return f"{who} @{sec.offset:#07x} body@{sec.body_offset:#07x} {len(sec.body)} bytes"


def cmd_dump(args) -> int:
    bf = BinFile.from_path(args.file)
    d = bf.data
    print(f"{args.file}: {len(d)} bytes")
    print(f"  header signature : {'standard' if bf.has_standard_header else 'UNEXPECTED'}")
    print(f"  bytes 0x12,0x13  : {d[0x12]:#04x} {d[0x13]:#04x}")
    print(f"  code ref         : {bf.code_ref:#06x} -> file offset {bf.code_ref + REF_BASE:#07x}")
    print(f"  name table end   : {bf.free_ref:#06x} -> file offset {bf.free_ref + REF_BASE:#07x}")
    print(f"  names            : {len(bf.names)} ({len(bf.procedures())} procedures)")
    print(f"  sections         : {len(bf.sections)}")
    for sec in bf.sections:
        print(f"    {_describe(sec)}")
        if sec.trailer:
            t = sec.trailer
            print(
                f"      trailer @{t.offset:#07x} lines={t.line_count} kind={t.kind:#06x} "
                f"head={' '.join(f'{w:04x}' for w in t.head)} c={t.unknown_c:#06x}"
            )
    if bf.tail:
        print(f"  trailing bytes   : {len(bf.tail)} @{bf.tail_offset:#07x}")
        _hexdump(bf.tail, bf.tail_offset, limit=64)
    return 0


def cmd_names(args) -> int:
    bf = BinFile.from_path(args.file)
    for entry in bf.names.values():
        if args.procs and not entry.is_proc:
            continue
        if entry.is_proc:
            kind = "proc"
        elif entry.flags == 0x04:
            kind = "label"
        elif entry.is_text:
            kind = "name"
        else:
            kind = "line"
        shown = entry.name if entry.is_text else (entry.label or entry.raw.hex(" "))
        print(f"{entry.ref:#06x} @{entry.offset:#07x} link={entry.link:#06x} {kind:<6} {shown}")
    return 0


def cmd_sections(args) -> int:
    bf = BinFile.from_path(args.file)
    for i, sec in enumerate(bf.sections):
        if args.index is not None and i != args.index:
            continue
        print(_describe(sec))
        if args.bytes:
            _hexdump(sec.body, sec.body_offset, limit=args.bytes or None)
    return 0


def _render(bf: BinFile, instr) -> str:
    parts = [instr.mnemonic]
    for word in instr.operands:
        entry = bf.symbol(word)
        if entry is not None and entry.is_text:
            parts.append(entry.name)
        elif entry is not None and entry.is_label:
            parts.append(f"@{entry.label}")
        else:
            parts.append(f"{word:#06x}")
    sig = instr.signature
    if sig is not None:
        name = bf.symbol(sig.ref)
        args = ", ".join(
            f"{(bf.symbol(p.ref).name if bf.symbol(p.ref) else hex(p.ref))}{p.suffix}"
            for p in sig.params
        )
        parts.append(f"{name.name if name else hex(sig.ref)}({args})")
    elif instr.text is not None:
        parts.append(repr(instr.text))
    elif instr.payload:
        parts.append(instr.payload.hex(" "))
    return " ".join(parts)


def cmd_lines(args) -> int:
    """Walk the token stream and print it one source line per row."""
    bf = BinFile.from_path(args.file)
    for i, ds in enumerate(decode_file(bf)):
        who = "<module text>" if ds.section.is_module else ds.section.name
        state = "ok" if ds.in_sync else f"OUT OF SYNC (expected {ds.expected_lines})"
        if args.index is not None and i != args.index:
            continue
        print(f"== {who}: {len(ds.lines)} lines, {state}")
        for n, line in enumerate(ds.lines, 1):
            label = ""
            if line.labelled:
                entry = bf.symbol(line.label_ref)
                label = f"{entry.label if entry else f'{line.label_ref:#06x}'} "
            body = " ".join(_render(bf, ins) for ins in line.instrs)
            print(f"{n:>5} |{'':<{line.indent}} {label}{body}")
    return 0


def cmd_stats(args) -> int:
    """How much of the token stream is understood, per section."""
    bf = BinFile.from_path(args.file)
    sections = decode_file(bf)
    total = unknown = 0
    for ds in sections:
        codes = [i.code for line in ds.lines for i in line.instrs]
        unk = ds.unknown_codes
        total += len(codes)
        unknown += len(unk)
        who = "<module text>" if ds.section.is_module else ds.section.name
        flag = "" if ds.in_sync else f"  OUT OF SYNC ({len(ds.lines)}/{ds.expected_lines})"
        print(f"  {who:<22} {len(codes):>6} opcodes, {len(unk):>5} unidentified{flag}")
    known = total - unknown
    pct = 100.0 * known / total if total else 100.0
    print(f"  {'TOTAL':<22} {total:>6} opcodes, {unknown:>5} unidentified ({pct:.1f}% identified)")
    print(f"  sections in sync: {sum(1 for d in sections if d.in_sync)}/{len(sections)}")
    return 0


def cmd_detok(args) -> int:
    """Write the file back out as ASCII BASIC."""
    bf = BinFile.from_path(args.file)
    lines = Renderer(bf).file(decode_file(bf))
    if args.output:
        # QB's own text files are CRLF, so writing one reproduces the format.
        with open(args.output, "w", encoding="latin-1", newline="") as fh:
            for line in lines:
                fh.write(line + "\r\n")
    else:
        for line in lines:
            print(line)
    unrendered = sum(1 for line in lines if "<qb45detok:" in line)
    if unrendered:
        print(
            f"qb45detok: {unrendered} line(s) could not be rendered and are "
            f"marked in the output",
            file=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qb45detok",
        description="Read Microsoft QuickBASIC 4.5 binary .BAS files (the 0xFC format).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dump", help="show header, symbol table and section layout")
    d.add_argument("file")
    d.set_defaults(func=cmd_dump)

    n = sub.add_parser("names", help="list the name table")
    n.add_argument("file")
    n.add_argument("--procs", action="store_true", help="only SUB/FUNCTION entries")
    n.set_defaults(func=cmd_names)

    s = sub.add_parser("sections", help="list code sections, optionally hexdumping them")
    s.add_argument("file")
    s.add_argument("--index", type=int, help="only this section (0 is the module text)")
    s.add_argument("--bytes", type=int, nargs="?", const=0, default=None,
                   help="hexdump the body, up to N bytes (0 or bare flag for all)")
    s.set_defaults(func=cmd_sections)

    ln = sub.add_parser("lines", help="decode the token stream, one source line per row")
    ln.add_argument("file")
    ln.add_argument("--index", type=int, help="only this section (0 is the module text)")
    ln.set_defaults(func=cmd_lines)

    st = sub.add_parser("stats", help="report how much of the token stream is identified")
    st.add_argument("file")
    st.set_defaults(func=cmd_stats)

    t = sub.add_parser("detok", help="convert to ASCII .BAS")
    t.add_argument("file")
    t.add_argument("-o", "--output", help="write CRLF text to this file instead of stdout")
    t.set_defaults(func=cmd_detok)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ParseError as exc:
        print(f"qb45detok: {args.file}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"qb45detok: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
