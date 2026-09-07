StartHere:
AVar% = 1
BVar% = AVar% + 1
GOSUB SubLabel
PRINT BVar%
END

SubLabel:
PRINT "hi"
RETURN
