# The QuickBASIC 4.5 binary `.BAS` format

Worked out by comparing tokenized programs against the text QB 4.5 produced
for the same programs. None of it comes from documentation. Anything marked
Verified is checked by the test suite against every pair in the corpus; the
rest is observation that still needs confirming.

## Overall layout

| Offset | Size | Contents |
|---|---|---|
| `0x00` | 28 | header |
| `0x1c` | 82 | symbol table: 41 hash buckets + 2 trailing words |
| `0x72` | var | name table |
| var | var | code sections: module text, then one per procedure |

## Symbol references

The single most important fact about the format: identifiers do not appear
inline in the token stream. Everything refers to the name table by a symbol
reference, and a reference is a byte offset measured from `0x1c`, the start
of the hash-bucket array, not from the start of the file.

    file_offset = ref + 0x1c

The same reference space is used by the header, the bucket slots, the chain
links inside name entries, and the operands in the token stream. Verified:
every reference in every corpus file resolves to an entry boundary.

## Header (`0x00`-`0x1b`)

    fc 00 01 00 0c 00 81 01 82 01 06 00 01 02 03 04 05 08  ..  ..  ff ff 24 00
     0                                                     12  13

Bytes `0x00`-`0x11` are byte-identical in all 34 files. `0xfc` at offset 0 is
the format magic.

- `0x12`: `0x10` everywhere except `PROJECT2.BAS`, which has `0x11`. Unknown.
- `0x13`: `0x51` or `0x10`, and it tracks how the program reached the
  editor rather than what format it was saved in. Every file typed or edited
  in QB and then saved holds `0x51`; every file loaded from ASCII text and
  saved holds `0x10`. Verified by generating a file both ways: both are
  ordinary tokenized programs and both decode identically, so this byte does
  not mark a failed conversion, even though it looks like it should.
- `0x14`-`0x17`: `ff ff 24 00` in every file. Unknown.
- `0x18`: a `DATA` pointer of some kind. Verified: it is `ffff` in every
  program that has no `DATA` statement and non-`ffff` in exactly the two that
  do. Neither value resolves as a name-table reference, so what it points into
  is still unknown.
- `0x1a`: code reference: ref of the first code section. Verified.

## Symbol table (`0x1c`-`0x71`)

41 `u16` hash buckets at `0x1c`, each holding the reference of the first name
entry in its chain, or 0 for an empty bucket. Verified: following every
bucket chain reaches every name-table entry exactly once, in all 34 files.

- `0x6e`: reference one past the last name entry, i.e. the end of the name
  table. Verified: walking entries from `0x72` lands exactly here.
- `0x70`: `0x0052` in all 34 files, which is the reference of `0x6e` itself.
  Probably a fixed "end of buckets" marker.

## Name table (from `0x72`)

Entries are packed with no padding and no alignment:

    u16  link    reference of the next entry in this hash bucket, 0 to end
    u8   flags
    u8   length
    ...  payload, `length` bytes

For `flags` `0x00` and `0x40` the payload is the identifier as ASCII.

| flags | Meaning |
|---|---|
| `0x00` | plain name (variable, label, external) |
| `0x40` | a name used in statement position: a `SUB`, or a `CALL` target |
| `0x02`, `0x04`, `0x06` | not a name, a 2-byte binary payload, unknown. Only in `DIRMAST`, `DRAWSCR1`, `PROJECT2`, `STARDEF`, the files with line numbers, `TYPE` and `DATA` |

`0x40` marks `SUB`s, not procedures in general. Across the corpus it is set on
all 383 `SUB` names and on none of the 93 `FUNCTION` names, with no exceptions.
That is why `DIRMAST` has declared procedures without it: they are functions.
It is also set on names that have no `DECLARE` at all but are used as a `CALL`
target, such as `ABSOLUTE`, so the rule is about statement position rather than
about being declared. Reading it is the cheapest way to tell a `SUB` from a
`FUNCTION` without decoding a signature.

The table also holds names that appear nowhere in the source. `OBJSCAN` has
`dir`, `Rotinue`, `Rotine` and `NMALLOC` for a 28-line program. QB evidently
does not garbage-collect names once entered, which is why a 284-byte program
can produce a 6,855-byte file.

## Code sections

The module-level text comes first, at `code_ref + 0x1c`, and is preceded by a
`u16` byte length. Verified: that length is exact in all 34 files.

