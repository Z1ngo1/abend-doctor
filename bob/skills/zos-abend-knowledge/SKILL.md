---
name: zos-abend-knowledge
description: Reference for diagnosing z/OS batch failures - system and user abend codes, COBOL FILE STATUS values, DB2 SQLCODEs and JES/LE message ids. Use when explaining an ABEND, JCL ERROR, FILE STATUS or SQLCODE.
---

# z/OS abend knowledge

The full machine-readable table is in `doctor/kb.json` (abends, file_status, sqlcode,
messages). Read it when you need a code that is not listed below.

## How to read a failed job
1. JESMSGLG: `IEF450I <job> <step> - ABEND=Sxxx Uyyyy REASON=...` names the step and code.
   `S000 Uxxxx` means a user/LE abend: use the U code.
2. JESYSMSG: `IEF142I ... COND CODE nnnn` per step, `IEF272I` = step not run,
   `IEF212I` = dataset not found, `IEF453I` = JCL error.
3. SYSOUT / CEEDUMP: LE messages give the program and location:
   `CEE3207S ... From compile unit PGM at statement N at compile unit offset +X`.
   Statement numbers appear when compiled with TEST; otherwise use the offset and the
   compiler LIST output, or the last DISPLAY that printed.
4. `IGZ0035S ... file DD in program PGM ... status code was NN` = file OPEN failed and the
   program has no FILE STATUS clause.

## Most common in training and production
| Code | Meaning | First thing to check |
|---|---|---|
| S0C7 | invalid packed/zoned data | spaces/letters in a numeric input field; FD vs real record layout |
| S0CB | decimal divide by zero | divisor = record count or total that can be 0 |
| S0C4 | protection exception | subscript beyond OCCURS; CALL USING mismatch |
| S806 | module not found | compile failed but RUN step executed; STEPLIB |
| S322 | CPU time exceeded | loop: EOF flag never set |
| SB37/SD37 | out of space | SPACE too small or runaway WRITE loop |
| U4038 | LE severe error | read the IGZ/CEE message before it |
| FS 35 | file not present | DD missing or dataset not created |
| FS 39 | attribute conflict | LRECL/RECFM/key in FD differ from dataset |
| FS 23 | record not found | key value / VSAM content |
| SQL -805 | package not in plan | BIND after compile |
| SQL -811 | SELECT INTO > 1 row | add cursor or tighter WHERE |
| SQL -911 | deadlock/timeout | rollback happened; retry logic |
