' Retry of TYPES with the QB 4.5 rejections removed
' TYPES.BAS used a member called Name, which is the NAME statement, and
' REDIM PRESERVE, which QB 4.5 does not have. Both were stored as text.
TYPE Person
        Full AS STRING * 20
        Age AS INTEGER
        Score AS DOUBLE
END TYPE
DIM Staff(1 TO 3) AS Person
DIM Solo AS Person
REDIM Dynamic(1 TO 2) AS Person
Solo.Full = "Ada"
Solo.Age = 36
Staff(1).Full = Solo.Full
Staff(1).Age = Solo.Age + 1
PRINT Solo.Full; Solo.Age; Staff(1).Age
PRINT UBOUND(Staff); LBOUND(Staff)
ERASE Staff
END
