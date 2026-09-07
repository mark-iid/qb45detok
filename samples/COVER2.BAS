' Declaration clauses and the calling forms the other samples miss
DEFINT A-Z
DECLARE SUB Ext CDECL ALIAS "extfn" (BYVAL N%)
DECLARE FUNCTION Half% (N%)
OPEN "T.DAT" FOR INPUT ACCESS READ AS #1
CLOSE #1
OPEN "T.DAT" FOR RANDOM ACCESS READ WRITE SHARED AS #2
CLOSE #2
CALLS Ext(3)
CALL ABSOLUTE(0)
PRINT Half%(8)
END

FUNCTION Half% (N%)
  Half% = N% \ 2
END FUNCTION
