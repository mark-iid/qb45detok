# qb45detok

**Convert Microsoft QuickBASIC 4.5 binary `.BAS` files back to readable BASIC
source text.** No DOS, no DOSBox, no copy of `QB.EXE` needed.

If you have old QuickBASIC or QB45 programs that show up as binary garbage in a
text editor, and the file starts with byte `0xFC`, this is the tool for them.

```
qb45detok detok OLDPROG.BAS -o OLDPROG.TXT
```

QuickBASIC could save a program either as plain text or in its own tokenized
binary format. The binary format is smaller and loads faster, so a lot of code
from the era ended up stored that way — including a pile of my own from 1994.

## Is this the right tool for your file?

The BASICs of that era all had tokenized formats and none of them are
compatible. Check the first byte of the file:

```
head -c 1 OLDPROG.BAS | od -An -tx1
```

| First byte | Format | Use |
|---|---|---|
| `fc` | QuickBASIC 4.5 | **this tool** |
| `ff` | GW-BASIC, BASICA, or MSX-BASIC | [bascat], [gwbasic-decoder], [basbinizer] |
| `f9`, `f1`, `f3` | older Microsoft BASIC | [decode_ms_basic.py] |
| printable text | already ASCII | nothing to do |

Only the `fc` row is something I have verified myself, across nineteen files.
The rest is from those projects' own documentation, and is here so you do not
waste time on the wrong tool — as I did.

A note on versions. I have only tested this against QuickBASIC 4.5. QuickBASIC
4.0 and the later PDS / BASIC 7.x releases wrote their own variants, and QBasic
1.1 — the cut-down one bundled with MS-DOS 5 and 6 — saves plain text only, so
its files need nothing. If you have a `0xFC` file from something other than 4.5
and it does not decode, that is worth reporting.

If you are trying to decompile a QuickBASIC `.EXE` rather than read a `.BAS`,
that is a different problem: see [qbasic-reversing-notes].

[bascat]: https://github.com/rwtodd/bascat
[gwbasic-decoder]: https://github.com/danvk/gwbasic-decoder
[basbinizer]: https://github.com/Colpocorto/basbinizer
[decode_ms_basic.py]: https://mac-guyver.com/switham/2008/05/Decode_MS_BASIC/
[qbasic-reversing-notes]: https://github.com/maurom/qbasic-reversing-notes

## Why I wrote it

The only way I had to read these files was to boot DOS, load each one into
`QB.EXE`, and do File → Save As → Text. That is slow, it needs a working DOS
setup, and it has a nasty failure mode: pick the wrong format on the way out
and you get a file that looks converted and isn't.

Nothing existed that would do it directly. Every BASIC detokenizer I could find
handles one of the other formats above. QuickBASIC 4.5 is not like any of them.
It keeps identifiers in a hashed symbol table and refers to them by offset
rather than storing names inline, and statements are stored in reverse Polish
rather than as a flat token list.

So I worked the format out by saving programs both ways and comparing the two
sides. `docs/format.md` is what I found, written up properly — the layout, the
symbol table, the opcode encodings, and the parts I still cannot explain. If
you want to write your own reader, or port this to another language, start
there.

## Install

```
pip install -e .
```

Python 3.9 or newer. No dependencies.

## Usage

```
qb45detok detok PROGRAM.BAS              # write source to stdout
qb45detok detok PROGRAM.BAS -o OUT.BAS   # write a CRLF text file
```

There are also commands for looking at the format itself:

```
qb45detok dump PROGRAM.BAS         # header, symbol table, section layout
qb45detok names PROGRAM.BAS        # the name table
qb45detok lines PROGRAM.BAS        # decoded statements, one source line per row
qb45detok sections PROGRAM.BAS     # code sections, with --bytes to hexdump
qb45detok stats PROGRAM.BAS        # how much of the token stream is identified
```

## How well it works

Across the nineteen programs I tested it on — my own code, a QuickBASIC sample,
and some small programs written to exercise one feature each — **fifteen come
back byte for byte identical** to what QuickBASIC itself writes with Save As
Text. The largest of those is 353 lines.

Underneath that, the opcode table now covers **99.95%** of the opcodes in those
programs, and 86 of 87 code sections decode to exactly the line count the file
records for them.

Nothing is guessed at silently. If the decoder cannot express a statement it
writes a marker on that line rather than dropping it or inventing something,
and `detok` exits non-zero so you know to look.

## Limits

The four programs that do not round-trip exactly are all large, and in each
case one bad line shifts everything after it, so they look worse than they are.
What is left:

- Six opcodes are still unidentified, each appearing once or twice.
- One code section decodes to the wrong line count.
- Random-access file I/O (`FIELD`, `GET #`, `PUT #`, `LSET`), `CHAIN`, `DRAW`
  and `ON TIMER` never appeared in anything I tested, so they are unhandled.

`docs/format.md` lists the open questions, including four hypotheses I ruled
out by experiment so nobody repeats them.

## Testing

The tests work by detokenizing a program and comparing against the text
QuickBASIC produced for the same program, so they need matched pairs of files:
the same source saved twice, once in QuickBASIC format and once as text.

That test data is mostly my own old programs and is not distributed here. To
run the full suite, put pairs in `corpus/bin/NAME.BAS` and `corpus/txt/NAME.BAS`
and the tests will pick them up.

`samples/` has small programs written to exercise one language feature each,
in plain text. Run each through QB 4.5 and save it twice — once in QuickBASIC
format, once as text — and you have a corpus to test against without needing
mine. `samples/README.md` has the steps.

```
pytest
```

Without a corpus the format-level tests still run and the rest skip.

## License

Apache 2.0. See `LICENSE`.
