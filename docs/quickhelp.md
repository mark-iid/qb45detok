# The Microsoft QuickHelp `.HLP` format

QuickBASIC 4.5 ships its language reference as two QuickHelp databases,
`QB45QCK.HLP` (the quick reference, 200 topics) and `QB45ADVR.HLP` (the full
reference, 533 topics). `HELPMAKE.EXE` built them and `QB.EXE` read them.
Nothing modern does, which is why `qb45detok` grew a reader: the reference is
the only complete statement of what QuickBASIC 4.5 accepts, and it is a better
oracle for the language than guessing from an opcode table.

Everything here was worked out from the two files themselves. Where something
is a best guess rather than a result, it says so.

The same reader handles the BASIC 7 PDS help files without changes:
`BAS7QCK.HLP` gives 428 topics and `BAS7ADVR.HLP` another 269, which is where
the list of what PDS adds over 4.5 in `format.md` comes from.

## Layout

    0x00  char[2]   "LN"
    0x02  u16       version, 2 in both files
    0x04  u16       flags, 0
    0x06  u16       0x3a in both files, meaning not known
    0x08  u16       topic count
    0x0a  u16       context count
    0x0c  u16       0x4e in both files, probably the display width
    0x10  char[18]  the file's own name, NUL-padded
    0x22  u32[6]    section offsets

The six sections, in the order their offsets appear:

| Section | Contents |
|---|---|
| topic index | `count + 1` u32 file offsets, one per topic plus an end marker |
| context strings | NUL-separated names such as `PRINT` or `.dtp.scoping.rules` |
| context map | one u16 per context: the topic it resolves to |
| keywords | the compression dictionary, each entry a length byte then that many characters |
| Huffman tree | always 1,024 bytes |
| topic text | the rest of the file |

Verified: the index is exactly `4 * (topics + 1)` bytes and the map exactly
`2 * contexts` bytes in both files, and every topic offset lies inside the
topic text.

## Huffman coding

The tree is 512 u16 words. A word with `0x8000` set is a leaf holding a byte
in its low half; both files use all 256 byte values exactly once. Any other
word is a byte offset into the tree.

Walking it is not the usual "bit picks one of a pair". Starting at word 0, a
1 bit steps to the next word and a 0 bit jumps to `tree[p] / 2`; after each
step, if the word now under the cursor is a leaf, its byte is emitted and the
cursor returns to 0.

Each topic begins with a u16 giving the decompressed size, and the bitstream
follows it. That size is what tells the decoder when to stop, since the last
byte of the stream is padded.

## Keyword compression

The bytes that come out of the Huffman stage are compressed again. A byte in
`0x10`-`0x17` is the first half of a two-byte reference into the keyword
dictionary: the low two bits of it are the high bits of the index and the
following byte is the low eight, giving 1,024 entries, which is exactly how
many each file has. A first byte of `0x14` or above means the same word
followed by a space.

Other control bytes:

| Byte | Meaning |
|---|---|
| `0x18 n` | a run of `n` spaces |
| `0x1a n` | set the display attribute, used for hyperlink highlighting |
| `0xff` | a hyperlink target follows, NUL-terminated, e.g. `QB45ADVR.HLP!.zpu` |
| `0x02 0x00` | start a display line |
| `0x02 0x01` | the hotspot table, which carries no display text |
| `0x02 0x02` | start a display line, another variant |
| `0x04 0x00` | continue the line already being built |

A record is usually followed by a byte giving how many characters it produces.
A record that opens with a control code has no such byte, so the reader treats
a value below `0x20` as the start of the content rather than as a length. That
rule is a best guess that happens to hold across both files; it is what makes
indented code examples come out right.

## What is still approximate

- A hyperlink's display text lives in the hotspot table rather than inline, so
  a line containing one loses those few characters. `... are Details.!.`
  should read `... are optional.`.
- The first line of each topic is the help browser's menu bar, and its tail
  runs into the hotspot table. `--body` drops it.
- A wrapped heading can pick up one stray character where two records join.
- The header words at `0x06` and `0x0c` are the same in both files and have
  not been identified.

Everything else comes out clean: 11,569 non-blank lines from the full
reference and 1,573 from the quick reference, with no control characters left
in any of them, and code examples reproduced exactly as the manual prints
them.