Every section is followed by a 16-byte trailer:

    u16 u16 u16 u16   unknown
    u32  line_count   source lines in the section
    u16  unknown
    u16  kind         0x0102 for the module text, 0x0c02 for a procedure
                      (one procedure in the corpus reads 0x0402 instead)

Those first four words are often all `0xff`, which made them look like a
signature worth scanning for. They are not: in `TORUS` most trailers read
`ff ff 04 00 ff ff ff ff`, and scanning finds only one of its seventeen
sections. Sections are found by chaining declared lengths instead -- every
section states how long its token stream is, so the walk is stream, trailer,
next section, to end of file. Verified: this reproduces the previously
scanned boundaries exactly and finds all of `TORUS`.

Verified: `line_count` equals the number of source lines in the
corresponding text, counting `SUB`/`FUNCTION` and `END SUB`/`END FUNCTION`
themselves, for every procedure in every file. This makes a useful oracle for
the detokenizer: it says exactly how many lines a section must produce.

Two adjustments are needed to make that come out exactly, and both are facts
about the format rather than fudges. A procedure's section begins at the run
of comment lines above its `SUB`, not at the keyword. And when the module
carries a `DEFINT`-style statement, QB copies the type defaults to the head of
every procedure as a hidden record; it occupies a counted line but is never
printed, so those procedures read one line longer in the binary.

Verified: `kind` cleanly separates module text from procedures.

Each procedure section follows its predecessor's trailer immediately and starts
with its own name:

    u8   0x00
    u8   length
    u8   0x00
    ...  name, `length` bytes
    ...  tokens

The preamble's kind byte carries at least one meaning: bit `0x80` marks a
`STATIC` procedure, which holds for all 73 procedures across the corpus. The
remaining values are `0x30` and `0x38`, differing by bit `0x08`, and every
`STATIC` procedure has that bit set as well. What it records on its own is not
known -- it does not track whether the procedure takes parameters, whether it
is a `FUNCTION` rather than a `SUB`, or whether its header carries `0017`.

Verified: these names match the `SUB`/`FUNCTION` names in the text exactly,
for all 34 files, and the sections tile the file from the code reference to
EOF with no gaps.

## Procedure sections in detail

A procedure section starts at the run of comment lines immediately above its
`SUB`/`FUNCTION` in the source, not at the keyword. Verified: with that
rule, `line_count` matches the text for every procedure in all 34 files --
including `PROJECT2`'s `MarkTest` and `BondCalc`, which look four lines short
otherwise.

## The token stream

Each section's stream is a sequence of 16-bit little-endian words. Getting the
alignment right matters: procedure names are variable-length, so the five-byte
preamble described above has to be skipped exactly or every opcode shifts.

### Lines

The stream is a list of source lines. Each begins with a header word:

    bits 15..10   indentation, in spaces
    bits  9.. 0   flags

Two flag bits are used:

- `0x004` -- the line carries a label. Two more words follow: an offset, and
  the name-table reference of the label. That reference resolves to a `flags`
  `0x04` entry for `Retry:` style labels and a `flags` `0x06` entry for
  numeric ones.
- `0x001` -- the indentation is in the following word rather than in the
  header's own field. That word holds a raw space count when the indent is 32
  or more, and is otherwise a header-shaped word (`indent << 10`). Why small
  indents sometimes take the escape is not understood, but both forms decode
  unambiguously: low ten bits set means a raw count.

A header is recognisable because no opcode has zero in its low ten bits apart
from those flags.

Verified: decoded indentation matches the leading spaces of QB's text
output on all 2,806 procedure lines that can be checked, with no exceptions.
That includes lines indented past 32, which only `TORUS` and `PROJECT2`
contain and which is what exposed the escape in the first place.

### Statements

Statements are stored in reverse Polish -- operands first, then the operator.
`Desc$ = SPACE$(80)` is

    PUSH_INT 80 | SPACE$ | STORE$ Desc

and `File$ = LCASE$(RTRIM$(LTRIM$(COMMAND$)))` is

    COMMAND$ | LTRIM$ | RTRIM$ | LCASE$ | STORE$ File

### Opcode encoding

Three families are encoded rather than enumerated:

