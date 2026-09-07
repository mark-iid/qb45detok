# qb45detok

[![tests](https://github.com/mark-iid/qb45detok/actions/workflows/tests.yml/badge.svg)](https://github.com/mark-iid/qb45detok/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.9%20to%203.13-blue)](https://github.com/mark-iid/qb45detok)
[![license](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

**Convert Microsoft QuickBASIC 4.5 binary `.BAS` files back to readable BASIC
source text.** No DOS, no DOSBox, no copy of `QB.EXE` needed.

If you have old QuickBASIC or QB45 programs that show up as binary garbage in a
text editor, and the file starts with byte `0xFC`, this is the tool for them.

```
qb45detok detok OLDPROG.BAS -o OLDPROG.TXT
```

QuickBASIC could save a program either as plain text or in its own tokenized
binary format. The binary format is smaller and loads faster, so a lot of code
from the era ended up stored that way, including a pile of my own from 1994.

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

Only the `fc` row is something I have verified myself, across fifty-seven files.
The rest is from those projects' own documentation, and is here so you do not
waste time on the wrong tool, as I did.

A note on versions. I have only tested this against QuickBASIC 4.5. QuickBASIC
4.0 and the later PDS / BASIC 7.x releases wrote their own variants, and QBasic
1.1, the cut-down one bundled with MS-DOS 5 and 6, saves plain text only, so
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
`QB.EXE`, and do File > Save As > Text. That is slow, it needs a working DOS
setup, and it has a nasty failure mode: pick the wrong format on the way out
and you get a file that looks converted and isn't.

Nothing existed that would do it directly. Every BASIC detokenizer I could find
handles one of the other formats above. QuickBASIC 4.5 is not like any of them.
It keeps identifiers in a hashed symbol table and refers to them by offset
rather than storing names inline, and statements are stored in reverse Polish
rather than as a flat token list.

So I worked the format out by saving programs both ways and comparing the two
sides. `docs/format.md` is what I found, written up properly: the layout, the
symbol table, the opcode encodings, and the parts I still cannot explain. If
you want to write your own reader, or port this to another language, start
there.

## Writing the format

It also goes the other way. `qb45detok tok` reads a text `.BAS` and writes the
binary QuickBASIC saves, so a program edited in a modern editor can be handed
back to QB 4.5 without opening the DOS editor.

```
qb45detok tok PROGRAM.TXT -o PROGRAM.BAS
```

That is the strongest test of the format there is. Reading a file lets you skip
the fields you have not worked out; writing one does not. Every corpus program
detokenizes, tokenizes and detokenizes again to the same text, bar the eight
cases `docs/format.md` sets out, and the opcodes chosen match the ones
QuickBASIC stored on every one of the 10,876 lines it tokenized.

Bucket placement in the symbol table is the one thing not reproduced exactly:
the hash QB uses for names is still unknown, so the writer puts everything in
one chain. QB rebuilds its own lookup on load and reads such a file normally.

## Reading old data files

Random access files written by these programs hold numbers in Microsoft Binary
Format, which predates IEEE 754. `qb45detok.mbf` converts both ways, so a data
file saved through `MKSMBF$` or `MKDMBF$` can be read from Python:

```python
from qb45detok import mbf

mbf.single_to_float(b"\x00\x00\x00\x81")   # 1.0
mbf.float_to_double(3.14159)
```

## Install

```
pip install -e .
```

Python 3.9 or newer. No dependencies.

## Usage

```
qb45detok detok PROGRAM.BAS              # write source to stdout
qb45detok detok PROGRAM.BAS -o OUT.BAS   # write a CRLF text file
qb45detok tok PROGRAM.TXT -o OUT.BAS     # and back again
```

If you have a directory of old files and do not know what is in it, start
here:

```
qb45detok triage OLDDISK/            # what each file is, and what will read it
qb45detok triage OLDDISK/ --convertible   # just the ones this tool handles
```

Every BASIC of the era is identified by its first byte, so this costs nothing
and saves feeding the wrong file to the wrong tool.

It also reads QuickBASIC's own help databases, which are a separate Microsoft
format that nothing modern opens:

```
qb45detok hlp-list QB45QCK.HLP              # the contexts it defines
qb45detok hlp-show QB45QCK.HLP PRINT --body # one reference topic as text
qb45detok hlp-dump QB45ADVR.HLP -o docs/    # every topic, one file each
```

`QB45ADVR.HLP` is the full QuickBASIC 4.5 language reference. Dumping it gives
about 11,500 lines of plain text, code examples included, which is the whole
reference in a form you can grep. `docs/quickhelp.md` describes that format.

There are also commands for looking at the format itself:

```
qb45detok dump PROGRAM.BAS         # header, symbol table, section layout
qb45detok names PROGRAM.BAS        # the name table
qb45detok lines PROGRAM.BAS        # decoded statements, one source line per row
qb45detok sections PROGRAM.BAS     # code sections, with --bytes to hexdump
qb45detok stats PROGRAM.BAS        # how much of the token stream is identified
```

## How well it works

Across the fifty-seven programs I have tested it on (my own code, a QuickBASIC
sample, and small programs written to exercise one feature each) **every one
comes back byte for byte identical** to what QuickBASIC itself writes with
Save As Text. The largest is 2,386 lines.

Every opcode in those programs is identified, and all 253 code sections decode
to exactly the line count the file records for them. Writing the format back
out is a stronger check than reading it, and the writer rebuilds all 57 files
byte for byte from their own decoded structure.

I also checked the opcode table against the 224 keywords in the QuickBASIC 4.5
help index. Every documented statement and function is covered. The only index
entries left over are `ABSOLUTE`, `INTERRUPT` and `INTERRUPTX`, which are
routines in `QB.QLB` rather than keywords, and which come through as ordinary
`CALL` targets.

Nothing is guessed at silently. If the decoder cannot express a statement it
writes a marker on that line rather than dropping it or inventing something,
and `detok` exits non-zero so you know to look.

## Limits

- A statement I have not run through it would show up as a marked line rather
  than silently wrong output. That is the main thing to know: the tool fails
  loudly rather than quietly.
- Fourteen of the 256 statement opcodes are still unidentified, but most of
  them are not missing statements. Nine are the line-header values, which
  cannot be statements at all; two more behave the same way, sending QB into
  a loop when it is handed one. That leaves three: two that write only a
  colon, and one that neither QuickBASIC 4.5 nor BASIC 7 will render. The one
  unassigned function code is an unused slot in the type-conversion family
  rather than a missing name.
- A handful of statement forms are stored identically and cannot be told
  apart. `LOCK #1, TO 32` and `LOCK #1, 1 TO 32` produce the same tokens, so
  the first comes back as the second.
- Only QuickBASIC 4.5 is tested. I have no QuickBASIC 4.0 files, so what that
  version writes is untested rather than known. BASIC 7 PDS files are also
  untested, though PDS reads 4.5 files correctly, and its editor was useful
  for identifying two opcodes 4.5 knows about but will not print.

`docs/format.md` lists the open questions, including the hypotheses I ruled out
by experiment so nobody repeats them, and how to ask QuickBASIC itself what an
opcode means rather than hunting for source that produces one.

## Testing

The tests work by detokenizing a program and comparing against the text
QuickBASIC produced for the same program, so they need matched pairs of files:
the same source saved twice, once in QuickBASIC format and once as text.

That test data is mostly my own old programs and is not distributed here. To
run the full suite, put pairs in `corpus/bin/NAME.BAS` and `corpus/txt/NAME.BAS`
and the tests will pick them up.

`samples/` has small programs written to exercise one language feature each,
in plain text. Run each through QB 4.5 and save it twice, once in QuickBASIC
format and once as text, and you have a corpus to test against without needing
mine. `samples/README.md` has the steps.

```
pytest
```

Without a corpus the format-level tests still run and the rest skip, which is
what happens in CI: 157 of them run there against 1,434 here, since most of
the suite is one test per corpus file.

## License

Apache 2.0. See `LICENSE`.
