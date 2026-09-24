' The PDS financial, date and format families, to settle whether they are
' language keywords with opcodes of their own or routines in a library.
DIM A AS DOUBLE
A = 1
PRINT FORMAT$(A, "#.##")
PRINT FORMATI$(A, "#")
PRINT FORMATL$(A, "#")
PRINT FORMATC$(A, "#")
PRINT FORMATD$(A, "#")
PRINT FORMATS$(A, "#")
SETFORMATCC 1
PRINT PMT#(A, A, A)
PRINT FV#(A, A, A)
PRINT PV#(A, A, A)
PRINT NPV#(A, A)
PRINT IRR#(A, A, A)
PRINT SLN#(A, A, A)
PRINT SYD#(A, A, A)
PRINT DDB#(A, A, A, A)
PRINT RATE#(A, A, A)
PRINT NPER#(A, A, A)
PRINT NOW#
PRINT DAY&(A)
PRINT YEAR&(A)
PRINT MONTH&(A)
PRINT HOUR&(A)
PRINT MINUTE&(A)
PRINT SECOND&(A)
PRINT WEEKDAY&(A)
PRINT DATESERIAL#(1, 1, 1)
PRINT DATEVALUE#("1/1/90")
PRINT TIMESERIAL#(1, 1, 1)
PRINT TIMEVALUE#("1:00:00")
