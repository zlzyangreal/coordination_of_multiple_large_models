#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Session contract anchor: pin the skill version, detect drift, print the status line.

Why: the contract is a live file. If it is re-read mid-session, calls quietly start
following a different rule set (the v1.4.0 regression: the session began on v1.3.1 and
kept calling with `--ephemeral` after the file had changed). Pin at start, compare every
round, never switch mid-session.

Usage:
    python scripts/session.py pin                    # lock version + mtime in _work/skill_pin.json
    python scripts/session.py check                  # compare; exit 2 on drift, print deviation line
    python scripts/session.py status --stage 评审     # one status line (contract + quota)
    python scripts/session.py note --text v6.dump.txt --request-id R20261024-01
                                                     # append sha256/mtime of the actual input
Exit codes: 0 ok, 2 contract drift (check), 1 error.
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
SKILL = os.path.abspath(os.path.join(HERE, "..", "SKILL.md"))
PIN = os.path.join("_work", "skill_pin.json")
ROUNDS = os.path.join("_work", "rounds.md")


def read_contract(path=SKILL):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    version = re.search(r"^\s*version:\s*(\S+)\s*$", text, re.M)
    updated = re.search(r"^\s*updated:\s*(\S+)\s*$", text, re.M)
    return {
        "version": version.group(1) if version else "unknown",
        "updated": updated.group(1) if updated else "unknown",
        "mtime": datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
        .isoformat(timespec="seconds"),
        "skill_path": path,
    }


def load_pin():
    try:
        with open(PIN, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cmd_pin(args):
    current = read_contract()
    existing = load_pin()
    if existing and not args.force:
        print(f"already pinned: v{existing['version']} at {existing['pinned_at']}")
        if (existing.get("version"), existing.get("mtime")) != (current["version"], current["mtime"]):
            print(f"repository now: v{current['version']} (mtime {current['mtime']}) - "
                  f"continuing on the pin; use --force to re-pin")
        return 0
    payload = dict(current)
    payload["pinned_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(PIN), exist_ok=True)
    with open(PIN, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"pinned: SKILL v{payload['version']} ({payload['updated']}) mtime {payload['mtime']}")
    print(f"        -> {os.path.abspath(PIN)}")
    return 0


def cmd_check(args):
    pin = load_pin()
    current = read_contract()
    if not pin:
        print("[WARN] no pin yet: run `session.py pin` at the start of the session")
        print(f"       repository: v{current['version']} mtime {current['mtime']}")
        return 2 if args.strict else 0
    drift = (pin.get("version") != current["version"]) or \
            (pin.get("mtime") != current["mtime"])
    print(f"pinned : v{pin.get('version')} mtime {pin.get('mtime')} ({pin.get('pinned_at')})")
    print(f"current: v{current['version']} mtime {current['mtime']}")
    if drift:
        print(f"DEVIATION: contract updated to v{current['version']} - this session continues "
              f"on v{pin.get('version')}; record the deviation, do not switch mid-session")
        return 2
    print("same contract; no deviation")
    return 0


def cmd_status(args):
    pin = load_pin() or read_contract()
    stage = args.stage or "未填"
    quota_text = "quota unknown"
    if not args.no_quota:
        try:
            sys.path.insert(0, HERE)
            import quota as quota_mod  # noqa: PLC0415
            q = quota_mod.collect("auto")
            if q.get("remaining") is None:
                quota_text = "quota unknown"
            else:
                weekly = "unknown" if q.get("weekly_remaining") is None \
                    else f"{q['weekly_remaining']:.0f}%"
                quota_text = f"quota 5h {q['remaining']:.0f}% / week {weekly}"
        except Exception:  # noqa: BLE001
            quota_text = "quota unreadable"
    print(f"SKILL v{pin.get('version')} (pinned {str(pin.get('pinned_at'))[11:16]}) "
          f"| {quota_text} | 阶段：{stage}")
    return 0


def append_round(label, value, digest="-", mtime="-", request_id="-"):
    """Append one row to _work/rounds.md; used by `note` and by build_envelope."""
    os.makedirs(os.path.dirname(ROUNDS), exist_ok=True)
    new = not os.path.exists(ROUNDS)
    with open(ROUNDS, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# round log\n\n")
            fh.write("| time | kind | value | sha256(8) | mtime | request |\n")
            fh.write("|---|---|---|---|---|---|\n")
        fh.write(f"| {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} | {label} | "
                 f"{value} | {digest} | {mtime} | {request_id or '-'} |\n")
    return os.path.abspath(ROUNDS)


def cmd_note(args):
    target = args.text or args.message
    digest = mtime = "-"
    label = args.label or ("text-source" if args.text else "note")
    if args.text:
        with open(args.text, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:8]
        mtime = datetime.fromtimestamp(os.path.getmtime(args.text)).astimezone() \
            .isoformat(timespec="seconds")
    path = append_round(label, target, digest, mtime, args.request_id)
    print(f"noted: {label} {target} sha256={digest} -> {path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="pin / check / status / note")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pin")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_pin)
    c = sub.add_parser("check")
    c.add_argument("--strict", action="store_true", help="exit 2 when no pin exists")
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("status")
    s.add_argument("--stage", default="")
    s.add_argument("--no-quota", action="store_true")
    s.set_defaults(func=cmd_status)
    n = sub.add_parser("note")
    n.add_argument("--text", help="file whose sha256 + mtime belongs in the round log")
    n.add_argument("--message", help="free-text note instead of a file")
    n.add_argument("--label", default="")
    n.add_argument("--request-id", default="")
    n.set_defaults(func=cmd_note)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
