' The graphics forms the first round never reached
SCREEN 1
DIM Img%(1 TO 200)
GET (0, 0)-(20, 20), Img%
PUT (30, 30), Img%, PSET
PUT (30, 30), Img%, PRESET
PUT (30, 30), Img%, AND
PUT (30, 30), Img%, OR
PUT (30, 30), Img%, XOR
LINE (0, 0)-STEP(10, 10), 2
LINE STEP(5, 5)-STEP(10, 10), 3, BF
CIRCLE (100, 100), 40, 1, 0, 3.14, 1
PSET STEP(1, 1), 2
PRESET (5, 5)
DRAW "U10 R10 D10 L10"
PAINT (50, 50), 1, 2
VIEW (0, 0)-(319, 199), 0, 1
WINDOW SCREEN (0, 0)-(319, 199)
VIEW PRINT 1 TO 25
PRINT PMAP(0, 0); POINT(1, 1)
SCREEN 0, , 0, 0
END
