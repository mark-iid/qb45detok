' TYPE with a fixed-length string, arrays of a user type, REDIM PRESERVE
TYPE Person
        Name AS STRING * 20
        Age AS INTEGER
        Score AS DOUBLE
END TYPE
DIM Staff(1 TO 3) AS Person
DIM Solo AS Person
REDIM Dynamic(1 TO 2) AS Person
Solo.Name = "Ada"
Solo.Age = 36
Staff(1).Name = Solo.Name
Staff(1).Age = Solo.Age + 1
REDIM PRESERVE Dynamic(1 TO 4)
PRINT Solo.Name; Solo.Age; Staff(1).Age
PRINT UBOUND(Staff); LBOUND(Staff)
ERASE Staff
END
