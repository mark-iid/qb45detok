' Record conversion, tracing and the remaining print helpers
DIM A AS INTEGER
DIM S AS SINGLE
DIM D AS DOUBLE
TRON
TROFF
A = CVI(MKI$(7))
S = CVS(MKS$(1.5))
D = CVD(MKD$(2.5))
PRINT SPC(3); TAB(10); "x"
PRINT POS(0); LPOS(0); FRE(0); SCREEN(1, 1)
LINE INPUT "prompt: ", Text$
RANDOMIZE 5
WIDTH LPRINT 80
KEY 1, "hello"
KEY LIST
KEY ON
KEY OFF
SLEEP 1
RESET
END
