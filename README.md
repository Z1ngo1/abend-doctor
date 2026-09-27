# 🩺 Abend Doctor

**From "my batch job abended" to "here is the line, the cause and the fix, in plain words" in about a minute, for any COBOL job on your z/OS system.**

Built with IBM Bob 2.0 for the IBM Bob 2.0 Hackathon.

## The problem

When a z/OS batch job fails, a developer has to:
1. open the job output and find the right message among hundreds of JES lines,
2. decode `S0C7`, `U4038`, `IGZ0035S`, `FILE STATUS 39` or `SQLCODE -811`,
3. work out which program and which statement failed,
4. find the source, the copybook, the JCL and the input data for that program,
5. figure out why, and fix it.

For a junior developer this takes 30 to 90 minutes per failure. Senior people who
"just know" what S0C7 means are retiring.

## The solution

Abend Doctor splits the work between a deterministic engine and IBM Bob:

| Part | Who does it | What it does |
|---|---|---|
| `doctor/abend_doctor.py` | Python, no dependencies | Reads the failed job's spool from z/OS, finds the failing program, **downloads its source from z/OS** (dataset taken from the job's own JCL), ranks suspect statements and writes a plain-words summary. Everything via Zowe CLI, read-only. No local code needed. |
| `doctor/kb.json` | knowledge base | Abend codes, FILE STATUS, SQLCODE, JES/LE message meanings. |
| **Abend Doctor mode** in Bob (`bob/abend-doctor-mode.md`) | IBM Bob | Reads the report, follows the evidence into source, copybooks, JCL and data files, explains the root cause, proposes a diff and prevention, writes `reports/<JOB>-diagnosis.md`. Several failed jobs are diagnosed as parallel subtasks and summarised in a triage table. |
| **zos-abend-knowledge skill** (`bob/skills/`) | IBM Bob | Mainframe expertise Bob loads when explaining a failure. |

```
 z/OS spool ──(zowe list/view, read-only)──▶ abend_doctor.py ──▶ reports/JOB.md
 z/OS source dataset ──(zowe view, read-only)──────┘                   │
                                                                       ▼
                                                           IBM Bob "Abend Doctor" mode
                                                     source + copybook + JCL + data files
                                                                       │
                                                                       ▼
                                                    reports/JOB-diagnosis.md + fix diff
```

### How the failing statement is found
- **LE statement number** (`CEE3207S ... at statement N`) when the program is compiled with `TEST`.
- **Last DISPLAY that printed**: the doctor knows every DISPLAY literal of every program, walks the
  spool in order and finds the last one that appeared. The failure happened after it.
- **Failure-specific patterns**: arithmetic for S0C7, divisions for S0CB, OPEN/READ/WRITE on the
  failing DD for file errors, `EXEC SQL` for SQLCODEs, the DD statement in the JCL for JCL errors.
- **Data-aware ranking**: for data exceptions, statements using fields read straight from a file rank higher.

It also identifies the program with no hints at all: run against the 21 saved SYSOUT files of
[COBOL-PRACTICE-TASKS](https://github.com/Z1ngo1/COBOL-PRACTICE-TASKS), it picked the correct
program from 31 candidates in 21 of 21 cases.

## Usage

```bash
python doctor/abend_doctor.py jobs --prefix "COMP*"       # recent jobs on z/OS, failed ones marked
python doctor/abend_doctor.py diagnose --latest           # newest failed job
python doctor/abend_doctor.py diagnose --job JOB01234
python doctor/abend_doctor.py view "HLQ.DATA(MEMBER)"     # read input data / JCL (read-only)
```

The source library is found in the job's JCL (compile-and-go jobs). For run-only jobs, list your
source libraries in `doctor.config.json`:

```json
{ "srclibs": ["Z12345.CBL", "Z12345.COB.PRAC"] }
```

Optional: `"repo": "../my-cobol-repo"` uses a local clone instead of downloading, and
`--spool file.txt` diagnoses saved output offline.

In IBM Bob, switch to the **🩺 Abend Doctor** mode and type: `diagnose my last failed job`.

Requirements: Python 3.8+ and [Zowe CLI](https://docs.zowe.org/) with a z/OSMF profile
(`npm install -g @zowe/cli`).

## Safety

- The only z/OS commands the doctor can run are `zowe zos-jobs list jobs`,
  `zowe zos-jobs view all-spool-content` and `zowe zos-files view data-set`. Anything else is refused in code.
- Bob's mode forbids submitting, cancelling or deleting jobs and datasets.
- Keep `zowe.config*.json` out of git (see `.gitignore`).

## Demo program

`demo/` contains `PAYCALC`, a small payroll program with deliberate bugs and one JCL per failure
(S0C7, S0CB, U4038/status 35, JCL error) so anyone can reproduce real abends on z/OS.

## Tests

```bash
python -m unittest discover tests
```
Fixtures in `tests/fixtures/` whose name starts with `synthetic_` are hand-written in the JES2 format
for unit tests. Real outputs captured from z/OS are in `samples/`.

## Data

No customer or personal data is used. The demo data is synthetic. Test fixtures are hand-written.

## License

MIT
