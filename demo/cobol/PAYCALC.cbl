       IDENTIFICATION DIVISION.
       PROGRAM-ID. PAYCALC.
      *****************************************************************
      * PAYCALC - DEMO PAYROLL BATCH PROGRAM FOR ABEND DOCTOR.        *
      * READS EMPLOYEE RECORDS, COMPUTES PAY = HOURS * RATE,          *
      * PRINTS TOTAL AND AVERAGE PAY.                                 *
      * THE BUGS BELOW ARE INTENTIONAL - THEY ARE TEST CASES:         *
      *  - NO VALIDATION OF EMP-HOURS    -> S0C7 ON BAD INPUT         *
      *  - NO CHECK FOR ZERO RECORDS     -> S0CB ON EMPTY INPUT       *
      *  - NO FILE STATUS CHECK ON OPEN  -> U4038 (STATUS 35)         *
      *****************************************************************
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT EMP-FILE ASSIGN TO EMPFILE
               ORGANIZATION IS SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  EMP-FILE
           RECORDING MODE F.
       01  EMP-REC.
           05 EMP-ID            PIC X(5).
           05 EMP-NAME          PIC X(20).
           05 EMP-HOURS         PIC 9(3).
           05 EMP-RATE          PIC 9(3)V99.
           05 FILLER            PIC X(47).
       WORKING-STORAGE SECTION.
       01  WS-EOF               PIC X            VALUE 'N'.
       01  WS-COUNT             PIC S9(5)   COMP-3 VALUE 0.
       01  WS-HOURS             PIC S9(3)   COMP-3.
       01  WS-PAY               PIC S9(7)V99 COMP-3.
       01  WS-TOTAL             PIC S9(9)V99 COMP-3 VALUE 0.
       01  WS-AVG               PIC S9(7)V99 COMP-3.
       01  WS-PAY-OUT           PIC Z,ZZZ,ZZ9.99.
       01  WS-COUNT-OUT         PIC ZZZZ9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT EMP-FILE
           PERFORM READ-EMP
           PERFORM UNTIL WS-EOF = 'Y'
               PERFORM CALC-PAY
               PERFORM READ-EMP
           END-PERFORM
           PERFORM CALC-AVERAGE
           CLOSE EMP-FILE
           STOP RUN.
       READ-EMP.
           READ EMP-FILE
               AT END MOVE 'Y' TO WS-EOF
           END-READ.
       CALC-PAY.
           MOVE EMP-HOURS TO WS-HOURS
           COMPUTE WS-PAY = WS-HOURS * EMP-RATE
           ADD WS-PAY TO WS-TOTAL
           ADD 1 TO WS-COUNT
           MOVE WS-PAY TO WS-PAY-OUT
           DISPLAY EMP-ID ' ' EMP-NAME ' PAY: ' WS-PAY-OUT.
       CALC-AVERAGE.
           COMPUTE WS-AVG = WS-TOTAL / WS-COUNT
           MOVE WS-AVG   TO WS-PAY-OUT
           MOVE WS-COUNT TO WS-COUNT-OUT
           DISPLAY 'EMPLOYEES: ' WS-COUNT-OUT ' AVG PAY: ' WS-PAY-OUT.
