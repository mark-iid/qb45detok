# qb45detok

[![tests](https://github.com/mark-iid/qb45detok/actions/workflows/tests.yml/badge.svg)](https://github.com/mark-iid/qb45detok/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.9%20to%203.13-blue)](https://github.com/mark-iid/qb45detok)
[![license](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

**Convert Microsoft QuickBASIC binary `.BAS` files back to readable BASIC
source text.** QuickBASIC 4.5 and BASIC 7 PDS. No DOS, no DOSBox, no copy of
`QB.EXE` needed.

If you have old QuickBASIC or QB45 programs that show up as binary garbage in a
text editor, and the file starts with byte `0xFC`, this is the tool for them.

```
qb45detok detok OLDPROG.BAS -o OLDPROG.TXT
```

QuickBASIC could save a program either as plain text or in its own binary
format. The Save As dialog calls it **Fast Load and Save**, and you will also
see it called the binary, compressed or tokenized format; they are all the same
thing. It is smaller and loads faster, so a lot of code from the era ended up
stored that way, including a pile of my own from 1994.

## Is this the right tool for your file?

The BASICs of that era all had tokenized formats and none of them are
compatible. Check the first byte of the file:

```
head -c 1 OLDPROG.BAS | od -An -tx1
```

| First bytes | Format | Use |
|---|---|---|
| `fc 00` | QuickBASIC 4.5 | **this tool** |
| `fc 02` | BASIC 7 PDS, saved by QBX | **this tool** |
| `ff` | GW-BASIC, BASICA, or MSX-BASIC | [bascat], [gwbasic-decoder], [basbinizer] |
| `f9`, `f1`, `f3` | older Microsoft BASIC | [decode_ms_basic.py] |
| printable text | already ASCII | nothing to do |

The byte after `fc` says which product wrote the file. `qb45detok triage` reads
it for you.

Only the `fc` row is something I've verified myself, across fifty-eight files.
The rest is from those projects' own documentation, and is here so you don't
waste time on the wrong tool, as I did.

A note on versions. QuickBASIC 4.5 and BASIC 7 PDS are both read and both
tested. QuickBASIC 4.0 wrote its own variant and I have no 4.0 files, so that
one is untested. QBasic 1.1 (the cut-down one bundled with MS-DOS 5 and 6)
saves plain text only, so its files need nothing.

If you're trying to decompile a QuickBASIC `.EXE` rather than read a `.BAS`,
that's a different problem: see [qbasic-reversing-notes].

[bascat]: https://github.com/rwtodd/bascat
[gwbasic-decoder]: https://github.com/danvk/gwbasic-decoder
[basbinizer]: https://github.com/Colpocorto/basbinizer
[decode_ms_basic.py]: https://mac-guyver.com/switham/2008/05/Decode_MS_BASIC/
[qbasic-reversing-notes]: https://github.com/maurom/qbasic-reversing-notes

## Other tools that read this format

The only way I had to read these files was to boot DOS, load each one into
`QB.EXE`, and do File > Save As > Text. That's slow, it needs a working DOS
setup, and it has a nasty failure mode: pick the wrong format on the way out and
you get a file that looks converted and isn't.

QuickBASIC 4.5 isn't like the other formats in the table above. It keeps
identifiers in a hashed symbol table and refers to them by offset rather than
storing names inline, and statements are stored in reverse Polish rather than as
a flat token list, so the detokenizers written for GW-BASIC and its relatives
don't read it.

Two other projects do. QB64 Phoenix Edition builds [QB45BIN], by qarnos, which
its IDE runs when it opens a 4.5 binary and which also works from the command
line. [jeredw/qbc], a QBasic for the browser, has a `Qb45Format.ts` written on
top of that. Both convert in one direction, and both are a component of a larger
program rather than something you can pick up on its own.

|  | QB45BIN | qbc | this |
|---|---|---|---|
| binary to text | yes | yes | yes |
| text to binary | no | no | yes |
| the format written up | no | no | `docs/format.md` |
| shipped as | a utility inside QB64pe | a class inside a web app | a CLI and a library |
| needs | QB64 Phoenix Edition | a browser | Python 3.9, no dependencies |

Where the three opcode tables overlap, they agree. Twelve entries here are in
neither of the others. `GOSUB`, `GOTO`, `IF ... THEN` and `PUT` each have a
second form that QuickBASIC writes and those two don't decode. `SEG` marks an
argument passed by segment. `TO` and `CHDRIVE` are keywords 4.5 keeps a slot for
and won't print, which took BASIC 7 PDS to read back. The remaining five are
markers that carry no text, which a decoder still has to know about so it
doesn't choke on them.

Going the other way turned up most of that. Writing the format settled the
argument markers, the jump slots left empty until a program runs, the separate
opcode per written form of `LINE`, `CIRCLE`, `PSET`, `GET` and `PUT`, and the
default types a procedure records at its head.

`docs/format.md` is the result written up: the layout, the symbol table, the
opcode encodings, and the parts I still can't explain. If you want to write your
own reader, or port this to another language, start there.

[QB45BIN]: https://github.com/QB64-Phoenix-Edition/QB64pe
[jeredw/qbc]: https://github.com/jeredw/qbc

## Writing the format

It also goes the other way. `qb45detok tok` reads a text `.BAS` and writes the
binary QuickBASIC saves, so a program edited in a modern editor can be handed
back to QB 4.5 without opening the DOS editor.

```
qb45detok tok PROGRAM.TXT -o PROGRAM.BAS
```

Writing a format is a harder check than reading one, since a reader can skip a
field it hasn't worked out and still look correct. Every corpus program
detokenizes, tokenizes and detokenizes again to the same text, bar the eight
cases `docs/format.md` sets out, and the opcodes chosen match the ones
QuickBASIC stored on every one of the 10,876 lines it tokenized.

QuickBASIC itself reads what this writes. Four programs tokenized here were
handed back to QB 4.5, which loaded each one and saved it as text identical to
what it had written from its own copy: a graphics program of eleven sections
and 25 names, one with periods in its procedure names, one exercising every
graphics statement, and one with an `$INCLUDE`.

Bucket placement in the symbol table is the one thing not reproduced exactly:
the hash QB uses for names is still unknown, so the writer puts everything in
one chain. That is what those four files test, and QB rebuilds its own lookup
on load rather than trusting the one it was given.

## Porting to QB64

QB64 Phoenix Edition aims at QuickBASIC 4.5 compatibility and gets most of the
way there, but about two dozen keywords are missing. The awkward part is what
its own documentation says about them: "older code that uses these keywords
won't generate errors, as these are ignored by the compiler." The program
builds, runs, and quietly does something else.

```
qb45detok lint OLDPROG.BAS
```

```
QB64 ignores these. The program will build and run, and do something else.

  <module>:9               CALLS
                           CALLS Ext(3)
                           -> call the procedure directly; QB64 passes by reference already

These have to be rewritten before it will build.

  <module>:10              CALL ABSOLUTE
                           CALL ABSOLUTE(0)
                           -> machine code in a DATA statement will not run; rewrite the routine in BASIC
```

It takes either format, and exits non-zero when it finds something. The rules
come from the list QB64 ships in `internal/help/`, not from my recollection of
what it supports.

The check is made against the tokens rather than the text, which is why it
lives here. QuickBASIC recorded what it understood each line to mean, so a
variable called `fre`, the word `TRON` inside a string and a comment mentioning
`IOCTL` are all invisible to it. It also separates cases the text can't:
reading `DATE$` is supported and assigning to it is not, and QB stores those as
two different opcodes. `CALL ABSOLUTE` and `CALL INTERRUPT` are caught by the
name they call, since they're routines in `QB.QLB` rather than keywords.

Across my corpus, 39 of the 58 programs need no changes at all.

## BASIC 7 PDS

PDS wrote a variant of the same format and nothing else reads it. QB64 refuses
it outright ("QBX 7.1 binary format not supported"), and the only advice I can
find on the usenet and forum threads where people ask is to find someone who
still has PDS installed and have them resave the files. This reads them
directly.

```
qb45detok detok OLDPROG.BAS      # it works out which product wrote the file
```

Four differences, all of them small, and `docs/format.md` has the detail:

- one extra byte in the header, which moves everything after it up by one
- `CURRENCY` takes type code 5, which pushes `STRING` to 6. This is the one
  that quietly changes meaning rather than failing, since a 4.5 reader turns
  `AS CURRENCY` into `AS STRING`
- two more bytes per parameter in a procedure signature
- a block of opcodes of its own for the keywords PDS adds

The opcode block is only partly mapped. Seven are named, read off a program
written to use them: `CURDIR$`, `DIR$`, `CVC`, `MKC$`, `SSEG`, `SSEGADD` and
`CCUR`, plus `CHDRIVE`, which 4.5 keeps a slot for and will not print. PDS adds
about fifty keywords over 4.5, so there are more to find, and the gaps in the
`0181` block say roughly where they are.

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
qb45detok lint PROGRAM.BAS               # what will not port to QB64
```

If you have a directory of old files and don't know what's in it, start here:

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

Across the fifty-eight programs I've tested it on (my own code, a QuickBASIC
sample, and small programs written to exercise one feature each) every one comes
back byte for byte identical to what QuickBASIC itself writes with Save As Text.
The largest is 2,386 lines.

Every opcode in those programs is identified, and all 254 code sections decode
to exactly the line count the file records for them. The writer rebuilds all 58
files byte for byte from their own decoded structure.

I also checked the opcode table against the 224 keywords in the QuickBASIC 4.5
help index. Every documented statement and function is covered. The only index
entries left over are `ABSOLUTE`, `INTERRUPT` and `INTERRUPTX`, which are
routines in `QB.QLB` rather than keywords, and which come through as ordinary
`CALL` targets.

If the decoder can't express a statement it writes a marker on that line rather
than dropping it or inventing something, and `detok` exits non-zero so you know
to look. Nothing is guessed at quietly.

## Limits

- A statement I haven't run through it would show up as a marked line rather
  than as silently wrong output.
- Every statement opcode is accounted for. Thirteen of the 256 values are
  line headers or other structure rather than statements, and the single
  unassigned function code is an unused slot in the type-conversion family
  rather than a missing name.
- `$INCLUDE` works both ways. QuickBASIC stores every line of the included
  file in the binary and leaves them out when it saves as text, and both
  directions here do the same. `tok` reads the `.BI` from beside the source,
  so it has to be there.
- A handful of statement forms are stored identically and can't be told apart.
  `LOCK #1, TO 32` and `LOCK #1, 1 TO 32` produce the same tokens, so the first
  comes back as the second.
- Only QuickBASIC 4.5 is tested. I have no QuickBASIC 4.0 files, so what that
  version writes is untested rather than known. BASIC 7 PDS files are also
  untested, though PDS reads 4.5 files correctly, and its editor was useful for
  identifying two opcodes 4.5 knows about but won't print.

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
what happens in CI: 183 of them run there against 1,533 here, since most of
the suite is one test per corpus file.

## License

Apache 2.0. See `LICENSE`.
