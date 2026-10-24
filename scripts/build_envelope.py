#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a review request envelope: hash + mtime + page_map + closed findings + schema path.

Contract decisions baked in (feedback v1.4.0, BUG-1/3/5/7):
    * the reviewer writes nothing: `artifact_access: read-only` + `result_capture: runner`;
      the result comes back as the final message and runner `-o` captures it;
    * `output_schema` is an absolute path to a real JSON Schema, not the string "findings[]";
    * `prior_findings` is generated from the review ledger - a human cannot forget it;
    * the envelope records which contract version it was built under.

Usage:
    python scripts/build_envelope.py --artifact report_v6.docx --out _work/review/env_v6.yaml \
        --ledger _work/review_ledger.md --pages 4,8,13 --scope "只审 2.1-2.4"
    python scripts/build_envelope.py ... --init-ledger      # first round, header only
    python scripts/build_envelope.py ... --capture direct   # explicit, probes writability

Exit codes:
    0  written
    2  invalid input (missing artifact/ledger, path collision, read-only + direct, not writable)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SKILL = os.path.abspath(os.path.join(HERE, "..", "SKILL.md"))
SCHEMA = os.path.abspath(os.path.join(HERE, "..", "assets", "result.schema.json"))
PIN = os.path.join("_work", "skill_pin.json")
LEDGER_HEADER = (
    "# review ledger\n\n"
    "| id | round | page | status | reason | owner | budget_change |\n"
    "|---|---|---|---|---|---|---|\n"
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(timespec="seconds")


def posix(path):
    return path.replace(os.sep, "/")


def contract():
    """Pinned contract when the session pinned one, else the current file."""
    try:
        with open(PIN, encoding="utf-8") as fh:
            pin = json.load(fh)
        return pin.get("version", "unknown"), pin.get("pinned_at", "")
    except (OSError, ValueError):
        pass
    with open(SKILL, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^\s*version:\s*(\S+)\s*$", text, re.M)
    return (m.group(1) if m else "unknown"), ""


def parse_ledger(path):
    """Rows from the review ledger table; closed items become prior_findings."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    header = None
    rows = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if "id" in cells and "status" in cells:
                header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        record = dict(zip(header, cells))
        if record.get("id"):
            rows.append(record)
    closed = [r for r in rows if r.get("status", "").lower() not in ("open", "")]
    return rows, closed


def probe_writable(directory):
    probe = os.path.join(directory, ".dsh_write_probe")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", required=True, help="review_ledger.md; prior_findings come from it")
    ap.add_argument("--init-ledger", action="store_true", help="create an empty ledger (round 1)")
    ap.add_argument("--pages", default="")
    ap.add_argument("--render-dir", default="_work/renders")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--artifact-access", default="read-only",
                    choices=["read-only", "workspace-write", "danger-full-access"])
    ap.add_argument("--capture", default="runner", choices=["runner", "direct"],
                    help="runner (default) = reviewer writes nothing, -o captures the result")
    ap.add_argument("--schema", default=SCHEMA)
    ap.add_argument("--scope", default="")
    ap.add_argument("--text-dump", default=None,
                    help="the text the reviewer will actually read; hash goes to _work/rounds.md")
    args = ap.parse_args()

    artifact = os.path.abspath(args.artifact)
    if not os.path.isfile(artifact):
        print(f"[FAIL] artifact not found: {artifact}")
        return 2
    if not os.path.isfile(args.schema):
        print(f"[FAIL] schema not found: {os.path.abspath(args.schema)}")
        return 2

    ledger = os.path.abspath(args.ledger)
    if not os.path.isfile(ledger):
        if not args.init_ledger:
            print(f"[FAIL] ledger not found: {ledger}")
            print("       round 1: add --init-ledger; later rounds must pass the real ledger")
            return 2
        os.makedirs(os.path.dirname(ledger), exist_ok=True)
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write(LEDGER_HEADER)
        print(f"[OK] ledger initialised: {ledger}")

    if args.artifact_access == "read-only" and args.capture == "direct":
        print("[FAIL] read-only + direct is impossible: the reviewer cannot write result_path")
        print("       use --capture runner (default) or grant write access deliberately")
        return 2
    if args.capture == "direct" and not probe_writable(os.path.dirname(ledger)):
        print("[FAIL] direct capture requires a writable result directory; probe failed")
        return 2

    out = os.path.abspath(args.out)
    payload = os.path.abspath(args.payload) if args.payload else \
        os.path.join(os.path.dirname(out), "payload.md")
    result = os.path.abspath(args.result) if args.result else \
        os.path.join(os.path.dirname(out), "result.json")
    if len({os.path.normcase(p) for p in (artifact, out, payload, result)}) < 4:
        print("[FAIL] artifact / out / payload / result must be four distinct paths")
        return 2

    _, closed = parse_ledger(ledger)
    version, pinned_at = contract()
    pages = [int(p.strip()) for p in args.pages.split(",") if p.strip().isdigit()]
    rid = "R" + datetime.now().strftime("%Y%m%d-%H%M%S")

    lines = [
        f"request_id: {rid}",
        f"skill_version: {version}",
        f"skill_pinned_at: {pinned_at or 'not-pinned'}",
        f"artifact: {artifact}",
        f"version_hash: {sha256(artifact)}",
        f"artifact_mtime: {mtime_iso(artifact)}",
        f"artifact_access: {args.artifact_access}",
        f"result_capture: {args.capture}",
        f"output_path: {posix(payload)}",
        f"result_path: {posix(result)}",
        f"output_schema: {posix(os.path.abspath(args.schema))}",
    ]
    if pages:
        lines.append("page_map:")
        for p in pages:
            img = posix(os.path.join(args.render_dir, f"p{p:03d}.png"))
            lines += [f"  - page: {p}", f"    image: {img}", '    expected_title: ""']
    lines.append(f'scope: "{args.scope}"')
    lines += ["acceptance:", '  - ""']
    if closed:
        lines.append("prior_findings:")
        for row in closed:
            lines += [f"  - id: {row.get('id', '')}",
                      f"    page: {row.get('page', '') or 0}",
                      f"    status: {row.get('status', '')}",
                      f"    reason: \"{row.get('reason', '').replace(chr(34), chr(39))}\""]
    else:
        lines.append("prior_findings: []")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"[OK] envelope written: {out}")
    print(f"     contract=v{version} hash={sha256(artifact)[:12]}... "
          f"prior_findings={len(closed)} capture={args.capture}")
    if pages:
        print(f"     fill {len(pages)} expected_title entries before sending")
    if args.text_dump:
        if not os.path.isfile(args.text_dump):
            print(f"[FAIL] --text-dump not found: {args.text_dump}")
            return 2
        try:
            import session as session_mod
            session_mod.append_round("text-source", os.path.basename(args.text_dump),
                                     sha256(args.text_dump)[:8],
                                     mtime_iso(args.text_dump), rid)
            print(f"     text source noted: {os.path.basename(args.text_dump)} "
                  f"sha256={sha256(args.text_dump)[:8]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] could not write round log: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
