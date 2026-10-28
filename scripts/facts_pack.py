#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Version and freeze a facts pack so a review cannot be run against stale numbers.

The hash is over the canonical body (UTF-8, LF newlines, the single `facts_hash` line
removed, trailing newline kept). `freeze` writes the header block last; `check` recomputes
it. Any scope or budget change must use `bump --reason`, which increments `facts_version`,
appends a row to `## version log`, and re-freezes.

Usage:
    python scripts/facts_pack.py freeze --path _work/facts_pack.md
    python scripts/facts_pack.py bump --reason "scope: add retry module"
    python scripts/facts_pack.py set-budget review_calls 3 --reason "third review approved"
    python scripts/facts_pack.py check [--json]
    python scripts/facts_pack.py show [--json]

Exit codes:
    0  ok
    1  malformed (duplicate/missing header on a check)
    2  missing file / bad invocation
    3  stale (body changed without a freeze) or unversioned
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_common as common  # noqa: E402

HEADER_RE = re.compile(r"^\s*(facts_version|facts_hash|frozen_at)\s*:")
DEFAULT = os.path.join("_work", "facts_pack.md")
VERSION_LOG = "## version log"


def _lines(path):
    text = common.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def _strip_header(lines):
    return [line for line in lines if not HEADER_RE.match(line)]


def _insert_header(lines, version, frozen_at, body_hash=None):
    heading = 0
    for index, line in enumerate(lines):
        if line.startswith("# "):
            heading = index
            break
    block = [f"facts_version: {version}", f"frozen_at: {frozen_at}"]
    if body_hash:
        block.append(f"facts_hash: {body_hash}")
    rest = lines[:heading + 1] + [""] + block + lines[heading + 1:]
    # Avoid three consecutive blank lines around the block.
    out = []
    for line in rest:
        if not line and out and out[-1] == "" and len(out) > 1 and out[-2] == "":
            continue
        out.append(line)
    return out


def _append_version_row(lines, version, at, reason):
    reason = (reason or "").replace("|", "/").replace("\n", " ").strip()
    row = f"| {version} | {at} | {reason} |"
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() == VERSION_LOG:
            start = index
            break
    if start is None:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", VERSION_LOG, "", "| version | at | reason |", "|---|---|---|", row, ""]
        return lines
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    insert_at = end
    for index in range(start + 1, end):
        if lines[index].strip().startswith("|") and not re.match(
                r"^\|\s*:?-{2,}", lines[index].strip()):
            insert_at = index + 1
    lines.insert(insert_at, row)
    return lines


def _current_version(lines):
    for line in lines:
        match = re.match(r"^\s*facts_version\s*:\s*(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def freeze(path, version=None, reason=None, bump=False):
    lines = _strip_header(_lines(path))
    current = _current_version(_lines(path))
    if bump:
        new_version = (current or 1) + 1
    elif version is not None:
        new_version = int(version)
    else:
        new_version = current or 1
    at = common.now_iso()
    if bump or reason:
        lines = _append_version_row(lines, new_version, at, reason or "re-freeze")
    without_hash = "\n".join(_insert_header(lines, new_version, at)).rstrip("\n") + "\n"
    body_hash = common.sha256_text(without_hash)
    final = _insert_header(lines, new_version, at, body_hash)
    text = "\n".join(final).rstrip("\n") + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return common.facts_info(path)


def set_budget(path, key, value, reason):
    """Change one budgets-table value, then bump+freeze. Returns facts_info."""
    if key not in ("review_calls", "review_minutes"):
        raise ValueError(f"unsupported budget {key!r}")
    lines = _lines(path)
    inside = False
    changed = False
    for index, line in enumerate(lines):
        if re.match(r"^##\s+budgets\b", line, re.I):
            inside = True
            continue
        if inside and re.match(r"^##\s+", line):
            break
        if not inside or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0].lower() == key:
            unit = " per milestone" if key == "review_calls" else " min"
            cells[1] = f"{int(value)}{unit}"
            lines[index] = "| " + " | ".join(cells) + " |"
            changed = True
            break
    if not changed:
        raise ValueError(f"budget row {key!r} not found in {path}")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    return freeze(path, bump=True, reason=reason)


def check(path):
    info = common.facts_info(path)
    if not info["exists"]:
        return 2, f"facts pack not found: {os.path.abspath(path)}", info
    if info["header_count"] == 0:
        return 3, ("facts pack has no facts_version/facts_hash; run `facts_pack.py freeze` "
                   "before building an envelope"), info
    if info["header_count"] > 1:
        return 1, "facts pack has more than one facts_hash header", info
    if not info["version"]:
        return 3, "facts pack has no facts_version; run `facts_pack.py freeze`", info
    if info["declared_hash"] != info["actual_hash"]:
        return 3, (f"facts pack is stale: body hash {info['actual_hash'][:12]}... != declared "
                   f"{str(info['declared_hash'])[:12]}...; run `facts_pack.py bump --reason ...` "
                   f"or re-freeze"), info
    return 0, f"facts pack v{info['version']} ok ({info['actual_hash'][:12]}...)", info


def main():
    ap = argparse.ArgumentParser(description="facts pack version/freeze/check")
    ap.add_argument("action", choices=("freeze", "bump", "check", "show", "set-budget"))
    ap.add_argument("--path", default=DEFAULT)
    ap.add_argument("--reason", default="")
    ap.add_argument("--version", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("extra", nargs="*")
    args = ap.parse_args()

    if args.action == "set-budget":
        if len(args.extra) != 2:
            print("[FAIL] set-budget needs a key and a value (review_calls N | "
                  "review_minutes N)", file=sys.stderr)
            return 2
        key, raw = args.extra
        try:
            value = int(raw)
        except ValueError:
            print(f"[FAIL] budget value must be an integer: {raw!r}", file=sys.stderr)
            return 2
        try:
            info = set_budget(args.path, key, value, args.reason or f"{key} -> {value}")
        except (OSError, ValueError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"[OK] {key}={value}; facts pack now v{info['version']}")
        return 0

    if args.action in ("freeze", "bump"):
        if not os.path.isfile(args.path):
            print(f"[FAIL] facts pack not found: {os.path.abspath(args.path)}", file=sys.stderr)
            return 2
        if args.action == "bump" and not args.reason.strip():
            print("[FAIL] bump needs --reason (why the facts changed)", file=sys.stderr)
            return 2
        try:
            info = freeze(args.path, version=args.version, reason=args.reason,
                          bump=args.action == "bump")
        except (OSError, ValueError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"[OK] facts pack v{info['version']} frozen "
                  f"({str(info['declared_hash'])[:12]}...) -> {info['path']}")
        return 0

    code, message, info = check(args.path)
    if args.action == "show":
        if not info["exists"]:
            print(message, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"path      : {info['path']}")
            print(f"version   : {info['version'] or '-'}")
            print(f"frozen_at : {info['frozen_at'] or '-'}")
            print(f"hash      : {str(info['declared_hash'])[:16]}... "
                  f"({'stale' if info['stale'] else 'ok'})")
            print(f"review    : {info['budgets'].get('review_calls')} calls / "
                  f"{info['budgets'].get('review_minutes')} min")
        return 0
    if not args.quiet:
        print(("[OK] " if code == 0 else "[FAIL] ") + message,
              file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
