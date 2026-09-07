# Sample programs

Plain ASCII BASIC, written to exercise one language feature per line. They are
not test data on their own: the tests compare a tokenized file against the text
QuickBASIC produced for the *same* program, so each of these has to be run
through QB 4.5 first to make the pair.

## Making a pair

In QB 4.5, for each program:

1. `File -> Open` the `.BAS`.
2. `File -> Save As`, format **QuickBASIC** — this is the tokenized side, and
   it should start with byte `0xFC`.
3. `File -> Save As`, format **Text** — this is the side the tests compare
   against.

Use QB's own text output, not the file in this directory. QB reformats
keywords, spacing and capitalisation when it loads a program, so the source
here will not match what it writes back out.

Then put the two in `corpus/bin/NAME.BAS` and `corpus/txt/NAME.BAS`.

## What each one covers

| File | Covers |
|---|---|
| `FILEIO.BAS` | `OPEN`, `CLOSE`, `PRINT #`, `WRITE #`, `LINE INPUT #`, `EOF`, `LOF`, `KILL` |
| `DEFFN.BAS` | `DEF FN` in both forms, `CONST`, `COMMON SHARED`, `STATIC` |
| `DEFTYPE.BAS` | one `DEF<type>` per letter range, which pins down the letter mask |
| `TYPES.BAS` | `TYPE` with a fixed-length string, arrays of a user type, `REDIM PRESERVE` |
| `TYPES2.BAS` | the same without the two constructs QB 4.5 rejects (see below) |
| `MISC.BAS` | `SGN` `SQR` `EXP` `LOG` `ATN` `TAN` `FIX` `CINT` `CLNG` `CSNG` `CDBL` `DATE$` `TIME$` `UCASE$` `OCT$`, `BEEP`, `EXIT FOR`, `EXIT DO` |
| `TRAIL1.BAS`, `TRAIL2.BAS` | identical but for trailing whitespace |
| `EDIT1.BAS` | same program as `TRAIL1`, for saving after a run |

`TYPES.BAS` is deliberately kept as it is. Its `TYPE` member is called `Name`,
which QB reads as the `NAME` statement, so the declaration is rejected — and
`REDIM PRESERVE` does not exist in 4.5 either. QB stores both lines as raw
source text rather than tokenizing them, which is the only example I have of
that happening. `TYPES2.BAS` is the corrected version.

`TRAIL1`/`TRAIL2` and `EDIT1` are experiments rather than coverage. See the
"Known unknowns" section of `docs/format.md` for what they answered.
