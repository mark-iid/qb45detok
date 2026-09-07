' Random-access files and record locking
DIM Buf AS STRING * 20
OPEN "REC.DAT" FOR RANDOM AS #1 LEN = 20
FIELD #1, 10 AS Nm$, 10 AS Vl$
LSET Nm$ = "alpha"
RSET Vl$ = "beta"
PUT #1, 1
GET #1, 1
LOCK #1, 1
UNLOCK #1, 1
PRINT LOC(1); SEEK(1); LOF(1)
SEEK #1, 1
CLOSE #1
OPEN "REC.DAT" FOR BINARY AS #2
GET #2, 1, Buf
PUT #2, 1, Buf
CLOSE #2
KILL "REC.DAT"
END
