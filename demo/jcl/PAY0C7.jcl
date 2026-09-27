//PAY0C7   JOB 1,NOTIFY=&SYSUID
//* RECORD 00002 HAS BLANK HOURS -> EXPECTED ABEND S0C7
//RUN      EXEC PGM=PAYCALC
//STEPLIB  DD DSN=&SYSUID..LOAD,DISP=SHR
//SYSOUT   DD SYSOUT=*
//CEEDUMP  DD SYSOUT=*
//EMPFILE  DD *
00001IVANOV IVAN         04001500                                               
00002PETROVA ANNA           02200                                               
00003SIDOROV OLEG        04201850                                               
/*
