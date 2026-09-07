DEFINT A-Z
DIM Arr(100)
SCREEN 1
LINE (0, 0)-(10, 10)
LINE -(20, 20)
LINE (0, 0)-(10, 10), 1, B
LINE (0, 0)-(10, 10), 1, BF
LINE (0, 0)-(10, 10), 1, , &HFF00
LINE -(5, 5), 2
PUT (0, 0), Arr
END
