' Keywords BASIC 7 PDS adds over QuickBASIC 4.5. Not meant to run: this is
' here to be saved through QBX so that the opcodes it stores can be read off.
DEFCUR C
DIM Money AS CURRENCY
DIM Rate AS CURRENCY
Money = 1
Rate = 2
PRINT CCUR(3)
PRINT MKC$(Money)
PRINT CVC("12345678")
CHDRIVE "C"
PRINT CURDIR$
PRINT DIR$("*.BAS")
SIGNAL(1) ON
SIGNAL(1) OFF
SIGNAL(1) STOP
ON SIGNAL(1) GOSUB Handler
PRINT SSEG(Money)
PRINT SSEGADD(Money)
PRINT STRINGADDRESS(Money)
PRINT STRINGLENGTH(Money)
STRINGASSIGN Money, Rate
STRINGRELEASE Money
PRINT SADD(Money)
Handler:
RETURN
