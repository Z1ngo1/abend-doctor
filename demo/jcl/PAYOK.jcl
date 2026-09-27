//PAYOK    JOB 1,NOTIFY=&SYSUID
//* HAPPY PATH - EXPECTED RC=0000
//RUN      EXEC PGM=PAYCALC
//STEPLIB  DD DSN=&SYSUID..LOAD,DISP=SHR
//SYSOUT   DD SYSOUT=*
//CEEDUMP  DD SYSOUT=*
//EMPFILE  DD *
00001IVANOV IVAN         04001500                                               
00002PETROVA ANNA        03802200                                               
00003SIDOROV OLEG        04201850                                               
/*
