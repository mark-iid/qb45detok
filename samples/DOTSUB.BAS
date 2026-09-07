DECLARE SUB Aa.Bb ()
DECLARE SUB Cc.Dd (P.Q%)
DECLARE SUB Ee (R%)
Aa.Bb
Cc.Dd 1
Ee 2
END

SUB Aa.Bb
PRINT "a"
END SUB

SUB Cc.Dd (P.Q%)
PRINT P.Q%
END SUB

SUB Ee (R%)
PRINT R%
END SUB
