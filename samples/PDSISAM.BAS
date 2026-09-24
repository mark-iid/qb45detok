' The ISAM statements BASIC 7 PDS adds, plus the other PDS-only keywords that
' are not obviously library routines. Written to be saved through QBX so the
' opcodes can be read off; it is not meant to run.
TYPE Rec
  Key AS INTEGER
  Name AS STRING * 10
END TYPE
DIM R AS Rec
OPEN "DB.MDB" FOR ISAM Rec "Table1" AS #1
BEGINTRANS
CHECKPOINT
SAVEPOINT
COMMITTRANS
ROLLBACK
CREATEINDEX #1, "Idx1", 0, "Key"
SETINDEX #1, "Idx1"
PRINT GETINDEX$(#1)
DELETEINDEX #1, "Idx1"
MOVEFIRST #1
MOVENEXT #1
MOVEPREVIOUS #1
MOVELAST #1
SEEKEQ #1, 1
SEEKGE #1, 1
SEEKGT #1, 1
INSERT #1, R
RETRIEVE #1, R
UPDATE #1, R
DELETE #1
PRINT BOF(1)
DELETETABLE "DB.MDB", "Table1"
SETUEV
