#!/usr/bin/env python3
"""
Abend Doctor - finds WHERE and WHY a z/OS batch job failed, for any COBOL repo.

It does the deterministic part (parse spool, map the failing program to its source,
rank suspect statements).  IBM Bob does the reasoning part (root cause + fix),
driven by the "Abend Doctor" mode in bob/.

Read-only by design: the only z/OS commands it runs are Zowe CLI *list/view*.

Usage:
  python doctor/abend_doctor.py index  --repo PATH
  python doctor/abend_doctor.py jobs   [--prefix PAY*]            # failed jobs on z/OS
  python doctor/abend_doctor.py diagnose --repo PATH --spool FILE_OR_DIR
  python doctor/abend_doctor.py diagnose --repo PATH --job JOB01234
  python doctor/abend_doctor.py diagnose --repo PATH --latest [--prefix X*]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = json.loads((HERE / "kb.json").read_text(encoding="utf-8"))

SRC_EXT = {".cbl", ".cob", ".cobol"}
CPY_EXT = {".cpy", ".copy"}
JCL_EXT = {".jcl", ".jcls", ".proc"}
MAX_SUSPECTS = 12


# --------------------------------------------------------------------------- #
# Repository index                                                            #
# --------------------------------------------------------------------------- #
def cobol_lines(path):
    """Yield (line_no, code) for non-comment lines. Handles fixed format (cols 8-72)."""
    for no, raw in enumerate(path.read_text(encoding="latin-1").splitlines(), 1):
        if len(raw) >= 7 and raw[6] in "*/":
            continue
        fixed = len(raw) >= 7 and (raw[:6].strip() == "" or raw[:6].isdigit())
        code = raw[7:72] if fixed else raw
        if code.strip():
            yield no, code.rstrip()


def parse_program(path):
    lines = list(cobol_lines(path))
    text_up = "\n".join(c.upper() for _, c in lines)
    m = re.search(r"PROGRAM-ID\.\s*([A-Z0-9#@$-]+)", text_up)
    name = (m.group(1) if m else path.stem).upper()

    info = {"name": name, "source": path, "paragraphs": [], "displays": [],
            "selects": {}, "copybooks": [], "lines": {n: c for n, c in lines}}

    in_proc = False
    in_file_sec = False
    info["fd_fields"] = set()
    select_name = None
    for idx, (no, code) in enumerate(lines):
        up = code.upper()
        if re.search(r"\bPROCEDURE\s+DIVISION\b", up):
            in_proc = True
            continue
        # SELECT x ASSIGN TO dd (may span lines)
        sm = re.search(r"\bSELECT\s+(?:OPTIONAL\s+)?([A-Z0-9-]+)", up)
        if sm:
            select_name = sm.group(1)
        am = re.search(r"\bASSIGN\s+(?:TO\s+)?(?:[A-Z]{2}-)?(?:[A-Z]-)?([A-Z0-9#@$]+)", up)
        if am and select_name:
            info["selects"][am.group(1)] = select_name
            select_name = None
        cm = re.search(r"\bCOPY\s+([A-Z0-9#@$-]+)", up)
        if cm:
            info["copybooks"].append(cm.group(1))
        if re.search(r"\bFILE\s+SECTION\b", up):
            in_file_sec = True
        elif re.search(r"\b(WORKING-STORAGE|LOCAL-STORAGE|LINKAGE)\s+SECTION\b", up):
            in_file_sec = False
        if in_file_sec:
            fm = re.match(r"^\s*(0[2-9]|[1-4]\d)\s+([A-Z0-9-]+)", up)
            if fm and fm.group(2) != "FILLER":
                info["fd_fields"].add(fm.group(2))
        if not in_proc:
            continue
        # paragraph / section label: starts in area A, single word ending with '.'
        if re.match(r"^ {0,3}[A-Z0-9][A-Z0-9-]*(\s+SECTION)?\.\s*$", up) and not up.strip().startswith(("END-", "EXIT", "GOBACK", "STOP")):
            info["paragraphs"].append((no, up.strip().rstrip(".")))
        if re.search(r"\bDISPLAY\b", up):
            chunk = code
            for _, nxt in lines[idx + 1: idx + 3]:
                if re.match(r"^\s+['\"A-Z0-9-]", nxt) and not re.search(
                        r"\b(DISPLAY|MOVE|IF|PERFORM|COMPUTE|ADD|READ|WRITE|CALL|EXEC|END-\w+)\b", nxt.upper()):
                    chunk += " " + nxt
                else:
                    break
            for lit in re.findall(r"'([^']*)'|\"([^\"]*)\"", chunk):
                lit = (lit[0] or lit[1]).strip()
                if len(lit) >= 4:
                    info["displays"].append((no, lit))
    return info


def index_repo(root):
    root = Path(root).resolve()
    progs, copybooks, jcls = {}, {}, []
    for p in root.rglob("*"):
        if not p.is_file() or ".git" in p.parts:
            continue
        ext = p.suffix.lower()
        if ext in SRC_EXT:
            info = parse_program(p)
            progs[info["name"]] = info
            progs.setdefault(p.stem.upper(), info)   # load module = member name
        elif ext in CPY_EXT:
            copybooks[p.stem.upper()] = p
        elif ext in JCL_EXT:
            jcls.append(p)
    unique = {id(i): i for i in progs.values()}.values()
    for info in unique:
        src = info["source"]
        info["task_dir"] = src.parent.parent if src.parent.name.upper() in ("COBOL", "SRC", "CBL") else src.parent
        info["jcl"] = []
    for j in jcls:
        up = j.read_text(encoding="latin-1").upper()
        names = set(re.findall(r"\bMEMBER=([A-Z0-9#@$]+)", up))
        names |= set(re.findall(r"\bPGM=([A-Z0-9#@$]+)", up))
        names |= set(re.findall(r"\bPROG(?:RAM)?\(([A-Z0-9#@$]+)\)", up))
        names |= set(re.findall(r"\bCALL\s+'([A-Z0-9#@$]+)'", up))
        for n in names:
            if n in progs and j not in progs[n]["jcl"]:
                progs[n]["jcl"].append(j)
    # subprograms inherit the JCL of their callers
    for info in unique:
        info["calls"] = sorted(set(re.findall(r"\bCALL\s+'([A-Z0-9#@$]+)'", "\n".join(info["lines"].values()).upper())))
    for info in unique:
        for callee in info["calls"]:
            if callee in progs:
                progs[callee].setdefault("called_by", []).append(info["name"])
                for j in info["jcl"]:
                    if j not in progs[callee]["jcl"]:
                        progs[callee]["jcl"].append(j)
    return {"root": root, "programs": progs, "copybooks": copybooks}


# --------------------------------------------------------------------------- #
# Spool parsing                                                                #
# --------------------------------------------------------------------------- #
def load_spool(path):
    path = Path(path)
    if path.is_dir():
        parts = []
        for f in sorted(path.rglob("*")):
            if f.is_file():
                parts.append(f"\n===== {f.relative_to(path)} =====\n")
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
        return "".join(parts)
    return path.read_text(encoding="utf-8", errors="replace")


MSG_RE = re.compile(r"\b((?:IEFC|IEF|IEC|CSV|IGZ|CEE|DSNT|IDC|IKJ|IGD|IAT)\d{3,4}[A-Z])\b(.*)")


def parse_spool(text):
    up = text.upper()
    s = {"job": None, "jobid": None, "abend": None, "reason": None, "failed_step": None,
         "steps": [], "jcl_error": False, "le": None, "file_errors": [], "sqlcodes": [],
         "messages": [], "programs_mentioned": []}

    m = re.search(r"\$HASP373\s+(\S+)\s+STARTED", up) or re.search(r"^\s*//(\S+)\s+JOB\b", up, re.M)
    if m:
        s["job"] = m.group(1)
    m = re.search(r"\b(JOB\d{5,7}|J\d{7})\b", up)
    if m:
        s["jobid"] = m.group(1)

    for m in re.finditer(r"IEF142I\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s+-\s+STEP WAS EXECUTED\s+-\s+COND CODE\s+(\d{4})", up):
        s["steps"].append({"step": ".".join(x for x in (m.group(2), m.group(3)) if x), "rc": m.group(4)})
    for m in re.finditer(r"IEF272I\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s+-\s+STEP WAS NOT EXECUTED", up):
        s["steps"].append({"step": ".".join(x for x in (m.group(2), m.group(3)) if x), "rc": "NOT RUN"})

    m = re.search(r"IEF450I\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s+-\s+ABEND=(S\w{3})\s+(U\d{4})(?:\s+REASON=(\w+))?", up)
    if m:
        s["failed_step"] = ".".join(x for x in (m.group(2), m.group(3)) if x)
        sys_c, usr_c = m.group(4), m.group(5)
        s["abend"] = usr_c if sys_c == "S000" else sys_c
        s["reason"] = m.group(6)
        s["steps"].append({"step": s["failed_step"], "rc": s["abend"]})
    if not s["abend"]:
        m = re.search(r"ABENDED\s+(S\w{3})\s+(U\d{4})", up) or re.search(r"ABEND[= ]+(S\w{3})\s+(U\d{4})", up)
        if m:
            s["abend"] = m.group(2) if m.group(1) == "S000" else m.group(1)
    if not s["abend"]:
        m = re.search(r"SYSTEM COMPLETION CODE=(\w{3})", up)
        if m:
            s["abend"] = "S" + m.group(1)

    s["jcl_issues"] = []
    for m in re.finditer(r"(IEF212I|IEF344I|IEF210I|IEF211I|IEF253I|IEF217I)\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s+(\S+)\s+-\s+(.+)", up):
        dd = m.group(5) if m.group(4) else m.group(5)
        s["jcl_issues"].append({"msg": m.group(1), "step": m.group(3), "dd": dd, "text": m.group(6).strip()})
    s["jcl_error"] = bool(re.search(r"JCL ERROR|IEF453I|IEFC6\d\dI", up))

    m = re.search(r"FROM COMPILE UNIT\s+(\S+)\s+AT ENTRY POINT\s+(\S+)(?:\s+AT STATEMENT\s+(\d+))?"
                  r"\s+AT COMPILE UNIT OFFSET\s+\+?([0-9A-F]+)", up)
    if m:
        s["le"] = {"program": m.group(1), "entry": m.group(2),
                   "statement": int(m.group(3)) if m.group(3) else None, "offset": m.group(4)}
    else:
        m = re.search(r"AT STATEMENT\s+(\d+)", up)
        if m:
            s["le"] = {"program": None, "entry": None, "statement": int(m.group(1)), "offset": None}

    for m in re.finditer(r"IGZ\d{4}\w\s+.*?FILE\s+(\S+)\s+IN PROGRAM\s+(\S+).*?STATUS CODE WAS\s+(\d{2})", up, re.S):
        s["file_errors"].append({"file": m.group(1).strip("'."), "program": m.group(2).strip("'."), "status": m.group(3)})
    for line in up.splitlines():
        if "STATUS" in line and not line.lstrip().startswith(("IGZ", "IEF", "$HASP")):
            for st in re.findall(r"STATUS\s*(?:CODE)?\s*(?:IS|WAS|=|:)?\s*'?(\d{2})'?\b", line):
                if st not in ("00", "10") and len(s["file_errors"]) < 10 \
                        and st not in {fe["status"] for fe in s["file_errors"]}:
                    s["file_errors"].append({"file": None, "program": None, "status": st, "line": line.strip()[:120]})

    for code in re.findall(r"SQLCODE\s*(?:=|:|IS)?\s*(-\d{1,4}|\+?100)\b", up):
        code = code.lstrip("+")
        if code not in s["sqlcodes"]:
            s["sqlcodes"].append(code)

    seen = set()
    for line in text.splitlines():
        mm = MSG_RE.search(line.upper())
        if mm and mm.group(1) not in ("IEF142I", "IEF373I", "IEF374I", "IEF236I", "IEF237I", "IEF285I",
                                      "IEF033I", "IEF403I", "IEF404I", "IEF375I", "IEF376I"):
            key = line.strip()
            if key not in seen:
                seen.add(key)
                s["messages"].append(key[:160])
    s["messages"] = s["messages"][:25]

    names = re.findall(r"\bMEMBER=([A-Z0-9#@$]+)", up) + re.findall(r"\bPGM=([A-Z0-9#@$]+)", up) \
        + re.findall(r"\bPROG(?:RAM)?\(([A-Z0-9#@$]+)\)", up) + re.findall(r"IN PROGRAM\s+([A-Z0-9#@$]+)", up)
    if s["le"] and s["le"]["program"]:
        names.append(s["le"]["program"])
    s["programs_mentioned"] = list(dict.fromkeys(names))
    return s


# --------------------------------------------------------------------------- #
# Diagnosis                                                                    #
# --------------------------------------------------------------------------- #
def pick_program(spool, text, idx):
    progs = idx["programs"]
    up = text.upper()
    scores = {}
    for name, info in progs.items():
        sc = 0
        if spool["le"] and spool["le"]["program"] in (name, info["name"]):
            sc += 100
        if any(fe.get("program") in (name, info["name"]) for fe in spool["file_errors"]):
            sc += 80
        if name in spool["programs_mentioned"]:
            sc += 20
        sc += sum(3 for _, lit in info["displays"] if lit.upper() in up)
        if sc:
            scores[name] = sc
    if not scores:
        return None
    return progs[max(scores, key=scores.get)]


ERROR_WORDS = re.compile(r"ERROR|FAIL|INVALID|NOT FOUND|ABORT|CANNOT|UNABLE|ROLLBACK|DEADLOCK|BAD ")


def classify(spool):
    if spool["jcl_error"] and not spool["abend"]:
        return "jcl_error"
    if spool["abend"]:
        return "abend"
    if spool["sqlcodes"] and any(c != "100" for c in spool["sqlcodes"]):
        return "sql_error"
    if spool["file_errors"]:
        return "file_error"
    if any(st["rc"] not in ("0000", "NOT RUN") for st in spool["steps"]):
        return "bad_return_code"
    return "no_failure_found"


def statement_patterns(spool, prog):
    code = spool["abend"] or ""
    pats = []
    if code in ("S0C7", "S0CA"):
        pats.append(r"\b(COMPUTE|ADD|SUBTRACT|MULTIPLY|DIVIDE)\b")
    if code in ("S0CB", "S0C9"):
        pats.append(r"\bDIVIDE\b|\bCOMPUTE\b.*/|^\s+.*\s/\s")
    if code in ("S0C4", "S0C5"):
        pats.append(r"\bCALL\b|\bSET\s+ADDRESS\b|\b[A-Z0-9-]+\s*\(\s*[A-Z0-9-]+\s*\)")
    if code == "S0C1":
        pats.append(r"\bCALL\b")
    if code in ("S322", "S222"):
        pats.append(r"\bPERFORM\b.*\bUNTIL\b|\bREAD\b")
    if code in ("SB37", "SD37", "SE37"):
        pats.append(r"\bWRITE\b")
    if code == "S806":
        pats.append(r"\bCALL\b")
    if spool["sqlcodes"]:
        pats.append(r"\bEXEC\s+SQL\b")
    files = [fe for fe in spool["file_errors"]]
    if files or code in ("U4038", "S013", "S213"):
        names = set()
        for fe in files:
            if fe.get("file"):
                names.add(fe["file"])
                if fe["file"] in prog["selects"]:
                    names.add(prog["selects"][fe["file"]])
        if names:
            alt = "|".join(re.escape(n) for n in names)
            pats.append(rf"\b(OPEN|CLOSE|READ|WRITE|REWRITE|START|DELETE)\b.*\b({alt})\b|\b({alt})\b")
        else:
            pats.append(r"\b(OPEN|READ|WRITE|REWRITE|START)\b")
    if not pats:
        pats.append(r"\b(COMPUTE|DIVIDE|CALL|OPEN|READ|WRITE|EXEC\s+SQL)\b")
    return [re.compile(p) for p in pats]


def paragraph_of(prog, line_no):
    cur = None
    for no, name in prog["paragraphs"]:
        if no <= line_no:
            cur = (no, name)
        else:
            break
    return cur


def last_reached_display(prog, text):
    """Walk the spool in order and remember the last DISPLAY of this program that printed."""
    last = None
    lits = sorted(prog["displays"], key=lambda d: -len(d[1]))
    for line in text.upper().splitlines():
        for no, lit in lits:
            if lit.upper() in line:
                last = (no, lit, line.strip()[:120])
                break
    return last


def diagnose(spool_text, idx):
    spool = parse_spool(spool_text)
    kind = classify(spool)
    prog = pick_program(spool, spool_text, idx)
    kb_abend = KB["abends"].get(spool["abend"] or "", None)

    result = {"kind": kind, "spool": spool, "program": None, "knowledge": kb_abend, "suspects": [],
              "last_display": None, "statement_line": None, "file_status_meaning": {}, "sqlcode_meaning": {},
              "message_meaning": {}, "jcl_locations": []}
    for fe in spool["file_errors"]:
        result["file_status_meaning"][fe["status"]] = KB["file_status"].get(fe["status"], "unknown status")
    for c in spool["sqlcodes"]:
        result["sqlcode_meaning"][c] = KB["sqlcode"].get(c, "see DB2 Codes manual")
    for msg in spool["messages"]:
        mid = msg.split()[0] if msg.split() else ""
        mm = MSG_RE.search(msg.upper())
        if mm and mm.group(1) in KB["messages"]:
            result["message_meaning"][mm.group(1)] = KB["messages"][mm.group(1)]

    if not prog:
        return result

    root = idx["root"]
    def rel(p):
        try:
            return str(Path(p).resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(Path(p)).replace("\\", "/")
    task_dir = prog["task_dir"]
    data_dir = task_dir / "DATA"
    result["program"] = {
        "name": prog["name"], "source": rel(prog["source"]), "task_dir": rel(task_dir),
        "jcl": [rel(j) for j in prog["jcl"]],
        "copybooks": [rel(idx["copybooks"][c]) for c in prog["copybooks"] if c in idx["copybooks"]],
        "data_files": [rel(f) for f in sorted(data_dir.glob("*"))] if data_dir.is_dir() else [],
        "selects": prog["selects"], "calls": prog.get("calls", []), "called_by": prog.get("called_by", []),
    }

    ld = last_reached_display(prog, spool_text)
    if ld and result["kind"] in ("no_failure_found", "bad_return_code") and ERROR_WORDS.search(ld[1].upper()):
        result["kind"] = "program_reported_error"
    if ld:
        para = paragraph_of(prog, ld[0])
        result["last_display"] = {"line": ld[0], "literal": ld[1], "spool_line": ld[2],
                                  "paragraph": para[1] if para else None}

    stmt = spool["le"]["statement"] if spool["le"] else None
    if stmt and stmt in prog["lines"]:
        result["statement_line"] = {"line": stmt, "code": prog["lines"][stmt].strip(),
                                    "note": "LE statement number mapped to source line; exact only if the program has no COPY members"}

    if spool["jcl_issues"]:
        locs = []
        for ji in spool["jcl_issues"]:
            for j in prog["jcl"]:
                for no, line in enumerate(j.read_text(encoding="latin-1").splitlines(), 1):
                    if re.match(rf"^//(?:\S+\.)?{re.escape(ji['dd'])}\s+DD\b", line.upper()):
                        locs.append({"file": rel(j), "line": no, "code": line.rstrip()[:72], "issue": ji["text"]})
        result["jcl_locations"] = locs
    if result["kind"] == "jcl_error":
        return result   # the program never ran - the problem is in the JCL, not the COBOL

    pats = statement_patterns(spool, prog)
    anchor = ld[0] if ld else (stmt or 0)
    anchor_para = paragraph_of(prog, anchor) if anchor else None
    proc_start = prog["paragraphs"][0][0] if prog["paragraphs"] else 0
    cands = []
    for no, code in prog["lines"].items():
        if no < proc_start:
            continue
        up = code.upper()
        if any(p.search(up) for p in pats):
            para = paragraph_of(prog, no)
            score = 0
            if stmt and no == stmt:
                score += 100
            if (spool["abend"] or "") in ("S0C7", "S0CA", "S0CB", "S0C9"):
                if any(re.search(rf"\b{re.escape(f)}\b", up) for f in prog.get("fd_fields", ())):
                    score += 15   # uses a field read straight from a file = external data
                if re.search(r"\bADD\s+1\s+TO\b", up):
                    score -= 8    # counters are rarely the culprit
            if anchor_para and para and para[0] == anchor_para[0]:
                score += 30 + (10 if no >= anchor else 0)
            if anchor and no >= anchor:
                score += 10
            score -= min(abs(no - anchor), 400) / 40 if anchor else 0
            cands.append({"line": no, "paragraph": para[1] if para else None, "code": code.strip(), "score": round(score, 1)})
    cands.sort(key=lambda c: (-c["score"], c["line"]))
    result["suspects"] = cands[:MAX_SUSPECTS]
    return result


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #
def plain_words(r):
    """A short explanation a beginner understands."""
    s, p = r["spool"], r["program"]
    who = f"program {p['name']}" if p else "the program"
    lines = []
    if r["kind"] == "jcl_error":
        what = "; ".join(f"DD {j['dd']} in step {j['step']}: {j['text'].lower()}" for j in s["jcl_issues"]) or "a JCL statement is wrong"
        lines.append(f"The job never started {who}: z/OS rejected the JCL ({what}).")
        lines.append("Fix the JCL (create the dataset first, or correct its name), then resubmit.")
    elif r["kind"] == "abend":
        k = r["knowledge"]
        lines.append(f"{(who[0].upper() + who[1:])} crashed with {s['abend']}" + (f" - {k['title'].lower()}." if k else "."))
        if s["file_errors"]:
            fe = s["file_errors"][0]
            lines.append(f"A file operation failed with FILE STATUS {fe['status']}: "
                         f"{r['file_status_meaning'].get(fe['status'], '').lower()}.")
        if r["statement_line"]:
            lines.append(f"It stopped at line {r['statement_line']['line']}: {r['statement_line']['code']}")
        elif r["suspects"]:
            c = r["suspects"][0]
            lines.append(f"Most likely place: line {c['line']} in {c['paragraph']}: {c['code']}")
        if k:
            lines.append(f"Most common reason: {k['causes'][0]}.")
    elif r["kind"] == "sql_error":
        for c, m in r["sqlcode_meaning"].items():
            lines.append(f"DB2 returned SQLCODE {c}: {m}.")
    elif r["kind"] == "file_error":
        for st, m in r["file_status_meaning"].items():
            lines.append(f"A file operation returned FILE STATUS {st}: {m}.")
    elif r["kind"] == "program_reported_error":
        lines.append(f"{(who[0].upper() + who[1:])} did not crash, but it reported an error: "
                     f"\"{r['last_display']['spool_line']}\".")
    elif r["kind"] == "bad_return_code":
        bad = [st for st in s["steps"] if st["rc"] not in ("0000", "NOT RUN")]
        lines.append("A step ended with a non-zero return code: " + ", ".join(f"{b['step']}={b['rc']}" for b in bad) + ".")
        lines.append("RC 4 is a warning, RC 8 or more means the step failed. Check the step's messages below.")
    else:
        lines.append("No failure was found in this job output.")
    return lines


def render_markdown(r):
    s = r["spool"]
    out = [f"# Abend Doctor report - {s['job'] or '?'} {s['jobid'] or ''}".rstrip(), ""]
    headline = {
        "abend": f"**ABEND {s['abend']}**" + (f" (reason {s['reason']})" if s["reason"] else ""),
        "jcl_error": "**JCL ERROR** - the step never started",
        "sql_error": f"**DB2 error** SQLCODE {', '.join(s['sqlcodes'])}",
        "file_error": "**File I/O error** (FILE STATUS)",
        "bad_return_code": "**Non-zero return code**",
        "program_reported_error": "**The program itself reported an error** (see last DISPLAY)",
        "no_failure_found": "No failure found in this output",
    }[r["kind"]]
    out.append(f"- Result: {headline}")
    if s["failed_step"]:
        out.append(f"- Failed step: `{s['failed_step']}`")
    if r["knowledge"]:
        out.append(f"- Meaning: {r['knowledge']['title']}")
    if r["program"]:
        p = r["program"]
        out.append(f"- Program: `{p['name']}` -> `{p['source']}`")
    out.append("")

    out += ["## In plain words", ""] + [f"> {l}" for l in plain_words(r)] + [""]
    if r.get("source_from"):
        out.append(f"_Source read from z/OS: `{r['source_from']}`_\n")

    if s["steps"]:
        out += ["## Steps", "", "| Step | RC / Abend |", "|---|---|"]
        out += [f"| {st['step']} | {st['rc']} |" for st in s["steps"]]
        out.append("")

    out += ["## Evidence from the spool", ""]
    if s["le"]:
        le = s["le"]
        parts = [f"compile unit `{le['program']}`"]
        if le["statement"]:
            parts.append(f"statement `{le['statement']}`")
        if le["offset"]:
            parts.append(f"offset `+{le['offset']}`")
        out.append("- Language Environment: " + ", ".join(parts))
    for ji in s["jcl_issues"]:
        out.append(f"- `{ji['msg']}` step `{ji['step']}` DD `{ji['dd']}`: {ji['text']}")
    for fe in s["file_errors"]:
        where = f" file `{fe['file']}`" if fe.get("file") else ""
        out.append(f"- FILE STATUS `{fe['status']}`{where}: {r['file_status_meaning'].get(fe['status'])}")
    for c, meaning in r["sqlcode_meaning"].items():
        out.append(f"- SQLCODE `{c}`: {meaning}")
    for mid, meaning in r["message_meaning"].items():
        out.append(f"- `{mid}`: {meaning}")
    if s["messages"]:
        out += ["", "```"] + s["messages"][:15] + ["```"]
    out.append("")

    if r["program"]:
        p = r["program"]
        out += ["## Where it failed", ""]
        for loc in r["jcl_locations"]:
            out.append(f"- JCL `{loc['file']}` line {loc['line']}: `{loc['code'].strip()}` -> {loc['issue']}")
        if r["statement_line"]:
            sl = r["statement_line"]
            out.append(f"- Statement from LE -> line {sl['line']}: `{sl['code']}` ({sl['note']})")
        if r["last_display"]:
            ld = r["last_display"]
            out.append(f"- Last DISPLAY that printed: line {ld['line']} in `{ld['paragraph']}` -> spool: `{ld['spool_line']}`")
            if r["kind"] == "abend":
                out.append("  (the abend happened AFTER this point)")
        if r["suspects"]:
            out += ["", "Suspect statements (ranked):", "", "| Line | Paragraph | Code |", "|---|---|---|"]
            out += [f"| {c['line']} | {c['paragraph']} | `{c['code']}` |" for c in r["suspects"]]
        out += ["", "## Related files", ""]
        out.append(f"- Task folder: `{p['task_dir']}`")
        for key, label in (("jcl", "JCL"), ("copybooks", "Copybook"), ("data_files", "Data")):
            for f in p[key]:
                out.append(f"- {label}: `{f}`")
        if p["selects"]:
            out.append("- DD -> file: " + ", ".join(f"`{dd}` -> `{fn}`" for dd, fn in p["selects"].items()))
        out.append("")
    elif r["kind"] != "no_failure_found":
        out += ["## Where it failed", "", "The failing program was not found in this repository.", ""]

    if r["knowledge"]:
        out += ["## Typical causes", ""] + [f"- {c}" for c in r["knowledge"]["causes"]] + [""]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Zowe CLI (read-only)                                                         #
# --------------------------------------------------------------------------- #
READ_ONLY = {("zos-jobs", "list", "jobs"), ("zos-jobs", "view", "all-spool-content"),
             ("zos-files", "view", "data-set")}


def zowe(*args):
    if tuple(args[:3]) not in READ_ONLY:
        raise SystemExit(f"refusing non read-only zowe command: {args}")
    exe = shutil.which("zowe") or shutil.which("zowe.cmd")
    if not exe:
        raise SystemExit("Zowe CLI not found. Install: npm install -g @zowe/cli")
    res = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise SystemExit(f"zowe failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout


def list_jobs(prefix):
    data = json.loads(zowe("zos-jobs", "list", "jobs", "--prefix", prefix, "--rfj"))
    jobs = data.get("data") or []
    return sorted(jobs, key=lambda j: int(re.sub(r"\D", "", j.get("jobid", "0")) or 0), reverse=True)


def is_bad(job):
    rc = (job.get("retcode") or "").upper()
    return rc and rc not in ("CC 0000",)


def program_name_from_spool(spool):
    if spool["le"] and spool["le"]["program"]:
        return spool["le"]["program"]
    for fe in spool["file_errors"]:
        if fe.get("program"):
            return fe["program"]
    skip = {"IKJEFT01", "IEFBR14", "IEBGENER", "IDCAMS", "SORT", "ICETOOL", "IGYCRCTL", "IEWL",
            "HEWL", "IEBCOPY", "DSNUTILB", "IKJEFT1B", "IRXJCL", "BPXBATCH", "ICEMAN", "DFSRRC00"}
    for n in reversed(spool["programs_mentioned"]):
        if n not in skip and not n.startswith("&"):
            return n
    return None


def source_dsn_candidates(spool_text, program, srclibs):
    up = spool_text.upper()
    found = re.findall(rf"DSN=([A-Z0-9#@$.]+)\({re.escape(program)}\)", up)
    cands = list(dict.fromkeys(f"{d}({program})" for d in found if ".LOAD" not in d))
    cands += [f"{lib}({program})" for lib in srclibs]
    return list(dict.fromkeys(cands))


def fetch_source_from_mainframe(spool_text, program, srclibs, outdir):
    """Download the COBOL source of the failing program from z/OS (read-only)."""
    for dsn in source_dsn_candidates(spool_text, program, srclibs):
        try:
            text = zowe("zos-files", "view", "data-set", dsn)
        except SystemExit:
            continue
        if "PROCEDURE" in text.upper():
            outdir.mkdir(parents=True, exist_ok=True)
            f = outdir / f"{program}.cbl"
            f.write_text(text, encoding="utf-8")
            return f, dsn
    return None, None


def fetch_job(jobid, outdir):
    text = zowe("zos-jobs", "view", "all-spool-content", jobid)
    outdir.mkdir(parents=True, exist_ok=True)
    f = outdir / f"{jobid}.txt"
    f.write_text(text, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Abend Doctor - z/OS job failure triage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("index", help="show what the doctor knows about a repo")
    a.add_argument("--repo", required=True)

    a = sub.add_parser("jobs", help="list recent jobs on z/OS and mark failed ones (read-only)")
    a.add_argument("--prefix", default="*")

    a = sub.add_parser("view", help="show a dataset or member from z/OS (read-only), e.g. input data")
    a.add_argument("dsn")

    a = sub.add_parser("diagnose", help="diagnose a failed job")
    a.add_argument("--repo", required=True)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--spool", help="spool file or directory already downloaded")
    g.add_argument("--job", help="job id to fetch with Zowe CLI (read-only)")
    g.add_argument("--latest", action="store_true", help="fetch the newest failed job")
    a.add_argument("--prefix", default="*")
    a.add_argument("--out", default="reports")
    a.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    a.add_argument("--offline", action="store_true", help="do not download source from z/OS")

    cfg = Path("doctor.config.json")
    conf = json.loads(cfg.read_text()) if cfg.exists() else {}
    default_repo = conf.get("repo")
    for sp in (sub.choices["index"], sub.choices["diagnose"]):
        for act in sp._actions:
            if act.dest == "repo":
                act.required = False
                act.default = default_repo
    args = ap.parse_args(argv)

    if args.cmd == "index":
        idx = index_repo(args.repo)
        uniq = {id(p): (n, p) for n, p in sorted(idx["programs"].items(), reverse=True)}
        for name, p in sorted(uniq.values(), key=lambda x: x[0]):
            print(f"{name:10} {Path(p['source']).relative_to(idx['root'])}  jcl={len(p['jcl'])} "
                  f"paragraphs={len(p['paragraphs'])} displays={len(p['displays'])} files={list(p['selects'])}")
        print(f"\n{len(uniq)} programs, {len(idx['copybooks'])} copybooks")
        return

    if args.cmd == "view":
        print(zowe("zos-files", "view", "data-set", args.dsn))
        return

    if args.cmd == "jobs":
        for j in list_jobs(args.prefix)[:30]:
            flag = "FAILED" if is_bad(j) else ""
            print(f"{j.get('jobid',''):10} {j.get('jobname',''):9} {str(j.get('retcode')):16} {flag}")
        return

    outdir = Path(args.out)
    if args.spool:
        spool_path = Path(args.spool)
    elif args.job:
        spool_path = fetch_job(args.job, outdir / "spool")
    else:
        bad = [j for j in list_jobs(args.prefix) if is_bad(j)]
        if not bad:
            raise SystemExit("no failed jobs found")
        print(f"newest failed job: {bad[0]['jobid']} {bad[0]['jobname']} {bad[0]['retcode']}", file=sys.stderr)
        spool_path = fetch_job(bad[0]["jobid"], outdir / "spool")

    spool_text = load_spool(spool_path)
    idx = index_repo(args.repo) if args.repo and Path(args.repo).is_dir() else None
    spool_info = parse_spool(spool_text)
    pgm = program_name_from_spool(spool_info)
    source_from = None
    if pgm and (idx is None or pgm not in idx["programs"]) and not args.offline:
        # the normal path: get the source straight from the mainframe
        src, dsn = fetch_source_from_mainframe(spool_text, pgm, conf.get("srclibs", []), outdir / "src")
        if src:
            idx = index_repo(src.parent)
            source_from = dsn
            print(f"source of {pgm} downloaded from z/OS: {dsn}", file=sys.stderr)
    if idx is None:
        idx = {"root": Path.cwd(), "programs": {}, "copybooks": {}}
    result = diagnose(spool_text, idx)
    result["source_from"] = source_from
    md = render_markdown(result)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = result["spool"]["jobid"] or spool_path.stem
    (outdir / f"{stem}.md").write_text(md, encoding="utf-8")
    (outdir / f"{stem}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str) if args.json else md)
    print(f"\n[saved {outdir / (stem + '.md')}]", file=sys.stderr)


if __name__ == "__main__":
    main()