- Variable access. The high byte is the data type and the low byte the
  operation. Types: `00` SINGLE, `04` INTEGER, `08` LONG, `0c` DOUBLE, `14`
  STRING. Operations: `0b` load, `0c` store, `0d` declare, `0e` array load,
  `0f` array store, `10` array declare. So `140b` is "load a string variable"
  and takes a symbol reference.
- Immediate constants. Low byte `64`, with the value in the high byte as
  `(high - 1) / 4`: `0164` pushes 0, `0564` pushes 1, `2964` pushes 10.
- Type conversions. Low byte `08`, with the target type in the high byte
  under the same `(high - 1) / 4` rule: `0508` is `CINT`, `0908` `CLNG`,
  `0d08` `CSNG`, `1108` `CDBL`.
- Literals. Low byte `65` takes the value in the following word; `016b`
  takes a 32-bit float in the next two and `016c` a 64-bit one in the next
  four; `016d` is a string, a word count followed by that many bytes.

Length-prefixed payloads are padded to a word boundary. String literals are
padded with the closing `"` rather than a zero.

### The dotted name marker

`0017` is not a statement. It is a one-word trailer on a line that names an
identifier containing a period, such as `Press.Any.Key` or `A.Var%`. It takes
no operands, produces no text, and appears exactly once at the end of such a
line however many dotted names the line uses. It occurs 131 times across the
corpus, always last on its line and never anywhere else.

A period is ambiguous in QB: `T.xc` is a field of the record `T`, while
`Press.Any.Key` is one identifier. QB resolves that when it tokenizes, and a
field access is stored as a field opcode rather than a name, so `TORUS`, which
uses records throughout, carries no `0017` at all. The marker appears to record
that the line took the other branch.

Verified with a matched pair, `samples/DOTS.BAS` and `samples/NODOTS.BAS`.
They are the same program apart from the periods in its identifiers. The
dotted one carries the marker on exactly the six lines that name one; the
other carries none at all. `samples/DOTSUB.BAS` extends this to `SUB` and
`DECLARE` headers and dotted parameters, where the rule holds just as exactly.

All three were loaded from ASCII text and saved, never edited, and their byte
at `0x13` is `0x10`. That disproves the earlier reading that the marker only
appears in files touched in the editor: it looked that way only because the
four corpus files with dotted names happened also to be the four that had been
edited.

### Reserved words that are not statements

`SIGNAL` and `LOCAL` appear in the 4.5 keyword index but have no syntax in 4.5.
QB parses neither and stores both lines as raw source text, which is the same
path a line with a genuine syntax error takes. They round-trip unchanged.

### Untokenized lines

A tokenized file can contain lines that were never tokenized. When QB cannot
parse a line it stores the source verbatim under opcode `000a`, in the same
`[u16 field][text]` shape a comment uses. A detokenizer has to emit those back
as they are.

`TYPES.BAS` shows it: its `TYPE` member is called `Name`, which is the QB
`NAME` statement, so the declaration was rejected -- and with it every later
line mentioning `Solo.Name`. `REDIM PRESERVE` went the same way; QB 4.5 has no
`PRESERVE`. Verified: every `000a` payload in the corpus appears verbatim
in QB's text output.

### Stored source text

Comment text is run-length encoded: `0x0d <count> <char>` stands for
`count` copies of `char`, which is how `TORUS` stores its banner comments.
The comment opcode's payload is a leading word followed by the text, and that
word is the column the apostrophe sits at -- which is what lets QB put an
inline `X = 1    ' note` back where it was.

Verified: 458 comments across the corpus expand to exactly the text QB
emitted, and the column matches the position of the quote on 218 of the 220
lines that can be checked. The two exceptions are `DIM ... AS <type>` lines,
where the same opcode carries a payload that is not source text at all -- its
leading word is 1 and its body contains NUL bytes. That form is not understood
yet, so callers test for NUL before treating a payload as text.

A comment's payload is padded to an even length with a space, and QB does not
write that pad back out, so a stored comment can be one character longer at
the right than the line it produced. Only four comments in the corpus hit it.
Trailing whitespace is otherwise kept exactly.

### Jump targets are filled in by running the program

Control-flow operands -- the target on `IF ... THEN`, `ELSE`, loop ends -- are
zero in a file that has been loaded from text and saved without running.
QB back-patches them when it compiles.

