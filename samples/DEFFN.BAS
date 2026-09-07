' DEF FN, CONST, COMMON, STATIC -- none of these are exercised
CONST PI = 3.14159
CONST GREETING$ = "hello"
COMMON SHARED Shared1%, Shared2$
DEF FnDouble (X) = X * 2
DEF FnArea# (R#)
        FnArea# = PI * R# * R#
END DEF
DECLARE SUB Counter ()
Shared1% = 3
PRINT FnDouble(Shared1%)
PRINT FnArea#(2#)
PRINT GREETING$
Counter
Counter
END

SUB Counter STATIC
        STATIC Times AS INTEGER
        Times = Times + 1
        PRINT Times
END SUB
