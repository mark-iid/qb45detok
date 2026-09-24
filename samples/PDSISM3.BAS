' The three PDS forms the first ISAM sample got wrong: SAVEPOINT and
' GETINDEX$ are functions rather than statements, and ROLLBACK takes an
' optional ALL. Each sits between two lines of a known shape.
TYPE Rec
  Key AS INTEGER
END TYPE
DIM R AS Rec
DIM S AS INTEGER
OPEN "DB.MDB" FOR ISAM Rec "Table1" AS #1
PRINT 1
S = SAVEPOINT
PRINT 1
PRINT SAVEPOINT
PRINT 1
PRINT GETINDEX$(1)
PRINT 1
ROLLBACK ALL
PRINT 1
ROLLBACK S
PRINT 1