Verified: `samples/EDIT1.BAS` saved after pressing F5 is `TRAIL1` saved
without running, and
the two token streams differ in exactly one word: the `IF`'s target, 0 before
and 116 after. This matters mostly as a warning -- an unresolved target is not
a decoding error.

### DEFtype records

`DEFINT`/`DEFLNG`/`DEFSNG`/`DEFDBL`/`DEFSTR` share opcode `001b` and three
words:

    u16  reference of the next DEFtype record, 0xffff for the last
    u16  letters Q-Z in bits 15..6, the type code in the low six bits
    u16  letters A-P, with A at bit 15

Type codes match the ones in a procedure signature: 1 INTEGER, 2 LONG, 3
SINGLE, 4 DOUBLE, 5 STRING. Verified: `DEFTYPE.BAS` declares one range per
type and produces exactly `e000 1c00 0380 0070 000e` with codes 1 to 5, and
`TORUS`'s `DEFINT A-Z` sets every letter in both masks.

### Signatures

`DECLARE` (`0044`), `FUNCTION` (`0058`) and `SUB` (`0076`) each carry a
length-prefixed signature:

    u16  procedure reference
    u16  kind
    u16  parameter count, or 0xffff when no list was written at all
    then per parameter: u16 reference, u16 mode, u16 type
    then the ALIAS string, unpadded, its length taken from `kind`

Parameter types are `1` INTEGER, `2` LONG, `3` SINGLE, `4` DOUBLE, `5` STRING.
Verified: `DESCFILE`'s single `DECLARE` comes out as
`IdentifyFile(FileName$, Description$, DescriptionLen%)`.

The `kind` word packs four things:

| Bits | Meaning |
|---|---|
| high `0x03` | `1` a `SUB`, `2` a `FUNCTION` |
| high `0x7c` | the length of the `ALIAS` string, shifted left two |
| high `0x80` | `CDECL` |
| low | the `FUNCTION`'s own return type, with `0x80` set when a suffix was written |

So `DECLARE SUB Ext CDECL ALIAS "extfn" (BYVAL N%)` gives `0x9500`: `0x01` for
`SUB`, `0x80` for `CDECL`, and `5 << 2` for the five characters of `extfn`.

The `ALIAS` string follows the parameter list with no length byte and no
terminator, padded to an even length with whatever byte followed it in the
source, usually the closing quote. The length in `kind` is the only way to find
its end. Verified across fourteen declarations covering every combination of
`CDECL`, `ALIAS` and `BYVAL`, with alias lengths from one to five.

### OPEN's mode word

`OPEN` (`00c9`) carries one trailing word holding the whole of the clause
between `FOR` and `AS`:

| Part | Meaning |
|---|---|
| low byte | 1 `INPUT`, 2 `OUTPUT`, 4 `RANDOM`, 8 `APPEND`, 32 `BINARY` |
| high `0x03` | the `ACCESS` clause: 1 `READ`, 2 `WRITE`, 3 `READ WRITE` |
| high `0x70` | sharing: `0x10` `LOCK READ WRITE`, `0x20` `LOCK WRITE`, `0x30` `LOCK READ`, `0x40` `SHARED` |

So `OPEN "T.DAT" FOR RANDOM ACCESS READ WRITE SHARED AS #2` is `0x4304`.
Verified over all ten combinations QB 4.5 accepts.

The pre-4.0 form, `OPEN "R", #1, "F.DAT", 128`, is a different opcode (`00cc`)
with no trailing word, and its arguments are already in source order.

### Parentheses are stored, not worked out from precedence

There is no precedence table to reconstruct. QB stores the parentheses the
programmer wrote as an explicit opcode (`016e`), redundant ones included, so
rendering an expression is just a stack walk. `DRAWSCR1`'s

    Bytes% = 4 + INT(((320 - 1 + 1) * (2) + 7) / 8) * 1 * ((200 - 1) + 1)

comes back with its `(2)` and its `((200 - 1) + 1)` intact.

### Parameter lists

A parameter's mode word records how it was written, not its type:

| Bit | Meaning |
|---|---|
| `0x0200` | a type suffix was written (`First%`) |
| `0x0400` | an array (`Array()`) |
| `0x0800` | `SEG` |
| `0x1000` | `BYVAL` |
| `0x2000` | an `AS` clause rather than a suffix |

