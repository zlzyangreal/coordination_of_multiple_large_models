#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Record one deterministic test run as evidence a reviewer can read but cannot fake.

A read-only reviewer on Windows cannot create a temp directory, so it cannot execute the
suite itself. The lead runs the command, this script records the exact argv, cwd, exit code,
duration, artifact hashes, the full log and a bounded summary; the envelope carries the
record and the log becomes a review unit. The summary is deliberately shorter than the log:
a result that copies the summary cannot satisfy `test_refs`.

Usage:
    python scripts/test_evidence.py record --name tests -- python scripts/selftest_models.py
    python scripts/test_evidence.py record --name e2e --artifact _work/out.bin -- .\\run-e2e.cmd

Exit code: the recorded command's exit code (124 on timeout), unless --allow-fail is given.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402

EVIDENCE = os.path.join("_work", "evidence")
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def summary_of(text):
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-20:])
    return tail[-1200:]


def record(args):
    if not SAFE.match(args.name):
        print("[FAIL] --name may contain only letters, digits, dot, dash, underscore",
              file=sys.stderr)
        return 2
    command = [part for part in args.command if part != "--"]
    if not command:
        print("[FAIL] no command after `--`", file=sys.stderr)
        return 2
    project = os.path.abspath(args.project)
    out_dir = os.path.join(project, EVIDENCE)
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, f"{args.name}.log")
    record_path = os.path.join(out_dir, f"{args.name}.json")
    started = common.now_iso()
    began = time.time()
    timed_out = False
    try:
        done = subprocess.run(command, cwd=project, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)
        code, stdout, stderr = done.returncode, done.stdout or "", done.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr += f"\n[test_evidence] timeout after {args.timeout}s\n"
    except OSError as exc:
        print(f"[FAIL] cannot run command: {exc}", file=sys.stderr)
        return 2
    duration = round(time.time() - began, 2)
    combined = stdout + ("\n[stderr]\n" + stderr if stderr else "")
    with open(log_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(combined)
    artifacts = []
    for path in args.artifact:
        full = os.path.abspath(path)
        artifacts.append({"path": path, "exists": os.path.isfile(full),
                          "sha256": common.sha256_file(full) if os.path.isfile(full) else None})
    record = {
        "id": args.name,
        "protocol_version": 2,
        "command": " ".join(shlex.quote(part) for part in command),
        "argv": command,
        "cwd": project,
        "exit_code": code,
        "timed_out": timed_out,
        "duration_s": duration,
        "started_at": started,
        "finished_at": common.now_iso(),
        "artifacts": artifacts,
        "log_path": os.path.relpath(log_path, project).replace(os.sep, "/"),
        "log_sha256": common.sha256_file(log_path),
        "stdout_sha256": sha(stdout),
        "stderr_sha256": sha(stderr),
        "summary": summary_of(combined),
    }
    common.write_json(record_path, record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"[OK] evidence {args.name}: exit {code} in {duration}s -> {record_path}",
          file=sys.stderr)
    if args.allow_fail:
        return 0
    return code if code >= 0 else 1


def main():
    ap = argparse.ArgumentParser(description="record test evidence for a review envelope")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("record")
    p.add_argument("--name", required=True)
    p.add_argument("--project", default=".")
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--allow-fail", action="store_true",
                   help="record a failing command but exit 0")
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    if args.action == "record":
        return record(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
