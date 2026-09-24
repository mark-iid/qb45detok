"""Report what in a QuickBASIC 4.5 program will not survive a port to QB64.

QB64 Phoenix Edition aims at QuickBASIC 4.5 compatibility and gets most of the
way, but a couple of dozen keywords are missing. The awkward part is what its
own documentation says about them: "older code that uses these keywords won't
generate errors, as these are ignored by the compiler." A program using them
builds and runs, and quietly does something else.

That is what this is for. The rules come from the list QB64 ships at
``internal/help/Keywords_currently_not_supported_by_QB64*.txt`` rather than
from anyone's recollection.

The checking is done against the decoded token stream rather than the source
text, which is the whole reason it belongs here. QuickBASIC recorded what it
understood each line to mean, so a variable called ``fre``, the word ``TRON``
inside a string, and a comment mentioning ``IOCTL`` are all invisible: only
the opcode QB stored counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .decode import DecodedSection, Line, decode_file
from .reader import BinFile
from .render import Renderer

#: What happens to a program that uses the thing.
IGNORED = "ignored"      #: compiles, does nothing, no diagnostic from QB64
REWORK = "rework"        #: has to be rewritten before it will compile
PLATFORM = "platform"    #: fine on Windows, a stub on Linux and macOS

SEVERITY_ORDER = {IGNORED: 0, REWORK: 1, PLATFORM: 2}

#: Opcode mnemonic to (severity, what to call it, what to do about it).
RULES: Dict[str, Tuple[str, str, str]] = {
    # -- ignored by the compiler, which is the dangerous group -----------
    "CALLS": (IGNORED, "CALLS", "call the procedure directly; QB64 passes by "
              "reference already"),
    "IOCTL": (IGNORED, "IOCTL", "device control has no equivalent; drop it"),
    "IOCTL$": (IGNORED, "IOCTL$", "device control has no equivalent; drop it"),
    "ERDEV": (IGNORED, "ERDEV", "DOS device error codes are gone; use ERR"),
    "ERDEV$": (IGNORED, "ERDEV$", "DOS device error codes are gone; use ERR"),
    "FILEATTR": (IGNORED, "FILEATTR", "no equivalent; track the mode yourself"),
    "FRE": (IGNORED, "FRE", "QB64 is not limited to 640K; the answer is "
            "meaningless, so remove the check"),
    "SETMEM": (IGNORED, "SETMEM", "no equivalent and none needed"),
    "SIGNAL_EVENT": (IGNORED, "SIGNAL", "not supported"),
    "TRON": (IGNORED, "TRON", "use $DEBUG, or _ECHO in the lines you care "
             "about"),
    "TROFF": (IGNORED, "TROFF", "use $DEBUG, or _ECHO in the lines you care "
              "about"),
    "DATE$_SET": (IGNORED, "DATE$ =", "setting the system clock is not "
                  "supported; reading DATE$ is"),
    "TIME$_SET": (IGNORED, "TIME$ =", "setting the system clock is not "
                  "supported; reading TIME$ is"),
    "PEN_EVENT": (IGNORED, "PEN", "light pen input is not supported"),
    "PEN_FUNC": (IGNORED, "PEN", "light pen input is not supported"),
    "PLAY_EVENT_N": (IGNORED, "PLAY(n)", "the PLAY event is not supported; "
                     "PLAY itself is"),
    "UEVENT": (IGNORED, "UEVENT", "user events are not supported"),
    "WIDTH_LPRINT": (IGNORED, "WIDTH LPRINT", "not supported as a combined "
                     "statement"),

    # -- has to be rewritten --------------------------------------------
    "DEF_FN": (REWORK, "DEF FN", "rewrite as a FUNCTION; QB64 has no DEF FN"),
    "END_DEF": (REWORK, "END DEF", "rewrite the DEF FN as a FUNCTION"),

    # -- works on Windows, a stub elsewhere ------------------------------
    "CHAIN": (PLATFORM, "CHAIN", "QB64 has no module size limit, so combine "
              "the programs into one"),
    "RUN_FILE": (PLATFORM, "RUN <file>", "QB64 has no module size limit, so "
                 "combine the programs into one"),
    "LPRINT": (PLATFORM, "LPRINT", "printing is not implemented on Linux or "
               "macOS"),
    "LOCK": (PLATFORM, "LOCK", "file locking is not implemented on Linux or "
             "macOS"),
    "UNLOCK": (PLATFORM, "UNLOCK", "file locking is not implemented on Linux "
               "or macOS"),
}

#: Routines from `QB.QLB` that a program reaches through CALL. They are not
#: keywords, so the opcode is an ordinary call and only the name gives them
#: away.
QLB_ROUTINES = {
    "ABSOLUTE": "machine code in a DATA statement will not run; rewrite the "
                "routine in BASIC",
    "INTERRUPT": "DOS and BIOS interrupts do not exist; find the QB64 "
                 "equivalent for what the interrupt did",
    "INTERRUPTX": "DOS and BIOS interrupts do not exist; find the QB64 "
                  "equivalent for what the interrupt did",
}

#: Device names that OPEN accepts in QuickBASIC and QB64 does not.
DEVICES = ("LPT1", "LPT2", "LPT3", "CON", "KYBD", "SCRN")


@dataclass
class Finding:
    """One thing that will not port, and where it is."""

    section: Optional[str]      #: None for the module text
    number: int                 #: line number within its section, from 1
    text: str                   #: the source line
    keyword: str
    severity: str
    advice: str

    @property
    def where(self) -> str:
        return f"{self.section or '<module>'}:{self.number}"


def _visible(ds: DecodedSection) -> List[Line]:
    """The lines a section renders, in the order it renders them.

    Mirrors what the renderer drops: a procedure's inherited DEFtype record,
    which is never written out, and anything pulled in by $INCLUDE.
    """
    lines = ds.lines
    if (not ds.section.is_module and lines and lines[0].instrs
            and lines[0].instrs[0].mnemonic == "DEFTYPE"):
        lines = lines[1:]
    return [line for line in lines if not line.included]


def _call_target(instr, bf: BinFile) -> Optional[str]:
    """The name a CALL names, if the instruction is one."""
    if instr.mnemonic not in ("CALL", "CALL_IMPLICIT", "CALLS"):
        return None
    if not instr.operands:
        return None
    entry = bf.symbol(instr.operands[-1])
    return entry.name if entry is not None else None


def check(bf: BinFile) -> List[Finding]:
    """Everything in a program that QB64 will not take as written."""
    out: List[Finding] = []
    renderer = Renderer(bf)
    for ds in decode_file(bf):
        text = renderer.section(ds)
        lines = _visible(ds)
        for number, line in enumerate(zip(lines, text), start=1):
            node, source = line
            seen = set()
            for instr in node.instrs:
                rule = RULES.get(instr.mnemonic)
                if rule is not None and instr.mnemonic not in seen:
                    seen.add(instr.mnemonic)
                    severity, keyword, advice = rule
                    out.append(Finding(ds.section.name, number, source.strip(),
                                       keyword, severity, advice))
                name = _call_target(instr, bf)
                if name is not None:
                    advice = QLB_ROUTINES.get(name.upper())
                    if advice is not None and name.upper() not in seen:
                        seen.add(name.upper())
                        out.append(Finding(ds.section.name, number,
                                           source.strip(), f"CALL {name}",
                                           REWORK, advice))
                if instr.mnemonic in ("OPEN", "OPEN_RANDOM",
                                      "OPEN_MODE_STRING"):
                    device = _device_in(source)
                    if device is not None:
                        out.append(Finding(
                            ds.section.name, number, source.strip(),
                            f"OPEN {device}:", IGNORED,
                            "opening a DOS device is not supported; only "
                            "files, and COM on Windows"))
    return out


def _device_in(source: str) -> Optional[str]:
    """The DOS device an OPEN names, if it names one.

    Read from the source rather than the stream, because the name is a string
    literal and nothing in the opcode says it is a device.
    """
    upper = source.upper()
    for device in DEVICES:
        if f'"{device}:' in upper:
            return device
    if '"COM1:' in upper or '"COM2:' in upper:
        return "COM"
    return None


def report(findings: Sequence[Finding]) -> List[str]:
    """The findings as text, worst first."""
    if not findings:
        return ["Nothing here needs changing for QB64."]
    order = sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity],
                                            f.section or "", f.number))
    headings = {
        IGNORED: "QB64 ignores these. The program will build and run, and do "
                 "something else.",
        REWORK: "These have to be rewritten before it will build.",
        PLATFORM: "These work on Windows and do nothing on Linux or macOS.",
    }
    out: List[str] = []
    current = None
    for f in order:
        if f.severity != current:
            current = f.severity
            out.append("")
            out.append(headings[current])
            out.append("")
        out.append(f"  {f.where:24} {f.keyword}")
        out.append(f"  {'':24} {f.text[:60]}")
        out.append(f"  {'':24} -> {f.advice}")
    counts = {s: sum(1 for f in findings if f.severity == s)
              for s in (IGNORED, REWORK, PLATFORM)}
    out.append("")
    out.append(f"{len(findings)} to look at: {counts[IGNORED]} ignored "
               f"silently, {counts[REWORK]} needing a rewrite, "
               f"{counts[PLATFORM]} platform-specific.")
    return out
