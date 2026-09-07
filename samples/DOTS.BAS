Start.Here:
A.Var% = 1
B.Var% = A.Var% + 1
GOSUB Sub.Label
PRINT B.Var%
END

Sub.Label:
PRINT "hi"
RETURN
