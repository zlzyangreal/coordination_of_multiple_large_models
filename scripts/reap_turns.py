#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Find Codex turns stuck in `inProgress` and (optionally) close them as `interrupted`.

A killed / timed-out `codex exec` leaves its row in `thread_turns` with
`status='inProgress'` and `completed_at=NULL` forever. Every consumer of that table
(Codex UI, desktop pet, any dashboard) then shows "working" for a turn that no longer
exists. This script is the cleanup path; `run_gpt.py` calls it automatically on timeout.

Safety:
    * dry-run by default; only `--apply` writes;
    * touches nothing except rows whose status is exactly `inProgress`;
    * `--thread` narrows the update to one conversation;
    * takes a consistent SQLite backup before writing (never a raw file copy of a WAL db).

Usage:
    python scripts/reap_turns.py                       # list stale turns
    python scripts/reap_turns.py --older-than 5        # threshold in minutes
    python scripts/reap_turns.py --apply               # mark them interrupted
    python scripts/reap_turns.py --thread <id> --apply  # targeted (used by run_gpt)
"""
import argparse
import glob
import os
import sqlite3
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_DEFAULT = os.path.join(os.path.expanduser("~"), ".codex", "thread_history_1.sqlite")
SESSIONS = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def connect(db, readonly=True):
    if readonly:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    else:
        con = sqlite3.connect(db, timeout=10)
        con.execute("pragma busy_timeout=5000")
    return con


def rollout_path(thread_id):
    pattern = os.path.join(SESSIONS, "**", f"*{thread_id}*.jsonl")
    hits = glob.glob(pattern, recursive=True)
    return hits[0] if hits else None


def stale_turns(db=DB_DEFAULT, older_than_min=30.0, thread=None):
    """Rows in `inProgress` older than the threshold (or all rows of one thread)."""
    if not os.path.exists(db):
        return []
    con = connect(db)
    try:
        if thread:
            rows = con.execute(
                "select thread_id, turn_id, started_at, rollout_ordinal "
                "from thread_turns where status='inProgress' and thread_id=?", (thread,)
            ).fetchall()
        else:
            rows = con.execute(
                "select thread_id, turn_id, started_at, rollout_ordinal "
                "from thread_turns where status='inProgress'"
            ).fetchall()
    finally:
        con.close()
    now = time.time()
    out = []
    for thread_id, turn_id, started_at, ordinal in rows:
        age_min = (now - started_at) / 60.0 if started_at else None
        if older_than_min is not None and age_min is not None and age_min < older_than_min:
            continue
        out.append({
            "thread_id": thread_id,
            "turn_id": turn_id,
            "started_at": started_at,
            "age_min": round(age_min, 1) if age_min else None,
            "rollout_path": rollout_path(thread_id),
        })
    out.sort(key=lambda r: r["age_min"] or 0, reverse=True)
    return out


def mark_interrupted(rows, db=DB_DEFAULT, backup_dir=None, reason="interrupted"):
    """Mark the given (thread_id, turn_id) rows interrupted; returns (backup, count)."""
    if not rows:
        return None, 0
    backup = None
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(backup_dir, f"thread_history_{stamp}.sqlite")
        src = connect(db)
        dst = sqlite3.connect(backup)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    con = connect(db, readonly=False)
    updated = 0
    try:
        now = int(time.time())
        with con:
            for row in rows:
                cur = con.execute(
                    "update thread_turns set status='interrupted', completed_at=?, "
                    "duration_ms=coalesce(duration_ms, ? * 1000), error_json=coalesce(error_json, ?) "
                    "where thread_id=? and turn_id=? and status='inProgress'",
                    (now, max(0, now - (row.get("started_at") or now)),
                     '{"reason":"' + reason + '","source":"coordination-skill"}',
                     row["thread_id"], row["turn_id"]),
                )
                updated += cur.rowcount
    finally:
        con.close()
    return backup, updated


def main():
    ap = argparse.ArgumentParser(description="close Codex turns stuck in inProgress")
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--older-than", type=float, default=30.0,
                    help="minutes; ignored when --thread is given (default 30)")
    ap.add_argument("--thread", help="only this thread id; age filter off")
    ap.add_argument("--apply", action="store_true", help="write; default is dry-run")
    ap.add_argument("--backup-dir", default=os.path.join("_work", "backups"))
    args = ap.parse_args()

    rows = stale_turns(args.db, None if args.thread else args.older_than, args.thread)
    if not rows:
        print("no stuck turns" + (f" for {args.thread}" if args.thread else
                                   f" older than {args.older_than:.0f} min"))
        return 0
    print(f"{'thread':38} {'turn':38} {'age':>9}  rollout")
    for row in rows:
        rollout = "missing" if not row["rollout_path"] else os.path.basename(row["rollout_path"])
        age = f"{row['age_min']:.0f} min" if row["age_min"] is not None else "unknown"
        print(f"{row['thread_id']:38} {row['turn_id']:38} {age:>9}  {rollout}")
    if not args.apply:
        print(f"\n{len(rows)} row(s); dry-run. Re-run with --apply to mark them interrupted.")
        return 0
    backup, updated = mark_interrupted(rows, args.db, args.backup_dir)
    print(f"\nupdated {updated} row(s) -> interrupted")
    if backup:
        print(f"backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
