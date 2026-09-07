' File I/O -- nothing in the corpus opens a file
DIM Line1 AS STRING
DIM Count AS INTEGER
OPEN "TEST.TXT" FOR OUTPUT AS #1
PRINT #1, "first line"
PRINT #1, "second"; 42
WRITE #1, "quoted", 7
CLOSE #1
OPEN "TEST.TXT" FOR INPUT AS #2
DO UNTIL EOF(2)
        LINE INPUT #2, Line1
        Count = Count + 1
LOOP
CLOSE #2
PRINT Count; LOF(1)
OPEN "TEST.TXT" FOR APPEND AS #3
PRINT #3, "appended"
CLOSE
KILL "TEST.TXT"
END