They combine, and the suffix goes inside the parentheses: `Array#()`. A bare
`0x0000` means the name was written with no suffix at all, taking its type
from a `DEF<type>`. Verified: `MATTMENU`'s 297 declarations round-trip,
including `MeanAverageD (Array#(), First%, ...)` and `FarPeek% (BYVAL DSeg%,
BYVAL DOfs%)`.

A `DECLARE` always writes its parentheses, even when there are no parameters;
a `SUB` or `FUNCTION` definition omits them.

### Layout

QB writes the module text first, then the procedures sorted by name --
not in the order the sections sit in the file. Each section is followed by a
blank line, unless it already ended with one.

The `DEFtype` record at the head of each procedure is counted as a line but is
printed only when it *changes* the default type. `TORUS` shows both halves: its
procedures all carry a copy of the module's `DEFINT A-Z` and stay silent, while
`TorusCalc` carries no record at all -- meaning the language default -- so QB
writes `DEFSNG A-Z` before it and `DEFINT A-Z` again before the next one.

A statement written with an optional argument it did not supply keeps the space
where the argument would have gone. That is why `CLS : END` has a space before
the colon and `DO: LOOP` does not, and why a lone `CLS` needs its trailing
space trimmed: QB trims trailing whitespace from a line that has content, but
leaves it on a line that is only whitespace.

### Coverage

`src/qb45detok/tokens.py` holds the opcodes identified so far. Against the
whole corpus that accounts for every one of the 14,129 opcodes, with all 107
sections decoding to exactly the line count their trailer records and decoded
indentation matching QB's text output on every procedure line that can be
checked. `qb45detok stats FILE` reports this per file.

Rendering those tokens back to source reproduces all 34 corpus files byte for
byte, the largest of them 1,091 lines, and all 4,773 lines overall.

Cross-checked against the 224 keywords in the QB 4.5 help index, every
documented statement and function is either an identified opcode or handled by
one of the encoding rules. The three remaining index entries are not language
keywords: `ABSOLUTE`, `INTERRUPT` and `INTERRUPTX` are routines in `QB.QLB`
that reach the file as ordinary `CALL` targets.

### Known unknowns

- `0017` is described above, and for anything tokenized from source the rule
  is exact. The residue is 13 `SUB` and `DECLARE` header lines in the corpus
  that name a dotted procedure and carry no marker. Every one of the 13 is in
  a file that was edited in the QB editor, and none of the freshly loaded
  probe files shows the behaviour, so the likeliest reading is that the editor
  regenerates those header lines without re-applying the marker. That is a
  guess, not a result.

  Ruled out by experiment along the way, so nobody repeats them: trailing
  whitespace, a compile artefact, word padding, a trailing colon, and the
  procedure preamble kind byte.

- The `PUT` raster operations are 0 `OR`, 1 `AND`, 2 `PRESET`, 3 `PSET`,
  4 `XOR`. `OPEN`'s trailing word is described below; only bit 16 of its low
  byte and bit `0x08` of its high byte have not been seen.
- The `DIM ... AS <type>` payload described above.
- The trailing word on statements like `LOCATE` and `COLOR` is twice the
  argument count, but on `LINE` it is the `B`/`BF` shape flag and on `PUT` the
  raster action -- so it is statement-specific, not a general argument count.
- Header offsets `0x12` and `0x14`-`0x19`, and `unknown_a`/`unknown_c` in the
  section trailer.

### What would help most

`TORUS` and `JOHNNY` closed most of the earlier gaps: between them they
supplied `TYPE ... END TYPE`, `SELECT CASE`, `ON ERROR`/`RESUME`, `GOSUB`,
`DEFINT`, `SWAP`, `PALETTE`, `PLAY` and double-precision arithmetic.

The programs in `samples/` closed the rest: file I/O, `DEF FN`,
`CONST`, `COMMON`, `STATIC`, all five `DEF<type>` ranges, `EXIT FOR`/`EXIT DO`,
double-precision literals and the numeric function set.

Everything in `samples/` has been through QB and is in the corpus. That is
where random access, record locking, the graphics forms, the directory, port
and error-handling statements, `CHAIN`, `RUN`, `IOCTL`, the record conversion
functions and the tracing statements came from. Nothing in the language is
known to be unreached, though that is not the same as saying nothing is.
