#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a review request envelope for any artifact: hash + mtime + units + closed findings.

Contract decisions baked in (feedback v1.4.0, BUG-1/3/5/7):
    * the reviewer writes nothing: `artifact_access: read-only` + `result_capture: runner`;
      the result comes back as the final message and runner `-o` captures it;
    * `output_schema` is an absolute path to a real JSON Schema, not the string "findings[]";
    * `prior_findings` is generated from the review ledger - a human cannot forget it;
    * the envelope records which contract version it was built under.

"Units" are whatever the reviewer must actually open: a file, a page, a diff, a section,
an endpoint, a cell range. There is no fixed pagination model.

Usage:
    python scripts/build_envelope.py --artifact src/ --out _work/review/env.yaml \
        --ledger _work/review_ledger.md --unit "api::src/api.py::handle_request" \
        --unit "tests::tests/test_api.py::"
    python scripts/build_envelope.py ... --units-file units.tsv
    python scripts/build_envelope.py ... --init-ledger      # first round, header only
    python scripts/build_envelope.py ... --capture direct   # explicit, probes writability

Exit codes:
    0  written
    2  invalid input (missing artifact/ledger, path collision, read-only + direct, bad units)
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
    "| id | round | where | status | reason | owner | budget_change |\n"
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


SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
             ".mypy_cache", ".pytest_cache", ".idea"}


def tree_hash(root):
    """Deterministic hash over relative paths + file hashes, skipping VCS/dep dirs."""
    h = hashlib.sha256()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            h.update(rel.encode("utf-8") + b"\0")
            h.update(sha256(full).encode("ascii") + b"\0")
            count += 1
    return h.hexdigest(), count


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
    with open(SKILL, encoding="utf-8-sig") as fh:
        text = fh.read()
    m = re.search(r"^\s*version:\s*(\S+)\s*$", text, re.M)
    return (m.group(1) if m else "unknown"), ""


def parse_ledger(path):
    """Rows from the review ledger table; closed items become prior_findings."""
    with open(path, encoding="utf-8-sig") as fh:
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


def load_units(args):
    """Units from --units-file (YAML list or TSV) and/or repeated --unit id::locator::expected."""
    units = []
    if args.units_file:
        with open(args.units_file, encoding="utf-8-sig") as fh:
            text = fh.read()
        stripped = text.lstrip()
        if stripped.startswith("- ") or stripped.startswith("units:") or stripped.startswith("#"):
            try:
                import yaml
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    data = data.get("units") or []
                for item in data or []:
                    if isinstance(item, dict) and item.get("id"):
                        units.append({
                            "id": str(item["id"]),
                            "kind": str(item.get("kind") or "unit"),
                            "locator": str(item.get("locator") or ""),
                            "expected": str(item.get("expected") or ""),
                        })
            except Exception as exc:  # noqa: BLE001
                print(f"[FAIL] cannot parse units file: {exc}")
                return None
        else:
            for line in text.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("\t")]
                if len(parts) < 2:
                    print(f"[FAIL] units TSV needs id<TAB>locator: {line!r}")
                    return None
                units.append({
                    "id": parts[0],
                    "kind": parts[3] if len(parts) > 3 and parts[3] else "unit",
                    "locator": parts[1],
                    "expected": parts[2] if len(parts) > 2 else "",
                })
    for spec in args.unit or []:
        parts = [p.strip() for p in spec.split("::")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            print(f"[FAIL] --unit expects id::locator[::expected[::kind]]: {spec!r}")
            return None
        units.append({
            "id": parts[0],
            "kind": parts[3] if len(parts) > 3 and parts[3] else "unit",
            "locator": parts[1],
            "expected": parts[2] if len(parts) > 2 else "",
        })
    ids = [u["id"] for u in units]
    if len(ids) != len(set(ids)):
        print("[FAIL] duplicate unit ids")
        return None
    return units


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
    ap.add_argument("--artifact", required=True, help="file or directory the review covers")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", required=True, help="review_ledger.md; prior_findings come from it")
    ap.add_argument("--init-ledger", action="store_true", help="create an empty ledger (round 1)")
    ap.add_argument("--unit", action="append", default=[],
                    help="id::locator[::expected[::kind]]; repeatable")
    ap.add_argument("--units-file", default=None, help="YAML list or TSV (id<TAB>locator<TAB>expected<TAB>kind)")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--artifact-access", default="read-only",
                    choices=["read-only", "workspace-write", "danger-full-access"])
    ap.add_argument("--capture", default="runner", choices=["runner", "direct"],
                    help="runner (default) = reviewer writes nothing, -o captures the result")
    ap.add_argument("--schema", default=SCHEMA)
    ap.add_argument("--scope", default="")
    ap.add_argument("--review-input", default=None,
                    help="the exact input the reviewer reads (diff, dump, render list); hash to rounds.md")
    args = ap.parse_args()

    artifact = os.path.abspath(args.artifact)
    if not os.path.exists(artifact):
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

    units = load_units(args)
    if units is None:
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
    rid = "R" + datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.path.isdir(artifact):
        version_hash, file_count = tree_hash(artifact)
    else:
        version_hash, file_count = sha256(artifact), 1

    lines = [
        f"request_id: {rid}",
        f"skill_version: {version}",
        f"skill_pinned_at: {pinned_at or 'not-pinned'}",
        f"artifact: {artifact}",
        f"version_hash: {version_hash}",
        f"artifact_mtime: {mtime_iso(artifact)}",
        f"artifact_access: {args.artifact_access}",
        f"result_capture: {args.capture}",
        f"output_path: {posix(payload)}",
        f"result_path: {posix(result)}",
        f"output_schema: {posix(os.path.abspath(args.schema))}",
    ]
    if units:
        lines.append("units:")
        for u in units:
            lines += [f"  - id: {u['id']}",
                      f"    kind: {u['kind']}",
                      f"    locator: {posix(u['locator'])}",
                      f"    expected: \"{u['expected']}\""]
    else:
        lines.append("units: []")
    lines.append(f'scope: "{args.scope}"')
    lines += ["acceptance:", '  - ""']
    if closed:
        lines.append("prior_findings:")
        for row in closed:
            lines += [f"  - id: {row.get('id', '')}",
                      f"    where: \"{row.get('where', '') or ''}\"",
                      f"    status: {row.get('status', '')}",
                      f"    reason: \"{row.get('reason', '').replace(chr(34), chr(39))}\""]
    else:
        lines.append("prior_findings: []")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"[OK] envelope written: {out}")
    print(f"     contract=v{version} hash={version_hash[:12]}... files={file_count} "
          f"prior_findings={len(closed)} units={len(units)} capture={args.capture}")
    empty = [u["id"] for u in units if not u["expected"]]
    if empty:
        print(f"     fill expected values before sending: {', '.join(empty)}")
    if args.review_input:
        if not os.path.isfile(args.review_input):
            print(f"[FAIL] --review-input not found: {args.review_input}")
            return 2
        try:
            import session as session_mod
            session_mod.append_round("review-input", os.path.basename(args.review_input),
                                     sha256(args.review_input)[:8],
                                     mtime_iso(args.review_input), rid)
            print(f"     review input noted: {os.path.basename(args.review_input)} "
                  f"sha256={sha256(args.review_input)[:8]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] could not write round log: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
